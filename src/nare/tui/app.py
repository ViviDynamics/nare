"""The Textual app. The only module in nare that imports Textual.

In-process on purpose (spec section 3): a subprocess cannot stop mid-turn to
ask a person about a tool call. The run is a Textual worker; cancelling it is
the interrupt, and step() turns that into `interrupted by the user` results.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult
from textual.await_remove import AwaitRemove
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Collapsible, Input, Static
from textual.worker import Worker, WorkerCancelled

from nare.accounting import Prices
from nare.cli import policy_from_args, transport_from_args, validate_run_args
from nare.contract import NEVER_STARTED
from nare.events import Event
from nare.loop import INTERRUPTED, configure_budgets, run
from nare.session import Session, Usage, loads, new_session, reopen, save
from nare.tools import Policy
from nare.transport import Transport
from nare.tui.approve import Approver
from nare.tui.attach import Watcher
from nare.tui.render import (
    Block,
    approval_text,
    attach_status,
    render_messages,
    render_outcome,
    render_user,
    settled,
    status_line,
)

CSS = """
#transcript { height: 1fr; }
#live { height: auto; max-height: 12; color: $text-muted; }
#outcome { height: auto; }
#status { height: 1; background: $boost; }
ApprovalScreen { align: center middle; }
ApprovalScreen > VerticalScroll {
    width: 90%; height: 80%; border: thick $accent; background: $surface;
}
"""

# A run is in one of these while a person may not type.
BUSY = ("working", "awaiting approval")


class Transcript(VerticalScroll):
    def add(self, blocks: list[Block]) -> None:
        for block in blocks:
            if block.title is None:
                self.mount(Static(block.content))
            else:
                self.mount(Collapsible(Static(block.content), title=block.title))
        self.scroll_end(animate=False)

    def reset(self) -> AwaitRemove:
        return self.remove_children()


class ApprovalScreen(ModalScreen[str]):
    BINDINGS: ClassVar[list[BindingType]] = [
        ("y", "dismiss('y')", "allow"),
        ("n", "dismiss('n')", "deny"),
        ("escape", "dismiss('n')", "deny"),
        ("a", "dismiss('a')", "always"),
    ]

    def __init__(self, tool: str, text: str, lexer: str) -> None:
        super().__init__()
        self.tool = tool
        self.text = text
        self.lexer = lexer

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(
                Text(
                    f"{self.tool}   y allow · n deny · a always allow {self.tool}",
                    style="bold",
                )
            )
            yield Static(Syntax(self.text, self.lexer, word_wrap=True), id="preview")


def _interrupted_bash(session: Session) -> bool:
    if len(session.messages) < 2:
        return False
    bash = {
        b.get("id")
        for b in session.messages[-2].content
        if b.get("type") == "tool_use" and b.get("name") == "bash"
    }
    return any(
        b.get("tool_use_id") in bash and b.get("content") == INTERRUPTED
        for b in session.messages[-1].content
    )


class TuiApp(App[None]):
    CSS = CSS
    BINDINGS: ClassVar[list[BindingType]] = [
        # Priority, so Ctrl-C interrupts even with the approval modal open.
        Binding("ctrl+c", "interrupt", "interrupt", priority=True),
        Binding("escape", "interrupt", "interrupt"),
        Binding("ctrl+q", "quit", "quit", priority=True),
    ]

    def __init__(
        self,
        session: Session | None,
        *,
        transport: Transport,
        policy: Policy,
        args: argparse.Namespace,
        prices: Prices | None,
        prompt: str | None,
    ) -> None:
        super().__init__()
        self.session = session
        self.transport = transport
        self.policy = policy
        self.args = args
        self.prices = prices
        self.prompt = prompt
        self.approver = Approver(self._ask)
        self.run_state = "idle"
        if session is not None:
            # A file left `working` is a run that died: treat it as stopped.
            self.run_state = (
                "interrupted" if session.status == "working" else session.status
            )
        self.note: str | None = None
        self.drawn = 0
        self.turns_from = 0 if session is None else session.turns
        self.live = Text()
        self.worker: Worker[None] | None = None

    def compose(self) -> ComposeResult:
        yield Transcript(id="transcript")
        # Anchored to its end, so the newest streamed text stays in view.
        with VerticalScroll(id="live"):
            yield Static(id="live-text")
        yield Static(id="outcome")
        yield Input(id="input")
        yield Static(id="status")

    def on_mount(self) -> None:
        self.query_one("#live", VerticalScroll).anchor()
        if self.session is not None:
            self._draw()
        self._set_state(self.run_state)
        if self.prompt:
            self._submit(self.prompt)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Off when nothing runs, so Ctrl-C still copies in the input box.
        # A cancelled worker still unwinds (MCP, stream cleanup): a second
        # cancel would cut that short.
        if action == "interrupt":
            return (
                self.worker is not None
                and self.worker.is_running
                and not self.worker.is_cancelled
            )
        return True

    def action_interrupt(self) -> None:
        if self.worker is not None:
            self.worker.cancel()

    async def action_quit(self) -> None:
        # Cancel and wait while the widgets still exist, so the run's own
        # `finally` can draw and save; main() saves once more after exit.
        if self.worker is not None and self.worker.is_running:
            if not self.worker.is_cancelled:
                self.worker.cancel()
            with contextlib.suppress(WorkerCancelled):
                await self.worker.wait()
        self.exit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if text and self.run_state not in BUSY:
            self._submit(text)

    def _submit(self, text: str) -> None:
        if self.session is None:
            self.session = new_session(text)
        else:
            reopen(self.session, text)
        # Drawn from the text, not from the session: reopen merges it into a
        # trailing user message the transcript has already drawn.
        self.query_one(Transcript).add(render_user(text))
        self.drawn = len(self.session.messages)
        self.query_one(Input).value = ""
        self.note = None
        self.worker = self.run_worker(self._drive(self.session), exclusive=True)

    async def _drive(self, session: Session) -> None:
        self.turns_from = session.turns
        saved = session.turns
        self._set_state("working")
        try:
            async for event in run(
                session,
                transport=self.transport,
                approve=self.approver,
                policy=self.policy,
                max_turns=self.args.max_turns,
                budget_tokens=self.args.budget_tokens,
                context_window=self.args.context_window,
                budget_usd=self.args.budget_usd,
                prices=self.prices,
                mcp_servers=self.args.mcp_servers,
                mcp_allow_all=self.args.tools is None,
            ):
                self._event(event)
                if session.turns != saved:
                    saved = session.turns
                    self._save()
            self._set_state(session.status)
        except asyncio.CancelledError:
            # Only a turn this drive committed: after a follow-up, an older
            # interrupted bash is still the last call in the session.
            if session.turns != self.turns_from and _interrupted_bash(session):
                # ponytail: a bash command already in its worker thread runs
                # on until it exits or times out; asyncio.to_thread cannot stop
                # it. Running bash in its own process group and killing the
                # group on cancel is the upgrade, when it bites.
                self.note = "an interrupted bash command may still be running"
            self._set_state("interrupted")
            raise
        finally:
            self.live = Text()
            self._draw()
            self._save()

    def _event(self, event: Event) -> None:
        if event.type in ("progress", "thinking"):
            # A notice (context window, compaction, image, MCP result) carries
            # detail and gets its own line; a streamed delta does not.
            text = f"{event.text}\n" if event.detail else event.text
            self.live.append(text, style="dim" if event.type == "thinking" else "")
        self._draw()

    def _draw(self) -> None:
        if self.session is None:
            return
        end = settled(self.session.messages)
        if end > self.drawn:
            blocks = render_messages(self.session.messages, self.drawn, end)
            self.query_one(Transcript).add(blocks)
            self.drawn = end
            self.live = Text()
        self.query_one("#live-text", Static).update(self.live)
        self.query_one("#outcome", Static).update(render_outcome(self.session))
        self._status()

    def _set_state(self, state: str) -> None:
        self.run_state = state
        box = self.query_one(Input)
        box.disabled = state in BUSY
        box.placeholder = (
            "answer the questions above"
            if state == "blocked"
            else "what should nare do?"
        )
        if not box.disabled:
            box.focus()
        self._status()

    def _status(self) -> None:
        line = status_line(
            self.args.model,
            0 if self.session is None else self.session.turns - self.turns_from,
            self.args.max_turns,
            Usage() if self.session is None else self.session.usage,
            None if self.session is None else self.session.budget.get("usd"),
            self.run_state,
            self.note,
        )
        self.query_one("#status", Static).update(line)

    def _save(self) -> None:
        if self.session is None or not self.args.session:
            return
        try:
            save(self.session, self.args.session)
        except OSError as exc:
            # Not fatal: a person is watching, and main() tries once more.
            self.note = f"could not write {self.args.session}: {exc}"
        self._status()

    async def _ask(self, tool: str, args: dict[str, Any]) -> str:
        text, lexer = approval_text(tool, args, self.policy)
        screen = ApprovalScreen(tool, text, lexer)
        answer: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        def done(key: str | None) -> None:
            if not answer.done():
                answer.set_result(key or "n")

        self._set_state("awaiting approval")
        self.push_screen(screen, done)
        try:
            return await answer
        finally:
            # Also on cancel: an interrupt closes a pending prompt.
            if self.screen is screen:
                self.pop_screen()
            self._set_state("working")


class AttachApp(App[None]):
    """Read-only: no input, no approvals, no provider, no API key."""

    CSS = CSS
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+q", "quit", "quit", priority=True),
    ]

    def __init__(self, path: Path, interval: float = 1.0) -> None:
        super().__init__()
        self.watcher = Watcher(path)
        self.interval = interval
        self.drawn = 0
        self.timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Transcript(id="transcript")
        yield Static(id="outcome")
        yield Static(id="status")

    async def on_mount(self) -> None:
        await self.poll()
        # Polling continues after done or blocked: Conductor resumes the same
        # path for its next round.
        self.timer = self.set_interval(self.interval, self.poll)

    async def poll(self) -> None:
        update = self.watcher.poll()
        if update is not None:
            transcript = self.query_one(Transcript)
            if update.redraw:
                # Awaited: a mount right after a sync remove_children() can
                # race its own removal (still in the DOM, just marked to
                # prune), so the old content and the new would briefly both
                # show. Awaiting it lets the prune finish first.
                await transcript.reset()
                self.drawn = 0
            end = settled(update.session.messages)
            transcript.add(render_messages(update.session.messages, self.drawn, end))
            self.drawn = end
            self.query_one("#outcome", Static).update(render_outcome(update.session))
        if self.watcher.fatal and self.timer is not None:
            self.timer.stop()
        mtime = self.watcher.mtime
        age = None if mtime is None else time.time() - mtime
        status = attach_status(self.watcher.session, self.watcher.problem, age)
        self.query_one("#status", Static).update(status)


class _Notices(logging.Handler):
    def __init__(self, app: App[None]) -> None:
        super().__init__()
        self.app = app

    def emit(self, record: logging.LogRecord) -> None:
        # notify() is thread-safe, so a record from asyncio.to_thread is fine.
        self.app.notify(
            self.format(record),
            severity="error" if record.levelno >= logging.ERROR else "warning",
            markup=False,
        )


@contextlib.contextmanager
def logs_as_notices(app: App[None]) -> Iterator[None]:
    """Textual draws on the real stderr, and a handler already holding that
    stream (nare run's basicConfig) would paint over the screen. Records
    become notifications instead, and the old handlers come back after.
    """
    root = logging.getLogger()
    saved, root.handlers = root.handlers, [_Notices(app)]
    try:
        yield
    finally:
        root.handlers = saved


def prepare(args: argparse.Namespace, transport: Transport | None = None) -> TuiApp:
    """Everything that can fail at startup, before Textual takes the screen."""
    prices = validate_run_args(args)
    policy = policy_from_args(args)
    session = None
    if args.resume:
        session = loads(Path(args.resume).read_text(encoding="utf-8"))
        # As in nare run: a bad saved budget exits 2, not in the worker.
        configure_budgets(session, args.budget_tokens, args.budget_usd)
    if transport is None:
        args.stream = True  # a person watches the turn arrive
        transport = transport_from_args(args)
    return TuiApp(
        session,
        transport=transport,
        policy=policy,
        args=args,
        prices=prices,
        prompt=args.prompt,
    )


def main(args: argparse.Namespace, transport: Transport | None = None) -> int:
    if args.attach:
        if args.prompt:
            print("nare: --attach watches a run; it takes no prompt", file=sys.stderr)
            return NEVER_STARTED
        # A missing or malformed file is a state the view shows, never a
        # startup failure.
        attach_app = AttachApp(Path(args.attach))
        with logs_as_notices(attach_app):
            attach_app.run()
        return attach_app.return_code or 0
    try:
        app = prepare(args, transport)
    except (ValueError, OSError) as exc:
        print(f"nare: {exc}", file=sys.stderr)
        return NEVER_STARTED
    try:
        with logs_as_notices(app):
            app.run()
    finally:
        # Also after a crash in the TUI's own code: the transcript is kept.
        if app.session is not None and args.session:
            try:
                save(app.session, args.session)
            except OSError as exc:
                print(f"nare: could not write {args.session}: {exc}", file=sys.stderr)
    # Textual reports a crash in its own code as return_code 1, not a raise.
    return app.return_code or 0
