"""Five tools, five hand-written schemas beside them.

A schema generator that introspects type hints is worth revisiting at roughly
fifteen tools. At five it is a dependency on cleverness for no gain.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nare.transport import ToolCall

MAX_TOOL_OUTPUT = 30_000


def _truncate(text: str) -> str:
    """One output cap for every tool. `limit` bounds a read by LINES, which says
    nothing about size: 2,000 lines of minified code is a context window.
    """
    if len(text) <= MAX_TOOL_OUTPUT:
        return text
    return text[:MAX_TOOL_OUTPUT] + f"\n[truncated at {MAX_TOOL_OUTPUT} chars]"


def read_file(path: str, offset: int = 0, limit: int = 2000) -> str:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return _truncate("\n".join(lines[offset : offset + limit]))


def write_file(path: str, content: str) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} characters to {path}"


def edit_file(path: str, old: str, new: str) -> str:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        raise ValueError(f"old not found in {path}")
    if count > 1:
        raise ValueError(
            f"old appears {count} times in {path}; include enough "
            "surrounding context to make it unique"
        )
    target.write_text(text.replace(old, new), encoding="utf-8")
    return f"edited {path}"


def run_bash(command: str, timeout: int = 120, *, cwd: str | None = None) -> str:
    """Run a shell command in its own process group, with no stdin.

    `subprocess.run(timeout=...)` kills the shell and nothing the shell started,
    so `make dev &` outlives the call and the run leaks a server. The group is
    what makes the timeout mean what it says.

    stdin is DEVNULL because nare's own stdin belongs to conductor: a command
    that reads would either eat conductor's pipe or block until the timeout.
    """
    proc = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
        cwd=cwd,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # ponytail: killpg reaches the group the shell leads. A grandchild that
        # calls setsid itself escapes it; a pid-tree walk is the upgrade, and
        # not before something actually escapes.
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        # Raised, not returned: dispatch turns it into an is_error result, so
        # the model still sees the timeout and can adapt.
        raise
    output = (out + err).strip()
    return f"exit {proc.returncode}\n{_truncate(output)}".rstrip()


def ask(questions: list[str]) -> str:
    """Terminate the run with questions for the caller.

    Tool calls are the only structured channel a model has, so asking is a tool.
    The loop reads the questions off the call and sets status=blocked; this
    result exists so the transcript stays well-formed for a resume.
    """
    return "Questions recorded. The session is blocked pending answers."


TOOLS: dict[str, Callable[..., str]] = {
    "read": read_file,
    "write": write_file,
    "edit": edit_file,
    "bash": run_bash,
    "ask": ask,
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "read",
        "description": "Read a UTF-8 text file and return its contents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file."},
                "offset": {"type": "integer", "description": "First line to return."},
                "limit": {"type": "integer", "description": "How many lines."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write",
        "description": (
            "Write a file, creating parent directories and overwriting any "
            "existing contents."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file."},
                "content": {"type": "string", "description": "Full file contents."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit",
        "description": (
            "Replace an exact string in a file. The old string must appear "
            "exactly once; include surrounding context to make it unique."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file."},
                "old": {"type": "string", "description": "Exact text to replace."},
                "new": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old", "new"],
        },
    },
    {
        "name": "bash",
        "description": (
            "Run a shell command and return its exit code and combined output. "
            "Use this with rg and find for search."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command to run."},
                "timeout": {"type": "integer", "description": "Seconds before kill."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "ask",
        "description": (
            "Stop and ask the caller for missing information. Use this when the "
            "task cannot be completed without a decision only the caller can "
            "make. This ends the session."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "One question per item.",
                }
            },
            "required": ["questions"],
        },
    },
]

Approve = Callable[[str, dict[str, Any]], bool]


def approve_all(tool: str, args: dict[str, Any]) -> bool:
    """Slice 1's only approval policy. `nare run` refuses to start without
    --yes, which is what makes one implementation honest rather than lax.
    """
    return True


PATH_TOOLS = frozenset({"read", "write", "edit"})


@dataclass(frozen=True)
class Policy:
    """What a session is allowed to do: which tools, and where they may reach.

    A prompt is not a permission system, so neither is a tool description. The
    allowlist also decides which schemas the model is shown: a tool it cannot
    call is a tool it should never be offered.
    """

    tools: frozenset[str] = field(default_factory=lambda: frozenset(TOOLS))
    root: Path | None = None

    def __post_init__(self) -> None:
        unknown = sorted(self.tools - set(TOOLS))
        if unknown:
            raise ValueError(
                f"unknown tool {', '.join(unknown)}; "
                f"nare has: {', '.join(sorted(TOOLS))}"
            )

    def schemas(self) -> list[dict[str, Any]]:
        return [s for s in TOOL_SCHEMAS if s["name"] in self.tools]

    def recorded(self) -> dict[str, Any]:
        """What the session stores, so a run's permissions are readable after it."""
        return {
            "tools": sorted(self.tools),
            "root": None if self.root is None else str(self.root),
        }

    def resolve(self, path: str) -> Path:
        """Resolve a model-supplied path, refusing anything outside the root.

        `resolve()` follows symlinks and normalizes `..`, so a link planted
        inside the root during the run is caught here rather than obeyed. A
        relative path is relative to the root when there is one, which is the
        same directory bash runs in.
        """
        if self.root is None:
            return Path(path)
        root = self.root.resolve()
        candidate = Path(path)
        candidate = root / candidate if not candidate.is_absolute() else candidate
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"{path} is outside the root {root}")
        return resolved


