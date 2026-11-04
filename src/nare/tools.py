"""Seven tools, seven hand-written schemas beside them.

A schema generator that introspects type hints is worth revisiting at roughly
fifteen tools. At seven it is a dependency on cleverness for no gain.
"""

from __future__ import annotations

import asyncio
import base64
import bisect
import contextlib
import difflib
import fnmatch
import hashlib
import inspect
import json
import os
import re
import shutil
import signal
import subprocess
from collections.abc import Awaitable, Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, TypeGuard

from PIL import Image

from nare.events import redact
from nare.mcp import ExternalTool
from nare.schema import validate
from nare.transport import CREDENTIAL_ENV, ToolCall

MAX_TOOL_OUTPUT = 30_000
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_EDGE = 8000
# list and search: entries per page by default and at most, how much of a
# matched line a search result shows, and how much of a file decides binary.
LIST_LIMIT = 200
SEARCH_LIMIT = 100
MAX_PAGE = 1000
MATCH_WIDTH = 200
BINARY_SNIFF = 8192


# What a cut bash stream says about seeing the rest of it.
_BASH_HINT = (
    "; to see all of it, redirect the output to a file and read that with "
    "offset and limit"
)


def _cut_middle(text: str, room: int, what: str, hint: str = "") -> str:
    """At most `room` characters of `text`: its head and its tail, with a
    marker between them saying how much was cut, all within `room`.

    Test runners, compilers and build tools print their verdict last, so a
    head-only cut showed a long run's passes and never its failure.
    """
    if len(text) <= room:
        return text
    cut = len(text) - room
    while True:
        marker = f"\n[... {cut:,} characters of {what} cut here{hint} ...]\n"
        keep = max(room - len(marker), 0)
        if len(text) - keep == cut:
            break
        cut = len(text) - keep
    head = keep // 2
    return text[:head] + marker + text[len(text) - (keep - head) :]


def _cap_streams(out: str, err: str, cap: int = MAX_TOOL_OUTPUT) -> str:
    """stdout then stderr, within `cap` together, each cut in its middle.

    Capped apart, so a flood of stdout cannot push stderr out: a stream that
    fits in half the cap keeps all of it, and the other gets the rest.
    """
    if len(out) + len(err) <= cap:
        return out + err
    half = cap // 2
    if len(err) <= half:
        err_room = len(err)
    elif len(out) <= half:
        err_room = cap - len(out)
    else:
        err_room = half
    return _cut_middle(out, cap - err_room, "stdout", _BASH_HINT) + _cut_middle(
        err, err_room, "stderr", _BASH_HINT
    )


# Evidence: what one read, list or search returned, as a record that holds no
# content, so it is safe to keep unredacted. dispatch() hands it to the caller
# beside the result, never on it: a transport sends tool_result blocks as they
# are.
Evidence = dict[str, Any]


def _digest(data: bytes) -> Evidence:
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def _read_with_image(
    path: str,
    offset: int = 0,
    limit: int = 2000,
    *,
    image_input: bool = False,
    image_model: str = "unconfigured transport/model",
    base: str | None = None,
) -> tuple[str | dict[str, Any], Evidence]:
    """A read, and its evidence. The file is read once, and the digest is of
    the same bytes that are decoded, so it names the version the model saw.
    """
    target = Path(path)
    shown = _shown(Path(os.path.abspath(path)), base)
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
            whole = {"start": 0, "end": len(data)}
            evidence = {"path": shown, **_digest(data), "lines": None, "bytes": whole}
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
            }, {**evidence, "truncated": False}
        handle.seek(0)
        data = handle.read()
    text, ranges = _read_lines(data, offset, limit)
    return text, {"path": shown, **_digest(data), **ranges}


