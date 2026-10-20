import asyncio
import io
import json
import logging
import signal
import subprocess
import sys
import time
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any, cast

import pytest
from rich.console import Console, RenderableType
from rich.text import Text
from textual.app import App
from textual.pilot import Pilot
from textual.widgets import Input, Static

from fake_provider import FakeProvider, StreamingProvider, text_reply, tool_reply
from nare.cli import build_parser, main
from nare.events import Event
from nare.loop import INTERRUPTED
from nare.session import Message, loads, new_session, save
from nare.tools import group_alive, stop_group
from nare.transport import Delta, Reply, Transport
from nare.tui.app import (
    OFFER_GRACE,
    ApprovalScreen,
    AttachApp,
    GroupsScreen,
    Transcript,
    TuiApp,
    exit_summary,
    logs_as_notices,
    prepare,
)
from nare.tui.render import Block


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


async def test_a_read_only_command_runs_without_a_prompt(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("A-CONTENT\n")
    replies = [tool_reply("bash", {"command": "cat a.txt"}), text_reply("ok")]
    fake = FakeProvider(replies)
    app = make(tmp_path, fake, prompt="go")
    async with app.run_test() as pilot:
        await until(pilot, lambda: app.run_state == "done")
    assert "A-CONTENT" in fake.calls[-1][0][-1].content[0]["content"]


async def test_ctrl_c_during_approval_interrupts_then_follow_up(tmp_path: Path) -> None:
    replies = [tool_reply("bash", {"command": "touch a.txt"}), text_reply("resumed")]
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


async def test_a_log_warning_is_a_transcript_line_not_stderr(
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
                # In the transcript, not a toast that is gone in seconds.
                await until(pilot, lambda: "discovery [failed]" in shown(app))
        assert stderr in logging.getLogger().handlers  # put back for nare run
    finally:
        logging.getLogger().removeHandler(stderr)
    assert "discovery" not in capsys.readouterr().err


async def test_notices_stay_in_the_transcript_after_the_turn(tmp_path: Path) -> None:
    app = make(tmp_path, FakeProvider([text_reply("ok")]))
    async with app.run_test() as pilot:
        compacted = "compacted: elided 3 tool results, ~900 -> ~300 tokens"
        app._event(Event("progress", compacted, {"compaction": {}}))
        await submit(pilot, app, "go")
        await until(pilot, lambda: app.run_state == "done")
        assert compacted in shown(app)
        assert "context window 32000 (default)" in shown(app)
        assert app.live.plain == ""
        await submit(pilot, app, "again")
        await until(pilot, lambda: app.run_state == "error")  # out of replies
        assert shown(app).count("context window 32000") == 1  # unchanged


def test_an_image_notice_gets_its_own_line_in_the_live_area(tmp_path: Path) -> None:
    app = make(tmp_path, FakeProvider([]))
    app._event(Event("progress", "read a.png", {"tool": "read", "image": {}}))
    app._event(Event("progress", "hello"))
    assert app.live.plain == "read a.png\nhello"


async def test_the_newest_block_stays_in_view(tmp_path: Path) -> None:
    app = make(tmp_path, FakeProvider([]))
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one(Transcript).add([Block(Text(f"B{n:02d}x")) for n in range(60)])
        await pilot.pause()
        live = Text("\n".join(f"live {n}" for n in range(10)))
        app.query_one("#live-text", Static).update(live)
        await pilot.pause()
        assert "B59x" in app.export_screenshot()
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        assert "B59x" in app.export_screenshot()


async def test_a_settled_turn_leaves_a_scrolled_up_transcript_alone(
    tmp_path: Path,
) -> None:
    transport = Streaming("thinking it over\n")
    app = make(tmp_path, transport)
    async with app.run_test() as pilot:
        transcript = app.query_one(Transcript)
        transcript.add([Block(Text(f"B{n:02d}x")) for n in range(60)])
        await submit(pilot, app, "go")
        await until(pilot, lambda: "thinking it over" in app.live.plain)
        await pilot.press("pageup")
        await pilot.pause()
        where = transcript.scroll_y
        assert where < transcript.max_scroll_y
        transport.release.set()
        await until(pilot, lambda: app.run_state == "done")
        await pilot.pause()
        assert transcript.scroll_y == where
        # Sending a prompt brings you back to the newest content.
        await submit(pilot, app, "again")
        await until(pilot, lambda: app.run_state == "done")
        await pilot.pause()
        assert transcript.scroll_y == transcript.max_scroll_y


async def test_page_keys_scroll_the_transcript_while_you_type(tmp_path: Path) -> None:
    app = make(tmp_path, FakeProvider([]))
    async with app.run_test() as pilot:
        transcript = app.query_one(Transcript)
        transcript.add([Block(Text(f"B{n:02d}x")) for n in range(60)])
        await pilot.pause()
        assert app.focused is app.query_one(Input)
        await pilot.press("pageup")
        await pilot.pause()
        assert transcript.scroll_y < transcript.max_scroll_y
        await pilot.press("pagedown")
        await pilot.pause()
        # Back at the bottom, the anchor holds again.
        transcript.add([Block(Text("newest"))])
        await pilot.pause()
        assert transcript.scroll_y == transcript.max_scroll_y
        # A page key with nowhere to go must not let go of the bottom either.
        await pilot.press("pagedown")
        transcript.add([Block(Text(f"C{n:02d}x")) for n in range(30)])
        await pilot.pause()
        assert "C29x" in app.export_screenshot()


async def test_pageup_on_a_short_transcript_keeps_following(tmp_path: Path) -> None:
    app = make(tmp_path, FakeProvider([]))
    async with app.run_test() as pilot:
        await pilot.press("pageup")
        app.query_one(Transcript).add([Block(Text(f"B{n:02d}x")) for n in range(60)])
        await pilot.pause()
        assert "B59x" in app.export_screenshot()


def test_a_tool_call_with_a_notice_shaped_argument_is_not_a_notice(
    tmp_path: Path,
) -> None:
    app = make(tmp_path, FakeProvider([]))
    app._event(Event("tool_use", "mcp_llm_query", {"context_window": 8000}))
    assert app.window is None


async def test_esc_during_a_streamed_turn_leaves_a_mark(tmp_path: Path) -> None:
    transport = Streaming("half an answer\n")
    app = make(tmp_path, transport, prompt="go")
    async with app.run_test() as pilot:
        await until(pilot, lambda: "half an answer" in app.live.plain)
        await pilot.press("escape")
        await until(pilot, lambda: app.run_state == "interrupted")
        await pilot.pause()
        assert "— interrupted —" in shown(app)


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


async def test_the_first_line_names_model_root_and_session(tmp_path: Path) -> None:
    app = make(tmp_path, FakeProvider([]))
    async with app.run_test():
        first = str(app.query(Static).first().content)
    assert first.startswith("nare ")
    assert f"fake · root {tmp_path} · session {tmp_path / 's.json'}" in first


async def test_a_session_file_inside_the_project_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    app = make(tmp_path, FakeProvider([]))
    assert "inside the project" in capsys.readouterr().err
    async with app.run_test():
        assert "inside the project" in shown(app)
    project = tmp_path / "proj"
    project.mkdir()
    args = build_parser().parse_args(
        ["tui", "--model", "fake", "--root", str(project),
         "--session", str(tmp_path / "s.json")]
    )  # fmt: skip
    prepare(args, FakeProvider([]))
    assert capsys.readouterr().err == ""
    monkeypatch.chdir(tmp_path)  # under the cwd, though not under --root
    prepare(args, FakeProvider([]))
    assert "inside the project" in capsys.readouterr().err


def test_no_warning_for_a_file_in_nares_own_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Resuming the printed path from $HOME, which holds the state dir.
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    home.mkdir()
    monkeypatch.chdir(home)
    path = home / ".local" / "state" / "nare" / "sessions" / "x.json"
    args = build_parser().parse_args(["tui", "--model", "fake", "--session", str(path)])
    prepare(args, FakeProvider([]))
    assert capsys.readouterr().err == ""


async def test_without_session_the_run_is_saved_under_xdg_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "st ate"))
    args = build_parser().parse_args(["tui", "--model", "fake", "go"])
    app = prepare(args, FakeProvider([text_reply("all done")]))
    async with app.run_test() as pilot:
        await until(pilot, lambda: app.run_state == "done")
    assert app.session is not None
    path = tmp_path / "st ate" / "nare" / "sessions" / f"{app.session.id}.json"
    assert json.loads(path.read_text())["status"] == "done"
    assert f"resume: nare tui --resume '{path}'" in exit_summary(app)


