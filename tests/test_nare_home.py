"""NARE_HOME: the folder nare keeps its local files in (issue #72).

Sessions saved without --session go to $NARE_HOME/sessions (default ~/.nare).
Sessions saved before this change, under $XDG_STATE_HOME/nare/sessions, are
read in place: the picker lists them and a resume by path works. Nothing is
moved.
"""

from __future__ import annotations

import json
import os
import shlex
import stat
import tempfile
from pathlib import Path

import pytest

from fake_provider import FakeProvider, text_reply
from nare.cli import build_parser, main
from nare.session import (
    legacy_sessions_dir,
    nare_home,
    new_session,
    resumable_in,
    save,
    sessions_dir,
)
from nare.tui.app import exit_summary, prepare


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home directory with no NARE_HOME and no XDG override, as a fresh user has."""
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.delenv("NARE_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.chdir(folder)
    return folder


def saved(folder: Path, name: str, cwd: str, prompt: str, age: int) -> Path:
    session = new_session(prompt, session_id=name)
    session.cwd = cwd
    session.status = "done"
    path = folder / f"{name}.json"
    folder.mkdir(parents=True, exist_ok=True)
    save(session, path)
    stamp = 1_700_000_000 + age
    os.utime(path, (stamp, stamp))
    return path


def test_the_default_home_is_dot_nare(home: Path) -> None:
    assert nare_home() == home / ".nare"
    assert sessions_dir() == home / ".nare" / "sessions"


def test_nare_home_moves_the_sessions_folder(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NARE_HOME", str(tmp_path / "x"))
    assert sessions_dir() == tmp_path / "x" / "sessions"


def test_a_relative_nare_home_is_refused(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NARE_HOME", "relative/home")
    with pytest.raises(ValueError, match="NARE_HOME must be an absolute path"):
        sessions_dir()


def test_the_legacy_folder_is_the_old_xdg_path(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert legacy_sessions_dir() == tmp_path / "state" / "nare" / "sessions"


def test_a_relative_xdg_state_home_is_ignored_for_the_legacy_folder(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", "state")
    assert legacy_sessions_dir() == home / ".local" / "state" / "nare" / "sessions"


async def test_without_session_the_run_is_saved_under_nare_home(home: Path) -> None:
    args = build_parser().parse_args(["tui", "--model", "fake", "go"])
    app = prepare(args, FakeProvider([text_reply("all done")]))
    async with app.run_test() as pilot:
        for _ in range(300):
            if app.run_state == "done":
                break
            await pilot.pause(0.01)
    assert app.session is not None
    path = home / ".nare" / "sessions" / f"{app.session.id}.json"
    assert json.loads(path.read_text())["status"] == "done"
    assert f"resume: nare tui --resume {shlex.quote(str(path))}" in exit_summary(app)


def test_the_home_is_created_0700_and_session_files_are_0600(home: Path) -> None:
    args = build_parser().parse_args(["tui", "--model", "fake", "go"])
    prepare(args, FakeProvider([]))
    folder = home / ".nare" / "sessions"
    assert stat.S_IMODE((home / ".nare").stat().st_mode) == 0o700
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    path = Path(args.session)
    save(new_session("x"), path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_an_existing_home_keeps_its_own_permissions(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = home / ".nare"
    existing.mkdir(mode=0o755)
    os.chmod(existing, 0o755)
    prepare(build_parser().parse_args(["tui", "--model", "fake"]), FakeProvider([]))
    assert stat.S_IMODE(existing.stat().st_mode) == 0o755


def test_an_unwritable_nare_home_exits_two_naming_both_options(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A file where the folder should be: no mkdir can succeed, as root or not.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a folder", encoding="utf-8")
    monkeypatch.setenv("NARE_HOME", str(blocker / "nare"))
    assert main(["tui", "--model", "fake"], transport=FakeProvider([])) == 2
    err = capsys.readouterr().err
    assert "NARE_HOME" in err
    assert "--session" in err


def test_a_session_inside_nare_home_does_not_warn_from_home(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("NARE_HOME", str(home / ".nare"))
    path = home / ".nare" / "sessions" / "x.json"
    args = build_parser().parse_args(["tui", "--model", "fake", "--session", str(path)])
    prepare(args, FakeProvider([]))
    assert capsys.readouterr().err == ""


def test_a_session_outside_nare_home_from_home_still_warns(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = home / "project-notes.json"
    args = build_parser().parse_args(["tui", "--model", "fake", "--session", str(path)])
    prepare(args, FakeProvider([]))
    assert "inside the project" in capsys.readouterr().err


def test_an_old_xdg_session_still_resumes_by_path_and_stays_in_place(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    legacy = tmp_path / "state" / "nare" / "sessions"
    old = legacy / "old.json"
    legacy.mkdir(parents=True)
    save(new_session("the old prompt"), old)
    args = build_parser().parse_args(
        ["tui", "--model", "fake", "--resume", str(old), "more"]
    )
    app = prepare(args, FakeProvider([]))
    assert app.session is not None
    assert app.session.messages[0].content[0]["text"] == "the old prompt"
    # Nothing is moved: the resumed run is saved back to the same file.
    assert args.session == str(old)
    assert not (home / ".nare" / "sessions").exists()


def test_the_picker_lists_both_folders_newest_first(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    here = str(home)
    saved(tmp_path / "state" / "nare" / "sessions", "old", here, "from before", 10)
    saved(home / ".nare" / "sessions", "new", here, "from now", 20)
    saved(home / ".nare" / "sessions", "elsewhere", str(tmp_path), "not here", 30)
    rows = resumable_in(here)
    assert [row.path.stem for row in rows] == ["new", "old"]


def test_a_session_in_both_folders_is_listed_once(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # NARE_HOME pointed at the legacy parent: the same folder twice.
    monkeypatch.setenv("NARE_HOME", str(home / ".local" / "state" / "nare"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    saved(sessions_dir() or home, "dup", str(home), "once", 0)
    assert [row.path.stem for row in resumable_in(str(home))] == ["dup"]


def test_the_tui_picker_names_both_folders_when_none_match(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert main(["tui", "--resume"]) == 2
    err = capsys.readouterr().err
    assert "no saved session" in err
    assert str(home / ".nare" / "sessions") in err
    assert str(tmp_path / "state" / "nare" / "sessions") in err


def test_a_relative_nare_home_exits_two_from_the_picker(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("NARE_HOME", "rel")
    assert main(["tui", "--resume"]) == 2
    assert "NARE_HOME" in capsys.readouterr().err


def test_an_explicit_session_still_runs_with_a_relative_nare_home(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # --session never needed NARE_HOME; a bad value must not break it.
    monkeypatch.setenv("NARE_HOME", "relative/home")
    path = tmp_path / "explicit.json"
    args = build_parser().parse_args(["tui", "--model", "fake", "--session", str(path)])
    prepare(args, FakeProvider([]))
    assert args.session == str(path)


def test_an_unwritable_sessions_folder_is_refused_at_startup(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The folder exists and mkdir succeeds; only the write probe fails. It must
    # be caught before the app starts, not at exit, when the transcript is lost.
    (home / ".nare" / "sessions").mkdir(parents=True, mode=0o700)

    def denied(*args: object, **kwargs: object) -> object:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(tempfile, "TemporaryFile", denied)
    args = build_parser().parse_args(["tui", "--model", "fake"])
    with pytest.raises(ValueError, match="NARE_HOME.*--session"):
        prepare(args, FakeProvider([]))
