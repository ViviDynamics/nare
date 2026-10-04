from __future__ import annotations

from typing import Any

import httpx2
import pytest

from nare.session import Message
from nare.transport.anthropic import AnthropicTransport
from nare.transport.openai import OpenAITransport
from test_transport import CANNED_BODY


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize(
    "header,value,want",
    [
        ("x-litellm-response-cost", "0.25", 0.25),
        ("x-litellm-response-cost-original", "0", 0),
        ("x-litellm-response-cost", "nan", None),
    ],
)
async def test_both_rails_read_raw_response_cost(
    provider: str, header: str, value: str, want: float | None
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = (
            CANNED_BODY
            if provider == "anthropic"
            else {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        )
        return httpx2.Response(200, json=body, headers={header: value})

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    factory = AnthropicTransport if provider == "anthropic" else OpenAITransport
    transport = factory(model="m", api_key="offline", http_client=client)
    reply = await transport.turn([Message("user", [])], [])
    assert reply.cost == want


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("base", ["https://offline.test", "https://offline.test/v1/"])
async def test_backend_window_discovery(provider: str, base: str) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "data": [
                    {"model_name": "other", "model_info": {"max_input_tokens": 1}},
                    {"model_name": "m", "model_info": {"max_input_tokens": 12345}},
                ]
            },
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    factory = AnthropicTransport if provider == "anthropic" else OpenAITransport
    transport = factory(model="m", api_key="offline", base_url=base, http_client=client)
    assert await transport.context_window() == 12345
    assert str(requests[0].url) == "https://offline.test/v1/model/info"
    assert requests[0].extensions["timeout"]["read"] == 5


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"data": []},
        {"data": [{"model_name": "m", "model_info": {}}]},
        {"data": [{"model_name": "m", "model_info": {"max_input_tokens": 0}}]},
        {"data": [{"model_name": "m", "model_info": {"max_input_tokens": "bad"}}]},
        {"data": None},
        {"data": [None]},
        None,
    ],
)
async def test_bad_discovery_returns_none(
    body: Any, caplog: pytest.LogCaptureFixture
) -> None:
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=body))
    )
    transport = OpenAITransport(
        model="m",
        api_key="offline",
        base_url="https://offline.test/v1",
        http_client=client,
    )
    assert await transport.context_window() is None
    assert "context window" in caplog.text


@pytest.mark.parametrize("failure", ["timeout", "http", "json"])
async def test_discovery_failure_falls_back(failure: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if failure == "timeout":
            raise httpx2.ReadTimeout("offline timeout", request=request)
        return httpx2.Response(503 if failure == "http" else 200, text="not json")

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    transport = OpenAITransport(
        model="m",
        api_key="offline",
        base_url="https://offline.test/v1",
        http_client=client,
    )
    assert await transport.context_window() is None


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_direct_endpoint_never_discovers(provider: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("direct endpoints must not make discovery requests")

    factory = AnthropicTransport if provider == "anthropic" else OpenAITransport
    transport = factory(
        model="m",
        api_key="offline",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    assert await transport.context_window() is None
