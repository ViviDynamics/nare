"""AGENTS.md, with CLAUDE.md as the fallback, in the person-facing forms (#330).

Loaded after the system prompt and before --append-system, outermost first,
capped at 32 KiB. Never in nare run, unless it opts in with
--project-instructions.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider, text_reply
from nare import instructions
from nare.cli import DEFAULT_SYSTEM, main
from nare.session import loads, nare_home, newest_in


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "AGENTS.md").write_text("Root rule: run make test.\n")
    (root / "pkg").mkdir()
    (root / "pkg" / "AGENTS.md").write_text("Package rule: no new deps.\n")
    monkeypatch.chdir(root / "pkg")
    return root


@pytest.fixture
def systems(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """The system prompt each run would send."""
    seen: list[str | None] = []
    import nare.cli as cli

    original = cli.system_from_args

    def spy(args: argparse.Namespace) -> str | None:
        text = original(args)
        seen.append(text)
        return text

    monkeypatch.setattr(cli, "system_from_args", spy)
    return seen


def test_both_files_load_in_order_from_a_subdirectory(
    repo: Path, systems: list[str | None]
) -> None:
    assert main(["-p", "x"], transport=FakeProvider([text_reply("ok")])) == 0
    sent = systems[-1] or ""
    assert sent.startswith(DEFAULT_SYSTEM)
    assert sent.index("Root rule") < sent.index("Package rule")
    assert str(repo / "AGENTS.md") in sent


def test_claude_md_is_the_fallback(repo: Path, systems: list[str | None]) -> None:
    (repo / "pkg" / "AGENTS.md").unlink()
    (repo / "pkg" / "CLAUDE.md").write_text("Claude rule.\n")
    (repo / "CLAUDE.md").write_text("Ignored: the root has an AGENTS.md.\n")
    assert main(["-p", "x"], transport=FakeProvider([text_reply("ok")])) == 0
    sent = systems[-1] or ""
    assert "Claude rule." in sent and "Root rule" in sent
    assert "Ignored" not in sent


def test_a_directory_with_only_claude_md_loads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, systems: list[str | None]
) -> None:
    (tmp_path / "CLAUDE.md").write_text("Only Claude.\n")
    monkeypatch.chdir(tmp_path)
    assert main(["exec", "x"], transport=FakeProvider([text_reply("ok")])) == 0
    assert "Only Claude." in (systems[-1] or "")


def test_the_home_file_comes_first(repo: Path, systems: list[str | None]) -> None:
    home = nare_home()
    assert home is not None
    home.mkdir(parents=True, exist_ok=True)
    (home / "AGENTS.md").write_text("Personal rule.\n")
    assert main(["-p", "x"], transport=FakeProvider([text_reply("ok")])) == 0
    sent = systems[-1] or ""
    assert sent.index("Personal rule") < sent.index("Root rule")


def test_append_system_comes_after(repo: Path, systems: list[str | None]) -> None:
    fake = FakeProvider([text_reply("ok")])
    assert main(["-p", "--append-system-prompt", "Persona.", "x"], transport=fake) == 0
    sent = systems[-1] or ""
    assert sent.index("Package rule") < sent.index("Persona.")


def test_a_progress_event_names_the_files(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["-p", "--json", "x"], transport=FakeProvider([text_reply("ok")])) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    [loaded] = [e for e in events if "project_instructions" in e.get("detail", {})]
    detail = loaded["detail"]["project_instructions"]
    assert detail["files"] == [str(repo / "AGENTS.md"), str(repo / "pkg" / "AGENTS.md")]
    assert detail["truncated"] is False


def test_the_total_is_capped_and_says_so(
    repo: Path, systems: list[str | None], capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "AGENTS.md").write_text("x" * (40 * 1024))
    assert main(["-p", "--json", "x"], transport=FakeProvider([text_reply("ok")])) == 0
    sent = systems[-1] or ""
    loaded = instructions.load(str(repo / "pkg"))
    assert loaded is not None and loaded.truncated
    assert len(loaded.text.encode()) <= instructions.MAX_BYTES + 512
    assert "cut at 32 KiB" in sent
    assert '"truncated": true' in capsys.readouterr().out


def test_no_project_instructions_turns_it_off(
    repo: Path, systems: list[str | None]
) -> None:
    fake = FakeProvider([text_reply("ok")])
    assert main(["-p", "--no-project-instructions", "x"], transport=fake) == 0
    assert "Root rule" not in (systems[-1] or "")


def test_run_loads_neither(
    repo: Path, systems: list[str | None], capsys: pytest.CaptureFixture[str]
) -> None:
    """The contract: nare run --yes --jsonl sends what it always sent."""
    fake = FakeProvider([text_reply("ok")])
    assert main(["run", "--yes", "--jsonl", "x"], transport=fake) == 0
    assert systems[-1] == DEFAULT_SYSTEM
    assert "project_instructions" not in capsys.readouterr().out


def test_run_may_opt_in(repo: Path, systems: list[str | None]) -> None:
    fake = FakeProvider([text_reply("ok")])
    assert main(["run", "--yes", "--project-instructions", "x"], transport=fake) == 0
    assert "Package rule" in (systems[-1] or "")


def test_without_a_git_root_only_the_working_directory_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "AGENTS.md").write_text("Above, outside any project.\n")
    work = tmp_path / "work"
    work.mkdir()
    (work / "AGENTS.md").write_text("Here.\n")
    monkeypatch.chdir(work)
    loaded = instructions.load(str(work))
    assert loaded is not None
    assert [Path(f).parent for f in loaded.files] == [work]


def test_nothing_to_load(tmp_path: Path) -> None:
    assert instructions.load(str(tmp_path)) is None


def test_the_line_session_loads_them(
    repo: Path, monkeypatch: pytest.MonkeyPatch, systems: list[str | None]
) -> None:
    import io
    import sys

    from nare import person

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(person, "tui_available", lambda: False)
    monkeypatch.setattr(person, "interactive_terminal", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert main(["x"], transport=FakeProvider([text_reply("ok")])) == 0
    assert "Root rule" in (systems[-1] or "")
    assert re.search(r"Package rule", systems[-1] or "")


def test_a_link_out_of_the_project_is_skipped_and_named(
    repo: Path, tmp_path: Path, systems: list[str | None]
) -> None:
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("Not project text.\n")
    (repo / "pkg" / "AGENTS.md").unlink()
    (repo / "pkg" / "AGENTS.md").symlink_to(outside)
    loaded = instructions.load(str(repo / "pkg"))
    assert loaded is not None
    assert "Not project text" not in loaded.text
    assert loaded.files == [str(repo / "AGENTS.md")]
    assert loaded.skipped == [str(repo / "pkg" / "AGENTS.md")]
    event = loaded.event()
    assert "skipped" in event.text and "outside the project" in event.text
    detail = event.detail["project_instructions"]
    assert detail["skipped"] == [
        {"file": str(repo / "pkg" / "AGENTS.md"), "reason": "outside the project"}
    ]
    assert main(["-p", "x"], transport=FakeProvider([text_reply("ok")])) == 0
    assert "Not project text" not in (systems[-1] or "")


def test_a_skipped_link_still_reports_when_nothing_loads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    systems: list[str | None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("Not project text.\n")
    work = tmp_path / "work"
    (work / ".git").mkdir(parents=True)
    (work / "CLAUDE.md").symlink_to(outside)
    monkeypatch.chdir(work)
    loaded = instructions.load(str(work))
    assert loaded is not None
    assert loaded.files == [] and loaded.text == ""
    assert loaded.skipped == [str(work / "CLAUDE.md")]
    assert main(["-p", "--json", "x"], transport=FakeProvider([text_reply("ok")])) == 0
    assert systems[-1] == DEFAULT_SYSTEM
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    [event] = [e for e in events if "project_instructions" in e.get("detail", {})]
    detail = event["detail"]["project_instructions"]
    assert detail["files"] == [] and detail["skipped"][0]["file"].endswith("CLAUDE.md")


def test_a_skipped_agents_md_falls_back_to_claude_md(
    repo: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("Not project text.\n")
    (repo / "pkg" / "AGENTS.md").unlink()
    (repo / "pkg" / "AGENTS.md").symlink_to(outside)
    (repo / "pkg" / "CLAUDE.md").write_text("Claude fallback.\n")
    loaded = instructions.load(str(repo / "pkg"))
    assert loaded is not None
    assert "Claude fallback." in loaded.text and "Not project text" not in loaded.text
    assert loaded.skipped == [str(repo / "pkg" / "AGENTS.md")]
    assert loaded.files[-1] == str(repo / "pkg" / "CLAUDE.md")


def test_a_link_inside_the_project_still_loads(repo: Path) -> None:
    (repo / "docs").mkdir()
    (repo / "docs" / "agents.md").write_text("Shared rule.\n")
    (repo / "pkg" / "AGENTS.md").unlink()
    (repo / "pkg" / "AGENTS.md").symlink_to(repo / "docs" / "agents.md")
    loaded = instructions.load(str(repo / "pkg"))
    assert loaded is not None
    assert "Shared rule." in loaded.text
    assert loaded.skipped == []


def test_the_home_file_may_link_into_a_dotfiles_folder(
    repo: Path, tmp_path: Path
) -> None:
    """Only the person writes NARE_HOME, and a link from it into their own
    dotfiles checkout is a common setup, so the home file is read wherever
    it points."""
    home = nare_home()
    assert home is not None
    home.mkdir(parents=True, exist_ok=True)
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    (dotfiles / "AGENTS.md").write_text("Personal rule.\n")
    (home / "AGENTS.md").symlink_to(dotfiles / "AGENTS.md")
    loaded = instructions.load(str(repo / "pkg"))
    assert loaded is not None
    assert "Personal rule." in loaded.text
    assert loaded.skipped == []


def test_a_file_that_fills_the_cap_exactly_ends_the_list(repo: Path) -> None:
    (repo / "AGENTS.md").write_text("x" * instructions.MAX_BYTES)
    loaded = instructions.load(str(repo / "pkg"))
    assert loaded is not None and loaded.truncated
    assert loaded.files == [str(repo / "AGENTS.md")]
    assert "Package rule" not in loaded.text and str(repo / "pkg") not in loaded.text
    assert loaded.text.endswith(instructions.CUT)


def test_a_file_is_read_only_up_to_the_cap(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    big = tmp_path / "AGENTS.md"
    with big.open("wb") as handle:
        handle.truncate(64 * 1024 * 1024)
    assert len(instructions.read_head(big, 100)) == 101
    loaded = instructions.load(str(tmp_path))
    assert loaded is not None and loaded.truncated
    assert loaded.bytes == instructions.MAX_BYTES


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """The system prompt each transport is built with: what is sent."""
    sent: list[str | None] = []

    def factory(provider: str, **kwargs: Any) -> FakeProvider:
        sent.append(kwargs["system"])
        return FakeProvider([text_reply("ok")])

    monkeypatch.setattr("nare.cli.make_transport", factory)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    return sent


def recorded(repo: Path) -> str:
    saved = newest_in(str(repo / "pkg"))
    system = loads(saved.read_text(encoding="utf-8")).system
    assert system is not None
    return system


def test_resumes_keep_one_copy_of_the_instructions(
    repo: Path, built: list[str | None]
) -> None:
    """Each resume sends the recorded prompt, which already holds them (#348)."""
    assert main(["-p", "hello"]) == 0
    first = recorded(repo)
    assert first.count("Root rule") == 1
    for argv in (
        ["-p", "-c", "again"],
        ["-p", "-c", "again"],
        ["exec", "resume", "--last", "more"],
    ):
        assert main(argv) == 0
        assert recorded(repo) == first
    assert built == [first] * 4


def test_the_tui_resume_keeps_one_copy_of_the_instructions(
    repo: Path, built: list[str | None], monkeypatch: pytest.MonkeyPatch
) -> None:
    from nare import person
    from nare.tui import app

    assert main(["-p", "hello"]) == 0
    first = recorded(repo)
    apps: list[app.TuiApp] = []

    def start(args: argparse.Namespace, transport: Any = None) -> int:
        apps.append(app.prepare(args, transport))
        return 0

    monkeypatch.setattr(person, "tui_available", lambda: True)
    monkeypatch.setattr(person, "interactive_terminal", lambda: True)
    monkeypatch.setattr(app, "main", start)
    assert main(["-c"]) == 0
    assert main(["resume", "--last"]) == 0
    assert [tui.session.system for tui in apps if tui.session] == [first, first]
    assert built == [first] * 3


def test_a_resume_with_a_system_option_rebuilds_it_once(
    repo: Path, built: list[str | None], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["-p", "hello"]) == 0
    capsys.readouterr()
    assert main(["-p", "-c", "--json", "--append-system-prompt", "Persona.", "x"]) == 0
    sent = built[-1] or ""
    assert sent.startswith(DEFAULT_SYSTEM)
    assert sent.count("Root rule") == 1
    assert sent.index("Package rule") < sent.index("Persona.")
    assert recorded(repo) == sent
    out = capsys.readouterr().out
    assert '"system_prompt": {"changed": true}' in out


def test_a_resume_that_sends_the_record_names_no_files(
    repo: Path, built: list[str | None], capsys: pytest.CaptureFixture[str]
) -> None:
    """The record is what is sent, so no event says the files were added."""
    assert main(["-p", "--json", "hello"]) == 0
    assert "project_instructions" in capsys.readouterr().out
    assert main(["-p", "-c", "--json", "again"]) == 0
    assert "project_instructions" not in capsys.readouterr().out


def test_a_line_session_resume_names_no_files(
    repo: Path, built: list[str | None], monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import sys

    from nare import person

    seen: list[str] = []
    monkeypatch.setattr(
        "nare.terminal.progress", lambda event: seen.append(str(event.detail))
    )
    assert main(["-p", "hello"]) == 0
    assert any("project_instructions" in detail for detail in seen)
    seen.clear()
    monkeypatch.setattr(person, "tui_available", lambda: False)
    monkeypatch.setattr(person, "interactive_terminal", lambda: True)
    monkeypatch.setattr("nare.cli.stdin_is_terminal", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("and more\n"))
    assert main(["-c"]) == 0
    assert seen and not any("project_instructions" in detail for detail in seen)
    assert built[-1] == recorded(repo)


def tui_events(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], transport: Any = None
) -> list[dict[str, Any]]:
    """Start the terminal UI as `nare <argv>` would, run its first turn, and
    return the project_instructions detail of each event the run emitted."""
    from nare import person
    from nare.tui import app

    apps: list[app.TuiApp] = []

    def start(args: argparse.Namespace, transport: Any = None) -> int:
        apps.append(app.prepare(args, transport))
        return 0

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(person, "tui_available", lambda: True)
    monkeypatch.setattr(person, "interactive_terminal", lambda: True)
    monkeypatch.setattr(app, "main", start)
    assert main(argv, transport=transport) == 0
    [tui] = apps
    seen: list[dict[str, Any]] = []
    shown = tui._event

    def spy(event: Any) -> None:
        if "project_instructions" in event.detail:
            seen.append(event.detail["project_instructions"])
        shown(event)

    monkeypatch.setattr(tui, "_event", spy)

    async def drive() -> None:
        async with tui.run_test() as pilot:
            for _ in range(300):
                if tui.session is not None and tui.session.status == "done":
                    break
                await pilot.pause(0.01)
            assert tui.session is not None and tui.session.status == "done"

    asyncio.run(drive())
    return seen


def test_the_tui_names_the_files_in_a_new_session(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """contract.md: every person-facing form emits the event (#352)."""
    [detail] = tui_events(
        monkeypatch, ["hello"], transport=FakeProvider([text_reply("ok")])
    )
    assert detail["files"] == [str(repo / "AGENTS.md"), str(repo / "pkg" / "AGENTS.md")]


def test_the_tui_names_a_skipped_file_when_nothing_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("Not project text.\n")
    work = tmp_path / "work"
    (work / ".git").mkdir(parents=True)
    (work / "CLAUDE.md").symlink_to(outside)
    monkeypatch.chdir(work)
    [detail] = tui_events(
        monkeypatch, ["hello"], transport=FakeProvider([text_reply("ok")])
    )
    assert detail["files"] == []
    assert detail["skipped"] == [
        {"file": str(work / "CLAUDE.md"), "reason": "outside the project"}
    ]


def test_a_tui_resume_names_the_files_only_when_it_adds_them(
    repo: Path, built: list[str | None], monkeypatch: pytest.MonkeyPatch
) -> None:
    """As in nare -p: a resume that sends the record adds none, and one given a
    system option reads them again, once."""
    assert main(["-p", "hello"]) == 0
    assert tui_events(monkeypatch, ["-c", "again"]) == []
    [detail] = tui_events(
        monkeypatch, ["-c", "--append-system-prompt", "Persona.", "again"]
    )
    assert detail["files"] == [str(repo / "AGENTS.md"), str(repo / "pkg" / "AGENTS.md")]
