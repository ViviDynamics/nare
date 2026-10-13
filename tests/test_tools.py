import asyncio
import os
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


needs_pkill = pytest.mark.skipif(shutil.which("pkill") is None, reason="needs pkill")

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
