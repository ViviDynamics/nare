"""Schema-constrained answers on the anthropic rail (issue 144), no key."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
from anthropic import transform_schema

from nare.cli import main
from nare.contract import describe
from nare.loop import check_structured_output
from nare.session import Message
from nare.transport import StructuredTransport
from nare.transport.anthropic import AnthropicTransport, response_format_from
from test_streaming import Bytes, anthropic_chunks

PERSON: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
    "additionalProperties": False,
}
TOOL = {"name": "read", "description": "Read a file.", "input_schema": {}}
HI = [Message("user", [{"type": "text", "text": "hi"}])]

ANSWER: dict[str, Any] = {
    "id": "m",
    "type": "message",
    "role": "assistant",
    "model": "m",
    "content": [{"type": "text", "text": '{"name": "ada"}'}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 7, "output_tokens": 5},
}


def rail(seen: list[dict[str, Any]], *, streaming: bool = False, **kw: Any) -> Any:
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        if streaming:
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=Bytes(anthropic_chunks()),
            )
        return httpx2.Response(200, json=ANSWER)

    return AnthropicTransport(
        model="m",
        api_key="test",
        streaming=streaming,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        **kw,
    )


# mypy fails here if the rail drifts away from the structured protocol.
_check: StructuredTransport = AnthropicTransport(model="m", api_key="k")


def test_the_anthropic_rail_declares_structured_output() -> None:
    assert AnthropicTransport.supports_structured_output is True


@pytest.mark.parametrize("streaming", [False, True])
async def test_a_constrained_turn_sends_the_format_and_no_tools(
    streaming: bool,
) -> None:
    seen: list[dict[str, Any]] = []
    await rail(seen, streaming=streaming).turn(HI, [TOOL], response_schema=PERSON)
    assert seen[0]["output_config"] == {
        "format": {"type": "json_schema", "schema": transform_schema(PERSON)}
    }
    assert not seen[0].get("tools")


@pytest.mark.parametrize("streaming", [False, True])
async def test_an_unconstrained_turn_is_unchanged(streaming: bool) -> None:
    seen: list[dict[str, Any]] = []
    await rail(seen, streaming=streaming).turn(HI, [TOOL])
    assert "output_config" not in seen[0]
    assert seen[0]["tools"][0]["name"] == "read"


async def test_the_format_sits_beside_effort() -> None:
    seen: list[dict[str, Any]] = []
    await rail(seen, effort="low").turn(HI, [], response_schema=PERSON)
    assert seen[0]["output_config"]["effort"] == "low"
    assert seen[0]["output_config"]["format"]["type"] == "json_schema"
    # A turn that is not constrained still sends effort alone.
    await rail(seen, effort="low").turn(HI, [])
    assert seen[1]["output_config"] == {"effort": "low"}


def test_the_wire_schema_is_the_sdk_transform_of_the_callers() -> None:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "score": {"type": "integer", "minimum": 1, "maximum": 5},
            "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            "kind": {"oneOf": [{"type": "string"}, {"type": "null"}]},
        },
        "required": ["score"],
    }
    before = json.dumps(schema)
    sent = response_format_from(schema)
    assert sent == {"type": "json_schema", "schema": transform_schema(schema)}
    wire = sent["schema"]
    assert wire["additionalProperties"] is False
    assert "minimum" not in wire["properties"]["score"]
    assert "anyOf" in wire["properties"]["kind"]
    # The caller's schema is not touched: nare validates against it.
    assert json.dumps(schema) == before


@pytest.mark.parametrize(
    ("schema", "complaint"),
    [
        (
            {"type": "object", "properties": {"x": {}}},
            "properties.x",
        ),
        (
            {"type": "object", "additionalProperties": {"type": "string"}},
            "map",
        ),
        (
            {"type": "object", "additionalProperties": True},
            "map",
        ),
        (
            {
                "type": "object",
                "properties": {"a": {"$ref": "#/definitions/A"}},
                "definitions": {"A": {"type": "string"}},
            },
            "$defs",
        ),
        (
            {
                "type": "object",
                "properties": {"a": {"allOf": [{"$ref": "#/$defs/A"}]}},
                "$defs": {"A": {"type": "string"}},
            },
            "allOf",
        ),
        (
            {"type": "object", "properties": {"a": {"type": "object", "enum": [{}]}}},
            "enum",
        ),
        (
            {
                "type": "object",
                "properties": {f"p{n}": {"type": "string"} for n in range(25)},
            },
            "24 optional",
        ),
        (
            {
                "type": "object",
                "properties": {
                    f"p{n}": {"anyOf": [{"type": "string"}, {"type": "null"}]}
                    for n in range(17)
                },
                "required": [f"p{n}" for n in range(17)],
            },
            "16",
        ),
    ],
)
def test_a_schema_anthropic_cannot_enforce_is_refused(
    schema: dict[str, Any], complaint: str
) -> None:
    with pytest.raises(ValueError, match="anthropic") as raised:
        check_structured_output(
            AnthropicTransport(model="m", api_key="k"), schema, "json_schema"
        )
    assert complaint in str(raised.value)


@pytest.mark.parametrize(
    "schema",
    [
        PERSON,
        {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"$ref": "#/$defs/Item"}},
                "count": {"type": "integer", "minimum": 0},
            },
            "required": ["items", "count"],
            "$defs": {
                "Item": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "maxLength": 80}},
                    "required": ["name"],
                }
            },
        },
        {
            "type": "object",
            "properties": {f"p{n}": {"type": "string"} for n in range(24)},
        },
    ],
)
def test_a_schema_the_transform_handles_passes(schema: dict[str, Any]) -> None:
    check_structured_output(
        AnthropicTransport(model="m", api_key="k"), schema, "json_schema"
    )


@pytest.fixture
def schema_file(tmp_path: Path) -> Path:
    path = tmp_path / "person.schema.json"
    path.write_text(json.dumps(PERSON), encoding="utf-8")
    return path


def test_a_tool_free_cli_run_sends_the_format(
    schema_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[dict[str, Any]] = []
    argv = ["run", "--yes", "--jsonl", "--provider", "anthropic", "--tools", "none"]
    argv += ["--schema", str(schema_file), "--structured-output", "json_schema", "go"]
    assert main(argv, transport=rail(seen)) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["status"] == "done"
    assert result["output"] == {"name": "ada"}
    assert seen[0]["output_config"]["format"] == {
        "type": "json_schema",
        "schema": transform_schema(PERSON),
    }


def test_a_refused_schema_exits_two_before_any_request(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "map.schema.json"
    path.write_text(
        json.dumps({"type": "object", "additionalProperties": {"type": "string"}})
    )
    seen: list[dict[str, Any]] = []
    argv = ["run", "--yes", "--jsonl", "--provider", "anthropic", "--tools", "none"]
    argv += ["--schema", str(path), "--structured-output", "json_schema", "go"]
    assert main(argv, transport=rail(seen)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "map" in captured.err
    assert seen == []


def test_the_contract_lists_every_constraining_provider() -> None:
    structured = describe()["structured_output"]
    assert structured["providers"] == ["anthropic", "bedrock", "openai"]
    assert structured["anthropic_not_enforced_at_decode"] == [
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "pattern",
        "maxItems",
        "minItems above 1",
        "uniqueItems",
        "const",
        "oneOf exclusivity",
        "additionalProperties other than false",
    ]