def _read_lines(data: bytes, offset: int, limit: int) -> tuple[str, Evidence]:
    """Lines offset..offset+limit of a UTF-8 file, under the output cap, and
    the range of the file they came from.

    Lines are 1-based and inclusive; bytes are offsets into the file, end
    exclusive, and cover each whole line returned with its line ending. A read
    the cap cuts ends at the last character returned, mid-line or not. A read
    that returns nothing has no range.

    The cut is head-first, since paging covers the rest: `limit` bounds a read
    by LINES, which says nothing about size, and 2,000 lines of minified code
    is a context window. A read that stops before the end of the file says
    which lines it returned and where to continue, after the returned text, so
    the evidence still describes exactly the file's bytes.
    """
    lines = data.decode("utf-8").splitlines(keepends=True)
    # range() slices exactly as the list does, negative offsets included.
    chosen = range(len(lines))[offset : offset + limit]
    bodies = [(lines[i].splitlines() or [""])[0] for i in chosen]
    joined = "\n".join(bodies)
    if not chosen:
        return joined, {"lines": None, "bytes": None, "truncated": False}
    start = len("".join(lines[: chosen[0]]).encode())
    truncated = len(joined) > MAX_TOOL_OUTPUT
    partial = False
    if not truncated:
        text = joined
        end = start + len("".join(lines[i] for i in chosen).encode())
        last = chosen[-1]
    else:
        text = joined[:MAX_TOOL_OUTPUT]
        # Walk the returned characters back to the lines and bytes they came
        # from: each line before the cut whole, with its ending.
        end, last, room = start, chosen[0], MAX_TOOL_OUTPUT
        for i, body in zip(chosen, bodies, strict=True):
            if room <= 0:
                break
            last = i
            if len(body) >= room:
                end += len(body[:room].encode())
                partial = len(body) > room
                break
            end += len(lines[i].encode())
            room -= len(body) + 1
    text += _read_notice(chosen[0] + 1, last + 1, len(lines), truncated, partial)
    return text, {
        "lines": {"first": chosen[0] + 1, "last": last + 1},
        "bytes": {"start": start, "end": end},
        "truncated": truncated,
    }


def _read_notice(
    first: int, last: int, total: int, truncated: bool, partial: bool
) -> str:
    """Where a read stopped, when it stopped before the end of the file.

    Without it the model could not tell a short file from a partial read.
    """
    if not truncated and last >= total:
        return ""
    where = f"lines {first} to {last} of {total}"
    if last < total:
        where += f"; continue with offset={last}"
    if not truncated:
        return f"\n[{where}]"
    inside = f", partway through line {last}" if partial else ""
    return f"\n[truncated at {MAX_TOOL_OUTPUT} chars{inside}: {where}]"


def read_file(path: str, offset: int = 0, limit: int = 2000) -> str:
    return _read_lines(Path(path).read_bytes(), offset, limit)[0]


def write_file(path: str, content: str) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} characters to {path}"


def edit_file(path: str, old: str, new: str) -> str:
    """Replace the one exact occurrence of `old`, or fail saying why not.

    Exact text that fails cleanly is the design: a fuzzy or line-numbered
    edit corrupts files silently. So the help goes in the error, which says
    whether `old` matches with whitespace ignored, where it nearly matches,
    or on which lines it appears more than once. The edit still fails.
    """
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if not old:
        raise ValueError("old is empty; to replace a whole file, use write")
    count = text.count(old)
    if count == 0:
        raise ValueError(_not_found(path, text, old))
    if count > 1:
        raise ValueError(
            f"old appears {count} times in {path}, at lines "
            f"{_listed(_occurrences(text, old))}; include enough "
            "surrounding context to make it unique"
        )
    target.write_text(text.replace(old, new), encoding="utf-8")
    return f"edited {path}"


# How much of the file an edit error shows: lines, line numbers listed, and
# characters of a line. Enough to correct the call, never the whole file.
EDIT_CONTEXT_LINES = 10
EDIT_LISTED_LINES = 20
# How alike a line must be to old's first line to be offered as the closest.
_CLOSE_ENOUGH = 0.6


def _occurrences(text: str, old: str) -> list[int]:
    """The line each non-overlapping occurrence starts on, as count() counts."""
    lines, at = [], text.find(old)
    while at != -1:
        lines.append(text.count("\n", 0, at) + 1)
        at = text.find(old, at + len(old))
    return lines


def _listed(numbers: list[int]) -> str:
    """1 and 7; 1, 4 and 9; the first EDIT_LISTED_LINES, then how many more."""
    shown = [str(n) for n in numbers[:EDIT_LISTED_LINES]]
    if len(numbers) > EDIT_LISTED_LINES:
        return f"{', '.join(shown)}, and {len(numbers) - len(shown)} more"
    return shown[0] if len(shown) == 1 else f"{', '.join(shown[:-1])} and {shown[-1]}"


def _squeezed(line: str) -> str:
    return "".join(line.split())


