import asyncio
import io
import json
import logging
import sys
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any, cast

import pytest
from rich.console import Console, RenderableType
from textual.app import App
from textual.pilot import Pilot
from textual.widgets import Input, Static

from fake_provider import FakeProvider, StreamingProvider, text_reply, tool_reply
from nare.cli import build_parser, main
from nare.events import Event
from nare.loop import INTERRUPTED
from nare.session import Message, loads, new_session, save
from nare.transport import Delta, Reply, Transport
from nare.tui.app import ApprovalScreen, AttachApp, TuiApp, logs_as_notices, prepare


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
    """Model call number `stall` never returns; the others answer from replies."""

    def __init__(self, *replies: Reply, stall: int = 1) -> None:
        self.replies = list(replies)
        self.stall = stall
        self.calls = 0

    async def context_window(self) -> int | None:
        return None

    async def turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> Reply:
        self.calls += 1
        if self.calls == self.stall:
            await asyncio.Event().wait()
        return self.replies.pop(0)


class Streaming:
    """Streams its deltas, then holds the turn open until released."""

    streaming = True

    def __init__(self, *deltas: str) -> None:
        self.deltas = deltas
        self.release = asyncio.Event()

    async def context_window(self) -> int | None:
        return None

    async def turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> Reply:
        raise AssertionError("a streaming run never calls turn()")

    async def stream_turn(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncGenerator[Delta | Reply, None]:
        for text in self.deltas:
            yield Delta("progress", text)
        await self.release.wait()
        yield text_reply("".join(self.deltas))


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


async def test_esc_in_the_approval_modal_denies(tmp_path: Path) -> None:
    replies = [
        tool_reply("write", {"path": "a.txt", "content": "hi\n"}),
        text_reply("ok"),
    ]
    app = make(tmp_path, FakeProvider(replies), prompt="go")
    async with app.run_test() as pilot:
        await until(pilot, lambda: isinstance(app.screen, ApprovalScreen))
        await pilot.press("escape")
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
        await submit(pilot, app, "go on")
        await until(pilot, lambda: app.run_state == "done")
        assert "> go on" in shown(app)
        assert "resumed" in shown(app)
    # The follow-up merged into the trailing tool-result message.
    sent = fake.calls[-1][0]
    assert sent[-1].content[-1] == {"type": "text", "text": "go on"}


async def test_a_streamed_turn_is_not_saved_while_its_call_awaits_approval(
    tmp_path: Path,
) -> None:
    replies = [
        tool_reply("write", {"path": "a.txt", "content": "hi"}),
        text_reply("ok"),
    ]
    app = make(tmp_path, StreamingProvider(replies), prompt="go")
    async with app.run_test() as pilot:
        await until(pilot, lambda: isinstance(app.screen, ApprovalScreen))
        # What a closed terminal leaves while the modal waits on a person.
        saved = tmp_path / "s.json"
        assert (
            not saved.exists() or loads(saved.read_text()).messages[-1].role == "user"
        )
        await pilot.press("y")
        await until(pilot, lambda: app.run_state == "done")


async def test_a_follow_up_on_a_file_that_ends_in_an_unanswered_call(
    tmp_path: Path,
) -> None:
    # An older nare saved mid-dispatch, so a file can end in a call with no
    # result. The follow-up must answer it, and draw the turn it held back.
    s = new_session("go")
    s.messages.append(
        Message(
            "assistant",
            [
                {"type": "text", "text": "ASSISTANT-TEXT"},
                {
                    "type": "tool_use",
                    "id": "c1",
                    "name": "bash",
                    "input": {"command": "ls"},
                },
            ],
        )
    )
    save(s, tmp_path / "s.json")
    fake = FakeProvider([text_reply("ok")])
    app = make(tmp_path, fake, "--resume", str(tmp_path / "s.json"))
    async with app.run_test() as pilot:
        assert app.run_state == "interrupted"
        await submit(pilot, app, "continue")
        await until(pilot, lambda: app.run_state == "done")
        assert "ASSISTANT-TEXT" in shown(app)
    result, text = fake.calls[-1][0][-1].content
    assert result["tool_use_id"] == "c1" and result["is_error"] is True
    assert text == {"type": "text", "text": "continue"}


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


async def test_a_second_interrupt_lets_the_first_finish(tmp_path: Path) -> None:
    transport = Stalled()
    app = make(tmp_path, transport, prompt="go")
    async with app.run_test() as pilot:
        await until(pilot, lambda: transport.calls == 1)
        assert app.check_action("interrupt", ()) is True
        app.action_interrupt()
        # Cancelled but still unwinding: another Esc must not cut that short.
        assert app.worker is not None and app.worker.is_running
        assert app.check_action("interrupt", ()) is False
        await until(pilot, lambda: app.run_state == "interrupted")


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


async def test_a_log_warning_is_a_notification_not_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # What cli.main's basicConfig gives nare run.
    stderr = logging.StreamHandler(sys.stderr)
    logging.getLogger().addHandler(stderr)
    try:
        app = make(tmp_path, FakeProvider([]))
        with logs_as_notices(app):
            async with app.run_test() as pilot:
                logging.getLogger("nare.transport").warning("discovery [failed]")
                await until(
                    pilot,
                    lambda: any(
                        n.message == "discovery [failed]" for n in app._notifications
                    ),
                )
        assert stderr in logging.getLogger().handlers  # put back for nare run
    finally:
        logging.getLogger().removeHandler(stderr)
    assert "discovery" not in capsys.readouterr().err


def test_a_notice_gets_its_own_line_in_the_live_area(tmp_path: Path) -> None:
    app = make(tmp_path, FakeProvider([]))
    notice = {"context_window": {"tokens": 32000, "source": "default"}}
    app._event(Event("progress", "context window 32000 (default)", notice))
    app._event(Event("progress", "hello"))
    assert app.live.plain == "context window 32000 (default)\nhello"


async def test_the_live_block_shows_the_newest_streamed_text(tmp_path: Path) -> None:
    # Each line wraps on the 80-column test screen and ends with its label, so
    # the newest text is the last label: it must be on screen, the first not.
    lines = ["pad " * 40 + f"L{n:02d}x\n" for n in range(30)]
    transport = Streaming(*lines)
    app = make(tmp_path, transport, prompt="go")
    async with app.run_test() as pilot:
        await until(pilot, lambda: "L29x" in app.export_screenshot())
        assert "L00x" not in app.export_screenshot()
        transport.release.set()
        await until(pilot, lambda: app.run_state == "done")


async def test_an_interrupt_with_an_mcp_server_connected(tmp_path: Path) -> None:
    server = Path(__file__).with_name("mcp_server.py")
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {"local": {"command": sys.executable, "args": [str(server)], "timeout": 10}}
        )
    )
    transport = Stalled(text_reply("after"))
    app = make(tmp_path, transport, "--mcp-config", str(config), prompt="go")
    async with app.run_test() as pilot:
        await until(pilot, lambda: transport.calls == 1)
        await pilot.press("escape")
        await until(pilot, lambda: app.run_state == "interrupted")
        assert app.session is not None
        assert app.session.error is None
        await submit(pilot, app, "again")
        await until(pilot, lambda: app.run_state == "done")
        assert "after" in shown(app)


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