def test_a_relative_xdg_state_home_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The XDG spec says so, and here it would put the file in the project.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_STATE_HOME", "state")
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(["tui", "--model", "fake"])
    prepare(args, FakeProvider([]))
    sessions = tmp_path / "home" / ".local" / "state" / "nare" / "sessions"
    assert Path(args.session).parent == sessions


def test_no_home_directory_exits_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_home() -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", no_home)
    assert main(["tui", "--model", "fake"], transport=FakeProvider([])) == 2
    assert "--session" in capsys.readouterr().err


async def test_esc_then_quit_records_the_interrupt_and_resume_clears_it(
    tmp_path: Path,
) -> None:
    transport = Stalled()
    app = make(tmp_path, transport, prompt="go")
    async with app.run_test() as pilot:
        await until(pilot, lambda: transport.calls == 1)
        await pilot.press("escape")
        await until(pilot, lambda: app.run_state == "interrupted")
        await pilot.press("ctrl+q")
    path = tmp_path / "s.json"
    assert loads(path.read_text()).interrupted_at is not None
    transport = Stalled()
    app = make(tmp_path, transport, "--resume", str(path))
    async with app.run_test() as pilot:
        await submit(pilot, app, "again")
        await until(pilot, lambda: transport.calls == 1)
        # Mid-turn: --attach must not read the live run as interrupted.
        assert loads(path.read_text()).interrupted_at is None
        await pilot.press("ctrl+q")