def _excerpt(lines: list[str], first: int, last: int) -> str:
    """Lines first..last (1-based, inclusive), numbered, bounded."""
    shown = []
    for number in range(first, min(last, first + EDIT_CONTEXT_LINES - 1) + 1):
        line = lines[number - 1]
        if len(line) > MATCH_WIDTH:
            line = line[:MATCH_WIDTH] + "..."
        shown.append(f"{number:>5}: {line}")
    more = last - first + 1 - len(shown)
    if more > 0:
        shown.append(f"  ... {more} more lines")
    return "\n".join(shown)


def _whitespace_match(lines: list[str], old: str) -> tuple[int, int] | None:
    """The first lines `old` matches with all whitespace ignored, 1-based.

    `old` may start and end partway through a line, so its first line need
    only end its line, its last only start one, and a single line need only
    appear in one.
    """
    wanted = [_squeezed(line) for line in old.splitlines()]
    while wanted and not wanted[0]:
        wanted.pop(0)
    while wanted and not wanted[-1]:
        wanted.pop()
    if not wanted:
        return None
    squeezed = [_squeezed(line) for line in lines]
    span = len(wanted)
    for start in range(len(lines) - span + 1):
        window = squeezed[start : start + span]
        if span == 1:
            found = wanted[0] in window[0]
        else:
            found = (
                window[0].endswith(wanted[0])
                and window[-1].startswith(wanted[-1])
                and window[1:-1] == wanted[1:-1]
            )
        if found:
            return start + 1, start + span
    return None


def _closest(lines: list[str], old: str) -> tuple[int, int] | None:
    """The lines most like `old`, anchored on its first non-blank line."""
    anchor = next((line.strip() for line in old.splitlines() if line.strip()), "")
    if not anchor:
        return None
    anchor = anchor[: MATCH_WIDTH * 2]
    matcher = difflib.SequenceMatcher(autojunk=False)
    matcher.set_seq2(anchor)
    best, best_ratio = None, _CLOSE_ENOUGH
    for number, line in enumerate(lines, 1):
        matcher.set_seq1(line.strip()[: MATCH_WIDTH * 2])
        if matcher.real_quick_ratio() < best_ratio:
            continue
        if matcher.quick_ratio() < best_ratio:
            continue
        ratio = matcher.ratio()
        if ratio > best_ratio or (best is None and ratio >= best_ratio):
            best, best_ratio = number, ratio
    if best is None:
        return None
    span = len(old.strip("\n").splitlines()) or 1
    return best, min(best + span - 1, len(lines))


def _not_found(path: str, text: str, old: str) -> str:
    lines = text.splitlines()
    near = _whitespace_match(lines, old)
    if near is not None:
        first, last = near
        return (
            f"old not found in {path}, but it matches lines {first} to {last} "
            "when whitespace is ignored: the indentation or spacing differs. "
            f"The file has:\n{_excerpt(lines, first, last)}"
        )
    near = _closest(lines, old)
    if near is not None:
        first, last = near
        return (
            f"old not found in {path}. The closest lines are {first} to {last}; "
            "the file may have changed since you read it:\n"
            f"{_excerpt(lines, first, last)}"
        )
    return (
        f"old not found in {path}, and no line resembles its first line; "
        "read the file again to find the text"
    )


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


def _shell_env(credentials: bool) -> dict[str, str] | None:
    """nare's environment without the provider credentials, or all of it when
    the policy opts in (None inherits).

    Read at each spawn, not at import, so the shell sees nare's environment as
    it is now.
    """
    if credentials:
        return None
    return {k: v for k, v in os.environ.items() if k not in CREDENTIAL_ENV}


async def _spawn(
    command: str, cwd: str | None, *, credentials: bool = False
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
            env=_shell_env(credentials),
        )
    except Exception as exc:
        return exc


async def bash_outcome(
    command: str,
    timeout: int = 120,
    *,
    cwd: str | None = None,
    credentials: bool = False,
) -> BashOutcome:
    """Run a command in bash with pipefail (sh without bash), in its own
    process group, with no stdin.

    `subprocess.run(timeout=...)` kills the shell and nothing the shell started,
    so `make dev &` outlives the call and the run leaks a server. The group is
    what makes the timeout mean what it says, and a cancel too.

    Async, not a worker thread: a thread in communicate() cannot be cancelled,
    so an interrupted command ran on and quitting the TUI waited for it.

    stdin is DEVNULL because nare's own stdin belongs to its caller: a command
    that reads would either eat the caller's pipe or block until the timeout.

    The environment is nare's without CREDENTIAL_ENV, the keys nare reads for
    its own requests: `env` would put them in the transcript, and a file the
    model reads can ask for exactly that. `credentials` restores them.
    """
    spawn = asyncio.create_task(_spawn(command, cwd, credentials=credentials))
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
    output = _cap_streams(
        out.decode(errors="replace"), err.decode(errors="replace")
    ).strip()
    code = proc.returncode
    assert code is not None  # communicate() returns after the exit
    text = f"{_status(code)}\n{output}".rstrip()
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


