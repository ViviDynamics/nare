import asyncio
import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from rich.console import Console, RenderableType
from textual.app import App
from textual.pilot import Pilot
from textual.widgets import Input, Static

from fake_provider import FakeProvider, text_reply, tool_reply
from nare.cli import build_parser, main
from nare.loop import INTERRUPTED
from nare.session import Message
from nare.transport import Reply, Transport
from nare.tui.app import ApprovalScreen, TuiApp, prepare


async def until(pilot: Pilot[None], condition: Callable[[], bool]) -> None:
    for _ in range(300):
        if condition():
            return
        await pilot.pause(0.01)
    raise AssertionError("condition never held")


def shown(app: App[None]) -> str:
    """Everything the main screen draws, as plain text."""
    console = Console(width=120, file=io.StringIO(), record=True)
    for widget in app.screen_stack[0].query(Static):
        console.print(cast(RenderableType, widget.content), markup=False)
    return console.export_text()


def text_of(app: App[None], selector: str) -> str:
    return str(app.screen_stack[0].query_one(selector, Static).content)


def make(
    tmp_path: Path, transport: Transport, *argv: str, prompt: str | None = None
) -> TuiApp:
    args = build_parser().parse_args(
        ["tui", "--model", "fake", "--root", str(tmp_path),
         "--session", str(tmp_path / "s.json"), *argv,
         *([prompt] if prompt else [])]
    )  # fmt: skip
    return prepare(args, transport)


async def submit(pilot: Pilot[None], app: TuiApp, text: str) -> None:
    app.query_one(Input).value = text
    await pilot.press("enter")


class Stalled:
    """The first model call never returns; later ones answer from replies."""

    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)
        self.calls = 0

    async def context_window(self) -> int | None:
        return None

    async def turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> Reply:
        self.calls += 1
        if self.calls == 1:
            await asyncio.Event().wait()
        return self.replies.pop(0)


async def test_prompt_to_done(tmp_path: Path) -> None:
    app = make(tmp_path, FakeProvider([text_reply("all done")]), prompt="say hi")
    async with app.run_test() as pilot:
        await until(pilot, lambda: app.run_state == "done")
        assert "> say hi" in shown(app)
        assert "all done" in shown(app)
        status = text_of(app, "#status")
        assert "fake · turn 1/50 · 15 tok" in status
        assert status.endswith("done")
        assert app.query_one(Input).disabled is False
    saved = json.loads((tmp_path / "s.json").read_text())
    assert saved["status"] == "done"


async def test_y_runs_the_write(tmp_path: Path) -> None:
    replies = [
        tool_reply("write", {"path": "a.txt", "content": "hi\n"}),
        text_reply("ok"),
    ]
    app = make(tmp_path, FakeProvider(replies), prompt="go")
    async with app.run_test() as pilot:
        await until(pilot, lambda: isinstance(app.screen, ApprovalScreen))
        assert cast(ApprovalScreen, app.screen).text.startswith("new file ")
        assert "awaiting approval" in text_of(app, "#status")
        await pilot.press("y")
        await until(pilot, lambda: app.run_state == "done")
    assert (tmp_path / "a.txt").read_text() == "hi\n"


async def test_n_denies_and_the_run_continues(tmp_path: Path) -> None:
    replies = [
        tool_reply("write", {"path": "a.txt", "content": "hi\n"}),
        text_reply("ok"),
    ]
    app = make(tmp_path, FakeProvider(replies), prompt="go")
    async with app.run_test() as pilot:
        await until(pilot, lambda: isinstance(app.screen, ApprovalScreen))
        await pilot.press("n")
        await until(pilot, lambda: app.run_state == "done")
        assert "write was not approved" in shown(app)
    assert not (tmp_path / "a.txt").exists()


