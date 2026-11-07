"""`nare run --resume PATH --fork`: a new session file, and the original left alone.

A fork is a resume into a new file. Redaction, the recorded system prompt and
the changed-prefix rules (issue 100) apply as they do on resume, so these tests
check the fork path against the same rules rather than restating them.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

import nare
from fake_provider import FakeProvider, text_reply, tool_reply
from nare.cli import main
from nare.contract import CONTRACT_VERSION, describe
from nare.session import Message, Session, dumps, fork, loads, new_session, save
from nare.tools import Policy


def lines(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def blocked_session(path: Path) -> Session:
    """A session that stopped to ask, saved at `path` by a run."""
    fake = FakeProvider([tool_reply("ask", {"questions": ["which file?"]})])
    main(["run", "--yes", "--jsonl", "--session", str(path), "go"], transport=fake)
    return loads(path.read_text(encoding="utf-8"))


def signed_thinking_session(path: Path) -> None:
    """A saved session whose last assistant turn carries signed thinking."""
    session = new_session("go")
    session.messages = [
        Message("user", [{"type": "text", "text": "go"}]),
        Message(
            "assistant",
            [
                {"type": "thinking", "thinking": "signed", "signature": "sig"},
                {"type": "text", "text": "reading"},
            ],
        ),
        Message("user", [{"type": "text", "text": "more"}]),
    ]
    session.policy = Policy(tools=frozenset({"read"})).recorded()
    session.status = "blocked"
    save(session, path)


def signed_blocks(session: Session) -> int:
    return sum(
        1
        for message in session.messages
        for block in message.content
        if block.get("type") == "thinking" and block.get("signature")
    )


def test_fork_writes_a_new_file_and_leaves_the_original_byte_identical(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    original = tmp_path / "orig.json"
    source = blocked_session(original)
    before = original.read_bytes()
    new = tmp_path / "fork.json"

    resumed = FakeProvider([text_reply("finished the branch")])
    code = main(
        [
            "run", "--yes", "--jsonl", "--resume", str(original), "--fork",
            "--session", str(new), "use bar.py",
        ],
        transport=resumed,
    )  # fmt: skip

    assert code == 0
    assert original.read_bytes() == before
    saved = loads(new.read_text(encoding="utf-8"))
    assert saved.id != source.id
    assert saved.status == "done"
    assert saved.turns == source.turns + 1
    # The branch sends the original transcript, then the follow-up.
    sent = resumed.calls[0][0]
    assert sent[-1].content[-1] == {"type": "text", "text": "use bar.py"}
    result = lines(capsys.readouterr().out)[-1]
    assert result["session_id"] == saved.id


def test_the_fork_record_names_the_session_and_turn_it_came_from(
    tmp_path: Path,
) -> None:
    original = tmp_path / "orig.json"
    source = blocked_session(original)
    new = tmp_path / "fork.json"

    main(
        [
            "run", "--yes", "--resume", str(original), "--fork",
            "--session", str(new), "branch",
        ],
        transport=FakeProvider([text_reply("ok")]),
    )  # fmt: skip

    record = json.loads(new.read_text(encoding="utf-8"))["forked_from"]
    assert record == {
        "session_id": source.id,
        "turn": source.turns,
        "path": str(original.resolve()),
    }
    # The original never records a fork.
    assert loads(original.read_text(encoding="utf-8")).forked_from is None


def test_a_fork_announces_itself_before_the_first_turn(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    original = tmp_path / "orig.json"
    blocked_session(original)
    capsys.readouterr()

    main(
        [
            "run", "--yes", "--jsonl", "--resume", str(original), "--fork",
            "--session", str(tmp_path / "fork.json"), "branch",
        ],
        transport=FakeProvider([text_reply("ok")]),
    )  # fmt: skip

    events = [e for e in lines(capsys.readouterr().out) if e.get("type") == "progress"]
    [fork_event] = [e for e in events if "fork" in (e.get("detail") or {})]
    assert fork_event["detail"]["fork"]["turn"] == 1


@pytest.mark.parametrize(
    "argv_tail, reason",
    [
        (["--fork", "--session", "NEW", "go"], "no --resume"),
        (["--resume", "ORIG", "--fork", "go"], "no --session"),
        (["--resume", "ORIG", "--fork", "--session", "ORIG", "go"], "same path"),
        (["--resume", "ORIG", "--fork", "--session", "EXISTING", "go"], "exists"),
    ],
)
def test_fork_refusals_exit_two_with_no_stdout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    argv_tail: list[str],
    reason: str,
) -> None:
    original = tmp_path / "orig.json"
    blocked_session(original)
    existing = tmp_path / "existing.json"
    existing.write_text("keep me", encoding="utf-8")
    before = original.read_bytes()
    capsys.readouterr()

    substitutions = {
        "ORIG": str(original),
        "NEW": str(tmp_path / "new.json"),
        "EXISTING": str(existing),
    }
    argv = ["run", "--yes", "--jsonl"] + [substitutions.get(a, a) for a in argv_tail]
    code = main(argv, transport=FakeProvider([text_reply("never")]))

    captured = capsys.readouterr()
    assert code == 2, reason
    assert captured.out == ""
    assert original.read_bytes() == before
    assert existing.read_text(encoding="utf-8") == "keep me"
    assert not (tmp_path / "new.json").exists()


def test_fork_redacts_like_resume_and_the_original_keeps_its_secret(
    tmp_path: Path,
) -> None:
    key = "sk-ant-api03-" + "A" * 40
    original = tmp_path / "orig.json"
    # Written raw, as an older file would be, so the secret is on disk unredacted.
    session = new_session(f"the key is {key}")
    raw = asdict(session)
    raw.pop("events")
    original.write_text(json.dumps(raw), encoding="utf-8")
    before = original.read_bytes()
    new = tmp_path / "fork.json"

    main(
        [
            "run", "--yes", "--resume", str(original), "--fork",
            "--session", str(new), "branch",
        ],
        transport=FakeProvider([text_reply("ok")]),
    )  # fmt: skip

    assert original.read_bytes() == before
    text = new.read_text(encoding="utf-8")
    assert key not in text
    assert "[redacted]" in text


def test_fork_drops_signed_thinking_on_changed_tools_and_keeps_it_in_the_original(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    original = tmp_path / "orig.json"
    signed_thinking_session(original)
    before = original.read_bytes()
    new = tmp_path / "fork.json"
    capsys.readouterr()

    main(
        [
            "run", "--yes", "--jsonl", "--resume", str(original), "--fork",
            "--session", str(new), "--tools", "read,bash", "branch",
        ],
        transport=FakeProvider([text_reply("with bash")]),
    )  # fmt: skip

    assert original.read_bytes() == before
    assert signed_blocks(loads(original.read_text(encoding="utf-8"))) == 1
    assert signed_blocks(loads(new.read_text(encoding="utf-8"))) == 0
    events = lines(capsys.readouterr().out)
    assert any("tool set changed on resume" in e.get("text", "") for e in events)


def test_fork_keeps_the_recorded_system_prompt(tmp_path: Path) -> None:
    original = tmp_path / "orig.json"
    session = new_session("go")
    session.system = "be terse"
    session.status = "blocked"
    save(session, original)
    new = tmp_path / "fork.json"
    fake = FakeProvider([text_reply("ok")])

    main(
        [
            "run", "--yes", "--resume", str(original), "--fork",
            "--session", str(new), "branch",
        ],
        transport=fake,
    )  # fmt: skip

    assert loads(new.read_text(encoding="utf-8")).system == "be terse"


def test_fork_of_a_file_with_a_bad_record_exits_two(tmp_path: Path) -> None:
    original = tmp_path / "orig.json"
    session = new_session("go")
    raw = asdict(session)
    raw.pop("events")
    raw["forked_from"] = "not an object"
    original.write_text(json.dumps(raw), encoding="utf-8")

    code = main(
        [
            "run",
            "--yes",
            "--resume",
            str(original),
            "--fork",
            "--session",
            str(tmp_path / "fork.json"),
            "branch",
        ],
        transport=FakeProvider([text_reply("never")]),
    )

    assert code == 2
    assert not (tmp_path / "fork.json").exists()


def test_forked_from_round_trips_and_defaults_to_none() -> None:
    session = new_session("go")
    assert session.forked_from is None
    session.forked_from = {"session_id": "abc", "turn": 2, "path": "/x.json"}
    assert loads(dumps(session)).forked_from == session.forked_from
    raw = json.loads(dumps(new_session("go")))
    raw.pop("forked_from")
    assert loads(json.dumps(raw)).forked_from is None


def test_a_fork_record_is_redacted_on_save() -> None:
    key = "sk-ant-api03-" + "B" * 40
    session = new_session("go")
    session.forked_from = {"session_id": "abc", "turn": 1, "path": f"/tmp/{key}"}
    assert key not in dumps(session)


def test_library_fork_copies_and_leaves_the_original_untouched(tmp_path: Path) -> None:
    source = new_session("go")
    source.turns = 3
    before = dumps(source)

    child = fork(source, source=tmp_path / "orig.json")

    assert child.id != source.id
    assert child.turns == 3
    assert child.forked_from == {
        "session_id": source.id,
        "turn": 3,
        "path": str((tmp_path / "orig.json").resolve()),
    }
    assert source.forked_from is None
    child.messages.append(Message("user", [{"type": "text", "text": "branch"}]))
    child.process_groups.append({"pgid": 1, "command": "sleep"})
    assert dumps(source) == before
    assert child.events == []


def test_library_fork_runs_and_saves_a_branch_beside_the_original(
    tmp_path: Path,
) -> None:
    from nare.loop import run
    from nare.session import reopen
    from nare.tools import approve_all

    original = tmp_path / "orig.json"
    blocked_session(original)
    before = original.read_bytes()

    branch = fork(loads(original.read_text(encoding="utf-8")), source=original)
    reopen(branch, "use bar.py")
    fake = FakeProvider([text_reply("branched")])

    async def drive() -> None:
        async for _ in run(
            branch, transport=fake, approve=approve_all, policy=Policy()
        ):
            pass

    asyncio.run(drive())
    save(branch, tmp_path / "branch.json")

    assert original.read_bytes() == before
    saved = loads((tmp_path / "branch.json").read_text(encoding="utf-8"))
    assert saved.status == "done"
    assert saved.forked_from is not None
    assert saved.forked_from["session_id"] != saved.id


def test_library_fork_without_a_source_records_no_path() -> None:
    child = fork(new_session("go"))
    assert child.forked_from is not None
    assert child.forked_from["path"] is None


def test_fork_is_exported_from_the_package() -> None:
    assert nare.fork is fork


def test_tui_accepts_fork_with_the_same_flag() -> None:
    """`nare tui --fork` is the run flag: one definition, one help text."""
    from nare.cli import build_parser

    tui = build_parser().parse_args(
        ["tui", "--resume", "a.json", "--fork", "--session", "b.json"]
    )
    run = build_parser().parse_args(
        ["run", "--resume", "a.json", "--fork", "--session", "b.json", "go"]
    )
    assert tui.fork is True and run.fork is True


def test_contract_describes_the_fork_and_keeps_the_version() -> None:
    assert describe()["session_fork"] == {
        "flag": "--fork",
        "requires": ["--resume", "--session"],
        "session_field": "forked_from",
        "fields": ["session_id", "turn", "path"],
    }
    assert CONTRACT_VERSION == 3


def test_the_documented_contract_describes_the_fork() -> None:
    doc = Path(__file__).parent.parent / "docs" / "contract.md"
    text = doc.read_text(encoding="utf-8")
    assert "## Forking a saved session" in text
    assert "`forked_from`" in text
    assert "nare.fork" in text


@pytest.mark.parametrize(
    "extra, message",
    [
        (["--resume", "{src}", "--fork"], "--fork writes a new session"),
        (["--resume", "{src}", "--fork", "--session", "{src}"], "is the resumed file"),
        (["--resume", "{src}", "--fork", "--session", "{exists}"], "already exists"),
        (["--fork", "--session", "{new}"], "--fork needs --resume PATH"),
    ],
)
def test_tui_fork_refusals_match_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    extra: list[str],
    message: str,
) -> None:
    """`nare tui --fork` follows the same rules as `nare run --fork`."""
    src = tmp_path / "src.json"
    src.write_text(dumps(new_session("go")), encoding="utf-8")
    exists = tmp_path / "exists.json"
    exists.write_text("{}", encoding="utf-8")
    values = {
        "src": str(src),
        "exists": str(exists),
        "new": str(tmp_path / "new.json"),
    }
    argv = ["tui", "--model", "fake", *[a.format(**values) for a in extra]]
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert message in captured.err
    assert captured.out == ""
    assert not (tmp_path / "new.json").exists()
