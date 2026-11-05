"""The turn-limit summary keeps the turn's tool set (issue 100).

A signed thinking block binds to the tools array of the request that made it,
so a summary request that rebuilt the tool set would be rejected. The summary
keeps the tools, sends tool_choice none, and refuses any call anyway.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from fake_provider import FakeProvider, StructuredProvider, text_reply, tool_reply
from nare.loop import SUMMARY_REFUSED, run, step
from nare.session import Message, new_session
from nare.tools import Policy, approve_all
from nare.transport.anthropic import AnthropicTransport
from nare.transport.openai import OpenAITransport
from test_streaming import Bytes, anthropic_chunks

HI = [Message("user", [{"type": "text", "text": "hi"}])]
TOOL = {
    "name": "read",
    "description": "Read a file.",
    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
}
PERSON: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
    "additionalProperties": False,
}


def anthropic_answer() -> dict[str, Any]:
    return {
        "id": "m",
        "type": "message",
        "role": "assistant",
        "model": "m",
        "content": [{"type": "text", "text": "done"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 7, "output_tokens": 5},
    }


def anthropic(seen: list[dict[str, Any]], *, streaming: bool) -> AnthropicTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        if streaming:
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=Bytes(anthropic_chunks()),
            )
        return httpx2.Response(200, json=anthropic_answer())

    return AnthropicTransport(
        model="m",
        api_key="test",
        streaming=streaming,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


def openai_completion() -> dict[str, Any]:
    return {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "done"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def openai(seen: list[dict[str, Any]]) -> OpenAITransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(200, json=openai_completion())

    return OpenAITransport(
        model="m",
        api_key="k",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


@pytest.mark.parametrize("streaming", [False, True])
async def test_anthropic_tool_choice_none_keeps_the_tools_on_the_wire(
    streaming: bool,
) -> None:
    seen: list[dict[str, Any]] = []
    transport = anthropic(seen, streaming=streaming)

    if streaming:
        async for _ in transport.stream_turn(HI, [TOOL], tool_choice="none"):
            pass
    else:
        await transport.turn(HI, [TOOL], tool_choice="none")

    assert seen[0]["tool_choice"] == {"type": "none"}
    assert [tool["name"] for tool in seen[0]["tools"]] == ["read"]


async def test_anthropic_without_a_tool_choice_is_the_old_request() -> None:
    seen: list[dict[str, Any]] = []

    await anthropic(seen, streaming=False).turn(HI, [TOOL])
    await anthropic(seen, streaming=False).turn(HI, [])

    assert "tool_choice" not in seen[0]
    # A choice needs tools to choose among, so none is sent without them.
    assert "tool_choice" not in seen[1]


async def test_openai_tool_choice_none_keeps_the_tools_on_the_wire() -> None:
    seen: list[dict[str, Any]] = []

    await openai(seen).turn(HI, [TOOL], tool_choice="none")

    assert seen[0]["tool_choice"] == "none"
    assert seen[0]["tools"][0]["function"]["name"] == "read"


async def test_openai_without_a_tool_choice_is_the_old_request() -> None:
    seen: list[dict[str, Any]] = []

    await openai(seen).turn(HI, [TOOL])

    assert "tool_choice" not in seen[0]


async def test_summary_of_a_schema_run_keeps_tools_and_is_not_constrained(
    tmp_path: Path,
) -> None:
    (tmp_path / "NOTES.txt").write_text("ada\n", encoding="utf-8")
    provider = StructuredProvider(
        [
            tool_reply("read", {"path": "NOTES.txt"}),
            text_reply("Read the notes; nothing remains."),
        ]
    )
    session = new_session("who?")
    policy = Policy(tools=frozenset({"read"}), root=tmp_path)

    _ = [
        event
        async for event in run(
            session,
            transport=provider,
            policy=policy,
            schema=PERSON,
            structured_output="json_schema",
            max_turns=1,
        )
    ]

    # The summary is free text on the same tools: a constrained turn would
    # withhold them, which is the change the vendor rejects.
    assert provider.constraints == [None, None]
    assert provider.calls[-1][1] == provider.calls[0][1] != []
    assert provider.choices == [None, "none"]
    # A free-text summary is not an answer, so it spends no schema correction.
    assert session.schema_corrections == 0
    assert session.status == "error"
    assert session.stop_reason == "max_turns"


async def test_a_summary_with_no_tools_sends_no_tool_choice() -> None:
    provider = FakeProvider([text_reply("nothing to call")])
    session = new_session("go")

    policy = Policy(tools=frozenset())
    await step(session, provider, approve_all, policy, summary=True)

    assert provider.calls[-1][1] == []
    assert provider.choices == [None]


async def test_a_call_on_the_summary_turn_is_refused_not_run(tmp_path: Path) -> None:
    target = tmp_path / "must-not-exist"
    provider = FakeProvider(
        [tool_reply("write", {"path": str(target), "content": "bad"})]
    )
    session = new_session("go")
    policy = Policy(tools=frozenset({"write"}), root=tmp_path)

    await step(session, provider, approve_all, policy, summary=True)

    assert not target.exists()
    result = session.messages[-1].content[0]
    assert result["is_error"] is True
    assert result["content"] == SUMMARY_REFUSED