async def run_bash(
    command: str,
    timeout: int = 120,
    *,
    cwd: str | None = None,
    credentials: bool = False,
) -> str:
    """The text of bash_outcome(): what the model reads. dispatch() calls
    bash_outcome() itself, to learn whether the result is an error.
    """
    return (await bash_outcome(command, timeout, cwd=cwd, credentials=credentials)).text


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


# Navigation: list and search. Pure Python, so a session without bash can still
# find its way around, and confined by never following a symlink while walking:
# Policy.resolve() vets the one path the model names, and nothing below it may
# lead back out of the root. A result is a list of records rendered at the
# end, so a record can carry more than its line without reshaping the page.
NAVIGATION_TOOLS = frozenset({"list", "search"})


def _clamp(limit: object, default: int) -> int:
    """A page size the model asked for, held to 1..MAX_PAGE."""
    wanted = default
    if isinstance(limit, int | float | str):
        with contextlib.suppress(ValueError, OverflowError):
            wanted = int(limit)
    return min(max(wanted, 1), MAX_PAGE)


def _printable(text: str) -> str:
    """One line per record: a name with a newline, a control character or an
    undecodable byte is shown JSON-quoted rather than breaking the page.
    """
    return text if text.isprintable() else json.dumps(text)


def _shown(path: Path, base: str | None) -> str:
    """A path as results show it: relative to base (the root, which `read`
    accepts) when under it, else absolute.
    """
    if base is not None and path.is_relative_to(base):
        return _printable(path.relative_to(base).as_posix())
    return _printable(str(path))


def _is_binary(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            return b"\0" in handle.read(BINARY_SNIFF)
    except OSError:
        return False


def _call_id(*parts: object) -> str:
    """Which call a cursor belongs to, so one is never applied to another."""
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]


