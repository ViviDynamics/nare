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


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("window", [None, 16000])
@pytest.mark.parametrize("priced", [False, True])
async def test_streamed_cost_uses_metadata_or_stays_unknown(
    provider: str, window: int | None, priced: bool
) -> None:
    from nare.loop import run
    from nare.session import new_session
    from nare.tui.render import status_line
    from test_streaming import Bytes, anthropic_chunks, openai_chunks

    requests: list[str] = []
    info: dict[str, Any] = {"max_input_tokens": 12345}
    if priced:
        info.update(
            input_cost_per_token=0.000001,
            output_cost_per_token=0.000002,
            cache_read_input_token_cost=0.0000005,
            cache_creation_input_token_cost=0.000003,
        )

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.url.path)
        if request.url.path == "/v1/model/info":
            return httpx2.Response(
                200, json={"data": [{"model_name": "m", "model_info": info}]}
            )
        return httpx2.Response(
            200,
            headers={
                "content-type": "text/event-stream",
                "x-litellm-response-cost-original": "0.0",
            },
            stream=Bytes(
                openai_chunks() if provider == "openai" else anthropic_chunks()
            ),
        )

    factory = AnthropicTransport if provider == "anthropic" else OpenAITransport
    transport = factory(
        model="m",
        api_key="offline",
        base_url="https://offline.test/v1",
        streaming=True,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    session = new_session("hi")
    events = [e async for e in run(session, transport=transport, context_window=window)]
    assert requests.count("/v1/model/info") == 1
    if priced:
        from nare.accounting import Prices

        assert await transport.prices() == Prices(1, 2, 0.5, 3)
        assert session.usage.cost == pytest.approx(0.0000185)
        assert session.usage.estimated is True
        assert "estimate" in next(e.text for e in events if e.type == "cost")
        assert "estimate" in status_line("m", 1, 50, session.usage, None, "done").plain
    else:
        assert session.usage.cost is None
        assert (
            "cost unknown" in status_line("m", 1, 50, session.usage, None, "done").plain
        )


@pytest.mark.parametrize("bad", [None, -1, True, "bad", float("inf"), float("nan")])
async def test_invalid_discovered_prices_stay_unknown(bad: Any) -> None:
    transport = OpenAITransport(
        model="m",
        api_key="offline",
        base_url="https://offline.test/v1",
        http_client=httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda _: httpx2.Response(
                    200,
                    json={
                        "data": [
                            {
                                "model_name": "m",
                                "model_info": {
                                    "input_cost_per_token": bad,
                                    "output_cost_per_token": 0.000002,
                                },
                            }
                        ]
                    },
                )
            )
        ),
    )
    assert await transport.prices() is None


@pytest.mark.parametrize("override", [False, True])
async def test_streamed_price_estimates_enforce_usd_budget(
    override: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nare.accounting import prices_from_env
    from nare.loop import run
    from nare.session import new_session
    from test_streaming import Bytes, openai_chunks

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v1/model/info":
            return httpx2.Response(
                200,
                json={
                    "data": [
                        {
                            "model_name": "m",
                            "model_info": {
                                "max_input_tokens": 32000,
                                "input_cost_per_token": 1,
                                "output_cost_per_token": 1,
                                "cache_read_input_token_cost": 1,
                                "cache_creation_input_token_cost": 1,
                            },
                        }
                    ]
                },
            )
        return httpx2.Response(
            200,
            headers={
                "content-type": "text/event-stream",
                "x-litellm-response-cost-original": "0.0",
            },
            stream=Bytes(openai_chunks()),
        )

    for key in (
        "NARE_PRICE_IN",
        "NARE_PRICE_OUT",
        "NARE_PRICE_CACHE_READ",
        "NARE_PRICE_CACHE_WRITE",
    ):
        monkeypatch.delenv(key, raising=False)
    if override:
        monkeypatch.setenv("NARE_PRICE_IN", "2")
        monkeypatch.setenv("NARE_PRICE_OUT", "2")
    session = new_session("keep using tools")
    # A read turn makes the loop check the budget before another request.
    from test_streaming import sse

    chunks = openai_chunks()
    chunks[0:0] = [
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
                                    "function": {
                                        "name": "read",
                                        "arguments": '{"path":"/missing"}',
                                    },
                                }
                            ]
                        },
                    }
                ]
            }
        )
    ]
    # Use the real stream parser with its usual final usage.
    original_handler = handler

    def tool_handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v1/model/info":
            return original_handler(request)
        return httpx2.Response(
            200,
            headers={
                "content-type": "text/event-stream",
                "x-litellm-response-cost-original": "0.0",
            },
            stream=Bytes(chunks),
        )

    transport = OpenAITransport(
        model="m",
        api_key="offline",
        base_url="https://offline.test/v1",
        streaming=True,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(tool_handler)),
    )
    events = [
        e
        async for e in run(
            session, transport=transport, budget_usd=0.000001, prices=prices_from_env()
        )
    ]
    assert session.stop_reason == "budget"
    assert session.turns == 1
    assert session.usage.cost == pytest.approx(0.000030 if override else 15)
    assert any("USD budget exhausted" in e.text for e in events)
