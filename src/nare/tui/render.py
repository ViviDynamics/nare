"""Pure rendering: messages and sessions in, Rich renderables out.

Shared by the interactive view and the attach view. Imports Rich, never
Textual, so it is tested as a plain unit. Nothing here returns a bare str for
display: Textual parses a str as markup, and a transcript is full of brackets.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text

from nare.session import Message, Session, Usage, unanswered
from nare.tools import Policy

RESULT_LINES = 5
DIFF_LINES = 20
# A leading `cd <dir> &&`, the dir bare or quoted.
_CD = re.compile(r"""cd\s+("[^"]*"|'[^']*'|[^\s;&|]+)\s*&&\s*""")


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
    return [Block(Group(Rule(style="dim"), Text(f"> {text}", style="bold")))]


def render_messages(
    messages: list[Message], start: int, end: int, root: str | None = None
) -> list[Block]:
    """`root` is the run's --root: commands and paths are shown relative to it.
    A relative one is ignored: a saved session can be watched from anywhere.
    """
    if root is not None and not os.path.isabs(root):
        root = None
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
                markdown = _markdown(str(block.get("text", "")))
                blocks.append(Block(Group(Text(), markdown)))
            elif kind == "thinking":
                thought = Text(str(block.get("thinking", "")), style="dim")
                blocks.append(Block(thought, title="thinking"))
            elif kind == "tool_use":
                answer = answers.get(block.get("id"))
                blocks.append(Block(_call(block, answer, root)))
    return blocks


def _markdown(text: str) -> Markdown:
    """Markdown that keeps single newlines. CommonMark joins them into a
    space, so a model's line-per-item list read as one paragraph. The parser
    leaves code blocks alone: their newlines are not breaks.
    """
    markdown = Markdown(text)
    for token in markdown.parsed:
        for child in token.children or []:
            if child.type == "softbreak":
                child.type = "hardbreak"
    return markdown


def _head(text: str, keep: int = RESULT_LINES) -> str:
    lines = text.splitlines()
    if len(lines) <= keep:
        return text
    return "\n".join(lines[:keep] + [f"… {len(lines) - keep} more lines"])


def _cut(line: str) -> str:
    line = " ".join(line.split())
    return line if len(line) <= 100 else line[:99] + "..."


def _relative(path: str, root: str | None) -> str:
    if root is None or not os.path.isabs(path):
        return path
    rel = os.path.relpath(path, root)
    return path if rel == os.pardir or rel.startswith(os.pardir + os.sep) else rel


def _command(command: str, root: str | None) -> str:
    """The command without the model's `cd <root> &&`, which every call
    repeats, and one line of it: a heredoc says how long it is instead.
    """
    command = command.strip()
    cd = _CD.match(command)
    if cd and root is not None:
        target = os.path.join(root, cd.group(1).strip("'\""))
        if os.path.normpath(target) == os.path.normpath(root):
            command = command[cd.end() :]
    first, *rest = command.splitlines() or [""]
    return _cut(first) + (f" (+{len(rest)} lines)" if rest else "")


def _summary(name: str, args: dict[str, Any], root: str | None) -> str:
    if name == "bash":
        return _command(str(args.get("command", "")), root)
    if "path" in args:
        return _cut(_relative(str(args["path"]), root))
    return _cut(json.dumps(args, ensure_ascii=False))


def _failed(name: str, answer: dict[str, Any]) -> bool:
    if answer.get("is_error"):
        return True
    if name != "bash":
        return False
    first = str(answer.get("content", "")).split("\n")[0]
    code = re.fullmatch(r"exit (-?\d+)", first)
    # Negative is a signal: the shell itself was killed.
    return code is not None and int(code[1]) != 0


def _done(
    name: str, args: dict[str, Any], answer: dict[str, Any], root: str | None
) -> str:
    """A successful result, in one line where the call already says the rest."""
    path = _relative(str(args.get("path", "")), root)
    content = str(answer.get("content", ""))
    if name == "write":
        return f"wrote {path} ({len(str(args.get('content', '')).splitlines())} lines)"
    if name == "read" and "image" not in answer:
        return f"read {path} ({len(content.splitlines())} lines)"
    if name == "edit":
        return f"edited {path}"
    return _head(content)


def _call(
    block: dict[str, Any], answer: dict[str, Any] | None, root: str | None
) -> RenderableType:
    name = str(block.get("name", ""))
    args = block.get("input") or {}
    failed = answer is not None and _failed(name, answer)
    if name == "ask":
        # The questions are in the outcome panel; a success says nothing more.
        questions = args.get("questions")
        n = len(questions) if isinstance(questions, list) else 0
        header = Text(
            f"? asked {n} question{'' if n == 1 else 's'}", style="bold yellow"
        )
        if not failed:
            return header
    else:
        header = Text(f"● {name} {_summary(name, args, root)}", style="bold cyan")
    parts: list[RenderableType] = [header]
    if name == "edit":
        diff = unified(
            str(args.get("old", "")),
            str(args.get("new", "")),
            _relative(str(args.get("path", "")), root),
        )
        parts.append(Syntax(_head(diff, DIFF_LINES), "diff"))
    elif name == "write":
        parts.append(Text(_head(str(args.get("content", ""))), style="dim"))
    if answer is not None:
        if failed:
            parts.append(Text(_head(str(answer.get("content", ""))), style="red"))
        else:
            parts.append(Text(_done(name, args, answer, root), style="dim"))
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
