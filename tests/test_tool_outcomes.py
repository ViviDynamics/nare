"""Outcome consumers distinguish protocol refusals without parsing tool prose."""

from __future__ import annotations

from typing import Any

import pytest

from fake_provider import FakeProvider
from nare import Tool, ToolError, new_session, run
from nare.caller_tools import as_external
from test_caller_tools import call_reply, final_reply, result_of


@pytest.mark.parametrize(
    "scenario", ["schema", "coded", "expected", "unexpected", "ok"]
)
async def test_typed_caller_outcome_does_not_enter_provider_blocks(
    scenario: str,
) -> None:
    called = []

    async def lookup(args: dict[str, Any]) -> str:
        called.append(args)
        if scenario == "coded":
            raise ToolError("read the next batch", code="outside_catalog")
        if scenario == "expected":
            raise ToolError("try again")
        if scenario == "unexpected":
            raise RuntimeError("private failure text")
        return "record"

    tool = Tool(
        "lookup",
        "Fetch a record",
        {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
        lookup,
    )
    session = new_session("look up a record")
    provider = FakeProvider(
        [
            call_reply("lookup", {"id": "wrong" if scenario == "schema" else 1}),
            final_reply(),
        ]
    )
    events = [event async for event in run(session, transport=provider, tools=[tool])]
    result = next(
        event.detail["tool_result"] for event in events if "tool_result" in event.detail
    )
    expected = {
        "schema": {"kind": "invalid_arguments"},
        "coded": {"kind": "caller_error", "code": "outside_catalog"},
        "expected": {"kind": "caller_error"},
        "unexpected": {"kind": "unexpected_error"},
        "ok": {"kind": "ok"},
    }
    assert result["outcome"] == expected[scenario]
    assert bool(called) is (scenario != "schema")
    wire = result_of(session)
    assert set(wire) == {"type", "tool_use_id", "content", "is_error"}
    assert wire["is_error"] is (scenario != "ok")
    assert "private" not in str(result["outcome"])


@pytest.mark.parametrize("code", ["", "a b", "secret/value", "a" * 65])
def test_tool_error_code_refuses_unbounded_or_freeform_metadata(code: str) -> None:
    with pytest.raises(ValueError):
        ToolError("message", code=code)


@pytest.mark.parametrize("args", [(), ("missing",), ("missing", 17)])
@pytest.mark.parametrize("code", [None, "missing_record"])
async def test_tool_error_preserves_exception_arguments(
    args: tuple[object, ...], code: str | None
) -> None:
    async def lookup(_: dict[str, Any]) -> str:
        raise ToolError(*args, code=code)

    external = as_external(Tool("lookup", "Look up", {"type": "object"}, lookup))
    assert external.classified_call is not None
    result = await external.classified_call({})
    assert result.failed
    assert result.text == str(Exception(*args))
    assert result.outcome.kind == "caller_error"
    assert result.outcome.code == code
    assert await external.call({}) == (str(Exception(*args)), True)