async def test_the_exit_summary_keeps_the_answer_and_the_resume_command(
    tmp_path: Path,
) -> None:
    answer = "\n".join(f"line {n}" for n in range(1, 31))
    app = make(tmp_path, FakeProvider([text_reply(answer)]))
    assert exit_summary(app) == ""  # nothing typed, nothing saved
    async with app.run_test() as pilot:
        await submit(pilot, app, "go")
        await until(pilot, lambda: app.run_state == "done")
    summary = exit_summary(app).splitlines()
    path = tmp_path / "s.json"
    assert summary[:2] == ["line 1", "line 2"]
    assert summary[19:21] == ["line 20", "... 10 more lines"]
    assert summary[-2].startswith("fake · turn 1/50") and summary[-2].endswith("done")
    assert summary[-1] == f"session: {path} · resume: nare tui --resume {path}"


def test_the_exit_summary_names_the_question_or_the_error(tmp_path: Path) -> None:
    app = make(tmp_path, FakeProvider([]))
    session = new_session("go")
    session.messages += [
        Message("assistant", [{"type": "text", "text": "I looked at foo.py"}]),
        Message("user", [{"type": "text", "text": "now bar"}]),
        Message("assistant", [{"type": "tool_use", "id": "c1", "name": "ask",
                               "input": {"questions": ["which file?"]}}]),
    ]  # fmt: skip
    session.status, session.questions = "blocked", ["which file?"]
    app.session = session
    summary = exit_summary(app)
    assert "foo.py" not in summary  # an earlier run's answer
    assert "1. which file?" in summary
    session.status, session.error = "error", "RuntimeError: connection reset"
    assert "RuntimeError: connection reset" in exit_summary(app)


