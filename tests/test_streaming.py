from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2
import pytest

from nare.session import Message
from nare.transport.anthropic import AnthropicTransport
from nare.transport.openai import OpenAITransport


def sse(payload: dict[str, Any], event: str | None = None) -> bytes:
    prefix = f"event: {event}\n" if event else ""
    return (prefix + "data: " + json.dumps(payload) + "\n\n").encode()


class Bytes(httpx2.AsyncByteStream):
    def __init__(self, chunks: list[bytes], gate: asyncio.Event | None = None) -> None:
        self.chunks = chunks
        self.gate = gate
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for index, chunk in enumerate(self.chunks):
            if index and self.gate is not None:
                await self.gate.wait()
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def openai_chunks() -> list[bytes]:
    return [
        sse(
            {
                "choices": [
                    {"index": 0, "delta": {"content": "hello "}, "finish_reason": None}
                ]
            }
        ),
        sse(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"reasoning_content": "think "},
                        "finish_reason": None,
                    }
                ]
            }
        ),
        sse(
            {
                "choices": [
                    {"index": 0, "delta": {"content": "world"}, "finish_reason": "stop"}
                ]
            }
        ),
        sse(
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "prompt_tokens_details": {"cached_tokens": 3},
                },
            }
        ),
        b"data: [DONE]\n\n",
    ]


def anthropic_chunks() -> list[bytes]:
    raw: list[dict[str, Any]] = [
        {
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
                    "cache_read_input_tokens": 3,
                    "cache_creation_input_tokens": 0,
                },
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "hello "},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "world"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]
    return [sse(p, p["type"]) for p in raw]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_stream_normalizes_final_reply_and_usage(provider: str) -> None:
    data = Bytes(openai_chunks() if provider == "openai" else anthropic_chunks())
    seen: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(
            200,
            headers={
                "content-type": "text/event-stream",
                "x-litellm-response-cost": "0.012",
            },
            stream=data,
        )

    cls = OpenAITransport if provider == "openai" else AnthropicTransport
    transport = cls(
        model="m",
        api_key="test",
        streaming=True,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    reply = await transport.turn(
        [Message("user", [{"type": "text", "text": "hi"}])], []
    )
    assert reply.content[-1] == {"type": "text", "text": "hello world"}
    assert reply.usage.input == 7
    assert reply.usage.output == 5
    assert reply.usage.cache_read == 3
    assert reply.cost == 0.012
    assert reply.stop_reason == "end_turn"
    assert seen[0]["stream"] is True
    assert data.closed


@pytest.mark.asyncio
async def test_openai_delta_arrives_before_final_chunk() -> None:
    from nare.transport import Delta, Reply

    gate = asyncio.Event()
    data = Bytes(openai_chunks(), gate)
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(
                200, headers={"content-type": "text/event-stream"}, stream=data
            )
        )
    )
    transport = OpenAITransport(
        model="m", api_key="test", streaming=True, http_client=client
    )
    stream = transport.stream_turn([], [])
    delta = await asyncio.wait_for(anext(stream), 1)
    assert isinstance(delta, Delta)
    assert delta.text == "hello "
    assert not gate.is_set()
    gate.set()
    remaining = [item async for item in stream]
    assert isinstance(remaining[-1], Reply)


def test_anthropic_streaming_removes_nonstreaming_output_ceiling() -> None:
    transport = AnthropicTransport(
        model="m", api_key="test", max_tokens=50000, streaming=True
    )
    assert transport.max_tokens == 50000


@pytest.mark.asyncio
async def test_loop_progress_before_finalization_and_done_wins() -> None:
    from nare.loop import run
    from nare.session import new_session

    gate = asyncio.Event()
    data = Bytes(openai_chunks(), gate)
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, stream=data)
        )
    )
    transport = OpenAITransport(
        model="m", api_key="test", streaming=True, http_client=client
    )
    session = new_session("hi")
    stream = run(session, transport=transport, context_window=1000, budget_tokens=12)
    async with asyncio.timeout(1):
        while True:
            event = await anext(stream)
            if event.text == "hello ":
                break
    assert not gate.is_set()
    assert session.turns == 0
    gate.set()
    events = [event async for event in stream]
    assert session.status == "done"
    assert session.usage.total_tokens == 15
    assert len([e for e in events if e.type == "cost"]) == 1
    assert session.messages[-1].content[-1]["text"] == "hello world"


