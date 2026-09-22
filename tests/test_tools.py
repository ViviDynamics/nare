import os
import subprocess
import sys
import time
from pathlib import Path

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
    questions_from,
    read_file,
    run_bash,
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


def test_bash_reports_stdout_and_exit_code() -> None:
    assert run_bash("echo hi") == "exit 0\nhi"


def test_bash_reports_a_failure_without_raising() -> None:
    assert run_bash("exit 3").startswith("exit 3")


def test_bash_captures_stderr() -> None:
    assert "boom" in run_bash("echo boom >&2")


def test_bash_times_out() -> None:
    import subprocess

    with pytest.raises(subprocess.TimeoutExpired):
        run_bash("sleep 5", timeout=1)


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


def test_a_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    # subprocess.run's timeout kills the shell and nothing the shell started,
    # so `sleep 41 & sleep 42` left both sleeps running. In a real run that is
    # an orphaned dev server or test watcher per timed-out command.
    pidfile = tmp_path / "pid"
    with pytest.raises(subprocess.TimeoutExpired):
        run_bash(f"sleep 30 & echo $! > {pidfile}; wait", timeout=1)

    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.02)
    pytest.fail(f"pid {pid} outlived the timeout that was supposed to kill it")


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
    run_bash("true")
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
