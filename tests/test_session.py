from pathlib import Path

import pytest

from nare.events import Event
from nare.session import (
    SESSION_VERSION,
    Message,
    Usage,
    append_user_text,
    dumps,
    loads,
    new_session,
    reopen,
    save,
)


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


def test_reopen_appends_text_and_resets_per_run_state() -> None:
    s = new_session("go")
    s.messages.append(Message("assistant", [{"type": "text", "text": "which?"}]))
    s.status = "blocked"
    s.questions = ["which?"]
    s.schema_retried = True
    s.error = "x"
    s.stop_reason = "tool_use"
    s.output = {"found": 1}
    reopen(s, "bar.py")
    assert s.status == "working"  # type: ignore[comparison-overlap]
    assert s.questions == []
    assert s.schema_retried is False
    assert s.error is None
    assert s.stop_reason is None
    # Partial findings survive a resume, as they did in cli._load_or_new.
    assert s.output == {"found": 1}
    assert s.messages[-1] == Message("user", [{"type": "text", "text": "bar.py"}])


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
    s = new_session("go")
    s.status = "error"
    reopen(s)
    assert s.status == "working"  # type: ignore[comparison-overlap]
    assert len(s.messages) == 1


def test_save_is_atomic_and_owner_only(tmp_path: Path) -> None:
    s = new_session("go")
    path = tmp_path / "s.json"
    save(s, path)
    assert loads(path.read_text()) == s
    assert path.stat().st_mode & 0o777 == 0o600
    assert [f.name for f in tmp_path.iterdir()] == ["s.json"]
