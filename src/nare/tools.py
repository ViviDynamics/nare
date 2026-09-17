"""Five tools, five hand-written schemas beside them.

A schema generator that introspects type hints is worth revisiting at roughly
fifteen tools. At five it is a dependency on cleverness for no gain.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from nare.transport import ToolCall

MAX_TOOL_OUTPUT = 30_000


def read_file(path: str, offset: int = 0, limit: int = 2000) -> str:
    lines = Path(path).read_text().splitlines()
    return "\n".join(lines[offset : offset + limit])


def write_file(path: str, content: str) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return f"wrote {len(content)} characters to {path}"


def edit_file(path: str, old: str, new: str) -> str:
    target = Path(path)
    text = target.read_text()
    count = text.count(old)
    if count == 0:
        raise ValueError(f"old_string not found in {path}")
    if count > 1:
        raise ValueError(
            f"old_string appears {count} times in {path}; include enough "
            "surrounding context to make it unique"
        )
    target.write_text(text.replace(old, new))
    return f"edited {path}"


def run_bash(command: str, timeout: int = 120) -> str:
    done = subprocess.run(
        command, shell=True, capture_output=True, text=True, timeout=timeout
    )
    output = (done.stdout + done.stderr).strip()
    if len(output) > MAX_TOOL_OUTPUT:
        output = output[:MAX_TOOL_OUTPUT] + f"\n[truncated at {MAX_TOOL_OUTPUT} chars]"
    return f"exit {done.returncode}\n{output}".rstrip()


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


def tool_result(call_id: str, text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": call_id,
        "content": text,
        "is_error": is_error,
    }


async def dispatch(call: ToolCall, approve: Approve) -> dict[str, Any]:
    """Run one tool call. Every failure comes back as an is_error result rather
    than an exception: the model adapts, which is what it is good at.
    """
    function = TOOLS.get(call.name)
    if function is None:
        return tool_result(
            call.id,
            f"unknown tool {call.name!r}; available: {', '.join(sorted(TOOLS))}",
            is_error=True,
        )
    if not approve(call.name, call.args):
        return tool_result(call.id, f"{call.name} was not approved", is_error=True)
    try:
        return tool_result(call.id, await asyncio.to_thread(function, **call.args))
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
