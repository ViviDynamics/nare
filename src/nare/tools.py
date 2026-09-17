"""Five tools, five hand-written schemas beside them.

A schema generator that introspects type hints is worth revisiting at roughly
fifteen tools. At five it is a dependency on cleverness for no gain.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

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
