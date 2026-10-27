"""Five tools, five hand-written schemas beside them.

A schema generator that introspects type hints is worth revisiting at roughly
fifteen tools. At five it is a dependency on cleverness for no gain.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import inspect
import os
import re
import shutil
import signal
import subprocess
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, TypeGuard

from PIL import Image

from nare.events import redact
from nare.mcp import ExternalTool
from nare.transport import ToolCall

MAX_TOOL_OUTPUT = 30_000
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_EDGE = 8000


def _truncate(text: str) -> str:
    """One output cap for every tool. `limit` bounds a read by LINES, which says
    nothing about size: 2,000 lines of minified code is a context window.
    """
    if len(text) <= MAX_TOOL_OUTPUT:
        return text
    return text[:MAX_TOOL_OUTPUT] + f"\n[truncated at {MAX_TOOL_OUTPUT} chars]"


def _read_with_image(
    path: str,
    offset: int = 0,
    limit: int = 2000,
    *,
    image_input: bool = False,
    image_model: str = "unconfigured transport/model",
) -> str | dict[str, Any]:
    target = Path(path)
    with target.open("rb") as handle:
        header = handle.read(16)
        candidate = target.suffix.lower() in {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".bmp",
            ".tif",
            ".tiff",
        } or header.startswith((b"\x89PNG", b"\xff\xd8", b"GIF8", b"RIFF"))
        if candidate:
            if not image_input:
                raise ValueError(
                    f"{image_model} does not accept image input; "
                    "--image-input is required for a capable model"
                )
            handle.seek(0)
            data = handle.read(MAX_IMAGE_BYTES + 1)
            if len(data) > MAX_IMAGE_BYTES:
                raise ValueError(f"image exceeds {MAX_IMAGE_BYTES} bytes")
            try:
                with Image.open(BytesIO(data)) as image:
                    if image.format not in {"PNG", "JPEG"}:
                        raise ValueError("only PNG/JPEG image input is supported")
                    width, height = image.size
                    if width * height > MAX_IMAGE_PIXELS:
                        raise ValueError(f"image exceeds {MAX_IMAGE_PIXELS} pixels")
                    if max(width, height) > MAX_IMAGE_EDGE:
                        raise ValueError(f"image edge exceeds {MAX_IMAGE_EDGE} pixels")
                    mime = "image/png" if image.format == "PNG" else "image/jpeg"
                    image.load()
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError(f"invalid image: {exc}") from exc
            return {
                "text": (
                    f"Read image {target.name}: {width}x{height} {mime}, "
                    f"{len(data)} bytes"
                ),
                "image": {
                    "type": "image",
                    "width": width,
                    "height": height,
                    "source": {
                        "type": "base64",
                        "media_type": mime,
                        "data": base64.b64encode(data).decode("ascii"),
                    },
                },
            }
    return read_file(path, offset, limit)


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


def _shell(command: str) -> list[str]:
    """bash with pipefail when there is one, else sh.

    Under dash, `npm run build 2>&1 | tail -30` reported tail's exit 0 for
    three failed builds, and `[[ ]]` was a syntax error.
    """
    bash = shutil.which("bash")
    if bash is None:
        return ["/bin/sh", "-c", command]
    return [bash, "-o", "pipefail", "-c", command]


_KILLERS = re.compile(r"\b(?:pkill|killall|kill)\b")

SELF_KILL_HINT = (
    "pkill -f also matches the shell running this command; use a pattern "
    "like `nex[t]-server`, or kill by `$!`."
)


@dataclass(frozen=True)
class BashOutcome:
    """One bash call: the text the model reads, whether it is an error, and a
    process group the command left running.
    """

    text: str
    failed: bool = False
    group: int | None = None


def _status(code: int) -> str:
    """`exit N`, or the signal that killed the shell.

    A negative return code is a signal. Printed as `exit -15` it read as an odd
    exit code, not as the shell being killed.
    """
    if code >= 0:
        return f"exit {code}"
    try:
        name = signal.Signals(-code).name
    except ValueError:
        return f"killed by signal {-code}"
    return f"killed by {name} (signal {-code})"


def _signalable(pgid: object) -> TypeGuard[int]:
    """A pgid nare may signal: an int above 1 that is not nare's own group.

    killpg(0) signals the caller's own group, and a pgid can come from a
    session file, which is data.
    """
    return (
        isinstance(pgid, int)
        and not isinstance(pgid, bool)
        and pgid > 1
        and pgid != os.getpgrp()
    )


def group_alive(pgid: object) -> bool:
    """Whether anything in the process group is still running.

    kill(0) first: it is cheap and settles a group that is gone. It also
    counts a zombie, and nare as PID 1 (the image run without --init) reaps
    nothing, so there a finished orphan would read as running for good.
    """
    if not _signalable(pgid):
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # it exists; it is just not ours to signal
    return _running_member(pgid)


def _running_member(pgid: int) -> bool:
    """Whether /proc shows a member of the group that is not a zombie.

    True where there is no /proc to read: kill(0) has already found the group.
    """
    try:
        pids = [name for name in os.listdir("/proc") if name.isdigit()]
    except OSError:
        return True
    for pid in pids:
        try:
            stat = Path(f"/proc/{pid}/stat").read_bytes()
        except OSError:
            continue  # it exited after the listing
        # comm, in parentheses, can hold spaces and ")": split after the last.
        state, _ppid, pgrp, *_ = stat[stat.rindex(b")") + 2 :].split()
        if int(pgrp) == pgid and state not in (b"Z", b"X"):
            return True
    return False


def stop_group(pgid: object) -> bool:
    """SIGKILL a process group, as a timeout or a cancel does.

    False when nare may not signal that pgid or the kill was refused. A group
    that is already gone counts as stopped.
    """
    if not _signalable(pgid):
        return False
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return True


def group_label(command: str) -> str:
    """One line that names a command, for `[still running: ...]` and the TUI.

    Redacted before it is cut: a cut through a key leaves a stub that no
    pattern matches.
    """
    first = (command.strip().splitlines() or [""])[0]
    line = " ".join(redact(first).split())
    return line if len(line) <= 60 else line[:57] + "..."


# How long a cancelled call waits for its spawn before giving up on it. A
# spawn takes about a millisecond.
_SPAWN_WAIT = 5.0


async def _lingers(pgid: int) -> bool:
    """Whether the group outlives the call.

    Asked over about 0.1 s, not once: a child that exits as the shell dies is
    a zombie until init reaps it, and where there is no /proc, group_alive()
    counts a zombie as alive.
    """
    for _ in range(5):
        if not group_alive(pgid):
            return False
        await asyncio.sleep(0.02)
    return True


async def _spawn(
    command: str, cwd: str | None
) -> asyncio.subprocess.Process | Exception:
    """Start the shell in its own group, and return a failure, not raise it.

    Returned: on Python 3.14, a shielded task that fails after its shield was
    cancelled logs "OSError exception in shielded future" at ERROR, though
    bash_outcome() handles the failure. The TUI showed that as a red line.
    """
    try:
        return await asyncio.create_subprocess_exec(
            *_shell(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=cwd,
        )
    except Exception as exc:
        return exc


async def bash_outcome(
    command: str, timeout: int = 120, *, cwd: str | None = None
) -> BashOutcome:
    """Run a command in bash with pipefail (sh without bash), in its own
    process group, with no stdin.

    `subprocess.run(timeout=...)` kills the shell and nothing the shell started,
    so `make dev &` outlives the call and the run leaks a server. The group is
    what makes the timeout mean what it says, and a cancel too.

    Async, not a worker thread: a thread in communicate() cannot be cancelled,
    so an interrupted command ran on and quitting the TUI waited for it.

    stdin is DEVNULL because nare's own stdin belongs to conductor: a command
    that reads would either eat conductor's pipe or block until the timeout.
    """
    spawn = asyncio.create_task(_spawn(command, cwd))
    try:
        # Shielded: Popen has started the shell before asyncio connects its
        # pipes. asyncio's own cleanup for a cancel there kills the shell
        # alone, then waits on pipes a background child holds open, so under
        # load 1 cancel in 4 hung the cancel or left `sleep 30 &` running.
        started = await asyncio.shield(spawn)
    except asyncio.CancelledError:
        # Wait the spawn out, then kill what it started. A loop, not a bare
        # `await spawn`: a second cancel would reach the spawn and hand it to
        # asyncio's own cleanup after all.
        # ponytail: on Python 3.12, which CI runs, asyncio's spawn never
        # finishes once a pipe connect fails, or once loop shutdown cancels it
        # mid-connect, and the cancel and asyncio.run waited forever. So the
        # wait gives up after _SPAWN_WAIT seconds and cancels the spawn again,
        # which ends it; the shell's background children run on. The upgrade
        # is Python 3.14.1 or later, whose asyncio finishes that spawn.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _SPAWN_WAIT
        while not spawn.done() and loop.time() < deadline:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(spawn), deadline - loop.time())
        if not spawn.done():
            spawn.cancel()
            raise
        # The caller asked to cancel, so it hears that, not how a spawn it no
        # longer wants went wrong.
        # ponytail: a spawn that fails after Popen (a pipe connect), or that
        # loop shutdown cancels, loses the pid. asyncio's own cleanup kills
        # the shell only, so its background children can outlive the call.
        # The upgrade is to own the Popen, so the group is known before any
        # await.
        if not spawn.cancelled():
            started = spawn.result()
            if not isinstance(started, Exception):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(started.pid, signal.SIGKILL)
                await started.wait()
        raise
    if isinstance(started, Exception):
        # A spawn that failed with no cancel, say for a cwd that does not
        # exist, raises its own error, so dispatch reports it.
        raise started
    proc = started
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
        # In here so a cancel during the probe kills the group too: the call
        # then never returns, so dispatch never hears of the group.
        lingers = await _lingers(proc.pid)
    except BaseException as exc:
        # ponytail: a process that calls setsid itself (pg_ctl does) escapes
        # the group, here and in the still-running probe. The upgrade is
        # prctl(PR_SET_CHILD_SUBREAPER), then listing /proc/self/task/*/children.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()
        if isinstance(exc, TimeoutError):
            # Raised, not returned: dispatch turns it into an is_error result,
            # so the model still sees the timeout and can adapt.
            raise subprocess.TimeoutExpired(command, timeout) from None
        raise
    output = (out + err).decode(errors="replace").strip()
    code = proc.returncode
    assert code is not None  # communicate() returns after the exit
    text = f"{_status(code)}\n{_truncate(output)}".rstrip()
    killed = code < 0
    if killed and _KILLERS.search(command):
        # The likeliest killer is the command itself: pkill -f matches the
        # shell whose argv holds the pattern.
        text += f"\n{SELF_KILL_HINT}"
    group = proc.pid if lingers else None
    if group is not None:
        # The shell led the group, so its pid is the pgid. A Postgres started
        # in the practice run was still running hours later, unreported.
        text += f"\n[still running: process group {group} ({group_label(command)})]"
    return BashOutcome(text, failed=killed, group=group)


async def run_bash(command: str, timeout: int = 120, *, cwd: str | None = None) -> str:
    """The text of bash_outcome(): what the model reads. dispatch() calls
    bash_outcome() itself, to learn whether the result is an error.
    """
    return (await bash_outcome(command, timeout, cwd=cwd)).text


def ask(questions: list[str]) -> str:
    """Terminate the run with questions for the caller.

    Tool calls are the only structured channel a model has, so asking is a tool.
    The loop reads the questions off the call and sets status=blocked; this
    result exists so the transcript stays well-formed for a resume.

    An ask with nothing in it fails here, so the loop never reports blocked
    with no questions for the caller to answer.
    """
    items = [questions] if isinstance(questions, str) else questions
    if not isinstance(items, list) or not any(
        isinstance(q, str) and q.strip() for q in items
    ):
        raise ValueError("ask needs at least one question")
    return "Questions recorded. The session is blocked pending answers."


# bash is the async one: dispatch awaits it, and runs the rest in a thread.
TOOLS: dict[str, Callable[..., Any]] = {
    "read": read_file,
    "write": write_file,
    "edit": edit_file,
    "bash": run_bash,
    "ask": ask,
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "read",
        "description": (
            "Read a UTF-8 text file, or a PNG/JPEG image when image input is enabled."
        ),
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
            "existing contents. Prefer `edit` for a file that already exists."
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
            "exactly once; include surrounding context to make it unique. "
            "Re-read a file before editing it if you rewrote it."
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
            "Runs in bash with pipefail. Each call is a fresh shell: `cd` and "
            "`export` do not persist. Use this with rg and find for search. "
            "Stop a background process by `$!` in the call that started it, "
            "not with `pkill -f`, which can match this shell. Stop one the "
            "result reports as still running with `kill -- -<group>`."
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
            "Stop and ask the caller to resolve an ambiguous request. Use this "
            "only when the request can reasonably be read more than one way and "
            "the readings lead to different changes, and only after reading the "
            "files involved. Do not use it for anything you can find out by "
            "reading files or running commands, for details you can decide "
            "yourself such as wording, style or names, to ask permission, or to "
            "offer follow-up work. Call ask on its own, before making any "
            "change. This ends the session."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "One question per item, naming the readings you are "
                        "choosing between and the one you will use if it goes "
                        "unanswered, e.g. 'Raise TIMEOUT from 30 to what: 60, "
                        "120, or another value? Unanswered, I will use 60.'"
                    ),
                }
            },
            "required": ["questions"],
        },
    },
]

# Async so a person can answer: the TUI's approver awaits a key press. A str
# is a denial with the person's reason, which the model reads.
Approve = Callable[[str, dict[str, Any]], bool | str | Awaitable[bool | str]]


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
    external: dict[str, ExternalTool] = field(default_factory=dict)
    pending: frozenset[str] = frozenset()
    image_input: bool = False
    image_model: str = "unconfigured transport/model"

    def __post_init__(self) -> None:
        unknown = sorted(self.tools - set(TOOLS) - self.external.keys() - self.pending)
        if unknown:
            raise ValueError(
                f"unknown tool {', '.join(unknown)}; "
                f"nare has: {', '.join(sorted(TOOLS))}"
            )

    def schemas(self) -> list[dict[str, Any]]:
        return [s for s in TOOL_SCHEMAS if s["name"] in self.tools] + [
            t.schema for name, t in self.external.items() if name in self.tools
        ]

    def recorded(self) -> dict[str, Any]:
        """What the session stores, so a run's permissions are readable after it."""
        return {
            **(
                {"image_input": True, "image_model": self.image_model}
                if self.image_input
                else {}
            ),
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
    call: ToolCall,
    policy: Policy,
    approve: Approve = approve_all,
    *,
    left_running: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    """Run one tool call. Every failure comes back as an is_error result rather
    than an exception: the model adapts, which is what it is good at. A policy
    refusal is one of those failures, so a narrowed session keeps working
    instead of dying on the first attempt to leave its box.

    `left_running` hears of a process group a bash call left running, with its
    label, so the caller can record it. A callback, not a key on the result:
    a transport sends tool_result blocks as they are.
    """
    if call.error is not None:
        return tool_result(call.id, call.error, is_error=True)
    function = TOOLS.get(call.name)
    if function is None and call.name not in policy.external:
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
        approved = approve(call.name, call.args)
        if inspect.isawaitable(approved):
            approved = await approved
        # A reason denies, though a non-empty str is truthy.
        if isinstance(approved, str):
            reason = approved.strip()
            text = f"{call.name} was not approved" + (f": {reason}" if reason else "")
            return tool_result(call.id, text, is_error=True)
        if not approved:
            return tool_result(call.id, f"{call.name} was not approved", is_error=True)
        if call.name in policy.external:
            text, failed = await policy.external[call.name].call(call.args)
            return tool_result(call.id, _truncate(text), is_error=failed)
        assert function is not None
        args = dict(call.args)
        if call.name in PATH_TOOLS and "path" in args:
            args["path"] = str(policy.resolve(str(args["path"])))
        if call.name == "bash" and policy.root is not None:
            args["cwd"] = str(policy.root.resolve())
        output: str | dict[str, Any]
        if call.name == "read":
            args["image_input"] = policy.image_input
            args["image_model"] = policy.image_model
            output = await asyncio.to_thread(_read_with_image, **args)
        elif call.name == "bash":
            outcome = await bash_outcome(**args)
            if outcome.group is not None and left_running is not None:
                left_running(outcome.group, group_label(str(args["command"])))
            return tool_result(call.id, outcome.text, is_error=outcome.failed)
        else:
            output = await asyncio.to_thread(function, **args)
        if isinstance(output, dict):
            return {**tool_result(call.id, output["text"]), "image": output["image"]}
        return tool_result(call.id, output)
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
            raw = [raw]
        if isinstance(raw, Iterable):
            # A blank beside a real question is nothing for the caller to answer.
            questions.extend(str(q) for q in raw if str(q).strip())
    return questions