def _cursor(call: str, at: object) -> str:
    raw = json.dumps({"call": call, "at": at}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode("ascii").rstrip("=")


def _position(cursor: str | None, call: str) -> Any:
    """Where a cursor resumes: after a position, not at an index, so a page
    stays consistent when the tree changes between calls.
    """
    if cursor is None:
        return None
    try:
        padded = str(cursor) + "=" * (-len(str(cursor)) % 4)
        raw = json.loads(base64.urlsafe_b64decode(padded))
        owner, at = raw["call"], raw["at"]
    except (ValueError, KeyError, TypeError):
        raise ValueError(
            "invalid cursor; call again without one to start over"
        ) from None
    if owner != call:
        raise ValueError(
            "the cursor belongs to a different call; pass the same path, glob "
            "and pattern as the call that returned it"
        )
    return at


def _footer(cursor: str) -> str:
    return f'[next page: cursor "{cursor}"]'


def _paginate[T](
    records: Iterable[T],
    line: Callable[[T], str],
    at: Callable[[T], object],
    call: str,
    limit: int,
    room: int,
) -> tuple[list[tuple[T, str]], str | None]:
    """Take records until the limit, or until the next line and its cursor
    would pass MAX_TOOL_OUTPUT, so a page is never cut by the cap and
    never loses its cursor. `room` is what the header needs.

    The cursor is built only when there is a record after the page: a page
    that ends exactly at the end of the results has none.
    """
    taken: list[tuple[T, str]] = []
    size = room
    for record in records:
        if len(taken) == limit:
            return taken, _cursor(call, at(taken[-1][0]))
        text = line(record)
        fits = size + len(text) + 1 + len(_footer(_cursor(call, at(record))))
        if taken and fits > MAX_TOOL_OUTPUT:
            return taken, _cursor(call, at(taken[-1][0]))
        taken.append((record, text))
        size += len(text) + 1
    return taken, None


def _render(header: str, lines: Iterable[str], cursor: str | None) -> str:
    return "\n".join([header, *lines, *([_footer(cursor)] if cursor else [])])


@dataclass(frozen=True)
class Entry:
    """One directory entry as `list` reports it."""

    name: str
    kind: str  # dir, file, symlink or other
    size: int | None = None
    binary: bool = False

    def line(self) -> str:
        name = _printable(self.name)
        if self.kind == "dir":
            return f"{name}/  (dir)"
        if self.kind == "file":
            binary = ", binary" if self.binary else ""
            return f"{name}  (file, {self.size} bytes{binary})"
        return f"{name}  ({self.kind})"


def _entry(item: os.DirEntry[str]) -> Entry:
    try:
        if item.is_symlink():
            return Entry(item.name, "symlink")
        if item.is_dir(follow_symlinks=False):
            return Entry(item.name, "dir")
        if item.is_file(follow_symlinks=False):
            size = item.stat(follow_symlinks=False).st_size
            return Entry(item.name, "file", size, size > 0 and _is_binary(item.path))
    except OSError:
        pass  # gone or unreadable since the scan: say what is known
    return Entry(item.name, "other")


def list_dir(
    path: str = ".",
    cursor: str | None = None,
    limit: int = LIST_LIMIT,
    *,
    base: str | None = None,
) -> str:
    """One directory's entries, sorted by name, a page at a time.

    Every page sorts the whole directory, so a page is a function of the tree
    and the cursor alone. A file's binary marker reads its first bytes, which
    only the entries on the page pay for.
    """
    return _list_page(path, cursor, limit, base=base)[0]


def _list_page(
    path: str = ".",
    cursor: str | None = None,
    limit: int = LIST_LIMIT,
    *,
    base: str | None = None,
) -> tuple[str, Evidence]:
    """list_dir(), and its evidence: the directory and the entries returned.
    It opens no file but to sniff for binary, so it records no digest.
    """
    directory = Path(os.path.abspath(path))
    shown = _shown(directory, base)
    call = _call_id("list", str(directory))
    after = _position(cursor, call)
    if after is not None and not isinstance(after, str):
        raise ValueError("invalid cursor; call again without one to start over")
    with os.scandir(directory) as scan:
        items = sorted(scan, key=lambda item: item.name)
    evidence: Evidence = {"path": shown, "entries": [], "more": False}
    if not items:
        return f"{shown}: empty directory", evidence
    total = len(items)
    start = 0 if after is None else bisect.bisect_right([i.name for i in items], after)
    room = len(f"{shown}: entries {total}-{total} of {total}") + 1
    taken, more = _paginate(
        (_entry(item) for item in items[start:]),
        Entry.line,
        lambda entry: entry.name,
        call,
        _clamp(limit, LIST_LIMIT),
        room,
    )
    if not taken:
        return f"{shown}: no entries after the cursor ({total} in all)", evidence
    header = f"{shown}: entries {start + 1}-{start + len(taken)} of {total}"
    evidence["entries"] = [asdict(entry) for entry, _ in taken]
    evidence["more"] = more is not None
    return _render(header, (text for _, text in taken), more), evidence


SKIPPED_DIRS = frozenset({".git"})


def _glob_matcher(glob: str | None) -> Callable[[str], bool]:
    """Whether a path below the searched directory matches the glob.

    Without a `/` the glob matches a file name at any depth, as `rg -g` does.
    With one it matches the whole relative path, where `**` spans any number
    of directories and `*` stays within one.
    """
    if not glob:
        return lambda rel: True
    glob = glob.removeprefix("./").lstrip("/")
    if "/" not in glob:
        return lambda rel: fnmatch.fnmatchcase(rel.rsplit("/", 1)[-1], glob)
    out: list[str] = []
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        elif glob[i] == "[" and (close := glob.find("]", i + 2)) != -1:
            body = glob[i + 1 : close]
            body = "^" + body[1:] if body.startswith("!") else body
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = close + 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    compiled = re.compile("".join(out))
    return lambda rel: compiled.fullmatch(rel) is not None


def _walk(
    top: Path, after: tuple[str, ...] | None, prefix: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], str]]:
    """Regular files below `top`, as (name parts, path), in sorted order.

    Sorted per directory, so the order is that of the name-part tuples, which
    is what lets a cursor skip a whole subtree it has passed. Symlinks are
    never followed, to a directory or a file: the walk stays on the tree
    Policy.resolve() vetted. A directory it cannot read is passed over.
    """
    try:
        with os.scandir(top) as scan:
            items = sorted(scan, key=lambda item: item.name)
    except OSError:
        return
    for item in items:
        parts = (*prefix, item.name)
        if after is not None and parts < after and after[: len(parts)] != parts:
            continue  # this file, or every file below this directory, is done
        try:
            if item.is_symlink():
                continue
            if item.is_dir(follow_symlinks=False):
                if item.name not in SKIPPED_DIRS:
                    yield from _walk(Path(item.path), after, parts)
            elif item.is_file(follow_symlinks=False) and (
                after is None or parts >= after
            ):
                yield parts, item.path
        except OSError:
            continue