def test_the_exit_summary_is_redacted(tmp_path: Path) -> None:
    key = "sk-ant-" + "a" * 30
    app = make(tmp_path, FakeProvider([]))
    app.session = new_session("go")
    app.session.messages.append(Message("assistant", [{"type": "text", "text": key}]))
    summary = exit_summary(app)
    assert key not in summary and "[redacted]" in summary


LEAVE_RUNNING = "(sleep 30 >/dev/null 2>&1 &)"


def leaves_a_group(*after: Reply) -> FakeProvider:
    return FakeProvider(
        [tool_reply("bash", {"command": LEAVE_RUNNING}), text_reply("started"), *after]
    )


def stop_recorded(app: TuiApp) -> None:
    for group in [] if app.session is None else app.session.process_groups:
        stop_group(group["pgid"])


def group_gone(pgid: int) -> bool:
    for _ in range(300):
        if not group_alive(pgid):
            return True
        time.sleep(0.01)
    return False


async def to_groups_offer(pilot: Pilot[None], app: TuiApp) -> int:
    """Approve the bash call, wait until the offer takes answers, return the
    recorded pgid.
    """
    await until(pilot, lambda: isinstance(app.screen, ApprovalScreen))
    await pilot.press("y")
    await until(
        pilot,
        lambda: (
            isinstance(app.screen, GroupsScreen) and bool(app.screen.query("#groups"))
        ),
    )
    await pilot.pause(OFFER_GRACE)
    assert app.session is not None
    return int(app.session.process_groups[0]["pgid"])


async def test_a_run_that_leaves_a_group_running_offers_to_kill_it(
    tmp_path: Path,
) -> None:
    app = make(tmp_path, leaves_a_group(), prompt="start it")
    try:
        async with app.run_test() as pilot:
            pgid = await to_groups_offer(pilot, app)
            assert app.run_state == "done"
            screen = cast(GroupsScreen, app.screen)
            assert not screen.quitting
            listed = str(screen.query_one("#groups", Static).content)
            assert f"process group {pgid} ({LEAVE_RUNNING})" in listed
            await pilot.press("k")
            await until(pilot, lambda: not group_alive(pgid))
            assert not isinstance(app.screen, GroupsScreen)
            assert f"killed process group {pgid}" in shown(app)
    finally:
        stop_recorded(app)


async def test_typing_as_the_offer_appears_does_not_answer_it(tmp_path: Path) -> None:
    # The input box comes back as the run settles, and the offer takes the
    # keys in the same tick: a follow-up "keep going" killed the group.
    app = make(tmp_path, leaves_a_group(), prompt="start it")
    try:
        async with app.run_test() as pilot:
            await until(pilot, lambda: isinstance(app.screen, ApprovalScreen))
            await pilot.press("y")
            await until(pilot, lambda: isinstance(app.screen, GroupsScreen))
            await pilot.press(*"keep")
            await pilot.pause()
            assert isinstance(app.screen, GroupsScreen)
            assert "killed process group" not in shown(app)
            await pilot.pause(OFFER_GRACE)
            await pilot.press("l")
            await until(pilot, lambda: not isinstance(app.screen, GroupsScreen))
            assert "left running: process group" in shown(app)
    finally:
        stop_recorded(app)


async def test_leave_keeps_the_group_and_is_not_asked_again(tmp_path: Path) -> None:
    app = make(tmp_path, leaves_a_group(text_reply("again")), prompt="start it")
    try:
        async with app.run_test() as pilot:
            pgid = await to_groups_offer(pilot, app)
            await pilot.press("l")
            await until(pilot, lambda: not isinstance(app.screen, GroupsScreen))
            assert group_alive(pgid)
            assert f"left running: process group {pgid}" in shown(app)
            await submit(pilot, app, "and now?")
            await until(
                pilot,
                lambda: (
                    "again" in shown(app)
                    and app.worker is not None
                    and app.worker.is_finished
                ),
            )
            await pilot.pause()
            assert not isinstance(app.screen, GroupsScreen)
    finally:
        stop_recorded(app)