async def test_a_allows_the_tool_for_the_rest_of_the_process(tmp_path: Path) -> None:
    replies = [
        tool_reply("write", {"path": "a.txt", "content": "a"}, call_id="c1"),
        tool_reply("write", {"path": "b.txt", "content": "b"}, call_id="c2"),
        text_reply("ok"),
    ]
    app = make(tmp_path, FakeProvider(replies), prompt="go")
    async with app.run_test() as pilot:
        await until(pilot, lambda: isinstance(app.screen, ApprovalScreen))
        await pilot.press("a")
        # No second prompt: the run reaches done without another key.
        await until(pilot, lambda: app.run_state == "done")
    assert (tmp_path / "b.txt").read_text() == "b"


async def test_ctrl_c_during_approval_interrupts_then_follow_up(tmp_path: Path) -> None:
    replies = [tool_reply("bash", {"command": "echo hi"}), text_reply("resumed")]
    fake = FakeProvider(replies)
    app = make(tmp_path, fake, prompt="go")
    async with app.run_test() as pilot:
        await until(pilot, lambda: isinstance(app.screen, ApprovalScreen))
        await pilot.press("ctrl+c")
        await until(pilot, lambda: app.run_state == "interrupted")
        assert not isinstance(app.screen, ApprovalScreen)
        assert app.session is not None
        assert app.session.messages[-1].content[0]["content"] == INTERRUPTED
        assert "may still be running" in text_of(app, "#status")
        await submit(pilot, app, "go on")
        await until(pilot, lambda: app.run_state == "done")
        assert "> go on" in shown(app)
        assert "resumed" in shown(app)
    # The follow-up merged into the trailing tool-result message.
    sent = fake.calls[-1][0]
    assert sent[-1].content[-1] == {"type": "text", "text": "go on"}


async def test_esc_interrupts_a_model_call(tmp_path: Path) -> None:
    transport = Stalled(text_reply("second try"))
    app = make(tmp_path, transport, prompt="go")
    async with app.run_test() as pilot:
        await until(pilot, lambda: transport.calls == 1)
        await pilot.press("escape")
        await until(pilot, lambda: app.run_state == "interrupted")
        assert app.session is not None
        assert app.session.turns == 0
        assert [m.role for m in app.session.messages] == ["user"]
        await submit(pilot, app, "again")
        await until(pilot, lambda: app.run_state == "done")
        assert "second try" in shown(app)


async def test_blocked_then_answer_then_done(tmp_path: Path) -> None:
    replies = [tool_reply("ask", {"questions": ["which file?"]}), text_reply("thanks")]
    app = make(tmp_path, FakeProvider(replies), prompt="fix it")
    async with app.run_test() as pilot:
        # ask is exempt: no approval modal.
        await until(pilot, lambda: app.run_state == "blocked")
        assert "1. which file?" in text_of(app, "#outcome")
        assert app.query_one(Input).placeholder == "answer the questions above"
        await submit(pilot, app, "bar.py")
        await until(pilot, lambda: app.run_state == "done")
        assert "> bar.py" in shown(app)
        assert text_of(app, "#outcome") == ""


async def test_quitting_mid_run_saves(tmp_path: Path) -> None:
    app = make(tmp_path, Stalled(), prompt="go")
    async with app.run_test() as pilot:
        await until(pilot, lambda: app.run_state == "working")
        await pilot.press("ctrl+q")
    saved = json.loads((tmp_path / "s.json").read_text())
    assert saved["messages"][0]["content"] == [{"type": "text", "text": "go"}]


async def test_a_failed_save_is_a_warning_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("nare.session.os.replace", boom)
    app = make(tmp_path, FakeProvider([text_reply("ok")]), prompt="go")
    async with app.run_test() as pilot:
        await until(pilot, lambda: app.run_state == "done")
        assert "could not write" in text_of(app, "#status")


def test_startup_failure_exits_two_before_the_screen(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["tui", "--root", str(tmp_path / "nope")]) == 2
    assert "is not a directory" in capsys.readouterr().err


def test_an_unreadable_resume_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["tui", "--resume", str(tmp_path / "missing.json")]) == 2
    assert capsys.readouterr().err.startswith("nare: ")
