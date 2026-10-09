"""Pure rendering: messages and sessions in, Rich renderables out.

Shared by the interactive view and the attach view. Imports Rich, never
Textual, so it is tested as a plain unit. Nothing here returns a bare str for
display: Textual parses a str as markup, and a transcript is full of brackets.
"""

from __future__ import annotations

import difflib
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text

from nare.session import Message, Session, Usage, unanswered
from nare.tools import Policy

RESULT_LINES = 5


@dataclass(frozen=True)
class Block:
    """One transcript entry. With a title, the view folds the content under it."""

    content: RenderableType
    title: str | None = None


def settled(messages: list[Message]) -> int:
    """How many messages can be drawn. A call is drawn with its result, so an
    assistant turn whose calls are unanswered waits: mid-dispatch, and in a
    file an older `nare run --stream` saved there.
    """
    return len(messages) - 1 if unanswered(messages) else len(messages)


def render_user(text: str) -> list[Block]:
    return [Block(Text(f"> {text}", style="bold"))]


def render_messages(messages: list[Message], start: int, end: int) -> list[Block]:
    blocks: list[Block] = []
    for index in range(start, end):
        message = messages[index]
        if message.role == "user":
            # Tool results are drawn under their calls, not here.
            for block in message.content:
                if block.get("type") == "text":
                    blocks += render_user(str(block.get("text", "")))
            continue
        answers: dict[Any, dict[str, Any]] = {}
        if index + 1 < len(messages):
            answers = {
                b.get("tool_use_id"): b
                for b in messages[index + 1].content
                if b.get("type") == "tool_result"
            }
        for block in message.content:
            kind = block.get("type")
            if kind == "text":
                blocks.append(Block(Markdown(str(block.get("text", "")))))
            elif kind == "thinking":
                thought = Text(str(block.get("thinking", "")), style="dim")
                blocks.append(Block(thought, title="thinking"))
            elif kind == "tool_use":
                blocks.append(Block(_call(block, answers.get(block.get("id")))))
    return blocks


def _head(text: str) -> str:
    lines = text.splitlines()
    if len(lines) <= RESULT_LINES:
        return text
    rest = len(lines) - RESULT_LINES
    return "\n".join(lines[:RESULT_LINES] + [f"... {rest} more lines"])


def _summary(name: str, args: dict[str, Any]) -> str:
    if name == "bash":
        line = str(args.get("command", ""))
    elif "path" in args:
        line = str(args["path"])
    else:
        line = json.dumps(args, ensure_ascii=False)
    line = " ".join(line.split())
    return line if len(line) <= 100 else line[:99] + "..."


def _call(block: dict[str, Any], answer: dict[str, Any] | None) -> RenderableType:
    name = str(block.get("name", ""))
    args = block.get("input") or {}
    parts: list[RenderableType] = [
        Text(f"● {name} {_summary(name, args)}", style="bold cyan")
    ]
    if name == "edit":
        diff = unified(
            str(args.get("old", "")),
            str(args.get("new", "")),
            str(args.get("path", "")),
        )
        parts.append(Syntax(diff, "diff"))
    elif name == "write":
        parts.append(Text(_head(str(args.get("content", ""))), style="dim"))
    if answer is not None:
        style = "red" if answer.get("is_error") else "dim"
        parts.append(Text(_head(str(answer.get("content", ""))), style=style))
    return Group(*parts)


def unified(before: str, after: str, path: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(), after.splitlines(), f"a/{path}", f"b/{path}", lineterm=""
    )
    return "\n".join(lines)


def approval_text(tool: str, args: dict[str, Any], policy: Policy) -> tuple[str, str]:
    """What a person approves, and the lexer to colour it with.

    Anything that cannot be previewed honestly falls back to the raw
    arguments: dispatch() refuses or fails such a call itself, and the person
    should still be asked, not handed an exception.
    """
    raw = (json.dumps(args, indent=2, ensure_ascii=False), "json")
    if tool == "bash":
        return str(args.get("command", "")), "bash"
    if tool not in ("edit", "write"):
        return raw
    try:
        path = policy.resolve(str(args["path"]))
        if tool == "write":
            content = str(args["content"])
            if not path.exists():
                return f"new file {path}\n\n{content}", "text"
            return _rewrite(path.read_bytes().decode("utf-8"), content, path)
        # Bytes, not read_text: universal newlines would hide the CRLF to LF
        # rewrite edit_file makes. Its match is on the translated text.
        before = path.read_bytes().decode("utf-8")
        text = before.replace("\r\n", "\n").replace("\r", "\n")
        old = str(args["old"])
        if not old or text.count(old) != 1:
            return raw
        return _rewrite(before, text.replace(old, str(args["new"])), path)
    except (KeyError, ValueError, OSError):
        # ValueError covers a path outside --root and a file that is not UTF-8.
        return raw


def _rewrite(before: str, after: str, path: Path) -> tuple[str, str]:
    """The diff of a file's exact contents. splitlines() treats CRLF and LF
    alike and drops a final newline, so a change to those is named instead.
    """
    if before == after:
        return f"{path}: no change", "diff"
    diff = unified(before, after, str(path))
    if "\r" in before and "\r" not in after:
        return f"{path}: line endings change to LF\n{diff}".rstrip(), "diff"
    return diff or f"{path}: line endings or trailing newline change", "diff"


def _tokens(n: int) -> str:
    return f"{n} tok" if n < 1000 else f"{n / 1000:.1f}k tok"


def _cost(usage: Usage, limit: float | None) -> str:
    if usage.cost is None:
        return "cost unknown"
    spent = f"${usage.cost:.2f}"
    return spent if limit is None else f"{spent} / ${limit:.2f}"


def status_line(
    model: str,
    turns: int,
    max_turns: int,
    usage: Usage,
    limit: float | None,
    state: str,
    note: str | None = None,
) -> Text:
    parts = [
        model,
        f"turn {turns}/{max_turns}",
        _tokens(usage.total_tokens),
        _cost(usage, limit),
        state,
    ]
    return Text(" · ".join(parts + ([note] if note else [])))


def render_outcome(session: Session | None) -> Text:
    if session is None:
        return Text()
    if session.status == "blocked":
        lines = [f"  {n}. {q}" for n, q in enumerate(session.questions, 1)]
        return Text("\n".join(["nare is asking:", *lines]), style="bold yellow")
    if session.status == "error" and session.error:
        return Text(session.error, style="red")
    return Text()


def _ago(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s ago"
    if seconds < 3600:
        return f"{seconds // 60:.0f}m ago"
    return f"{seconds // 3600:.0f}h ago"


def _interrupted(stamp: str | None) -> str | None:
    if stamp is None:
        return None
    try:
        when = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):  # hand-edited or foreign
        return "interrupted"
    return f"interrupted {_ago(time.time() - when.timestamp())}"


def attach_status(
    session: Session | None, problem: str | None, age: float | None
) -> Text:
    """`working` in a file can mean the process died, so the age is shown."""
    parts: list[str] = []
    if session is not None:
        parts += [
            f"turn {session.turns}",
            _tokens(session.usage.total_tokens),
            _cost(session.usage, None),
            _interrupted(session.interrupted_at) or session.status,
        ]
    if age is not None:
        parts.append(f"last write {_ago(age)}")
    if problem:
        parts.append(problem)
    return Text(" · ".join(parts))
