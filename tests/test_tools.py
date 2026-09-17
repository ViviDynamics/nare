from pathlib import Path

import pytest

from nare.tools import (
    TOOL_SCHEMAS,
    TOOLS,
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
        ToolCall(id="c1", name="read", args={"path": str(target)}), approve_all
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
        approve_all,
    )
    assert result["is_error"] is True
    assert "FileNotFoundError" in result["content"]


async def test_an_unknown_tool_becomes_an_error_result() -> None:
    result = await dispatch(ToolCall(id="c1", name="grep", args={}), approve_all)
    assert result["is_error"] is True
    assert "grep" in result["content"]


async def test_a_bad_argument_becomes_an_error_result_not_a_crash() -> None:
    result = await dispatch(
        ToolCall(id="c1", name="read", args={"wrong": 1}), approve_all
    )
    assert result["is_error"] is True


async def test_denied_approval_blocks_the_tool(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"

    def deny(tool: str, args: dict[str, object]) -> bool:
        return False

    result = await dispatch(
        ToolCall(id="c1", name="write", args={"path": str(target), "content": "x"}),
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

    await dispatch(ToolCall(id="c1", name="bash", args={"command": "ls"}), record)
    assert seen == [("bash", {"command": "ls"})]


def test_questions_from_collects_across_calls() -> None:
    calls = [
        ToolCall(id="c1", name="read", args={"path": "a"}),
        ToolCall(id="c2", name="ask", args={"questions": ["which file?", "why?"]}),
    ]
    assert questions_from(calls) == ["which file?", "why?"]


def test_questions_from_is_empty_without_an_ask() -> None:
    assert questions_from([ToolCall(id="c1", name="read", args={})]) == []
