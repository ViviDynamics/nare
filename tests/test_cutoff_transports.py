"""The two rails name a cut-off reply the same way: a reply that repeats one
span is stopped and reported as repetition, a reply the vendor cut at its cap
is max_tokens, and a stream that loops is stopped early rather than read to
the end.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest

from nare.session import Message
from nare.transport.anthropic import AnthropicTransport
from nare.transport.openai import OpenAITransport

LOOP = " | 1"


def sse(payload: dict[str, Any], event: str | None = None) -> bytes:
    prefix = f"event: {event}\n" if event else ""
    return (prefix + "data: " + json.dumps(payload) + "\n\n").encode()


class Counted(httpx2.AsyncByteStream):
    """A response body that records how many chunks were read and whether it
    was closed, so a test can tell an early stop from a full read."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.pulled = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.pulled += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def openai_delta(
    content: str = "",
    *,
    reasoning: str | None = None,
    finish: str | None = None,
) -> bytes:
    delta: dict[str, Any] = {"content": content}
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    return sse({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})


OPENAI_USAGE = sse(
    {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 9}}
)
OPENAI_DONE = b"data: [DONE]\n\n"


def openai_transport(handler: Any, *, streaming: bool) -> OpenAITransport:
    return OpenAITransport(
        model="m",
        api_key="test",
        streaming=streaming,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


def user(text: str = "go") -> list[Message]:
    return [Message("user", [{"type": "text", "text": text}])]


def stream_handler(body: Counted) -> Any:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=body,
        )

    return handler


@pytest.mark.asyncio
async def test_an_openai_stream_that_loops_is_stopped_early_as_repetition() -> None:
    frames = [openai_delta('{"findings":[')]
    frames += [openai_delta(LOOP) for _ in range(5000)]
    frames += [openai_delta("", finish="length"), OPENAI_USAGE, OPENAI_DONE]
    body = Counted(frames)

    reply = await openai_transport(stream_handler(body), streaming=True).turn(
        user(), []
    )

    assert reply.stop_reason == "repetition"
    assert reply.content[-1]["text"].startswith('{"findings":[')
    assert reply.tool_calls == []
    assert body.pulled < 1000
    assert body.closed


@pytest.mark.asyncio
async def test_an_openai_stream_that_loops_in_reasoning_is_stopped_too() -> None:
    frames = [openai_delta("", reasoning="no, just finish. ") for _ in range(3000)]
    frames += [openai_delta("{}", finish="stop"), OPENAI_USAGE, OPENAI_DONE]
    body = Counted(frames)

    reply = await openai_transport(stream_handler(body), streaming=True).turn(
        user(), []
    )

    assert reply.stop_reason == "repetition"
    assert body.pulled < 1000


@pytest.mark.asyncio
async def test_a_stream_that_drops_on_a_loop_is_repetition_not_a_retry() -> None:
    # No final marker: a dropped connection would normally be resent, but a
    # resend of a looping reply loops again, so the reply is reported as the
    # loop it is.
    frames = [openai_delta('{"a":[')] + [openai_delta(LOOP) for _ in range(600)]
    body = Counted(frames)

    reply = await openai_transport(stream_handler(body), streaming=True).turn(
        user(), []
    )

    assert reply.stop_reason == "repetition"


@pytest.mark.asyncio
async def test_a_stream_length_cut_with_no_content_is_max_tokens() -> None:
    frames = [openai_delta("", finish="length"), OPENAI_USAGE, OPENAI_DONE]
    body = Counted(frames)

    reply = await openai_transport(stream_handler(body), streaming=True).turn(
        user(), []
    )

    assert reply.stop_reason == "max_tokens"
    assert reply.content == []


@pytest.mark.asyncio
async def test_a_complete_stream_that_is_not_looping_is_unchanged() -> None:
    frames = [openai_delta("a short answer.", finish="stop"), OPENAI_USAGE, OPENAI_DONE]

    reply = await openai_transport(
        stream_handler(Counted(frames)), streaming=True
    ).turn(user(), [])

    assert reply.stop_reason == "end_turn"
    assert reply.content[-1] == {"type": "text", "text": "a short answer."}


def openai_json(content: str, finish: str) -> Any:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": finish,
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            },
        )

    return handler


@pytest.mark.asyncio
async def test_a_finished_openai_reply_that_loops_is_repetition() -> None:
    reply = await openai_transport(
        openai_json('{"findings":[' + LOOP * 1000, "stop"), streaming=False
    ).turn(user(), [])

    assert reply.stop_reason == "repetition"


@pytest.mark.asyncio
async def test_an_openai_length_finish_is_max_tokens_without_streaming() -> None:
    reply = await openai_transport(openai_json("", "length"), streaming=False).turn(
        user(), []
    )

    assert reply.stop_reason == "max_tokens"


@pytest.mark.asyncio
async def test_a_valid_openai_reply_with_eight_identical_items_is_unchanged() -> None:
    content = json.dumps({"items": ["a"] * 8})

    reply = await openai_transport(openai_json(content, "stop"), streaming=False).turn(
        user(), []
    )

    assert reply.stop_reason == "end_turn"


def anthropic_events(*events: dict[str, Any]) -> list[bytes]:
    return [sse(event, event["type"]) for event in events]


ANTHROPIC_START: dict[str, Any] = {
    "type": "message_start",
    "message": {
        "id": "m",
        "type": "message",
        "role": "assistant",
        "model": "m",
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 7,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    },
}


def anthropic_text(text: str) -> dict[str, Any]:
    return {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": text},
    }


def anthropic_transport(handler: Any, *, streaming: bool) -> AnthropicTransport:
    return AnthropicTransport(
        model="m",
        api_key="test",
        streaming=streaming,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


@pytest.mark.asyncio
async def test_an_anthropic_stream_that_loops_is_stopped_early_as_repetition() -> None:
    events = [
        ANTHROPIC_START,
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
    ]
    events += [anthropic_text('{"findings":[')]
    events += [anthropic_text(LOOP) for _ in range(5000)]
    events += [{"type": "content_block_stop", "index": 0}, {"type": "message_stop"}]
    raw = [sse(e, e["type"]) for e in events]
    body = Counted(raw)

    reply = await anthropic_transport(stream_handler(body), streaming=True).turn(
        user(), []
    )

    assert reply.stop_reason == "repetition"
    assert reply.content == [
        {"type": "text", "text": reply.content[0]["text"]},
    ]
    assert reply.content[0]["text"].startswith('{"findings":[')
    assert reply.usage.input == 7
    assert reply.tool_calls == []
    assert body.pulled < 1000
    assert body.closed


@pytest.mark.asyncio
async def test_an_anthropic_finished_reply_that_loops_is_repetition() -> None:
    message = {
        "id": "m",
        "type": "message",
        "role": "assistant",
        "model": "m",
        "content": [{"type": "text", "text": LOOP * 1000}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 3, "output_tokens": 4},
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=message)

    reply = await anthropic_transport(handler, streaming=False).turn(user(), [])

    assert reply.stop_reason == "repetition"


@pytest.mark.asyncio
async def test_an_anthropic_max_tokens_cut_is_max_tokens() -> None:
    message: dict[str, Any] = {
        "id": "m",
        "type": "message",
        "role": "assistant",
        "model": "m",
        "content": [],
        "stop_reason": "max_tokens",
        "stop_sequence": None,
        "usage": {"input_tokens": 3, "output_tokens": 4},
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=message)

    reply = await anthropic_transport(handler, streaming=False).turn(user(), [])

    assert reply.stop_reason == "max_tokens"
    assert reply.content == []