def _clean(text: str) -> str:
    """A matched line as one line of the page, cut to MATCH_WIDTH."""
    text = "".join(c if c.isprintable() or c == "\t" else repr(c)[1:-1] for c in text)
    return text if len(text) <= MATCH_WIDTH else text[:MATCH_WIDTH] + "..."


@dataclass(frozen=True)
class Hit:
    """One search result: a file, or with a pattern, one line in it.

    `number` is 0 for a file and the 1-based line number for a line.
    """

    path: str
    parts: tuple[str, ...]
    number: int = 0
    text: str = ""
    # The whole file's digest, for a line: the evidence of what was searched.
    sha256: str | None = None
    size: int | None = None

    def line(self) -> str:
        if self.number == 0:
            return self.path
        return f"{self.path}:{self.number}: {_clean(self.text)}"

    def at(self) -> list[object]:
        return [list(self.parts), self.number]


def _lines(
    path: str, regex: re.Pattern[str], after: int
) -> Iterator[tuple[int, str, Evidence]]:
    """The matching lines of a text file after line `after`, each with the
    whole file's digest; none for a binary one or one that cannot be read.

    The file is read once, and the lines are matched in the buffer that was
    hashed, so the digest names the version the lines came from.
    """
    # ponytail: a text file is held in memory whole while it is searched, to
    # hash the bytes it is matched in. The upgrade for files larger than
    # memory is hashing while streaming, then reading the rest of a file a
    # page stops in.
    try:
        with open(path, "rb") as handle:
            head = handle.read(BINARY_SNIFF)
            if b"\0" in head:
                return
            data = head + handle.read()
    except OSError:
        return
    digest = _digest(data)
    for number, raw in enumerate(BytesIO(data), 1):
        if number <= after:
            continue
        text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if regex.search(text):
            yield number, text, digest


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


def search_files(
    path: str = ".",
    glob: str | None = None,
    pattern: str | None = None,
    cursor: str | None = None,
    limit: int = SEARCH_LIMIT,
    *,
    base: str | None = None,
) -> str:
    """Files under a directory by glob, and with a pattern, their matching
    lines, a page at a time.

    Without a pattern no file is opened, so a glob over a large tree costs a
    directory walk. With one, binary files are skipped.
    """
    return _search_page(path, glob, pattern, cursor, limit, base=base)[0]


def _search_page(
    path: str = ".",
    glob: str | None = None,
    pattern: str | None = None,
    cursor: str | None = None,
    limit: int = SEARCH_LIMIT,
    *,
    base: str | None = None,
) -> tuple[str, Evidence]:
    """search_files(), and its evidence: per file on the page, its path, and
    with a pattern, the file's digest and the line numbers returned. A glob
    alone opens no file, so its files carry only their path.
    """
    top = Path(os.path.abspath(path))
    shown = _shown(top, base)
    call = _call_id("search", str(top), glob or "", pattern or "")
    position = _position(cursor, call)
    after: tuple[str, ...] | None = None
    after_line = 0
    if position is not None:
        try:
            raw_parts, after_line = position
            after = tuple(str(part) for part in raw_parts)
            after_line = int(after_line)
        except (TypeError, ValueError):
            raise ValueError(
                "invalid cursor; call again without one to start over"
            ) from None
    regex: re.Pattern[str] | None = None
    if pattern:
        # ponytail: Python's re cannot be interrupted, so a pattern that
        # backtracks catastrophically holds the worker thread. The upgrade is
        # a linear-time engine, which is a dependency.
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid pattern {pattern!r}: {exc}") from None
    matches = _glob_matcher(glob)
    if top.is_file():
        files: Iterable[tuple[tuple[str, ...], str]] = (
            [((top.name,), str(top))] if after is None or (top.name,) >= after else []
        )
    elif top.is_dir():
        files = _walk(top, after)
    elif top.exists():
        raise ValueError(f"{path} is not a regular file or a directory")
    else:
        raise FileNotFoundError(f"{path} does not exist")

    def hits() -> Iterator[Hit]:
        for parts, file in files:
            if not matches("/".join(parts)):
                continue
            shown_file = _shown(Path(file), base)
            if regex is None:
                if parts != after:
                    yield Hit(shown_file, parts)
                continue
            skip = after_line if parts == after else 0
            for number, text, digest in _lines(file, regex, skip):
                yield Hit(shown_file, parts, number, text, **digest)

    limit = _clamp(limit, SEARCH_LIMIT)
    more = ", more on the next page"
    widest = f"{_count(limit, 'matching line')} in {_count(limit, 'file')}{more}"
    taken, next_cursor = _paginate(
        hits(), Hit.line, Hit.at, call, limit, len(f"{shown}: {widest}") + 1
    )
    evidence: Evidence = {
        "path": shown,
        "glob": glob or None,
        "pattern": pattern or None,
        "files": [],
        "more": next_cursor is not None,
    }
    if not taken:
        return f"{shown}: no {'matching lines' if regex else 'files'}", evidence
    hits_taken = [hit for hit, _ in taken]
    contributed: dict[str, Evidence] = {}
    for hit in hits_taken:
        if regex is None:
            contributed[hit.path] = {"path": hit.path}
            continue
        record = contributed.setdefault(
            hit.path,
            {"path": hit.path, "sha256": hit.sha256, "size": hit.size, "lines": []},
        )
        record["lines"].append(hit.number)
    evidence["files"] = list(contributed.values())
    if regex is None:
        summary = _count(len(hits_taken), "file")
    else:
        files_hit = len({hit.parts for hit in hits_taken})
        summary = (
            f"{_count(len(hits_taken), 'matching line')} in {_count(files_hit, 'file')}"
        )
    header = f"{shown}: {summary}" + (more if next_cursor else "")
    return _render(header, (text for _, text in taken), next_cursor), evidence