def test_live_redaction_handles_fragmented_credentials() -> None:
    from nare.events import LiveText

    live = LiveText()
    output = (
        "".join(
            live.feed(part)
            for part in [
                "hello ",
                "Author",
                "ization",
                ": ",
                "Bearer ",
                "private",
                "value ",
                "next",
            ]
        )
        + live.finish()
    )
    assert output.startswith("hello ")
    assert "private" not in output
    assert "[redacted]" in output


@pytest.mark.asyncio
async def test_dropped_stream_keeps_transcript_without_dispatch() -> None:
    from nare.loop import run
    from nare.session import new_session

    data = Bytes(openai_chunks()[:1])
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, stream=data)
        )
    )
    transport = OpenAITransport(
        model="m", api_key="test", streaming=True, http_client=client
    )
    session = new_session("hi")
    events = [
        event async for event in run(session, transport=transport, context_window=1000)
    ]
    assert session.status == "error"
    assert session.turns == 0
    assert len(session.messages) == 1
    assert session.usage.total_tokens == 0
    assert any(e.text == "hello " for e in events)
    assert data.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_stream_missing_final_usage_is_failure(provider: str) -> None:
    chunks = openai_chunks() if provider == "openai" else anthropic_chunks()
    if provider == "openai":
        chunks[-2] = sse({"choices": [], "usage": {}})
    else:
        raw = json.loads(chunks[-2].decode().split("data: ")[1])
        raw["usage"] = {}
        chunks[-2] = sse(raw, raw["type"])
    data = Bytes(chunks)
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(
                200, headers={"content-type": "text/event-stream"}, stream=data
            )
        )
    )
    cls = OpenAITransport if provider == "openai" else AnthropicTransport
    transport = cls(model="m", api_key="test", streaming=True, http_client=client)
    with pytest.raises(RuntimeError, match="usage"):
        await transport.turn([], [])
    assert data.closed


@pytest.mark.asyncio
async def test_split_tool_turn_enforces_budget_before_next_model_call(
    tmp_path: Path,
) -> None:
    from nare.loop import run
    from nare.session import new_session

    source = tmp_path / "source.txt"
    source.write_text("tool evidence")
    args = json.dumps({"path": str(source)})
    chunks = [
        sse(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "c",
                                    "function": {"name": "re", "arguments": args[:8]},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            }
        ),
        sse(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"name": "ad", "arguments": args[8:]},
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ),
        *openai_chunks()[-2:],
    ]
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        assert calls == 1, "model call after exhausted budget"
        return httpx2.Response(200, stream=Bytes(chunks))

    transport = OpenAITransport(
        model="m",
        api_key="test",
        streaming=True,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    session = new_session("read")
    events = [
        event
        async for event in run(
            session, transport=transport, budget_tokens=12, context_window=1000
        )
    ]
    assert session.status == "error"
    assert session.stop_reason == "budget"
    assert session.usage.total_tokens == 15
    assert session.messages[-1].content[0]["content"] == "tool evidence"
    assert any(e.type == "tool_use" and e.text == "read" for e in events)


@pytest.mark.asyncio
async def test_early_close_cancels_live_turn_and_closes_connection() -> None:
    from nare.loop import run
    from nare.session import new_session

    gate = asyncio.Event()
    data = Bytes(openai_chunks(), gate)
    transport = OpenAITransport(
        model="m",
        api_key="test",
        streaming=True,
        http_client=httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, stream=data)
            )
        ),
    )
    session = new_session("hi")
    stream = run(session, transport=transport, context_window=1000)
    while (await anext(stream)).text != "hello ":
        pass
    await stream.aclose()
    assert data.closed
    assert session.turns == 0


@pytest.mark.asyncio
async def test_stream_and_nonstream_have_identical_session() -> None:
    import copy

    from nare.loop import run
    from nare.session import new_session

    session = new_session("hi")
    other = copy.deepcopy(session)
    streamed = OpenAITransport(
        model="m",
        api_key="test",
        streaming=True,
        http_client=httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, stream=Bytes(openai_chunks()))
            )
        ),
    )
    plain_body = {
        "choices": [
            {
                "message": {"content": "hello world", "reasoning_content": "think "},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 3},
        },
    }
    plain = OpenAITransport(
        model="m",
        api_key="test",
        http_client=httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, json=plain_body)
            )
        ),
    )
    _ = [e async for e in run(session, transport=streamed, context_window=1000)]
    _ = [e async for e in run(other, transport=plain, context_window=1000)]
    assert session == other


