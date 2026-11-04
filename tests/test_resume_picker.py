import json
import os
from pathlib import Path

import pytest

from fake_provider import FakeProvider, text_reply
from nare.cli import build_parser, main
from nare.session import dumps, loads, new_session, save, saved_in
from nare.tui.app import SessionPicker
from test_tui_app import make, until


def saved(folder: Path, name: str, cwd: str | None, prompt: str, age: int) -> Path:
    session = new_session(prompt, session_id=name)
    session.cwd = cwd
    session.status = "done"
    path = folder / f"{name}.json"
    save(session, path)
    stamp = 1_700_000_000 + age
    os.utime(path, (stamp, stamp))
    return path


def test_the_resume_flag_takes_an_optional_path() -> None:
    parser = build_parser()
    assert parser.parse_args(["tui", "--resume"]).resume == ""
    assert parser.parse_args(["tui", "--resume", "a.json"]).resume == "a.json"
    assert parser.parse_args(["tui"]).resume is None


async def test_a_tui_run_records_its_directory_and_root(tmp_path: Path) -> None:
    app = make(tmp_path, FakeProvider([text_reply("hi")]), prompt="go")
    async with app.run_test() as pilot:
        await until(pilot, lambda: app.run_state == "done")
        assert app.session is not None
        assert app.session.cwd == os.getcwd()
        assert app.session.root == str(tmp_path.resolve())


def test_a_session_saved_before_the_fields_loads_with_them_unset() -> None:
    raw = json.loads(dumps(new_session("hi")))
    del raw["cwd"], raw["root"]
    session = loads(json.dumps(raw))
    assert session.cwd is None
    assert session.root is None


def test_saved_here_lists_only_this_directory_newest_first(tmp_path: Path) -> None:
    folder = tmp_path / "sessions"
    folder.mkdir()
    here = tmp_path / "project"
    here.mkdir()
    older = saved(folder, "older", str(here), "first task", age=0)
    newer = saved(folder, "newer", str(here), "second task", age=100)
    saved(folder, "elsewhere", str(tmp_path / "other"), "not this one", age=200)
    saved(folder, "unrecorded", None, "saved before the field", age=300)
    (folder / "junk.json").write_text("{not json", encoding="utf-8")
    rows = saved_in(folder, str(here))
    assert [row.path for row in rows] == [newer, older]
    assert rows[0].prompt == "second task"
    assert rows[0].status == "done"
    assert rows[0].turns == 0


def test_saved_here_is_empty_without_a_folder(tmp_path: Path) -> None:
    assert saved_in(tmp_path / "missing", str(tmp_path)) == []
    assert saved_in(None, str(tmp_path)) == []


async def test_the_picker_resumes_the_chosen_session(tmp_path: Path) -> None:
    folder = tmp_path / "sessions"
    folder.mkdir()
    first = saved(folder, "a", str(tmp_path), "alpha", age=0)
    second = saved(folder, "b", str(tmp_path), "beta", age=50)
    rows = saved_in(folder, str(tmp_path))
    picker = SessionPicker(rows)
    async with picker.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause()
    assert picker.return_value == str(second)
    assert first != second


async def test_the_picker_cancels_without_a_choice(tmp_path: Path) -> None:
    folder = tmp_path / "sessions"
    folder.mkdir()
    saved(folder, "a", str(tmp_path), "alpha", age=0)
    picker = SessionPicker(saved_in(folder, str(tmp_path)))
    async with picker.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause()
    assert picker.return_value is None


def test_tui_resume_with_no_session_exits_two_and_names_the_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    assert main(["tui", "--resume"]) == 2
    err = capsys.readouterr().err
    assert "no saved session" in err
    assert str(tmp_path) in err


def test_run_resume_without_a_path_exits_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    assert (
        main(["run", "--yes", "--resume", "--provider", "fake", "--replay", "x", "hi"])
        == 2
    )
    assert "--resume needs a path" in capsys.readouterr().err
