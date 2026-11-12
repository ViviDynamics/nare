"""Tools a caller registers: a Python callable in-process, or a command from the
command line. Both reach the model the way MCP tools do, through the policy's
external tools, so the allowlist, the approval seam, the argument checks and
the events are the same for all three.

A name has no `__`, so it can never equal an MCP tool's `alias__tool` name.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import os
import re
import signal
import subprocess
from collections.abc import Awaitable, Callable, Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nare.mcp import ExternalTool
from nare.schema import UnsupportedSchema, check_supported
from nare.tools import TOOLS, _shell_env, check_arguments

# A command tool that does not say how long it may run is stopped after this.
COMMAND_TIMEOUT_DEFAULT = 30.0

_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_COMMAND_KEYS = frozenset({"description", "input_schema", "command", "timeout"})


class ToolError(Exception):
    """A caller tool's expected failure. Its message is the error result the
    model sees. Any other exception a call raises becomes `Type: message` as an
    error result, so the run never stops on a tool's failure.
    """


@dataclass(frozen=True)
class Tool:
    """A tool the caller implements, offered to the model beside the built-ins.

    `call` receives the model's arguments, checked against `input_schema`, and
    returns the result: a str, or any other value, which is sent as JSON. It may
    be a plain function or a coroutine function. A sync call runs in a worker
    thread, so it does not block the event loop; nare cannot stop either kind,
    so a caller bounds its own function.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    call: Callable[[dict[str, Any]], Any]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or not _NAME.fullmatch(self.name)
            or "__" in self.name
        ):
            raise ValueError(
                "tool name must be 1 to 64 letters, digits, _ or -, with no __"
            )
        if self.name in TOOLS:
            raise ValueError(f"{self.name} is a built-in tool name")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError(f"tool {self.name} needs a nonempty description")
        if (
            not isinstance(self.input_schema, dict)
            or self.input_schema.get("type") != "object"
        ):
            raise ValueError(f"tool {self.name} needs an object input_schema")
        if not callable(self.call):
            raise ValueError(f"tool {self.name} needs a callable call")
        # Refused now, not at the first call: a schema nare cannot enforce would
        # otherwise fail inside the run.
        try:
            check_supported(self.input_schema)
        except UnsupportedSchema as exc:
            raise ValueError(f"tool {self.name} input_schema: {exc}") from exc


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


def as_external(tool: Tool) -> ExternalTool:
    """The policy's form of a caller tool: its schema, its call, and its
    argument check, which dispatch runs before approval.
    """

    async def call(args: dict[str, Any]) -> tuple[str, bool]:
        try:
            if inspect.iscoroutinefunction(tool.call):
                value = await tool.call(args)
            else:
                value = await asyncio.to_thread(tool.call, args)
            if inspect.isawaitable(value):
                value = await value
        except ToolError as exc:
            return str(exc), True
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}", True
        return _as_text(value), False

    def arguments(args: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        return check_arguments(tool.name, tool.input_schema, args)

    return ExternalTool(
        schema={
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        },
        call=call,
        source="caller",
        arguments=arguments,
    )


def external_tools(tools: Iterable[Tool]) -> dict[str, ExternalTool]:
    """Every caller tool in the policy's form. A name registered twice is
    refused, because the model could not tell the two apart.
    """
    external: dict[str, ExternalTool] = {}
    for tool in tools:
        if not isinstance(tool, Tool):
            raise TypeError("tools must be nare.Tool values")
        if tool.name in external:
            raise ValueError(f"tool {tool.name} is registered twice")
        external[tool.name] = as_external(tool)
    return external


def _kill_group(process: asyncio.subprocess.Process) -> None:
    # Own group, so a command's children die with it rather than keeping the
    # pipes open.
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)


