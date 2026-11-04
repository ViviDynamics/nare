"""A built-in tool call is checked against the tool's own input schema before
it runs, and a mistake comes back in the tool's terms, not Python's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nare.tools import TOOL_SCHEMAS, Policy, dispatch
from nare.transport import ToolCall


async def run(name: str, args: dict[str, Any], **policy: Any) -> dict[str, Any]:
    seen: list[str] = []

    def approve(tool: str, _: dict[str, Any]) -> bool:
        seen.append(tool)
        return True

    result = await dispatch(ToolCall("c1", name, args), Policy(**policy), approve)
    result["approved"] = seen
    return result


async def test_a_string_timeout_is_refused_before_any_shell_starts(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "ran"

    result = await run("bash", {"command": f"touch {marker}", "timeout": "60"})

    assert result["is_error"] is True
    assert result["content"] == "bash: timeout: expected integer, got string"
    assert not marker.exists()
    # Nobody is asked to approve a call that cannot run.
    assert result["approved"] == []


async def test_a_missing_parameter_names_the_tool_and_the_parameter(
    tmp_path: Path,
) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")

    result = await run("edit", {"path": str(target), "old": "old"})

    assert result["is_error"] is True
    assert result["content"] == "edit: missing required parameter new"
    assert "edit_file" not in result["content"]
    assert target.read_text(encoding="utf-8") == "old"


async def test_an_unknown_parameter_is_reported_in_the_tools_terms(
    tmp_path: Path,
) -> None:
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")

    result = await run("read", {"path": str(target), "lines": 5})

    assert result["is_error"] is True
    assert result["content"] == (
        "read: unknown parameter lines; read takes path, offset, limit"
    )


async def test_every_problem_is_reported_at_once() -> None:
    result = await run("bash", {"timeout": 1.5, "shell": "zsh"})

    assert result["content"] == (
        "bash: missing required parameter command; "
        "unknown parameter shell; bash takes command, timeout; "
        "timeout: expected integer, got number"
    )


async def test_parameters_nare_sets_itself_are_unknown_to_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-withheld")

    result = await run("bash", {"command": "env", "credentials": True})

    assert result["is_error"] is True
    assert result["content"].startswith("bash: unknown parameter credentials")


async def test_a_valid_call_still_runs(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("one\ntwo\n", encoding="utf-8")

    result = await run("read", {"path": str(target), "offset": 1, "limit": 1})

    assert result["is_error"] is False
    assert result["content"].startswith("two")


async def test_a_bare_string_ask_keeps_its_existing_leniency() -> None:
    """ask reads a bare string as one question, and a turn of only asks ends
    blocked on that. Refusing it here would change when blocked is reported.
    """
    result = await run("ask", {"questions": "which file?"})

    assert result["is_error"] is False


async def test_mcp_tools_are_left_to_their_servers() -> None:
    from nare.mcp import ExternalTool

    async def call(args: dict[str, Any]) -> tuple[str, bool]:
        return f"got {sorted(args)}", False

    tool = ExternalTool(
        {
            "name": "srv__q",
            "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
        call,
    )
    result = await run(
        "srv__q",
        {"q": 3, "extra": True},
        tools=frozenset({"srv__q"}),
        external={"srv__q": tool},
    )

    assert result["is_error"] is False
    assert result["content"] == "got ['extra', 'q']"


@pytest.mark.parametrize("schema", TOOL_SCHEMAS, ids=lambda s: s["name"])
def test_every_built_in_schema_is_one_nare_can_check(schema: dict[str, Any]) -> None:
    from nare.schema import check_supported

    check_supported(schema["input_schema"])


async def test_a_null_optional_parameter_is_read_as_absent(tmp_path: Path) -> None:
    """Some models send every optional parameter, null when unused."""
    target = tmp_path / "a.txt"
    target.write_text("one\ntwo\n", encoding="utf-8")

    result = await run("read", {"path": str(target), "offset": None, "limit": None})

    assert result["is_error"] is False
    assert result["content"] == "one\ntwo"


async def test_a_null_required_parameter_is_still_an_error() -> None:
    result = await run("bash", {"command": None})

    assert result["content"] == "bash: command: expected string, got null"
