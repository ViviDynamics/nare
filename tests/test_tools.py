import asyncio
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from nare.tools import (
    MAX_TOOL_OUTPUT,
    TOOL_SCHEMAS,
    TOOLS,
    Policy,
    approve_all,
    ask,
    dispatch,
    edit_file,
    group_alive,
    group_label,
    questions_from,
    read_file,
    run_bash,
    stop_group,
    write_file,
)
from nare.transport import ToolCall


def test_exactly_five_tools_with_matching_schemas() -> None:
    assert {s["name"] for s in TOOL_SCHEMAS} == {
        "read",
        "write",
        "edit",
        "bash",
        "ask",
    }
    assert set(TOOLS) == {s["name"] for s in TOOL_SCHEMAS}


@pytest.mark.parametrize("schema", TOOL_SCHEMAS, ids=lambda s: str(s["name"]))
def test_every_schema_is_anthropic_shaped(schema: dict[str, object]) -> None:
    assert set(schema) == {"name", "description", "input_schema"}
    assert schema["description"]
    assert isinstance(schema["input_schema"], dict)


def test_read_returns_file_contents(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("one\ntwo\nthree\n")
    assert read_file(str(target)) == "one\ntwo\nthree"


def test_read_honours_offset_and_limit(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("one\ntwo\nthree\n")
    assert read_file(str(target), offset=1, limit=1) == "two"


def test_read_raises_on_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_file(str(tmp_path / "nope.py"))


def test_write_creates_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "b.py"
    write_file(str(target), "hello")
    assert target.read_text() == "hello"


def test_edit_replaces_a_unique_string(tmp_path: Path) -> None:
    target = tmp_path / "c.py"
    target.write_text("def foo():\n    pass\n")
    edit_file(str(target), "pass", "return 1")
    assert target.read_text() == "def foo():\n    return 1\n"


def test_edit_refuses_an_ambiguous_string(tmp_path: Path) -> None:
    target = tmp_path / "c.py"
    target.write_text("x\nx\n")
    with pytest.raises(ValueError, match="2 times"):
        edit_file(str(target), "x", "y")


def test_edit_refuses_a_missing_string(tmp_path: Path) -> None:
    target = tmp_path / "c.py"
    target.write_text("x\n")
    with pytest.raises(ValueError, match="not found"):
        edit_file(str(target), "zzz", "y")


async def test_bash_reports_stdout_and_exit_code() -> None:
    assert await run_bash("echo hi") == "exit 0\nhi"


async def test_bash_reports_a_failure_without_raising() -> None:
    assert (await run_bash("exit 3")).startswith("exit 3")


async def test_bash_captures_stderr() -> None:
    assert "boom" in await run_bash("echo boom >&2")


async def test_bash_times_out() -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        await run_bash("sleep 5", timeout=1)


def test_ask_returns_an_acknowledgement() -> None:
    assert "blocked" in ask(["which file?"])


async def test_dispatch_returns_a_tool_result_block(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("hello")
    result = await dispatch(
        ToolCall(id="c1", name="read", args={"path": str(target)}),
        Policy(),
        approve_all,
    )
    assert result == {
        "type": "tool_result",
        "tool_use_id": "c1",
        "content": "hello",
        "is_error": False,
    }


async def test_a_tool_exception_becomes_an_error_result(tmp_path: Path) -> None:
    result = await dispatch(
        ToolCall(id="c1", name="read", args={"path": str(tmp_path / "nope")}),
        Policy(),
        approve_all,
    )
    assert result["is_error"] is True
    assert "FileNotFoundError" in result["content"]


async def test_an_unknown_tool_becomes_an_error_result() -> None:
    result = await dispatch(
        ToolCall(id="c1", name="grep", args={}), Policy(), approve_all
    )
    assert result["is_error"] is True
    assert "grep" in result["content"]


async def test_a_bad_argument_becomes_an_error_result_not_a_crash() -> None:
    result = await dispatch(
        ToolCall(id="c1", name="read", args={"wrong": 1}), Policy(), approve_all
    )
    assert result["is_error"] is True


async def test_denied_approval_blocks_the_tool(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"

    def deny(tool: str, args: dict[str, object]) -> bool:
        return False

    result = await dispatch(
        ToolCall(id="c1", name="write", args={"path": str(target), "content": "x"}),
        Policy(),
        deny,
    )
    assert result["is_error"] is True
    assert "not approved" in result["content"]
    assert not target.exists()


async def test_approve_sees_the_tool_name_and_args() -> None:
    seen: list[tuple[str, dict[str, object]]] = []

    def record(tool: str, args: dict[str, object]) -> bool:
        seen.append((tool, args))
        return False

    await dispatch(
        ToolCall(id="c1", name="bash", args={"command": "ls"}), Policy(), record
    )
    assert seen == [("bash", {"command": "ls"})]


def test_questions_from_collects_across_calls() -> None:
    calls = [
        ToolCall(id="c1", name="read", args={"path": "a"}),
        ToolCall(id="c2", name="ask", args={"questions": ["which file?", "why?"]}),
    ]
    assert questions_from(calls) == ["which file?", "why?"]


def test_questions_from_is_empty_without_an_ask() -> None:
    assert questions_from([ToolCall(id="c1", name="read", args={})]) == []


@pytest.mark.parametrize(
    "value,expected",
    [
        (["a", "b"], ["a", "b"]),
        ("which file?", ["which file?"]),
        (3, []),
        (None, []),
        ([], []),
    ],
)
def test_questions_from_survives_a_schema_violating_ask(
    value: object, expected: list[str]
) -> None:
    # The model controls this value. A bare string must not become a list of
    # characters, and a non-iterable must not turn blocked into error.
    call = ToolCall(id="c1", name="ask", args={"questions": value})
    assert questions_from([call]) == expected


async def test_a_raising_approve_comes_back_as_an_error_result() -> None:
    # A real approval seam prompts a human or calls a service, so it can fail.
    # Escaping here would leave a tool_use with no tool_result.
    def explode(tool: str, args: dict[str, object]) -> bool:
        raise ConnectionError("approval service is down")

    result = await dispatch(
        ToolCall(id="c1", name="bash", args={"command": "ls"}), Policy(), explode
    )
    assert result["is_error"] is True
    assert result["tool_use_id"] == "c1"
    assert "ConnectionError" in result["content"]


def test_the_file_tools_pin_utf8_regardless_of_locale(tmp_path: Path) -> None:
    # The schema promises UTF-8, but Path.read_text defaults to the locale
    # codec. Under LANG=C an edit would mojibake every non-ASCII byte in the
    # file, so this runs a child in that locale rather than trusting ours.
    target = tmp_path / "a.txt"
    target.write_bytes("héllo = 1\n".encode())
    script = tmp_path / "child.py"
    script.write_text(
        "import sys\n"
        "from nare.tools import read_file, edit_file\n"
        "assert read_file(sys.argv[1]) == 'h\\u00e9llo = 1'\n"
        "edit_file(sys.argv[1], '1', '2')\n",
        encoding="utf-8",
    )
    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0"}
    done = subprocess.run(
        [sys.executable, "-X", "utf8=0", str(script), str(target)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert done.returncode == 0, done.stderr
    assert target.read_bytes() == "héllo = 2\n".encode()


def _gone(pid: int) -> bool:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return True
        time.sleep(0.02)
    return False


async def test_a_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    # subprocess.run's timeout kills the shell and nothing the shell started,
    # so `sleep 41 & sleep 42` left both sleeps running. In a real run that is
    # an orphaned dev server or test watcher per timed-out command.
    pidfile = tmp_path / "pid"
    with pytest.raises(subprocess.TimeoutExpired):
        await run_bash(f"sleep 30 & echo $! > {pidfile}; wait", timeout=1)
    assert _gone(int(pidfile.read_text()))


async def test_a_cancel_kills_the_whole_process_group(tmp_path: Path) -> None:
    # In a worker thread, communicate() could not be stopped: quitting the
    # TUI mid-command waited out the command, or its timeout.
    pidfile = tmp_path / "pid"
    task = asyncio.create_task(run_bash(f"sleep 30 & echo $! > {pidfile}; wait"))
    for _ in range(500):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert _gone(int(pidfile.read_text()))


def _hold_pipe_connects(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    """Hold asyncio's pipe connects for the next spawn until the Event is set.

    Popen has started the shell by then, so a cancel lands mid-spawn every
    time, with or without load.
    """
    loop = asyncio.get_running_loop()
    real = loop.connect_read_pipe
    released = asyncio.Event()

    async def held(*args: Any, **kwargs: Any) -> Any:
        await released.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(loop, "connect_read_pipe", held)
    return released


async def _wait_for_pidfile(pidfile: Path) -> None:
    for _ in range(500):
        if pidfile.exists() and pidfile.read_text().strip():
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"the shell never wrote {pidfile}")


async def _result_within_5s(task: asyncio.Task[Any]) -> Any:
    """The cancelled call's result, or a failure if it has not ended in 5 s.

    Not asyncio.wait_for(task, 5): when its time runs out it cancels the call
    and waits for it, and a cancelled call that waits out its spawn ignores
    that cancel, so a regression there hung the test rather than failed it.
    Left running here, the call ends at teardown, which cancels its spawn too.
    """
    done, _ = await asyncio.wait({task}, timeout=5)
    if not done:
        pytest.fail("the cancelled call was still running after 5 s")
    return task.result()


async def test_a_cancel_while_asyncio_connects_the_pipes_kills_the_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A cancel while asyncio connected the pipes met asyncio's own cleanup,
    # which kills the shell alone, then waits on the pipes the background
    # sleep holds open. Under load 1 cancel in 4 hung or left the sleep.
    released = _hold_pipe_connects(monkeypatch)
    pidfile = tmp_path / "pid"
    task = asyncio.create_task(run_bash(f"sleep 30 & echo $! > {pidfile}; wait"))
    await _wait_for_pidfile(pidfile)
    task.cancel()
    released.set()
    with pytest.raises(asyncio.CancelledError):
        await _result_within_5s(task)
    assert _gone(int(pidfile.read_text()))


async def test_a_second_cancel_while_the_pipes_connect_still_kills_the_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # After a cancel the call waits for its spawn, to kill what it started. A
    # second cancel that reached the spawn there handed it to asyncio's own
    # cleanup after all: the sleep ran on, or held up the cancel.
    released = _hold_pipe_connects(monkeypatch)
    pidfile = tmp_path / "pid"
    task = asyncio.create_task(run_bash(f"sleep 30 & echo $! > {pidfile}; wait"))
    await _wait_for_pidfile(pidfile)
    task.cancel()
    # One yield delivers the first cancel, so the call is waiting on its
    # spawn when the second one comes.
    await asyncio.sleep(0)
    task.cancel()
    released.set()
    with pytest.raises(asyncio.CancelledError):
        await _result_within_5s(task)
    assert _gone(int(pidfile.read_text()))


async def test_a_spawn_that_fails_after_a_cancel_still_raises_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A cancelled call waits for its spawn so it can kill the group. A spawn
    # that then failed raised its own OSError, so the caller, who asked to
    # cancel, never heard that the call was cancelled.
    # The spawn fails here, not in connect_read_pipe as #78 first said: on
    # Python 3.12, which CI runs, asyncio's spawn never finishes once a pipe
    # connect fails, and the test hung.
    entered = asyncio.Event()
    released = asyncio.Event()

    async def failing(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        await released.wait()
        raise OSError("spawn failed")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", failing)
    task = asyncio.create_task(run_bash("true"))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    released.set()
    with pytest.raises(asyncio.CancelledError):
        await _result_within_5s(task)


async def test_a_spawn_that_fails_after_a_cancel_logs_no_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Fails first on Python 3.14 only: 3.12's shield never logs.

    On 3.14, a shielded task that fails after its shield was cancelled logs
    "OSError exception in shielded future" at ERROR, though the call handles
    the error. The TUI showed that as a red line, traceback and all.
    """
    entered = asyncio.Event()
    released = asyncio.Event()

    async def failing(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        await released.wait()
        raise OSError("spawn failed")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", failing)
    task = asyncio.create_task(run_bash("true"))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    released.set()
    with pytest.raises(asyncio.CancelledError):
        await _result_within_5s(task)
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []


async def test_a_cancel_when_loop_shutdown_also_cancels_the_spawn_still_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails first on Python 3.12 only: 3.14's asyncio ends that spawn itself.

    asyncio.run's shutdown cancels every task, the spawn and its pipe connect
    too. On 3.12 asyncio's spawn then waits for pipes that never connect, so
    the call, waiting its spawn out, hung, and asyncio.run with it.
    """
    monkeypatch.setattr("nare.tools._SPAWN_WAIT", 0.2)
    released = _hold_pipe_connects(monkeypatch)
    pidfile = tmp_path / "pid"
    before = asyncio.all_tasks()
    task = asyncio.create_task(run_bash(f"sleep 30 & echo $! > {pidfile}; wait"))
    await _wait_for_pidfile(pidfile)
    task.cancel()
    await asyncio.sleep(0)
    spawning = asyncio.all_tasks() - before - {task}
    for t in spawning:
        t.cancel()
    released.set()
    try:
        done, _ = await asyncio.wait({task}, timeout=5)
        assert done == {task}
        assert task.cancelled()
    finally:
        # The pid went with the spawn, so the background sleep runs on.
        try:
            os.kill(int(pidfile.read_text()), signal.SIGKILL)
        except ProcessLookupError:
            pass
        # A second cancel ends a stuck spawn, so a failure fails the test
        # instead of hanging it.
        for t in spawning:
            t.cancel()


def test_commands_do_not_inherit_nares_stdin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # nare's stdin belongs to conductor. Piping a line into nare and running
    # `cat` printed it back, so anything that reads would eat conductor's
    # input or block until the timeout. Asserted on the kwargs rather than
    # behaviorally: pytest already replaces fd 0, so a `cat` test would pass
    # with or without the fix.
    seen: dict[str, object] = {}
    real = subprocess.Popen

    def spy(*args: object, **kwargs: object) -> object:
        seen.update(kwargs)
        return real(*args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(subprocess, "Popen", spy)
    asyncio.run(run_bash("true"))
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["start_new_session"] is True


def test_read_is_capped_by_bytes_not_only_by_lines(tmp_path: Path) -> None:
    # `limit` bounds a read by LINES, which says nothing about size: one line
    # of minified code can be the whole context window.
    target = tmp_path / "bundle.min.js"
    target.write_text("x" * (MAX_TOOL_OUTPUT * 2))
    out = read_file(str(target))
    assert len(out) < MAX_TOOL_OUTPUT * 2
    assert out.endswith(f"[truncated at {MAX_TOOL_OUTPUT} chars]")


def test_a_missing_edit_target_names_the_parameter_the_model_sees(
    tmp_path: Path,
) -> None:
    # The schema calls it `old`. Saying `old_string` invites a retry with an
    # argument name that does not exist.
    target = tmp_path / "f.txt"
    target.write_text("hello")
    with pytest.raises(ValueError, match=r"\bold not found\b"):
        edit_file(str(target), "nope", "x")


async def test_an_async_approver_is_awaited(tmp_path: Path) -> None:
    async def allow(tool: str, args: dict[str, Any]) -> bool:
        await asyncio.sleep(0)
        return True

    target = tmp_path / "a.txt"
    call = ToolCall(id="c1", name="write", args={"path": str(target), "content": "hi"})
    result = await dispatch(call, Policy(), allow)
    assert result["is_error"] is False
    assert target.read_text() == "hi"


async def test_an_async_denial_refuses_the_call(tmp_path: Path) -> None:
    async def deny(tool: str, args: dict[str, Any]) -> bool:
        return False

    target = tmp_path / "a.txt"
    call = ToolCall(id="c1", name="write", args={"path": str(target), "content": "hi"})
    result = await dispatch(call, Policy(), deny)
    assert result == {
        "type": "tool_result",
        "tool_use_id": "c1",
        "content": "write was not approved",
        "is_error": True,
    }
    assert not target.exists()


@pytest.mark.parametrize(
    "reason, expected",
    [
        ("use the write tool", "bash was not approved: use the write tool"),
        (" use the write tool\n", "bash was not approved: use the write tool"),
        ("  ", "bash was not approved"),
        ("", "bash was not approved"),
    ],
)
@pytest.mark.parametrize("is_async", [False, True])
async def test_a_denial_carries_its_reason(
    reason: str, expected: str, is_async: bool, tmp_path: Path
) -> None:
    # A reason is truthy when it is not blank: it must still deny.
    async def later(tool: str, args: dict[str, Any]) -> str:
        return reason

    marker = tmp_path / "ran"
    call = ToolCall(id="c1", name="bash", args={"command": f"touch {marker}"})
    result = await dispatch(
        call, Policy(), later if is_async else (lambda tool, args: reason)
    )
    assert result["content"] == expected
    assert result["is_error"] is True
    assert not marker.exists()


async def test_bash_keeps_a_failure_inside_a_pipe() -> None:
    # Under dash, `npm run build 2>&1 | tail -30` said exit 0 for three
    # failed builds in the practice run.
    assert (await run_bash("false | true")).startswith("exit 1")


async def test_bash_runs_bash_syntax() -> None:
    assert (await run_bash("[[ 1 == 1 ]]")).startswith("exit 0")


def _argv_of(monkeypatch: pytest.MonkeyPatch, command: str) -> tuple[str, ...]:
    seen: dict[str, tuple[str, ...]] = {}
    real = subprocess.Popen

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen["argv"] = tuple(args[0])
        return real(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spy)
    asyncio.run(run_bash(command))
    return seen["argv"]


def test_bash_runs_bash_with_pipefail(monkeypatch: pytest.MonkeyPatch) -> None:
    argv = _argv_of(monkeypatch, "true")
    assert argv[0].endswith("bash")
    assert argv[1:] == ("-o", "pipefail", "-c", "true")


def test_bash_falls_back_to_sh_without_bash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nare.tools.shutil.which", lambda name: None)
    assert _argv_of(monkeypatch, "true") == ("/bin/sh", "-c", "true")


def test_the_bash_description_states_the_shell_semantics() -> None:
    schema = next(s for s in TOOL_SCHEMAS if s["name"] == "bash")
    assert (
        "Runs in bash with pipefail. Each call is a fresh shell: "
        "`cd` and `export` do not persist."
    ) in schema["description"]


def schema_of(name: str) -> dict[str, Any]:
    return next(s for s in TOOL_SCHEMAS if s["name"] == name)


def test_the_write_description_prefers_edit_for_an_existing_file() -> None:
    # The practice run rewrote files it had just written, five times.
    assert (
        "Prefer `edit` for a file that already exists."
        in (schema_of("write")["description"])
    )


def test_the_edit_description_says_to_reread_a_rewritten_file() -> None:
    # An edit after a rewrite used the text from before it, and failed.
    assert (
        "Re-read a file before editing it if you rewrote it."
        in (schema_of("edit")["description"])
    )


def test_the_bash_description_says_how_to_stop_what_it_started() -> None:
    description = schema_of("bash")["description"]
    assert "by `$!` in the call that started it" in description
    assert "`kill -- -<group>`" in description


def test_the_ask_question_states_its_default() -> None:
    questions = schema_of("ask")["input_schema"]["properties"]["questions"]
    assert "the one you will use if it goes unanswered" in questions["description"]


def test_the_ask_description_says_an_open_value_or_reading_is_no_detail() -> None:
    # Without the old example, sol and luna set a new timeout value themselves
    # instead of asking, and onprem deleted both unused helpers more often than
    # on main's text (#45). The sentence follows the exclusion for details you
    # can decide yourself, so "such a detail" means it.
    assert (
        "or for details you can decide yourself such as wording, style or names. "
        "A new value the request leaves open is not such a detail, and neither is "
        "which of several things the request could mean."
    ) in schema_of("ask")["description"]


# What a model could copy from the ask schema to pass an ambiguous case, from
# benchmarks/cases, as case-insensitive patterns, each under a short name. A
# name counts inside a longer identifier (HTTP_TIMEOUT, MAX_RETRIES), so only a
# letter or digit before it stops a match, and a value counts with a unit after
# it (60s). Shapes, under any names: an amount left open, as in the old
# example's "from 30 to what", an unused helper to choose, as in
# ambiguous-after-reading's prompt, and which of several things to delete,
# asked as a question. The prompts' verbs alone are not here: a description may
# say "delete" or "increase".
CASE_ANSWERS = {
    # ambiguous-request: "Increase the timeout."; config.py holds TIMEOUT = 30
    # and RETRIES = 3. Its judge accepts a question naming the current 30; the
    # old example offered 60 and 120, and every guess set 60.
    "TIMEOUT": r"(?<![a-z0-9])timeout",
    "RETRIES": r"(?<![a-z0-9])retries",
    "config.py": r"(?<![a-z0-9])config\.py",
    "30": r"(?<![\d.])30(?!\d)",
    "60": r"(?<![\d.])60(?!\d)",
    "120": r"(?<![\d.])120(?!\d)",
    # ambiguous-after-reading: "Delete the unused helper."; utils.py holds
    # format_bytes and slugify, which nothing calls, and parse_csv_line and
    # total, which main.py calls. "total" is an everyday word, so it is left out.
    "utils.py": r"(?<![a-z0-9])utils\.py",
    "main.py": r"(?<![a-z0-9])main\.py",
    "format_bytes": r"(?<![a-z0-9])format_bytes",
    "slugify": r"(?<![a-z0-9])slugify",
    "parse_csv_line": r"(?<![a-z0-9])parse_csv_line",
    # An amount left open: "from 30 to", "from 45 seconds to", "to 8 or 16",
    # "to 8 or to 16", the old example's list "to 8, 16, or", and "to what"
    # asked as a question ("to what:", "to what?", "to what value").
    "from N to": r"\bfrom \d[\w.]*(?: \w+)? to\b",
    "to N or M": r"\bto \d[\w.]* or \d",
    "to N or to M": r"\bto \d[\w.]* or to \d",
    "to N, M": r"\bto \d[\w.]*,\s*\d",
    "to what": r"\bto what(?=\s*[:?]| value)",
    # An unused helper to choose.
    "unused": r"\bunused\b",
    "dead helper": r"\b(?:dead|uncalled|unreferenced) (?:helper|function)s?\b",
    # Which of several things to delete, asked as a question: "delete",
    # "remove" or "drop", then "or", within one question. A dot inside a word,
    # as in a file name, does not end the question.
    "delete A or B?": (
        r"\b(?:delete|remove|drop)\b(?:[^.?!]|\.\w)*\bor\b(?:[^.?!]|\.\w)*\?"
    ),
}


def case_answers_in(text: str) -> list[str]:
    """The name of each pattern that matches `text`."""
    return [
        name
        for name, pattern in CASE_ANSWERS.items()
        if re.search(pattern, text, re.IGNORECASE)
    ]


def strings_in(value: object) -> list[str]:
    """Every string value in a schema, each checked on its own."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in strings_in(item)]
    if isinstance(value, list):
        return [s for item in value for s in strings_in(item)]
    return []


# Each near copy, with the pattern it must trigger. Every pattern is the only
# match of at least one near copy, so taking any pattern out, or narrowing it
# past its near copies, fails a test.
NEAR_COPIES = [
    # The old example, ambiguous-request's fixture and answer.
    (
        "Raise TIMEOUT from 30 to what: 60, 120, or another value? "
        "Unanswered, I will use 60.",
        "to what",
    ),
    # ambiguous-request's shape under other names, which spec 6.1 rules out.
    ("Raise MAX_WORKERS from 4 to 8 or 16? Unanswered, I will use 8.", "from N to"),
    ("Raise MAX_WORKERS to 8 or 16? Unanswered, I will use 8.", "to N or M"),
    ("Raise MAX_WORKERS to 8, 16, or another value?", "to N, M"),
    ("Set the pool size to 8 or to 16?", "to N or to M"),
    ("Lengthen the time limit from 45 to 90 or 180 seconds?", "from N to"),
    ("Lengthen the time limit from 45 seconds to 90?", "from N to"),
    ("Raise the pool size to what?", "to what"),
    # The case's names inside longer ones, and its values with a unit.
    ("Set HTTP_TIMEOUT to 60s or 120s?", "TIMEOUT"),
    ("Raise HTTP_TIMEOUT?", "TIMEOUT"),
    ("Raise MAX_RETRIES?", "RETRIES"),
    ("Edit app_config.py?", "config.py"),
    ("Keep the 30s default?", "30"),
    ("Wait 60s between tries?", "60"),
    ("Wait 120s between tries?", "120"),
    ("Which helper in string_utils.py?", "utils.py"),
    ("Is it called from cli_main.py?", "main.py"),
    ("Delete format_bytes?", "format_bytes"),
    ("Delete slugify?", "slugify"),
    ("Is parse_csv_line still called?", "parse_csv_line"),
    # ambiguous-after-reading's shape under other names.
    ("Remove the unused function, format_size or make_slug?", "unused"),
    ("Which unused helper do you mean?", "unused"),
    ("Delete the dead helper, or the uncalled function too?", "dead helper"),
    ("Which uncalled function do you mean?", "dead helper"),
    # Which of several to delete, asked as a question, under other names.
    ("Delete parse_a or parse_b? Unanswered, I will delete both.", "delete A or B?"),
    ("Delete helpers.py or strings.py?", "delete A or B?"),
    ("Drop helper_a or helper_b?", "delete A or B?"),
]


@pytest.mark.parametrize(("near_copy", "pattern"), NEAR_COPIES)
def test_the_ask_leak_check_catches_a_near_copy(near_copy: str, pattern: str) -> None:
    assert pattern in case_answers_in(near_copy)


def test_the_ask_leak_check_proves_each_pattern_alone() -> None:
    # Some near copy is caught by each pattern and by no other.
    caught = [case_answers_in(near_copy) for near_copy, _ in NEAR_COPIES]
    assert {found[0] for found in caught if len(found) == 1} == set(CASE_ANSWERS)


@pytest.mark.parametrize(
    "harmless",
    [
        "Do not delete files to make room.",
        "Do not delete or rename files.",
        # "delete", then a question with "or", in two sentences.
        "Do not delete files. Is it one or both?",
        "Keep the change to what the request asks for.",
        "Return to what you were doing.",
        "Wait up to 300 seconds.",
        # A number and a comma, with no second number.
        "Count up to 3, then stop.",
    ],
)
def test_the_ask_leak_check_passes_harmless_wording(harmless: str) -> None:
    assert case_answers_in(harmless) == []


def test_the_ask_schema_names_no_benchmark_cases_answer() -> None:
    # The old example was ambiguous-request's fixture and the answer its judge
    # looks for, so a model could pass that case by copying it (#45). Every
    # string in the schema is checked on its own, descriptions and all.
    leaks = {s: case_answers_in(s) for s in strings_in(schema_of("ask"))}
    assert {s: found for s, found in leaks.items() if found} == {}


# Darwin's pkill excludes its ancestors unless -a is supplied, so plain -f
# does not kill the shell whose argv contains the tag. These reproduce Linux.
needs_pkill = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("pkill") is None,
    reason="needs Linux pkill -f ancestor matching (darwin excludes ancestors)",
)

HINT = (
    "pkill -f also matches the shell running this command; use a pattern "
    "like `nex[t]-server`, or kill by `$!`."
)


def bash_call(command: str) -> ToolCall:
    return ToolCall(id="c1", name="bash", args={"command": command})


async def test_a_shell_killed_by_a_signal_says_so() -> None:
    text = await run_bash("echo hi; kill -TERM $$")
    assert text.startswith("killed by SIGTERM (signal 15)\nhi")
    assert HINT in text


@needs_pkill
async def test_a_command_that_pkills_its_own_tag_is_an_error() -> None:
    # The practice run's `pkill -f next-server` matched the tool's own shell,
    # read as `exit -15`, was not an error, and cost a turn of about 50k tokens.
    tag = f"nare-self-{uuid.uuid4().hex}"
    result = await dispatch(bash_call(f"pkill -f {tag}; echo unreachable"), Policy())
    assert result["is_error"] is True
    assert result["content"].startswith("killed by SIGTERM (signal 15)")
    assert "unreachable" not in result["content"]
    assert HINT in result["content"]


async def test_a_signal_without_a_kill_command_gets_no_pkill_hint() -> None:
    python = shlex.quote(sys.executable)
    text = await run_bash(f"ulimit -c 0; exec {python} -c 'import os; os.abort()'")
    assert text.split("\n")[0] == "killed by SIGABRT (signal 6)"
    assert "pkill" not in text


@pytest.mark.skipif(sys.platform != "linux", reason="realtime signals are Linux")
async def test_a_signal_python_cannot_name_still_reads_as_a_signal() -> None:
    # 35 is a realtime signal: signal.Signals has no member for it.
    assert (await run_bash("kill -35 $$")).split("\n")[0] == "killed by signal 35"


async def test_pipefail_reports_a_closed_pipe_as_an_exit_not_a_kill() -> None:
    # `yes` dies of SIGPIPE when head exits, so pipefail makes the pipeline
    # exit 141. The shell exited normally: an exit code, not a kill.
    result = await dispatch(bash_call("yes | head -1"), Policy())
    assert result["content"] == "exit 141\ny"
    assert result["is_error"] is False
    plain = await dispatch(bash_call("exit 3"), Policy())
    assert plain["is_error"] is False


def test_group_alive_and_stop_group_on_a_real_group() -> None:
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        assert group_alive(proc.pid)
        assert stop_group(proc.pid)
        assert proc.wait(timeout=5) == -signal.SIGKILL
        # Reaped, so nothing is left in the group.
        assert not group_alive(proc.pid)
        assert stop_group(proc.pid)  # already gone counts as stopped
    finally:
        proc.kill()
        proc.wait()


needs_proc = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="reads member states from /proc"
)


def _state(pid: int) -> str:
    stat = Path(f"/proc/{pid}/stat").read_text()
    return stat[stat.rindex(")") + 2]


@needs_proc
def test_a_group_holding_only_a_zombie_is_not_alive() -> None:
    # Not reaped, so `true` stays a zombie; kill(0) still finds the group.
    proc = subprocess.Popen(["true"], start_new_session=True)
    try:
        for _ in range(300):
            if _state(proc.pid) == "Z":
                break
            time.sleep(0.01)
        assert _state(proc.pid) == "Z"
        assert not group_alive(proc.pid)
    finally:
        proc.wait()


# nare as PID 1 (the image run without --init): orphans reparent to nare,
# which never reaps them, so each one that exits stays a zombie.
AS_PID_1 = """
import asyncio, ctypes
from nare.tools import run_bash
assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
print(asyncio.run(run_bash("(sleep 0.01 &); echo hi")))
"""


@needs_proc
def test_an_orphan_that_exited_is_not_reported_when_nare_is_pid_1() -> None:
    done = subprocess.run(
        [sys.executable, "-c", AS_PID_1], capture_output=True, text=True, timeout=30
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "exit 0\nhi"


@pytest.mark.parametrize("pgid", [0, 1, -5, True, "123", None, os.getpgrp()])
def test_nare_never_signals_a_pgid_it_may_not(
    monkeypatch: pytest.MonkeyPatch, pgid: object
) -> None:
    # killpg(0) is nare's own group, and a session file is data: a junk or
    # hostile pgid must never reach a signal.
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "nare.tools.os.killpg", lambda group, sig: sent.append((group, sig))
    )
    assert group_alive(pgid) is False
    assert stop_group(pgid) is False
    assert sent == []


def test_group_label_is_the_first_line_redacted_then_cut() -> None:
    assert group_label("  npm run dev\nsecond line") == "npm run dev"
    assert group_label("a   b\tc") == "a b c"
    assert group_label("") == ""
    # Redacted before the cut: cutting first would leave `sk-ant-aaaa`, a stub
    # too short for the key pattern to match.
    label = group_label("x" * 50 + " sk-ant-" + "a" * 30)
    assert len(label) == 60
    assert label.endswith("...")
    assert "sk-ant" not in label
    assert "aaa" not in label


LEAVE_RUNNING = "(sleep 30 >/dev/null 2>&1 &)"


def _reported_group(text: str) -> int | None:
    found = re.search(r"\[still running: process group (\d+) \(", text)
    return None if found is None else int(found[1])


async def test_bash_reports_a_process_group_it_leaves_running() -> None:
    # A Postgres started in the practice run was still running hours later,
    # and nothing in the result said so.
    command = "(sleep 2 >/dev/null 2>&1 &)"
    text = await run_bash(command)
    pgid = _reported_group(text)
    try:
        assert pgid is not None
        assert text == f"exit 0\n[still running: process group {pgid} ({command})]"
    finally:
        if pgid is not None:
            stop_group(pgid)


async def test_a_cancel_while_probing_for_a_group_left_running_kills_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The probe sleeps for about 0.1 s after the shell exits. A Ctrl-C there
    # skipped the kill, and the call never returned, so dispatch never heard
    # of the group: it ran on, neither killed nor listed at quit.
    probed: list[int] = []
    probing = asyncio.Event()
    real = group_alive

    def spy(pgid: object) -> bool:
        assert isinstance(pgid, int)
        probed.append(pgid)
        probing.set()
        return real(pgid)

    monkeypatch.setattr("nare.tools.group_alive", spy)
    task = asyncio.create_task(run_bash(LEAVE_RUNNING))
    await asyncio.wait_for(probing.wait(), 5)
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        deadline = time.monotonic() + 5
        while real(probed[0]) and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert not real(probed[0])
    finally:
        stop_group(probed[0])


async def test_bash_reports_nothing_when_nothing_is_left() -> None:
    assert await run_bash("sleep 0.1 & wait") == "exit 0"


@needs_pkill
async def test_a_self_kill_does_not_read_as_a_group_left_running() -> None:
    # Real Linux pkill integration. The transient-zombie regression below
    # controls reap timing instead of hoping repeated runs hit the old race.
    tag = f"nare-self-{uuid.uuid4().hex}"
    text = await run_bash(f"pkill -f {tag}; echo unreachable")
    assert text.startswith("killed by SIGTERM")
    assert "still running" not in text


async def test_a_self_kill_waits_for_a_zombie_to_be_reaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A one-shot group probe used to report pkill's unreaped zombie as still
    # running about one run in four. Model that OS observation deterministically,
    # including on hosts without /proc, while exercising the real tool path.
    alive = iter([True, True, True, False])
    monkeypatch.setattr("nare.tools.group_alive", lambda pgid: next(alive))
    result = await dispatch(bash_call("kill -TERM $$"), Policy())
    assert result["is_error"] is True
    assert result["content"].startswith("killed by SIGTERM")
    assert "still running" not in result["content"]


async def test_dispatch_hands_a_group_left_running_to_its_caller() -> None:
    seen: list[tuple[int, str]] = []

    def record(pgid: int, label: str) -> None:
        seen.append((pgid, label))

    result = await dispatch(bash_call(LEAVE_RUNNING), Policy(), left_running=record)
    try:
        assert [label for _, label in seen] == [LEAVE_RUNNING]
        pgid = seen[0][0]
        assert group_alive(pgid)
        assert result["is_error"] is False
        assert result["content"].endswith(
            f"[still running: process group {pgid} ({LEAVE_RUNNING})]"
        )
    finally:
        for pgid, _ in seen:
            stop_group(pgid)


async def test_the_kill_advice_stops_a_group_from_a_later_call() -> None:
    # Each call is a fresh shell, so `$!` from the call that started a process
    # is gone by the next one. The description names the reported group
    # instead, and following it has to work.
    pgid = _reported_group(await run_bash(LEAVE_RUNNING))
    assert pgid is not None
    try:
        assert await run_bash(f"kill -- -{pgid}") == "exit 0"
        deadline = time.monotonic() + 5
        while group_alive(pgid) and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert not group_alive(pgid)
    finally:
        stop_group(pgid)