async def _run_command(
    command: Sequence[str],
    payload: bytes,
    *,
    timeout: float,
    root: Path | None,
    credentials: bool,
    failure: type[Exception],
) -> tuple[int, bytes, bytes]:
    """Run a caller's argv, no shell, with `payload` on stdin, in `root` when
    there is one, in its own process group, with nare's environment minus the
    provider credentials unless `credentials`. A command that cannot start or
    runs past `timeout` raises `failure`, killed with its group first. A
    cancelled run (a quit, an interrupt, a deadline) kills the group too, so
    the command never outlives it, then passes the cancellation on.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=None if root is None else str(root.resolve()),
            env=_shell_env(credentials),
            start_new_session=True,
        )
    except OSError as exc:
        raise failure(f"cannot start {command[0]}: {exc.strerror}") from None
    try:
        out, err = await asyncio.wait_for(process.communicate(payload), timeout)
    except TimeoutError:
        _kill_group(process)
        await process.wait()
        raise failure(f"timed out after {timeout:g} seconds") from None
    except asyncio.CancelledError:
        _kill_group(process)
        # Reaped, bounded, so the cancellation is not held up by the process.
        with suppress(Exception):
            await asyncio.wait_for(process.wait(), 5)
        raise
    return process.returncode or 0, out, err


def _command_call(
    command: Sequence[str], timeout: float, root: Path | None, credentials: bool
) -> Callable[[dict[str, Any]], Awaitable[str]]:
    """The call a command tool runs: the arguments as JSON on stdin, the result
    on stdout. No shell, so the argv is exactly what the caller wrote. The
    environment is nare's minus the provider credentials, as bash's is.
    """

    async def call(args: dict[str, Any]) -> str:
        code, out, err = await _run_command(
            command,
            json.dumps(args).encode("utf-8"),
            timeout=timeout,
            root=root,
            credentials=credentials,
            failure=ToolError,
        )
        text = out.decode("utf-8", "replace")
        if code != 0:
            detail = err.decode("utf-8", "replace").strip() or text.strip()
            status = f"exit status {code}"
            raise ToolError(f"{status}: {detail}" if detail else status)
        return text

    return call


# What of a failing check command's output its error quotes.
_CHECK_DETAIL_CHARS = 2000


class CheckCommandFailed(Exception):
    """The check command did not give a verdict: it exited non-zero, timed out,
    or printed something other than a JSON list of errors. The run ends as it
    does when a Python check raises (#296)."""


def parse_check_command(text: str) -> list[str]:
    """The argv `--check-command` names: a JSON array of non-empty strings, run
    without a shell. Anything else is refused before the run starts."""
    try:
        argv = json.loads(text)
    except ValueError:
        argv = None
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(part, str) and part for part in argv)
    ):
        raise ValueError(
            "--check-command takes a JSON array of non-empty strings, the argv "
            'to run without a shell, such as \'["python3", "check.py"]\''
        )
    return argv


def _check_error_shape(n: int, item: Any) -> None:
    """Refuse an item of a check command's list that is not an error, so a
    malformed verdict ends the run with a reason, not a mangled correction."""
    if isinstance(item, str):
        return
    if not isinstance(item, dict):
        raise CheckCommandFailed(f"error {n} is not a string or an object")
    for key in ("code", "message"):
        if not isinstance(item.get(key), str):
            raise CheckCommandFailed(f"error {n} has no string {key}")
    location = item.get("location", [])
    if not isinstance(location, list) or not all(
        isinstance(part, (str, int)) and not isinstance(part, bool) for part in location
    ):
        raise CheckCommandFailed(
            f"error {n} location is not a list of keys and indexes"
        )
    if item.get("guidance") is not None and not isinstance(item["guidance"], str):
        raise CheckCommandFailed(f"error {n} guidance is not a string")


def command_check(
    command: Sequence[str], *, timeout: float, root: Path | None
) -> Callable[[Any, list[dict[str, Any]]], Awaitable[list[Any]]]:
    """A caller check the command line runs (#296), with the two-argument shape
    of a Python check.

    The command gets one JSON object on stdin, `{"answer": ..., "reads": [...]}`,
    and prints a JSON list of errors: mappings with `code` and `message`, and
    optional `location` and `guidance`, or plain strings. An empty list passes.
    It runs in `root` when there is one, in its own process group, with nare's
    environment minus the provider credentials, as a command tool does. A
    non-zero exit, a timeout or output that is not a list raises
    CheckCommandFailed; a cancellation kills the group and passes through.
    """
    argv = list(command)

    async def check(answer: Any, reads: list[dict[str, Any]]) -> list[Any]:
        code, out, err = await _run_command(
            argv,
            json.dumps({"answer": answer, "reads": reads}).encode("utf-8"),
            timeout=timeout,
            root=root,
            credentials=False,
            failure=CheckCommandFailed,
        )
        if code != 0:
            detail = err.decode("utf-8", "replace").strip()[:_CHECK_DETAIL_CHARS]
            status = f"exit status {code}"
            raise CheckCommandFailed(f"{status}: {detail}" if detail else status)
        try:
            found = json.loads(out.decode("utf-8", "replace"))
        except ValueError:
            raise CheckCommandFailed("stdout is not JSON") from None
        if not isinstance(found, list):
            raise CheckCommandFailed("stdout is not a JSON list of errors")
        for n, item in enumerate(found):
            _check_error_shape(n, item)
        return found

    return check


def command_tools(
    value: Any, *, root: Path | None = None, credentials: bool = False
) -> list[Tool]:
    """The tools a command-line file declares: an object keyed by tool name.

    Each entry has `description`, `input_schema`, `command` (an argv) and an
    optional `timeout` in seconds. Raises ValueError for anything malformed,
    before any run starts.
    """
    if not isinstance(value, dict):
        raise ValueError("command tools must be an object keyed by tool name")
    tools: list[Tool] = []
    for name, config in value.items():
        if not isinstance(config, dict) or set(config) - _COMMAND_KEYS:
            raise ValueError(f"invalid command tool configuration for {name}")
        command = config.get("command")
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) and part for part in command)
        ):
            raise ValueError(f"command for {name} must be a nonempty list of strings")
        timeout = config.get("timeout", COMMAND_TIMEOUT_DEFAULT)
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError(f"timeout for {name} must be a positive finite number")
        tools.append(
            Tool(
                name=name,
                description=config.get("description", ""),
                input_schema=config.get("input_schema", {}),
                call=_command_call(command, float(timeout), root, credentials),
            )
        )
    return tools
