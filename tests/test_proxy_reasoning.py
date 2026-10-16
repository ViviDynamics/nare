from __future__ import annotations

import logging
from typing import Any

import httpx2
import pytest

from nare.transport.anthropic import AnthropicTransport
from test_streaming import Bytes, anthropic_chunks, sse
from test_transport import CANNED_BODY

WARNING = (
    "reasoning is translated by a proxy; on this route LiteLLM can drop "
    "words or whole answers. Use --provider openai for this model."
)


def response_blocks(signature: str | None) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [{"type": "text", "text": "hello world"}]
    if signature is not None:
        blocks.extend(
            {"type": "thinking", "thinking": "consider this", "signature": signature}
            for _ in range(2)
        )
    return blocks


def reasoning_chunks(signature: str | None) -> list[bytes]:
    chunks = anthropic_chunks()
    thinking: list[bytes] = []
    for index, block in enumerate(response_blocks(signature)[1:], start=1):
        thinking.extend(
            [
                sse(
                    {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {**block, "signature": ""},
                    },
                    "content_block_start",
                ),
                sse(
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "signature_delta", "signature": signature},
                    },
                    "content_block_delta",
                ),
                sse(
                    {"type": "content_block_stop", "index": index}, "content_block_stop"
                ),
            ]
        )
    return chunks[:-2] + thinking + chunks[-2:]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("signature", ["", "signed-by-anthropic", None])
async def test_proxy_reasoning_warning_once_per_transport(
    streaming: bool, signature: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if streaming:
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=Bytes(reasoning_chunks(signature)),
            )
        return httpx2.Response(
            200, json={**CANNED_BODY, "content": response_blocks(signature)}
        )

    def transport() -> AnthropicTransport:
        return AnthropicTransport(
            model="m",
            api_key="test",
            streaming=streaming,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        )

    with caplog.at_level(logging.WARNING, logger="nare.transport.anthropic"):
        first = transport()
        for _ in range(3):
            reply = await first.turn([], [])
            assert reply.content == response_blocks(signature)
        expected = 1 if signature == "" else 0
        assert caplog.messages == [WARNING] * expected
        await transport().turn([], [])
        assert caplog.messages == [WARNING] * (expected * 2)