@pytest.mark.asyncio
async def test_anthropic_thinking_and_split_tool_arguments() -> None:
    from nare.transport import Delta, Reply

    raw: list[dict[str, Any]] = [
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "working "},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "sig"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "tool_use",
                "id": "a",
                "name": "read",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"pa'},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {
                "type": "input_json_delta",
                "partial_json": 'th":"/tmp/example"}',
            },
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]
    chunks = [anthropic_chunks()[0], *[sse(p, str(p["type"])) for p in raw]]
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(
                200, headers={"content-type": "text/event-stream"}, stream=Bytes(chunks)
            )
        )
    )
    transport = AnthropicTransport(
        model="m", api_key="test", streaming=True, http_client=client
    )
    stream = transport.stream_turn([], [])
    first = await anext(stream)
    assert isinstance(first, Delta)
    assert first.kind == "thinking"
    assert first.text == "working "
    remaining = [item async for item in stream]
    reply = remaining[-1]
    assert isinstance(reply, Reply)
    assert reply.tool_calls[0].args == {"path": "/tmp/example"}
    assert reply.content[0] == {
        "type": "thinking",
        "thinking": "working ",
        "signature": "sig",
    }
    assert reply.usage.total_tokens == 15


@pytest.mark.parametrize(
    "credential",
    [
        "sk-ant-" + "a" * 25,
        "ghp_" + "b" * 25,
        "OPENAI_API_KEY=" + "c" * 25,
        '"password": "privatevalue"',
        "Authorization: Bearer privatevalue",
        'password: " privatevalue',
        "password: secretvalue",
        "Authorization: Bearer tokenvalue",
        "password: authvalue password: credentialvalue",
    ],
)
def test_live_redaction_is_safe_at_every_chunk_boundary(credential: str) -> None:
    from nare.events import LiveText, redact

    live = LiveText()
    assert live.feed(credential + " ") + live.finish() == redact(credential + " ")
    for boundary in range(1, len(credential)):
        live = LiveText()
        output = (
            live.feed("start " + credential[:boundary])
            + live.feed(credential[boundary:] + " end ")
            + live.finish()
        )
        assert output == redact("start " + credential + " end ")


@pytest.mark.asyncio
async def test_incomplete_anthropic_tool_json_never_overwrites_file(
    tmp_path: Path,
) -> None:
    from nare.loop import run
    from nare.session import new_session

    target = tmp_path / "keep.txt"
    target.write_text("original")
    partial = json.dumps({"path": str(target), "content": "overwrite"})[:-1]
    raw: list[dict[str, Any]] = [
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "a",
                "name": "write",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": partial},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]
    data = Bytes([anthropic_chunks()[0], *[sse(p, str(p["type"])) for p in raw]])
    transport = AnthropicTransport(
        model="m",
        api_key="test",
        streaming=True,
        http_client=httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=data
                )
            )
        ),
    )
    session = new_session("write")
    _ = [
        event
        async for event in run(
            session, transport=transport, budget_tokens=12, context_window=1000
        )
    ]
    assert target.read_text() == "original"
    assert session.messages[-1].content[0]["is_error"]
    assert "JSON" in session.messages[-1].content[0]["content"]
    assert session.usage.total_tokens == 15


@pytest.mark.asyncio
async def test_anthropic_initial_block_text_is_live() -> None:
    from nare.transport import Delta

    gate = asyncio.Event()
    chunks = anthropic_chunks()
    start = json.loads(chunks[1].decode().split("data: ")[1])
    start["content_block"]["text"] = "hello "
    data = Bytes([chunks[0] + sse(start, "content_block_start"), *chunks[3:]], gate)
    transport = AnthropicTransport(
        model="m",
        api_key="test",
        streaming=True,
        http_client=httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=data
                )
            )
        ),
    )
    stream = transport.stream_turn([], [])
    try:
        delta = await asyncio.wait_for(anext(stream), 1)
        assert isinstance(delta, Delta)
        assert delta.text == "hello "
        assert not gate.is_set()
    finally:
        await stream.aclose()
    assert data.closed