# bash is the async one: dispatch awaits it, and runs the rest in a thread.
TOOLS: dict[str, Callable[..., Any]] = {
    "read": read_file,
    "list": list_dir,
    "search": search_files,
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
        "name": "list",
        "description": (
            "List one directory: each entry's name, kind (dir, file, symlink, "
            "other), a file's size, and whether it is binary, sorted by name. "
            "Read-only and runs nothing. Symlinks are listed, never followed. "
            "A long listing comes in pages: pass the cursor a page ends with "
            "to get the next one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory to list. Default: the project root.",
                },
                "cursor": {
                    "type": "string",
                    "description": "The cursor the previous page ended with.",
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        f"Entries per page (default {LIST_LIMIT}, at most {MAX_PAGE})."
                    ),
                },
            },
        },
    },
    {
        "name": "search",
        "description": (
            "Find files under a directory by glob, and with a pattern, the "
            "lines in them that match a Python regular expression. Read-only "
            "and runs nothing. Skips .git, binary files and symlinks. Each "
            "match is `path:line: text`; lines count from 1, so read it with "
            "offset line-1. Results come in pages: pass the cursor a page "
            "ends with to get the next one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Directory (or one file) to search. Default: the project root."
                    ),
                },
                "glob": {
                    "type": "string",
                    "description": (
                        "Which files: `*.py` matches a file name anywhere; a "
                        "glob with `/`, such as `src/**/*.py`, matches the path "
                        "below `path`. Default: every file."
                    ),
                },
                "pattern": {
                    "type": "string",
                    "description": (
                        "Regular expression to find in each line. `(?i)` makes "
                        "it case-insensitive. Default: list the files only."
                    ),
                },
                "cursor": {
                    "type": "string",
                    "description": "The cursor the previous page ended with.",
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        f"Results per page (default {SEARCH_LIMIT}, "
                        f"at most {MAX_PAGE})."
                    ),
                },
            },
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
            "reading files or running commands, to ask permission, to offer "
            "follow-up work, or for details you can decide yourself such as "
            "wording, style or names. A new value the request leaves open is not "
            "such a detail, and neither is which of several things the request "
            "could mean. Call ask on its own, before making any change. This ends "
            "the session."
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
                        "unanswered."
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


PATH_TOOLS = frozenset({"read", "write", "edit", "list", "search"})
# The tools that read the tree and say what they returned.
EVIDENCE_TOOLS = frozenset({"read", *NAVIGATION_TOOLS})


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
    # Whether bash sees the provider credentials nare reads (CREDENTIAL_ENV).
    bash_credentials: bool = False

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
            **({"bash_credentials": True} if self.bash_credentials else {}),
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


_INPUT_SCHEMAS = {schema["name"]: schema["input_schema"] for schema in TOOL_SCHEMAS}
# ask reads a bare string as one question, and a turn of only asks ends
# blocked on that; its own checks stand in for the schema's types, so a schema
# check cannot change when blocked is reported.
_OWN_TYPE_CHECKS = frozenset({"ask"})


