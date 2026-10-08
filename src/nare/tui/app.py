"""The Textual app. The only module in nare that imports Textual.

In-process on purpose (spec section 3): a subprocess cannot stop mid-turn to
ask a person about a tool call. The run is a Textual worker; cancelling it is
the interrupt, and step() turns that into `interrupted by the user` results.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from pathlib import Path
from typing import Any, ClassVar

from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Collapsible, Input, Static
from textual.worker import Worker, WorkerCancelled

from nare.accounting import Prices
from nare.cli import policy_from_args, transport_from_args, validate_run_args
from nare.contract import NEVER_STARTED
from nare.events import Event
from nare.loop import INTERRUPTED, run
from nare.session import Session, Usage, loads, new_session, reopen, save
from nare.tools import Policy
from nare.transport import Transport
from nare.tui.approve import Approver
from nare.tui.render import (
    Block,
    approval_text,
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

    def reset(self) -> None:
        self.remove_children()


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
        yield Static(id="live")
        yield Static(id="outcome")
        yield Input(id="input")
        yield Static(id="status")

    def on_mount(self) -> None:
        if self.session is not None:
            self._draw()
        self._set_state(self.run_state)
        if self.prompt:
            self._submit(self.prompt)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Off when nothing runs, so Ctrl-C still copies in the input box.
        if action == "interrupt":
            return self.worker is not None and self.worker.is_running
        return True

    def action_interrupt(self) -> None:
        if self.worker is not None:
            self.worker.cancel()

    async def action_quit(self) -> None:
        # Cancel and wait while the widgets still exist, so the run's own
        # `finally` can draw and save; main() saves once more after exit.
        if self.worker is not None and self.worker.is_running:
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
            if _interrupted_bash(session):
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
            self.live.append(
                event.text, style="dim" if event.type == "thinking" else ""
            )
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
        self.query_one("#live", Static).update(self.live)
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


def prepare(args: argparse.Namespace, transport: Transport | None = None) -> TuiApp:
    """Everything that can fail at startup, before Textual takes the screen."""
    prices = validate_run_args(args)
    policy = policy_from_args(args)
    session = (
        loads(Path(args.resume).read_text(encoding="utf-8")) if args.resume else None
    )
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
    try:
        app = prepare(args, transport)
    except (ValueError, OSError) as exc:
        print(f"nare: {exc}", file=sys.stderr)
        return NEVER_STARTED
    try:
        app.run()
    finally:
        # Also after a crash in the TUI's own code: the transcript is kept.
        if app.session is not None and args.session:
            try:
                save(app.session, args.session)
            except OSError as exc:
                print(f"nare: could not write {args.session}: {exc}", file=sys.stderr)
    return 0
