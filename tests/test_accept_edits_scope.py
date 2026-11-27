"""--permission-mode acceptEdits approves write and edit unasked only inside
the working folders: the root (the working directory when there is no
--root) and each --add-root, as Claude Code scopes it to the working
directory and its added directories. Anywhere else is asked about."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from fake_provider import FakeProvider, text_reply, tool_reply
from nare.cli import main

ACCEPT = ["--permission-mode", "acceptEdits"]


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ("NARE_PROVIDER", "NARE_MODEL", "NARE_BASE_URL", "NARE_BUDGET_USD"):
        monkeypatch.delenv(name, raising=False)
    folder = tmp_path / "work"
    folder.mkdir()
    monkeypatch.chdir(folder)
    return folder


def at_terminal(monkeypatch: pytest.MonkeyPatch, typed: str) -> None:
    monkeypatch.setattr("nare.cli.stdin_is_terminal", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO(typed))


def writes(*paths: str) -> FakeProvider:
    return FakeProvider(
        [
            *(
                tool_reply("write", {"path": path, "content": "hi"}, call_id=f"c{i}")
                for i, path in enumerate(paths)
            ),
            text_reply("done"),
        ]
    )


def test_inside_the_working_directory_runs_unasked(
    work: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    at_terminal(monkeypatch, "")
    (work / "sub").mkdir()
    fake = writes("inside.txt", str(work / "sub" / "deep.txt"))
    assert main(["run", *ACCEPT, "write"], transport=fake) == 0
    assert (work / "inside.txt").read_text() == "hi"
    assert (work / "sub" / "deep.txt").read_text() == "hi"
    assert "allow?" not in capsys.readouterr().err


@pytest.mark.parametrize("where", ["absolute", "dotdot", "link"])
def test_outside_the_working_directory_is_asked(
    where: str,
    work: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (work / "out").symlink_to(outside)
    path = {
        "absolute": str(outside / "x.txt"),
        "dotdot": "../outside/x.txt",
        "link": "out/x.txt",
    }[where]
    at_terminal(monkeypatch, "n\n")
    assert main(["run", *ACCEPT, "write"], transport=writes(path)) == 0
    assert not (outside / "x.txt").exists()
    assert "allow?" in capsys.readouterr().err


def test_a_yes_still_writes_outside(
    work: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "elsewhere.txt"
    at_terminal(monkeypatch, "y\n")
    assert main(["run", *ACCEPT, "write"], transport=writes(str(target))) == 0
    assert target.read_text() == "hi"


def test_the_root_and_added_roots_run_unasked_and_nothing_else(
    work: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, extra = tmp_path / "root", tmp_path / "extra"
    root.mkdir()
    extra.mkdir()
    at_terminal(monkeypatch, "")
    fake = writes("in-root.txt", str(extra / "in-extra.txt"))
    argv = ["run", *ACCEPT, "--root", str(root), "--add-root", str(extra), "write"]
    assert main(argv, transport=fake) == 0
    assert (root / "in-root.txt").read_text() == "hi"
    assert (extra / "in-extra.txt").read_text() == "hi"
    assert "allow?" not in capsys.readouterr().err
    # The working directory is not a root here, so it is asked about.
    at_terminal(monkeypatch, "n\n")
    argv = ["run", *ACCEPT, "--root", str(root), "write"]
    assert main(argv, transport=writes(str(work / "cwd.txt"))) == 0
    assert not (work / "cwd.txt").exists()
    assert "allow?" in capsys.readouterr().err


def test_print_mode_denies_a_write_outside(work: Path, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.txt"
    assert main(["-p", *ACCEPT, "write"], transport=writes(str(target))) == 0
    assert not target.exists()
