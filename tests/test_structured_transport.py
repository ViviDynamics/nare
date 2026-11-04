"""Schema-constrained answer turns on the OpenAI rail: what goes on the wire."""

from __future__ import annotations

import json
from typing import Any

import httpx2

from nare.session import Message
from nare.transport import Reply, StructuredTransport
from nare.transport.anthropic import AnthropicTransport
from nare.transport.openai import OpenAITransport

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
    "additionalProperties": False,
}
TOOL = {
    "name": "read",
    "description": "Read a file.",
    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
}
HI = [Message("user", [{"type": "text", "text": "hi"}])]


def completion() -> dict[str, Any]:
    return {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": '{"name": "ada"}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def stream_chunks() -> list[bytes]:
    frames = [
        {"choices": [{"index": 0, "delta": {"content": '{"name": '}}]},
        {"choices": [{"index": 0, "delta": {"content": '"ada"}'}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
    ]
    return [("data: " + json.dumps(f) + "\n\n").encode() for f in frames] + [
        b"data: [DONE]\n\n"
    ]


def transport(seen: list[dict[str, Any]], *, streaming: bool) -> OpenAITransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        if streaming:
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=b"".join(stream_chunks()),
            )
        return httpx2.Response(200, json=completion())

    return OpenAITransport(
        model="m",
        api_key="k",
        streaming=streaming,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


# mypy fails here if the rail drifts away from the structured protocol.
_check: StructuredTransport = OpenAITransport(model="m", api_key="k")

EXPECTED_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "answer", "schema": SCHEMA, "strict": True},
}


def test_both_rails_declare_structured_output() -> None:
    assert OpenAITransport.supports_structured_output is True
    # The anthropic rail's wire shape is in test_structured_anthropic.py.
    assert AnthropicTransport.supports_structured_output is True


async def test_a_constrained_turn_sends_the_schema_and_no_tools() -> None:
    seen: list[dict[str, Any]] = []

    reply = await transport(seen, streaming=False).turn(
        HI, [TOOL], response_schema=SCHEMA
    )

    assert seen[0]["response_format"] == EXPECTED_FORMAT
    assert "tools" not in seen[0]
    assert reply.content[-1] == {"type": "text", "text": '{"name": "ada"}'}


async def test_a_streamed_constrained_turn_sends_the_schema_and_no_tools() -> None:
    seen: list[dict[str, Any]] = []

    items = [
        item
        async for item in transport(seen, streaming=True).stream_turn(
            HI, [TOOL], response_schema=SCHEMA
        )
    ]

    assert seen[0]["response_format"] == EXPECTED_FORMAT
    assert "tools" not in seen[0]
    assert seen[0]["stream"] is True
    assert isinstance(items[-1], Reply)
    assert items[-1].content[-1] == {"type": "text", "text": '{"name": "ada"}'}


async def test_streaming_turn_forwards_the_schema_too() -> None:
    seen: list[dict[str, Any]] = []

    await transport(seen, streaming=True).turn(HI, [TOOL], response_schema=SCHEMA)

    assert seen[0]["response_format"] == EXPECTED_FORMAT
    assert "tools" not in seen[0]


async def test_an_unconstrained_turn_is_exactly_the_old_request() -> None:
    plain: list[dict[str, Any]] = []
    explicit: list[dict[str, Any]] = []

    await transport(plain, streaming=False).turn(HI, [TOOL])
    await transport(explicit, streaming=False).turn(HI, [TOOL], response_schema=None)

    assert "response_format" not in plain[0]
    assert plain[0]["tools"][0]["function"]["name"] == "read"
    assert plain == explicit


async def test_a_generated_schema_is_forwarded_unchanged() -> None:
    """References and bounds go to the backend as written: nare neither
    flattens nor drops what the provider is asked to enforce, so a provider
    limit surfaces as the provider's own refusal.
    """
    generated: dict[str, Any] = {
        "$defs": {"Name": {"type": "string", "maxLength": 3}},
        "type": "object",
        "properties": {
            "name": {"$ref": "#/$defs/Name"},
            "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
        "required": ["name", "note"],
        "additionalProperties": False,
    }
    sent = json.loads(json.dumps(generated))
    seen: list[dict[str, Any]] = []

    await transport(seen, streaming=False).turn(HI, [TOOL], response_schema=sent)

    assert seen[0]["response_format"]["json_schema"]["schema"] == generated
    assert sent == generated
