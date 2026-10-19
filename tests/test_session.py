import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from nare.events import Event
from nare.session import (
    SESSION_VERSION,
    Message,
    Session,
    Usage,
    append_user_text,
    dumps,
    loads,
    new_session,
    reopen,
    save,
    sent_back,
)
from nare.transport.anthropic import messages_from

THINKING = ("thinking", "redacted_thinking")


def test_usage_adds_every_field() -> None:
    total = Usage(1, 2, 3, 4) + Usage(10, 20, 30, 40)
    assert total == Usage(11, 22, 33, 44)


def test_new_session_starts_with_one_user_message() -> None:
    s = new_session("fix the bug")
    assert s.status == "working"
    assert s.turns == 0
    assert s.messages == [
        Message(role="user", content=[{"type": "text", "text": "fix the bug"}])
    ]
    assert s.id


def test_append_user_text_merges_into_a_trailing_user_message() -> None:
    s = new_session("first")
    append_user_text(s, "second")
    assert len(s.messages) == 1
    assert s.messages[0].content == [
        {"type": "text", "text": "first"},
        {"type": "text", "text": "second"},
    ]


def test_append_user_text_starts_a_new_turn_after_an_assistant_message() -> None:
    s = new_session("first")
    s.messages.append(
        Message(role="assistant", content=[{"type": "text", "text": "ok"}])
    )
    append_user_text(s, "second")
    assert len(s.messages) == 3
    assert s.messages[-1].role == "user"


def test_round_trip_preserves_the_session() -> None:
    s = new_session("go")
    s.usage = Usage(5, 6, 7, 8)
    s.turns = 2
    s.status = "blocked"
    s.questions = ["which file?"]
    s.stop_reason = "tool_use"
    back = loads(dumps(s))
    assert back == s


def test_events_do_not_survive_serialization() -> None:
    s = new_session("go")
    s.events.append(Event("progress", "thinking about it"))
    assert '"events"' not in dumps(s)
    assert loads(dumps(s)).events == []


def test_loads_rejects_a_foreign_version() -> None:
    raw = dumps(new_session("go")).replace(
        f'"version": {SESSION_VERSION}', '"version": 99'
    )
    with pytest.raises(ValueError, match="version 99"):
        loads(raw)


@pytest.mark.parametrize("body", ["[]", '"hello"', "42", "null", "true"])
def test_loads_rejects_json_that_is_not_an_object(body: str) -> None:
    with pytest.raises(ValueError, match="malformed session file"):
        loads(body)


def test_round_trip_is_equal_even_with_events_pending() -> None:
    s = new_session("go")
    s.events.append(Event("progress", "drained by run(), never persisted"))
    # events is compare=False precisely because dumps() drops it: __eq__ and
    # the persistence contract have to agree, or a resumed session compares
    # unequal to the one it was written from.
    assert loads(dumps(s)) == s
    assert loads(dumps(s)).events == []


def test_dumps_redacts_the_transcript() -> None:
    # The session file lands in a git checkout; it cannot be the one surface
    # that keeps secrets in the clear while events are scrubbed.
    s = new_session("go")
    s.messages.append(
        Message(
            role="user",
            content=[
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "exit 0\nANTHROPIC_API_KEY=sk-ant-" + "Z" * 30,
                }
            ],
        )
    )
    text = dumps(s)
    assert "sk-ant-" not in text
    assert "[redacted]" in text
    assert loads(text).status == "working"


def env_session(secret: str = "AUTH_TOKEN=Xk92mQ7vLp") -> Session:
    """A session that read a .env file and thought about what it found."""
    s = new_session("which port does the app use?")
    s.messages += [
        Message(
            "assistant",
            [
                {"type": "thinking", "thinking": "Read .env.", "signature": "sigA"},
                {"type": "tool_use", "id": "c1", "name": "read", "input": {}},
            ],
        ),
        Message(
            "user",
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "c1",
                    "content": f"{secret}\nPORT=8080",
                    "is_error": False,
                }
            ],
        ),
        Message(
            "assistant",
            [
                {
                    "type": "thinking",
                    "thinking": f"The port is 8080 and {secret} is set.",
                    "signature": "sigB",
                },
                {"type": "redacted_thinking", "data": "opaque"},
                {"type": "thinking", "thinking": "proxy", "signature": ""},
                {"type": "text", "text": "Port 8080."},
            ],
        ),
    ]
    return s


def test_a_resumed_session_sends_what_was_sent_up_to_the_first_redaction() -> None:
    # Newer Claude models reject signed thinking whose earlier context
    # changed, and a tampered signature always. Redacting the .env read edits
    # message 2, so the saved copy drops the thinking sent from there on, the
    # rule compaction follows. The live run keeps its thinking.
    s = env_session()
    live = copy.deepcopy(s.messages)
    saved = loads(dumps(s))
    assert s.messages == live
    sent, resumed = messages_from(live), messages_from(saved.messages)
    assert resumed[:2] == sent[:2]
    assert resumed[2]["content"][0]["content"] == "[redacted]\nPORT=8080"
    assert not [b for m in resumed[2:] for b in m["content"] if b["type"] in THINKING]
    # Unsigned thinking is never sent, so it stays as a record.
    assert {"type": "thinking", "thinking": "proxy", "signature": ""} in (
        saved.messages[3].content
    )