def tool_result(call_id: str, text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": call_id,
        "content": text,
        "is_error": is_error,
    }


async def dispatch(
    call: ToolCall, policy: Policy, approve: Approve = approve_all
) -> dict[str, Any]:
    """Run one tool call. Every failure comes back as an is_error result rather
    than an exception: the model adapts, which is what it is good at. A policy
    refusal is one of those failures, so a narrowed session keeps working
    instead of dying on the first attempt to leave its box.
    """
    function = TOOLS.get(call.name)
    if function is None:
        return tool_result(
            call.id,
            f"unknown tool {call.name!r}; available: {', '.join(sorted(TOOLS))}",
            is_error=True,
        )
    if call.name not in policy.tools:
        return tool_result(
            call.id,
            f"{call.name} is not allowed in this session; "
            f"allowed: {', '.join(sorted(policy.tools)) or 'none'}",
            is_error=True,
        )
    try:
        # approve() is inside the try on purpose: a real approval seam prompts
        # a human or calls a service, so it can raise. Escaping here would
        # leave the transcript with a tool_use and no tool_result.
        if not approve(call.name, call.args):
            return tool_result(call.id, f"{call.name} was not approved", is_error=True)
        args = dict(call.args)
        if call.name in PATH_TOOLS and "path" in args:
            args["path"] = str(policy.resolve(str(args["path"])))
        if call.name == "bash" and policy.root is not None:
            args["cwd"] = str(policy.root.resolve())
        return tool_result(call.id, await asyncio.to_thread(function, **args))
    except Exception as exc:
        return tool_result(call.id, f"{type(exc).__name__}: {exc}", is_error=True)


def questions_from(calls: Iterable[ToolCall]) -> list[str]:
    """Read the questions off the `ask` calls.

    The model controls this value and does not always honour the schema, so
    a bare string becomes one question rather than a list of characters, and
    a non-iterable becomes no questions rather than an exception that would
    turn a blocked session into an errored one.
    """
    questions: list[str] = []
    for call in calls:
        if call.name != "ask":
            continue
        raw = call.args.get("questions")
        if isinstance(raw, str):
            questions.append(raw)
        elif isinstance(raw, Iterable):
            questions.extend(str(q) for q in raw)
    return questions