async def test_escape_at_the_turn_end_offer_leaves_the_group(tmp_path: Path) -> None:
    app = make(tmp_path, leaves_a_group(), prompt="start it")
    try:
        async with app.run_test() as pilot:
            pgid = await to_groups_offer(pilot, app)
            await pilot.press("escape")
            await until(pilot, lambda: not isinstance(app.screen, GroupsScreen))
            assert group_alive(pgid)
            assert f"left running: process group {pgid}" in shown(app)
            assert pgid in app.left
    finally:
        stop_recorded(app)


async def test_a_kill_that_fails_at_turn_end_says_so_and_keeps_the_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make(tmp_path, leaves_a_group(), prompt="start it")
    monkeypatch.setattr("nare.tui.app.stop_group", lambda pgid: False)
    try:
        async with app.run_test() as pilot:
            pgid = await to_groups_offer(pilot, app)
            await pilot.press("k")
            await until(pilot, lambda: not isinstance(app.screen, GroupsScreen))
            assert f"could not kill process group {pgid}" in shown(app)
            assert f"killed process group {pgid}" not in shown(app)
            assert app.session is not None
            assert [g["pgid"] for g in app.session.process_groups] == [pgid]
    finally:
        stop_recorded(app)


async def test_a_run_that_leaves_nothing_running_offers_nothing(
    tmp_path: Path,
) -> None:
    replies = [tool_reply("bash", {"command": "true"}), text_reply("ok")]
    app = make(tmp_path, FakeProvider(replies), prompt="go")
    async with app.run_test() as pilot:
        await until(pilot, lambda: isinstance(app.screen, ApprovalScreen))
        await pilot.press("y")
        await until(pilot, lambda: app.worker is not None and app.worker.is_finished)
        await pilot.pause()
        assert app.run_state == "done"
        assert not isinstance(app.screen, GroupsScreen)


def sleeper() -> subprocess.Popen[bytes]:
    return subprocess.Popen(["sleep", "30"], start_new_session=True)


def resumable(tmp_path: Path, pgid: int) -> Path:
    """A saved session that recorded `pgid` as left running."""
    s = new_session("go")
    s.status = "done"
    s.process_groups.append({"pgid": pgid, "command": "sleep 30"})
    path = tmp_path / "earlier.json"
    save(s, path)
    return path


async def test_quitting_with_a_live_group_asks_first_and_k_kills_it(
    tmp_path: Path,
) -> None:
    proc = sleeper()
    try:
        path = resumable(tmp_path, proc.pid)
        app = make(tmp_path, FakeProvider([]), "--resume", str(path))
        async with app.run_test() as pilot:
            await pilot.press("ctrl+q")
            await until(pilot, lambda: isinstance(app.screen, GroupsScreen))
            assert cast(GroupsScreen, app.screen).quitting
            assert app.return_code is None
            await pilot.press("k")
            await until(pilot, lambda: app.return_code is not None)
        assert proc.wait(timeout=5) == -signal.SIGKILL
    finally:
        proc.kill()
        proc.wait()


async def test_l_at_the_quit_prompt_leaves_the_group_and_quits(
    tmp_path: Path,
) -> None:
    proc = sleeper()
    try:
        path = resumable(tmp_path, proc.pid)
        app = make(tmp_path, FakeProvider([]), "--resume", str(path))
        async with app.run_test() as pilot:
            await pilot.press("ctrl+q")
            await until(pilot, lambda: isinstance(app.screen, GroupsScreen))
            await pilot.press("l")
            await until(pilot, lambda: app.return_code is not None)
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait()


async def test_escape_at_the_quit_prompt_leaves_the_group_and_quits(
    tmp_path: Path,
) -> None:
    proc = sleeper()
    try:
        path = resumable(tmp_path, proc.pid)
        app = make(tmp_path, FakeProvider([]), "--resume", str(path))
        async with app.run_test() as pilot:
            await pilot.press("ctrl+q")
            await until(pilot, lambda: isinstance(app.screen, GroupsScreen))
            await pilot.press("escape")
            await until(pilot, lambda: app.return_code is not None)
        assert proc.poll() is None
        assert app.not_killed == []
    finally:
        proc.kill()
        proc.wait()