def _arguments(name: str, args: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """A built-in call's arguments checked against the tool's input schema:
    the arguments to run with, and every problem, in the tool's terms.

    A null optional parameter is read as absent: some models send every
    parameter, null when unused. Unknown parameters are refused, which also
    keeps the model away from the keywords nare sets itself (`base`, `cwd`,
    `credentials`).
    """
    schema = _INPUT_SCHEMAS[name]
    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])
    args = {k: v for k, v in args.items() if v is not None or k in required}
    errors = [f"missing required parameter {p}" for p in required if p not in args]
    takes = ", ".join(properties) or "no parameters"
    errors += [
        f"unknown parameter {p}; {name} takes {takes}"
        for p in args
        if p not in properties
    ]
    if name not in _OWN_TYPE_CHECKS:
        known = {p: v for p, v in args.items() if p in properties}
        errors += validate(known, {"type": "object", "properties": properties})
    return args, errors


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
    evidence: Callable[[Evidence], None] | None = None,
) -> dict[str, Any]:
    """Run one tool call. Every failure comes back as an is_error result rather
    than an exception: the model adapts, which is what it is good at. A policy
    refusal is one of those failures, so a narrowed session keeps working
    instead of dying on the first attempt to leave its box.

    `left_running` hears of a process group a bash call left running, with its
    label, so the caller can record it. A callback, not a key on the result:
    a transport sends tool_result blocks as they are.

    `evidence` hears of what a successful read, list or search returned, as
    a record with no content, for the same reason.
    """
    if call.error is not None:
        return tool_result(call.id, call.error, is_error=True)
    function = TOOLS.get(call.name)
    if function is None and call.name not in policy.external:
        return tool_result(
            call.id,
            # The session's tools, not every tool nare has: offered one it
            # may not call, the model switches to it and is refused.
            f"unknown tool {call.name!r}; this session allows: "
            f"{', '.join(sorted(policy.tools)) or 'none'}",
            is_error=True,
        )
    if call.name not in policy.tools:
        return tool_result(
            call.id,
            f"{call.name} is not allowed in this session; "
            f"allowed: {', '.join(sorted(policy.tools)) or 'none'}",
            is_error=True,
        )
    checked = dict(call.args)
    if function is not None:
        # Before approval: nobody is asked to approve a call that cannot run,
        # and nothing starts with arguments Python would only reject later.
        checked, problems = _arguments(call.name, checked)
        if problems:
            return tool_result(
                call.id, f"{call.name}: {'; '.join(problems)}", is_error=True
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
            return tool_result(
                call.id,
                _cut_middle(text, MAX_TOOL_OUTPUT, "the result"),
                is_error=failed,
            )
        assert function is not None
        args = checked
        if call.name in EVIDENCE_TOOLS:
            # Paths in results and evidence are relative to the root, which
            # `read` accepts. Set here, after the model's arguments, so it
            # cannot be moved.
            if call.name in NAVIGATION_TOOLS and args.get("path") in (None, ""):
                args["path"] = "."
            args["base"] = str(policy.root.resolve()) if policy.root else os.getcwd()
        if call.name in PATH_TOOLS and "path" in args:
            args["path"] = str(policy.resolve(str(args["path"])))
        if call.name == "bash" and policy.root is not None:
            args["cwd"] = str(policy.root.resolve())
        if call.name == "bash":
            # Set after the model's arguments, so it cannot be moved by them.
            args["credentials"] = policy.bash_credentials
        output: str | dict[str, Any]
        record: Evidence | None = None
        if call.name == "read":
            args["image_input"] = policy.image_input
            args["image_model"] = policy.image_model
            output, record = await asyncio.to_thread(_read_with_image, **args)
        elif call.name in NAVIGATION_TOOLS:
            page: Callable[..., tuple[str, Evidence]] = (
                _list_page if call.name == "list" else _search_page
            )
            text, record = await asyncio.to_thread(page, **args)
            output = text
        elif call.name == "bash":
            outcome = await bash_outcome(**args)
            if outcome.group is not None and left_running is not None:
                left_running(outcome.group, group_label(str(args["command"])))
            return tool_result(call.id, outcome.text, is_error=outcome.failed)
        else:
            output = await asyncio.to_thread(function, **args)
        if record is not None and evidence is not None:
            evidence({"tool": call.name, "tool_use_id": call.id, **record})
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
