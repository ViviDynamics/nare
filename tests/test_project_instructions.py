"""AGENTS.md, with CLAUDE.md as the fallback, in the person-facing forms (#330).

Loaded after the system prompt and before --append-system, outermost first,
capped at 32 KiB. Never in nare run, unless it opts in with
--project-instructions.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pytest

from fake_provider import FakeProvider, text_reply
from nare import instructions
from nare.cli import DEFAULT_SYSTEM, main
from nare.session import nare_home


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
