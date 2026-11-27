"""Sessions a person can continue (#327): a default store under
NARE_HOME/sessions/<project>/, -c/--continue, -r/--resume <id|path>,
--fork-session, Codex's resume, fork and exec resume, and nare sessions.

nare run without --session writes nothing to the store, as before.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider, text_reply
from nare import person
from nare.cli import main
from nare.session import loads, project_sessions_dir
from test_entry_points import piped

SECRET = "sk-ant-" + "Z" * 30


@pytest.fixture
def here(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


def store(here: Path) -> Path:
    folder = project_sessions_dir(str(here))
    assert folder is not None
    return folder


def files_in(here: Path) -> list[Path]:
    folder = store(here)
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


def user_texts(fake: FakeProvider, call: int = -1) -> list[str]:
    messages, _tools = fake.calls[call]
    return [
        str(block.get("text"))
        for message in messages
        if message.role == "user"
        for block in message.content
        if block.get("type") == "text"
    ]


@pytest.fixture
def tui_calls(monkeypatch: pytest.MonkeyPatch) -> list[argparse.Namespace]:
    calls: list[argparse.Namespace] = []

    def fake_tui(args: argparse.Namespace, transport: Any = None) -> int:
        calls.append(args)
        return 0

    # A key, so the person-facing check for one (#328) lets the TUI start.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(person, "tui_available", lambda: True)
    monkeypatch.setattr(person, "interactive_terminal", lambda: True)
    monkeypatch.setattr("nare.tui.app.main", fake_tui)
    return calls


def test_print_mode_saves_to_the_project_store(here: Path) -> None:
    assert main(["-p", "a"], transport=FakeProvider([text_reply("one")])) == 0
    [saved] = files_in(here)
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert stat.S_IMODE(store(here).stat().st_mode) == 0o700
    session = loads(saved.read_text())
    assert session.id == saved.stem
    assert session.started_at is not None


def test_continue_picks_up_the_same_transcript(here: Path) -> None:
    assert main(["-p", "a"], transport=FakeProvider([text_reply("one")])) == 0
    fake = FakeProvider([text_reply("two")])
    assert main(["-p", "-c", "b"], transport=fake) == 0
    assert user_texts(fake) == ["a", "b"]
    [saved] = files_in(here)  # written back, not a second file
    assert loads(saved.read_text()).turns == 2


def test_continue_takes_the_newest(here: Path) -> None:
    assert main(["-p", "first"], transport=FakeProvider([text_reply("1")])) == 0
    assert main(["-p", "second"], transport=FakeProvider([text_reply("2")])) == 0
    newest = max(files_in(here), key=lambda p: p.stat().st_mtime)
    os.utime(newest, (newest.stat().st_atime, newest.stat().st_mtime + 10))
    fake = FakeProvider([text_reply("3")])
    assert main(["-p", "--continue", "third"], transport=fake) == 0
    assert (
        user_texts(fake)[0] == loads(newest.read_text()).messages[0].content[0]["text"]
    )


@pytest.mark.parametrize("by", ["id", "path"])
def test_resume_by_id_or_path(by: str, here: Path) -> None:
    assert main(["-p", "a"], transport=FakeProvider([text_reply("one")])) == 0
    [saved] = files_in(here)
    ref = saved.stem if by == "id" else str(saved)
    fake = FakeProvider([text_reply("two")])
    assert main(["-p", "-r", ref, "b"], transport=fake) == 0
    assert user_texts(fake) == ["a", "b"]


def test_an_unknown_id_names_the_store(
    here: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["-p", "-r", "nosuchid", "b"], transport=FakeProvider([])) == 2
    err = capsys.readouterr().err
    assert "nosuchid" in err
    assert str(store(here)) in err


def test_continue_with_nothing_to_continue(
    here: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["-p", "-c", "b"], transport=FakeProvider([])) == 2
    assert "no session" in capsys.readouterr().err


def test_fork_session_writes_a_new_file(here: Path) -> None:
    assert main(["-p", "a"], transport=FakeProvider([text_reply("one")])) == 0
    [original] = files_in(here)
    before = original.read_bytes()
    fake = FakeProvider([text_reply("two")])
    assert main(["-p", "-c", "--fork-session", "b"], transport=fake) == 0
    assert original.read_bytes() == before
    [fork] = [p for p in files_in(here) if p != original]
    forked = loads(fork.read_text())
    assert forked.forked_from is not None
    assert forked.forked_from["session_id"] == loads(before.decode()).id


def test_fork_session_needs_a_session(
    here: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["-p", "--fork-session", "b"], transport=FakeProvider([])) == 2
    assert "--fork-session" in capsys.readouterr().err


def test_the_store_is_redacted(here: Path) -> None:
    prompt = f"use ANTHROPIC_API_KEY={SECRET}"
    assert main(["-p", prompt], transport=FakeProvider([text_reply("ok")])) == 0
    [saved] = files_in(here)
    assert SECRET not in saved.read_text()


def test_run_without_session_writes_nothing_to_the_store(here: Path) -> None:
    fake = FakeProvider([text_reply("ok")])
    assert main(["run", "--yes", "--jsonl", "a"], transport=fake) == 0
    assert files_in(here) == []


def test_an_explicit_session_is_used_instead(here: Path, tmp_path: Path) -> None:
    path = tmp_path / "mine.json"
    fake = FakeProvider([text_reply("ok")])
    assert main(["-p", "--session", str(path), "a"], transport=fake) == 0
    assert path.exists()
    assert files_in(here) == []


def test_sessions_lists_this_directory(
    here: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["-p", "fix the test"], transport=FakeProvider([text_reply("ok")])) == 0
    capsys.readouterr()
    assert main(["sessions"]) == 0
    out = capsys.readouterr().out
    [saved] = files_in(here)
    assert saved.stem in out
    assert "fix the test" in out
    assert "done" in out


def test_sessions_with_none(here: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["sessions"]) == 0
    assert "no saved session" in capsys.readouterr().out


def test_sessions_json(here: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["-p", "x"], transport=FakeProvider([text_reply("ok")])) == 0
    capsys.readouterr()
    assert main(["sessions", "--json"]) == 0
    [row] = json.loads(capsys.readouterr().out)
    assert set(row) >= {"id", "path", "started", "prompt", "status", "turns"}


def test_a_task_in_the_tui_saves_to_the_store(
    here: Path, tui_calls: list[argparse.Namespace]
) -> None:
    assert main(["do it"]) == 0
    [args] = tui_calls
    assert Path(args.session).parent == store(here)


def test_codex_resume_last(here: Path, tui_calls: list[argparse.Namespace]) -> None:
    assert main(["-p", "a"], transport=FakeProvider([text_reply("one")])) == 0
    [saved] = files_in(here)
    assert main(["resume", "--last"]) == 0
    assert Path(tui_calls[0].resume) == saved


def test_codex_resume_by_id(here: Path, tui_calls: list[argparse.Namespace]) -> None:
    assert main(["-p", "a"], transport=FakeProvider([text_reply("one")])) == 0
    [saved] = files_in(here)
    assert main(["resume", saved.stem]) == 0
    assert Path(tui_calls[0].resume) == saved


def test_codex_resume_alone_opens_the_picker(
    here: Path, tui_calls: list[argparse.Namespace]
) -> None:
    assert main(["resume"]) == 0
    assert tui_calls[0].resume == ""


def test_codex_fork(here: Path, tui_calls: list[argparse.Namespace]) -> None:
    assert main(["-p", "a"], transport=FakeProvider([text_reply("one")])) == 0
    [saved] = files_in(here)
    assert main(["fork", "--last"]) == 0
    [args] = tui_calls
    assert args.fork is True
    assert Path(args.resume) == saved
    assert Path(args.session).parent == store(here)
    assert Path(args.session) != saved


def test_codex_exec_resume(here: Path) -> None:
    assert main(["exec", "a"], transport=FakeProvider([text_reply("one")])) == 0
    fake = FakeProvider([text_reply("two")])
    assert main(["exec", "resume", "--last", "b"], transport=fake) == 0
    assert user_texts(fake) == ["a", "b"]
    [saved] = files_in(here)
    fake = FakeProvider([text_reply("three")])
    assert main(["exec", "resume", saved.stem, "c"], transport=fake) == 0
    assert user_texts(fake) == ["a", "b", "c"]


@pytest.mark.parametrize(
    "argv", [["-p", "-c"], ["-p", "-r", "ID"], ["exec", "resume", "--last"]]
)
def test_a_resume_with_no_task_takes_piped_stdin_as_the_follow_up(
    argv: list[str], here: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """echo "..." | nare -p -c (#349): stdin is the follow-up, and the
    finished session is not run again without it."""
    assert main(["-p", "a"], transport=FakeProvider([text_reply("one")])) == 0
    [saved] = files_in(here)
    piped(monkeypatch, b"my follow-up question\n")
    fake = FakeProvider([text_reply("two")])
    argv = [saved.stem if word == "ID" else word for word in argv]
    assert main(argv, transport=fake) == 0
    assert user_texts(fake) == ["a", "my follow-up question\n"]
    roles = [message.role for message in loads(saved.read_text()).messages]
    assert roles == ["user", "assistant", "user", "assistant"]


@pytest.mark.parametrize(
    "argv", [["-p", "-c"], ["-p", "-r", "ID"], ["exec", "resume", "--last"]]
)
def test_a_resume_with_no_task_and_no_stdin_needs_a_follow_up(
    argv: list[str], here: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["-p", "a"], transport=FakeProvider([text_reply("one")])) == 0
    [saved] = files_in(here)
    before = saved.read_text()
    capsys.readouterr()
    fake = FakeProvider([])
    argv = [saved.stem if word == "ID" else word for word in argv]
    assert main(argv, transport=fake) == 2
    assert fake.calls == []
    assert "follow-up" in capsys.readouterr().err
    assert saved.read_text() == before


@pytest.mark.parametrize("argv", [["-p", "-c"], ["-p", "-c", "-"]])
def test_a_blank_follow_up_on_stdin_calls_no_model(
    argv: list[str],
    here: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["-p", "a"], transport=FakeProvider([text_reply("one")])) == 0
    piped(monkeypatch, b"\n")
    fake = FakeProvider([])
    assert main(argv, transport=fake) == 2
    assert fake.calls == []
    assert "follow-up" in capsys.readouterr().err


@pytest.mark.parametrize("stdin", [None, b"\n"])
def test_a_session_that_stopped_partway_continues_without_a_follow_up(
    stdin: bytes | None, here: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An error, a limit or a signal leaves the task unjudged: a resume with
    no text continues it, as nare run --resume does."""
    assert main(["-p", "a"], transport=FakeProvider([text_reply("one")])) == 0
    [saved] = files_in(here)
    raw = json.loads(saved.read_text())
    raw["status"] = "error"
    saved.write_text(json.dumps(raw))
    if stdin is not None:
        piped(monkeypatch, stdin)
    fake = FakeProvider([text_reply("two")])
    assert main(["-p", "-c"], transport=fake) == 0
    assert len(fake.calls) == 1
    assert user_texts(fake) == ["a"]


def test_a_follow_up_on_stdin_is_read_once(
    here: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A follow-up that is itself a lone - is sent, not read as - again."""
    assert main(["-p", "a"], transport=FakeProvider([text_reply("one")])) == 0
    piped(monkeypatch, b"-")
    fake = FakeProvider([text_reply("two")])
    assert main(["-p", "-c", "-"], transport=fake) == 0
    assert user_texts(fake) == ["a", "-"]


def test_codex_exec_resume_needs_a_session(
    here: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["exec", "resume", "--model", "m"], transport=FakeProvider([])) == 2
    assert "--last" in capsys.readouterr().err


def test_the_resume_picker_lists_the_store(here: Path) -> None:
    from nare.session import resumable_in

    assert main(["-p", "a"], transport=FakeProvider([text_reply("one")])) == 0
    [saved] = files_in(here)
    assert [row.path for row in resumable_in(str(here))] == [saved]


def test_a_run_session_file_carries_no_start_record(here: Path, tmp_path: Path) -> None:
    """The store's started_at is written only when set: nare run never sets it."""
    path = tmp_path / "run.json"
    fake = FakeProvider([text_reply("ok")])
    assert main(["run", "--yes", "--session", str(path), "a"], transport=fake) == 0
    assert "started_at" not in json.loads(path.read_text())


def test_line_mode_resume_asks_first(
    here: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import io
    import sys

    assert main(["-p", "a"], transport=FakeProvider([text_reply("one")])) == 0
    monkeypatch.setattr(person, "tui_available", lambda: False)
    monkeypatch.setattr(person, "interactive_terminal", lambda: True)
    monkeypatch.setattr("nare.cli.stdin_is_terminal", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("and b\n"))
    fake = FakeProvider([text_reply("two")])
    assert main(["-c"], transport=fake) == 0
    assert user_texts(fake) == ["a", "and b"]
