"""System prompt routes and the session's record of them (#132).

The real parser, system prompt resolution, loop and persistence run; only the
vendor factory is replaced, so each test sees the exact `system` a transport
would have been built with.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider, text_reply
from nare.cli import DEFAULT_SYSTEM, main
from nare.events import REDACTED, redact
from nare.session import loads

PERSONA = "You review code.\r\nBe terse: ünïcode stays.\n\n"
SECRET = "api_key=supersecretvalue12345"


@pytest.fixture
def systems(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    sent: list[str | None] = []

    def factory(provider: str, **kwargs: Any) -> FakeProvider:
        sent.append(kwargs["system"])
        return FakeProvider([text_reply("done")])

    monkeypatch.setattr("nare.cli.make_transport", factory)
    return sent


def run(*argv: str) -> int:
    return main(["run", "--yes", "--jsonl", *argv])


def events(out: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.splitlines()]


def changed(out: str) -> list[dict[str, Any]]:
    return [e for e in events(out) if "system_prompt" in e.get("detail", {})]


def write(path: Path, text: str) -> str:
    path.write_bytes(text.encode("utf-8"))
    return str(path)


def test_a_system_file_is_sent_byte_for_byte(
    tmp_path: Path, systems: list[str | None]
) -> None:
    assert run("--system-file", write(tmp_path / "s.txt", PERSONA), "go") == 0
    assert systems == [PERSONA]


def test_an_append_system_file_extends_the_default(
    tmp_path: Path, systems: list[str | None]
) -> None:
    path = write(tmp_path / "a.txt", "be brief")
    assert run("--append-system-file", path, "go") == 0
    assert systems == [f"{DEFAULT_SYSTEM}\n\nbe brief"]


def test_both_file_routes_compose_like_their_argv_forms(
    tmp_path: Path, systems: list[str | None]
) -> None:
    base = write(tmp_path / "s.txt", "persona")
    extra = write(tmp_path / "a.txt", "be brief")
    assert run("--system-file", base, "--append-system-file", extra, "go") == 0
    assert systems == ["persona\n\nbe brief"]


@pytest.mark.parametrize(
    ("argv_flag", "file_flag"),
    [("--system", "--system-file"), ("--append-system", "--append-system-file")],
)
def test_a_file_route_and_its_argv_form_together_exit_two_naming_both(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    systems: list[str | None],
    argv_flag: str,
    file_flag: str,
) -> None:
    path = write(tmp_path / "s.txt", "persona")
    with pytest.raises(SystemExit) as exited:
        run(argv_flag, "x", file_flag, path, "go")

    assert exited.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert argv_flag in captured.err and file_flag in captured.err
    assert systems == []


@pytest.mark.parametrize("flag", ["--system-file", "--append-system-file"])
def test_an_unreadable_or_non_utf8_system_file_exits_two_naming_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    systems: list[str | None],
    flag: str,
) -> None:
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"persona\xff")
    for path in (str(bad), str(tmp_path / "missing.txt")):
        assert run(flag, path, "go") == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert f"{flag} {path}" in captured.err
    assert systems == []


def test_the_session_records_the_system_prompt_it_sent(
    tmp_path: Path, systems: list[str | None]
) -> None:
    saved = tmp_path / "s.json"
    path = write(tmp_path / "p.txt", PERSONA)
    assert run("--system-file", path, "--session", str(saved), "go") == 0
    assert json.loads(saved.read_text())["system"] == PERSONA


def test_a_run_that_sent_no_system_prompt_records_an_empty_one(
    tmp_path: Path, systems: list[str | None]
) -> None:
    saved = tmp_path / "s.json"
    assert run("--tools", "none", "--session", str(saved), "go") == 0
    assert systems == [None]
    assert json.loads(saved.read_text())["system"] == ""


def test_the_recorded_system_prompt_is_redacted(
    tmp_path: Path, systems: list[str | None]
) -> None:
    assert redact(SECRET) != SECRET
    saved = tmp_path / "s.json"
    assert run("--system", f"use {SECRET}", "--session", str(saved), "go") == 0
    recorded = json.loads(saved.read_text())["system"]
    assert "supersecretvalue" not in recorded
    assert REDACTED in recorded


def test_a_resume_with_no_system_option_sends_the_recorded_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], systems: list[str | None]
) -> None:
    saved = tmp_path / "s.json"
    path = write(tmp_path / "p.txt", PERSONA)
    assert run("--system-file", path, "--session", str(saved), "go") == 0
    capsys.readouterr()

    assert run("--resume", str(saved), "and more") == 0

    assert systems == [PERSONA, PERSONA]
    assert changed(capsys.readouterr().out) == []


def test_a_resume_keeps_sending_no_system_prompt_when_none_was_sent(
    tmp_path: Path, systems: list[str | None]
) -> None:
    saved = tmp_path / "s.json"
    assert run("--system", "", "--session", str(saved), "go") == 0
    assert run("--resume", str(saved), "and more") == 0
    assert systems == [None, None]


def test_a_resume_with_a_different_system_sends_it_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], systems: list[str | None]
) -> None:
    saved = tmp_path / "s.json"
    assert run("--system", "persona", "--session", str(saved), "go") == 0
    capsys.readouterr()

    assert run("--resume", str(saved), "--system", "other", "and more") == 0

    assert systems == ["persona", "other"]
    [event] = changed(capsys.readouterr().out)
    assert event["type"] == "progress"
    assert event["detail"] == {"system_prompt": {"changed": True}}
    assert "system prompt" in event["text"]
    assert json.loads(saved.read_text())["system"] == "other"


def test_a_resume_resending_the_same_system_reports_no_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], systems: list[str | None]
) -> None:
    saved = tmp_path / "s.json"
    prompt = f"persona with {SECRET}"
    assert run("--system", prompt, "--session", str(saved), "go") == 0
    capsys.readouterr()

    assert run("--resume", str(saved), "--system", prompt, "and more") == 0

    assert systems == [prompt, prompt]
    assert changed(capsys.readouterr().out) == []


def test_an_append_on_resume_builds_from_the_flags_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], systems: list[str | None]
) -> None:
    saved = tmp_path / "s.json"
    assert run("--system", "persona", "--session", str(saved), "go") == 0
    capsys.readouterr()

    assert run("--resume", str(saved), "--append-system", "be brief", "more") == 0

    assert systems == ["persona", f"{DEFAULT_SYSTEM}\n\nbe brief"]
    assert len(changed(capsys.readouterr().out)) == 1


def test_a_session_saved_before_the_record_keeps_todays_behaviour(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], systems: list[str | None]
) -> None:
    saved = tmp_path / "s.json"
    assert run("--system", "persona", "--session", str(saved), "go") == 0
    raw = json.loads(saved.read_text())
    del raw["system"]
    saved.write_text(json.dumps(raw))
    assert loads(saved.read_text()).system is None
    capsys.readouterr()

    assert run("--resume", str(saved), "and more") == 0

    assert systems == ["persona", DEFAULT_SYSTEM]
    assert changed(capsys.readouterr().out) == []
    assert json.loads(saved.read_text())["system"] == DEFAULT_SYSTEM


def test_a_recorded_system_prompt_that_is_not_text_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], systems: list[str | None]
) -> None:
    saved = tmp_path / "s.json"
    assert run("--session", str(saved), "go") == 0
    raw = json.loads(saved.read_text())
    raw["system"] = 7
    saved.write_text(json.dumps(raw))
    capsys.readouterr()

    assert run("--resume", str(saved), "and more") == 2
    assert "malformed session file" in capsys.readouterr().err
    assert len(systems) == 1


def test_the_tui_resumes_with_the_recorded_system_prompt(
    tmp_path: Path, systems: list[str | None]
) -> None:
    from nare.cli import build_parser
    from nare.tui.app import prepare

    saved = tmp_path / "s.json"
    path = write(tmp_path / "p.txt", PERSONA)
    assert run("--system-file", path, "--session", str(saved), "go") == 0

    args = build_parser().parse_args(
        ["tui", "--model", "fake", "--resume", str(saved), "--session", str(saved)]
    )
    prepare(args)

    assert systems == [PERSONA, PERSONA]


async def test_the_tui_records_the_system_prompt_of_a_new_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nare.cli import build_parser
    from nare.tui.app import prepare

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    path = write(tmp_path / "p.txt", PERSONA)
    args = build_parser().parse_args(
        ["tui", "--model", "fake", "--system-file", path, "go"]
    )
    app = prepare(args, FakeProvider([text_reply("all done")]))
    async with app.run_test() as pilot:
        for _ in range(200):
            if app.run_state == "done":
                break
            await pilot.pause(0.01)
    assert app.session is not None
    assert app.session.system == PERSONA
    saved = tmp_path / "state" / "nare" / "sessions" / f"{app.session.id}.json"
    assert json.loads(saved.read_text())["system"] == PERSONA