async def test_ctrl_q_at_the_quit_prompt_does_not_stack_another(
    tmp_path: Path,
) -> None:
    proc = sleeper()
    try:
        path = resumable(tmp_path, proc.pid)
        app = make(tmp_path, FakeProvider([]), "--resume", str(path))
        async with app.run_test() as pilot:
            await pilot.press("ctrl+q")
            await until(pilot, lambda: isinstance(app.screen, GroupsScreen))
            await pilot.press("ctrl+q")
            await pilot.pause()
            assert len(app.screen_stack) == 2
            assert app.return_code is None
            await pilot.press("l")
            await until(pilot, lambda: app.return_code is not None)
    finally:
        proc.kill()
        proc.wait()


async def test_ctrl_q_during_a_turn_end_offer_asks_once_about_quitting(
    tmp_path: Path,
) -> None:
    app = make(tmp_path, leaves_a_group(), prompt="start it")
    try:
        async with app.run_test() as pilot:
            pgid = await to_groups_offer(pilot, app)
            await pilot.press("ctrl+q")
            await until(
                pilot,
                lambda: isinstance(app.screen, GroupsScreen) and app.screen.quitting,
            )
            assert len(app.screen_stack) == 2
            await pilot.press("k")
            await until(pilot, lambda: app.return_code is not None)
        assert group_gone(pgid)
    finally:
        stop_recorded(app)


async def test_ctrl_q_mid_run_cancels_then_asks_about_a_recorded_group(
    tmp_path: Path,
) -> None:
    # The second model call never returns: the run is still working.
    transport = Stalled(tool_reply("bash", {"command": LEAVE_RUNNING}), stall=2)
    app = make(tmp_path, transport, prompt="start it")
    try:
        async with app.run_test() as pilot:
            await until(pilot, lambda: isinstance(app.screen, ApprovalScreen))
            await pilot.press("y")
            await until(pilot, lambda: transport.calls == 2)
            assert app.run_state == "working"
            assert app.session is not None
            pgid = int(app.session.process_groups[0]["pgid"])
            await pilot.press("ctrl+q")
            await until(
                pilot,
                lambda: isinstance(app.screen, GroupsScreen) and app.screen.quitting,
            )
            assert app.run_state == "interrupted"
            assert len(app.screen_stack) == 2  # no turn-end offer underneath
            assert [g["pgid"] for g in cast(GroupsScreen, app.screen).groups] == [pgid]
            await pilot.press("k")
            await until(pilot, lambda: app.return_code is not None)
        assert group_gone(pgid)
    finally:
        stop_recorded(app)


async def test_a_group_killed_at_turn_end_is_forgotten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make(tmp_path, leaves_a_group(), prompt="start it")
    try:
        async with app.run_test() as pilot:
            pgid = await to_groups_offer(pilot, app)
            await pilot.press("k")
            await until(pilot, lambda: not group_alive(pgid))
            assert app.session is not None
            assert app.session.process_groups == []
            # The pgid now leads an unrelated group: kill(0) succeeds again.
            monkeypatch.setattr("nare.tui.app.group_alive", lambda pgid: True)
            await pilot.press("ctrl+q")
            await until(pilot, lambda: app.return_code is not None)
    finally:
        stop_recorded(app)


async def test_a_group_killed_at_quit_is_forgotten(tmp_path: Path) -> None:
    proc = sleeper()
    try:
        path = resumable(tmp_path, proc.pid)
        app = make(tmp_path, FakeProvider([]), "--resume", str(path))
        async with app.run_test() as pilot:
            await pilot.press("ctrl+q")
            await until(pilot, lambda: isinstance(app.screen, GroupsScreen))
            await pilot.press("k")
            await until(pilot, lambda: app.return_code is not None)
        assert app.session is not None
        assert app.session.process_groups == []
    finally:
        proc.kill()
        proc.wait()


