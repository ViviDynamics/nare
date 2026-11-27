"""A person at a plain terminal: approval prompts and readable progress.

What `nare run` at a terminal, `nare -p` and the line-mode session share. The
policy is the TUI's (`nare.tui.approve.Approver`): reads never ask, a
read-only command never asks, and `a` approves a tool, or a program, for as
long as this process runs. No Textual and no Rich here, so it works without
the tui extra.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import sys
from typing import Any

from nare.events import Event, redact
from nare.tools import Policy
from nare.tui.approve import Approver

PREVIEW_LINES = 20
ALLOW = "allow? y yes, n no (n <why> tells the model why), a always: "


# Bytes read from stdin past the line read_line returned.
_pending = bytearray()


async def read_line(prompt: str) -> str | None:
    """One line typed at stdin, or None at the end of input. The prompt goes
    to stderr, which keeps stdout for answers.

    It waits on the event loop rather than in a thread, so a Ctrl-C while
    waiting interrupts cleanly instead of leaving a thread blocked in a read,
    and it reads the descriptor itself, so two lines typed at once are two
    answers.
    """
    sys.stderr.write(prompt)
    sys.stderr.flush()
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, ValueError, OSError):
        line = sys.stdin.readline()
        return line if line else None
    loop = asyncio.get_running_loop()
    while b"\n" not in _pending:
        ready: asyncio.Future[None] = loop.create_future()
        try:
            loop.add_reader(fd, _wake, ready)
        except (NotImplementedError, ValueError, OSError):
            ready.set_result(None)  # no reader on this loop: block instead
        try:
            await ready
        finally:
            loop.remove_reader(fd)
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        _pending.extend(chunk)
    if not _pending:
        return None
    end = _pending.find(b"\n")
    end = len(_pending) if end < 0 else end + 1
    line = bytes(_pending[:end])
    del _pending[:end]
    return line.decode("utf-8", errors="replace")


def _wake(ready: asyncio.Future[None]) -> None:
    if not ready.done():
        ready.set_result(None)


def preview(tool: str, args: dict[str, Any], policy: Policy) -> str:
    """What the person approves, as plain text: the command, the diff of an
    edit, the new content of a write, or the call's arguments."""
    if tool == "bash":
        return f"$ {args.get('command', '')}"
    path = str(args.get("path", ""))
    if tool == "edit":
        diff = difflib.unified_diff(
            str(args.get("old", "")).splitlines(),
            str(args.get("new", "")).splitlines(),
            f"a/{path}",
            f"b/{path}",
            lineterm="",
        )
        return _cut("\n".join(diff))
    if tool == "write":
        content = str(args.get("content", ""))
        try:
            exists = policy.resolve(path).exists()
        except (OSError, ValueError):
            exists = False
        head = f"{'overwrite' if exists else 'new file'} {path}"
        return _cut(f"{head}\n{content}")
    return _cut(json.dumps(args, indent=2, ensure_ascii=False))


def _cut(text: str) -> str:
    lines = text.splitlines()
    if len(lines) > PREVIEW_LINES:
        more = len(lines) - PREVIEW_LINES
        lines = lines[:PREVIEW_LINES] + [f"... {more} more lines"]
    return "\n".join(lines)


def terminal_approver(policy: Policy) -> Approver:
    """Asks the person at stdin before each call the TUI would ask about."""

    async def ask(tool: str, args: dict[str, Any]) -> tuple[str, str]:
        # Redacted, as an event is: a terminal's scrollback outlives the run.
        shown = redact(preview(tool, args, policy))
        indented = "\n".join(f"  {line}" for line in shown.splitlines())
        sys.stderr.write(f"\nnare wants to use {tool}:\n{indented}\n")
        while True:
            line = await read_line(ALLOW)
            if line is None:
                # The end of input answers no: nobody is there to say yes.
                return "n", ""
            typed = line.strip()
            key = typed[:1].lower()
            if key in ("y", "a"):
                return key, ""
            if key == "n":
                return "n", typed[1:].strip()

    return Approver(ask, policy)


DENIED = (
    "this is a non-interactive run, and nobody approved this call. Do the "
    "task without it, or say what it needs"
)


def deny_approver(policy: Policy) -> Approver:
    """nare -p and nare exec without a permission flag: what would be asked is
    denied, reported on stderr, and the run goes on, as `claude -p` does."""

    async def ask(tool: str, args: dict[str, Any]) -> tuple[str, str]:
        what = redact(str(args.get("command") or args.get("path") or ""))
        print(
            f"nare: denied {tool} {what}".rstrip()
            + " (pass --yes or --dangerously-skip-permissions to allow it)",
            file=sys.stderr,
            flush=True,
        )
        return "n", DENIED

    return Approver(ask, policy)


def progress(event: Event) -> None:
    """One readable line on stderr for what a person follows: each tool call
    and each error. The rest of the stream stays in --output-format
    stream-json."""
    line = progress_line(event)
    if line is not None:
        print(line, file=sys.stderr, flush=True)


def progress_line(event: Event) -> str | None:
    if event.type == "tool_use":
        detail = event.detail
        what = str(detail.get("command") or detail.get("path") or "")
        first = what.splitlines()[0] if what else ""
        if len(first) > 100:
            first = first[:99] + "…"
        return f"[{event.text}] {first}".rstrip()
    if event.type == "error":
        return f"nare: {event.text}"
    return None