def test_a_session_with_nothing_to_redact_keeps_its_thinking() -> None:
    s = env_session(secret="DEBUG=1")
    assert loads(dumps(s)) == s


def test_redacting_thinking_that_is_never_sent_keeps_the_rest() -> None:
    s = env_session(secret="DEBUG=1")
    s.messages[3].content[2]["thinking"] = "AUTH_TOKEN=Xk92mQ7vLp"
    text = dumps(s)
    assert "Xk92mQ7vLp" not in text
    assert messages_from(loads(text).messages) == messages_from(s.messages)


@pytest.mark.parametrize(
    ("block", "key", "value"),
    [
        (0, "thinking", "Keep sk-ant-" + "Z" * 30 + " out of the reply."),
        # Base64 ending in a segment that holds `auth` or `token` matches the
        # credential rule at its `=` padding, so a signature can change too.
        (0, "signature", "EqQBCkgIARABAuthQ=="),
        (1, "data", "opaqueTokenAbc=="),
    ],
)
def test_thinking_that_redaction_changes_is_never_saved(
    block: int, key: str, value: str
) -> None:
    s = env_session(secret="DEBUG=1")
    s.messages[3].content[block][key] = value
    saved = loads(dumps(s))
    assert saved.messages[:3] == s.messages[:3]
    assert not [
        b for b in saved.messages[3].content if b["type"] in THINKING and sent_back(b)
    ]


def test_reopen_appends_text_and_resets_per_run_state() -> None:
    # Built with replace(), not assigned: an assignment narrows the field's
    # type, and mypy would call the status check below impossible.
    s = replace(
        new_session("go"),
        status="blocked",
        questions=["which?"],
        schema_retried=True,
        error="x",
        stop_reason="tool_use",
        output={"found": 1},
        interrupted_at="2026-10-08T12:00:00+00:00",
    )
    s.messages.append(Message("assistant", [{"type": "text", "text": "which?"}]))
    reopen(s, "bar.py")
    assert s.status == "working"
    assert s.interrupted_at is None
    assert s.questions == []
    assert s.schema_retried is False
    assert s.error is None
    assert s.stop_reason is None
    # Partial findings survive a resume, as they did in cli._load_or_new.
    assert s.output == {"found": 1}
    assert s.messages[-1] == Message("user", [{"type": "text", "text": "bar.py"}])


def test_a_file_from_before_interrupted_at_still_loads() -> None:
    raw = json.loads(dumps(new_session("go")))
    del raw["interrupted_at"]
    assert loads(json.dumps(raw)).interrupted_at is None


def test_reopen_answers_calls_left_without_a_result() -> None:
    # Vendors reject a call with no result, so a follow-up on a file that
    # ends mid-dispatch killed every later turn.
    s = new_session("go")
    s.messages.append(
        Message("assistant", [{"type": "tool_use", "id": "c1", "name": "bash"}])
    )
    reopen(s, "continue")
    result, text = s.messages[-1].content
    assert result["type"] == "tool_result" and result["tool_use_id"] == "c1"
    assert result["is_error"] is True
    assert text == {"type": "text", "text": "continue"}


def test_reopen_without_text_leaves_the_transcript() -> None:
    s = replace(new_session("go"), status="error")
    reopen(s)
    assert s.status == "working"
    assert len(s.messages) == 1


def test_save_is_atomic_and_owner_only(tmp_path: Path) -> None:
    s = new_session("go")
    path = tmp_path / "s.json"
    save(s, path)
    assert loads(path.read_text()) == s
    assert path.stat().st_mode & 0o777 == 0o600
    assert [f.name for f in tmp_path.iterdir()] == ["s.json"]


def test_a_file_from_before_process_groups_still_loads() -> None:
    raw = json.loads(dumps(new_session("go")))
    del raw["process_groups"]
    assert loads(json.dumps(raw)).process_groups == []


@pytest.mark.parametrize(
    "junk", [None, {"pgid": 4242}, "4242", 4242], ids=["null", "object", "str", "int"]
)
def test_process_groups_that_are_not_a_list_load_as_empty(
    junk: object,
) -> None:
    # The TUI loops over the field and a bash call appends to it.
    raw = json.loads(dumps(new_session("go")))
    raw["process_groups"] = junk
    assert loads(json.dumps(raw)).process_groups == []


def test_process_groups_survive_a_save_and_a_reopen() -> None:
    s = new_session("go")
    s.process_groups.append({"pgid": 4242, "command": "npm run dev"})
    s = loads(dumps(s))
    reopen(s, "next")
    assert s.process_groups == [{"pgid": 4242, "command": "npm run dev"}]