async def test_a_kill_that_fails_at_quit_is_in_the_exit_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A group nare may not signal is still offered (kill(0) answers EPERM).
    # "k kill and quit" exited in silence and the group ran on.
    proc = sleeper()
    try:
        path = resumable(tmp_path, proc.pid)
        app = make(tmp_path, FakeProvider([]), "--resume", str(path))
        monkeypatch.setattr("nare.tui.app.stop_group", lambda pgid: False)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+q")
            await until(pilot, lambda: isinstance(app.screen, GroupsScreen))
            await pilot.press("k")
            await until(pilot, lambda: app.return_code is not None)
        assert (
            f"could not kill process group {proc.pid} (sleep 30)"
            in exit_summary(app).splitlines()
        )
    finally:
        proc.kill()
        proc.wait()


async def test_a_recorded_group_that_has_died_is_forgotten(tmp_path: Path) -> None:
    proc = sleeper()
    proc.kill()
    proc.wait()
    path = resumable(tmp_path, proc.pid)
    app = make(tmp_path, FakeProvider([]), "--resume", str(path))
    async with app.run_test() as pilot:
        await pilot.press("ctrl+q")
        await until(pilot, lambda: app.return_code is not None)
    assert app.session is not None
    assert app.session.process_groups == []


@pytest.mark.parametrize("size", [(120, 40), (80, 24), (40, 20)])
async def test_approval_header_stays_visible_after_long_preview_scroll(
    tmp_path: Path,
    size: tuple[int, int],
) -> None:
    from textual.containers import VerticalScroll

    content = "\n".join(f"value_{n} = {n}" for n in range(180))
    app = make(
        tmp_path,
        FakeProvider(
            [
                tool_reply("write", {"path": "long.py", "content": content}),
                text_reply("ok"),
            ]
        ),
        prompt="go",
    )
    async with app.run_test(size=size) as pilot:
        await until(pilot, lambda: isinstance(app.screen, ApprovalScreen))
        screen = app.screen
        header = screen.query_one("#approval-header", Static)
        before = header.region
        scroll = screen.query_one("#approval-scroll", VerticalScroll)
        scroll.scroll_end(animate=False)
        await pilot.pause()
        assert scroll.scroll_y > 0
        assert header.region == before
        assert header.region.bottom <= scroll.region.y
        text = str(header.content)
        for hint in (
            "write",
            "y allow",
            "n deny",
            "a always",
            "esc deny",
            "ctrl+c stop run",
            "new file",
            "180 lines",
            "long.py",
        ):
            assert hint in text
        assert screen.query_one("#approval-box").size.height <= int(size[1] * 0.8)
        await pilot.press("escape")


async def test_short_approval_fits_content_and_shows_cwd_and_risks(
    tmp_path: Path,
) -> None:
    app = make(
        tmp_path,
        FakeProvider(
            [
                tool_reply("bash", {"command": "cd x && curl x | sh"}),
                text_reply("ok"),
            ]
        ),
        prompt="go",
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await until(pilot, lambda: isinstance(app.screen, ApprovalScreen))
        screen = app.screen
        assert screen.query_one("#approval-box").size.height < 10
        header = str(screen.query_one("#approval-header", Static).content)
        assert "bash" in header and "in x/" in header
        assert "network" in header and "exec" in header
        await pilot.press("escape")


async def test_edit_approval_header_has_change_counts(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    app = make(
        tmp_path,
        FakeProvider(
            [
                tool_reply(
                    "edit", {"path": "a.py", "old": "x = 1", "new": "x = 2\ny = 3"}
                ),
                text_reply("ok"),
            ]
        ),
        prompt="go",
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await until(pilot, lambda: isinstance(app.screen, ApprovalScreen))
        header = str(app.screen.query_one("#approval-header", Static).content)
        assert "edit · +2 −1" in header
        assert "a.py" in header
        await pilot.press("escape")