def test_a_resume_with_a_bad_saved_budget_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    session = new_session("go")
    session.budget["tokens"] = -5
    path = tmp_path / "old.json"
    save(session, path)
    argv = ["tui", "--model", "fake", "--resume", str(path)]
    assert main(argv, transport=FakeProvider([])) == 2
    err = capsys.readouterr().err
    assert err.startswith("nare: ") and "budget_tokens" in err


async def test_attach_waits_then_draws(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    app = AttachApp(path, interval=0.02)
    async with app.run_test() as pilot:
        await until(pilot, lambda: "waiting for" in text_of(app, "#status"))
        save(new_session("late task"), path)
        await until(pilot, lambda: "late task" in shown(app))
        assert "last write" in text_of(app, "#status")


async def test_attach_redraws_when_the_file_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    save(new_session("first task"), path)
    app = AttachApp(path, interval=0.02)
    async with app.run_test() as pilot:
        await until(pilot, lambda: "first task" in shown(app))
        save(new_session("second task!"), path)  # different size, new stamp
        await until(pilot, lambda: "second task!" in shown(app))
        assert "first task" not in shown(app)


async def test_attach_stops_on_a_contract_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    save(new_session("go"), path)
    raw = json.loads(path.read_text())
    raw["contract"] = 99
    path.write_text(json.dumps(raw))
    app = AttachApp(path, interval=0.02)
    async with app.run_test() as pilot:
        await until(pilot, lambda: "contract 99" in text_of(app, "#status"))
        assert app.watcher.fatal is True


def test_attach_refuses_a_prompt(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tui", "--attach", "s.json", "do it"]) == 2
    assert "--attach" in capsys.readouterr().err


async def test_attach_polling_stops_with_its_screen(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    save(new_session("go"), path)
    app = AttachApp(path, interval=0.001)
    async with app.run_test() as pilot:
        await until(pilot, lambda: "> go" in shown(app))
        # The first step of App._shutdown: the screens and their widgets go.
        # A poll after this raises NoMatches, which run_test re-raises.
        await app._close_all()
        await asyncio.sleep(0.05)  # about fifty ticks at this interval
