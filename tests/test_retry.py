"""In-run retries of transient provider failures (issue 134)."""

from __future__ import annotations

import math
from collections.abc import AsyncGenerator
from copy import deepcopy
from typing import Any

import httpx2
import pytest

from fake_provider import tool_reply
from nare.events import Event
from nare.loop import RetryPolicy, run
from nare.session import Message, Session, Status, Usage, new_session, reopen
from nare.tools import Policy
from nare.transport import (
    Delta,
    ProviderStatusError,
    Reply,
    StreamDropped,
    Transient,
    TransientReason,
    transient,
)
from nare.transport.anthropic import AnthropicTransport
from nare.transport.openai import OpenAITransport
from test_streaming import Bytes, anthropic_chunks, openai_chunks, sse

REQUEST = httpx2.Request("POST", "http://proxy.invalid/v1/chat/completions")


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (httpx2.ReadTimeout("read timed out", request=REQUEST), "timeout"),
        (httpx2.ConnectTimeout("connect timed out", request=REQUEST), "timeout"),
        (httpx2.WriteTimeout("write timed out", request=REQUEST), "timeout"),
        (httpx2.ConnectError("connection refused", request=REQUEST), "connection"),
        (httpx2.ReadError("connection reset", request=REQUEST), "connection"),
        (
            httpx2.RemoteProtocolError("peer closed connection", request=REQUEST),
            "connection",
        ),
        (StreamDropped("incomplete model stream: final marker missing"), "stream"),
    ],
)
def test_transport_failures_are_transient(
    exc: Exception, reason: TransientReason
) -> None:
    assert transient(exc) == Transient(reason)


@pytest.mark.parametrize("status", [429, 502, 503, 504, 529])
def test_retryable_statuses_are_transient(status: int) -> None:
    exc = ProviderStatusError(f"HTTP {status}", status_code=status)
    assert transient(exc) == Transient("status", status=status)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422, 500, 524])
def test_other_statuses_are_permanent(status: int) -> None:
    assert transient(ProviderStatusError("no", status_code=status)) is None


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("connection reset"),
        ValueError("schema refused"),
        RuntimeError("incomplete model stream: invalid final usage"),
        httpx2.ProxyError("proxy refused", request=REQUEST),
        httpx2.UnsupportedProtocol("no such scheme", request=REQUEST),
    ],
)
def test_everything_else_is_permanent(exc: Exception) -> None:
    assert transient(exc) is None


def test_a_wrapped_cause_is_classified() -> None:
    # The anthropic SDK raises its own error from httpx's.
    try:
        try:
            raise httpx2.ReadTimeout("read timed out", request=REQUEST)
        except httpx2.ReadTimeout as inner:
            raise RuntimeError("Request timed out.") from inner
    except RuntimeError as outer:
        assert transient(outer) == Transient("timeout")


@pytest.mark.parametrize(
    ("header", "expected"), [("7", 7.0), ("0.5", 0.5), ("soon", None), ("-1", None)]
)
def test_retry_after_seconds_are_read(header: str, expected: float | None) -> None:
    exc = ProviderStatusError("slow down", status_code=429, retry_after=header)
    assert transient(exc) == Transient("status", status=429, retry_after=expected)


# Transports raise what the classifier reads.


def _client(handler: Any) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def _rail(provider: str, handler: Any, *, streaming: bool = False) -> Any:
    cls = OpenAITransport if provider == "openai" else AnthropicTransport
    return cls(
        model="m", api_key="test", streaming=streaming, http_client=_client(handler)
    )


HELLO = [Message("user", [{"type": "text", "text": "hi"}])]


@pytest.mark.parametrize("streaming", [False, True])
async def test_openai_status_failure_carries_status_and_retry_after(
    streaming: bool,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503, headers={"retry-after": "3"}, text="busy")

    with pytest.raises(ProviderStatusError) as raised:
        await _rail("openai", handler, streaming=streaming).turn(HELLO, [])
    assert str(raised.value) == "chat completion failed with HTTP 503: busy"
    assert transient(raised.value) == Transient("status", status=503, retry_after=3)


async def test_openai_stream_without_its_final_marker_dropped() -> None:
    data = Bytes(openai_chunks()[:1])

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, stream=data)

    with pytest.raises(StreamDropped):
        await _rail("openai", handler, streaming=True).turn(HELLO, [])


async def test_openai_stream_without_usage_is_not_dropped() -> None:
    chunks = openai_chunks()
    chunks[-2] = sse({"choices": [], "usage": {}})

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, stream=Bytes(chunks))

    with pytest.raises(RuntimeError) as raised:
        await _rail("openai", handler, streaming=True).turn(HELLO, [])
    assert transient(raised.value) is None


async def test_anthropic_stream_without_message_stop_dropped() -> None:
    chunks = anthropic_chunks()[:-1]

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Bytes(chunks)
        )

    with pytest.raises(StreamDropped):
        await _rail("anthropic", handler, streaming=True).turn(HELLO, [])


@pytest.mark.parametrize("status", [500, 503, 529])
async def test_anthropic_is_called_once_and_its_failure_classified(
    status: int,
) -> None:
    calls: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return httpx2.Response(
            status,
            headers={"retry-after": "1"},
            json={"type": "error", "error": {"type": "api_error", "message": "x"}},
        )

    rail = _rail("anthropic", handler)
    with pytest.raises(Exception) as raised:
        await rail.turn(HELLO, [])
    # The SDK's own silent retries are off: nare's budget is the only one.
    assert calls == [1]
    assert rail.transient(raised.value) == Transient(
        "status", status=status, retry_after=1
    )


@pytest.mark.parametrize("status", [400, 401, 404])
async def test_anthropic_refusals_are_permanent(status: int) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status,
            json={"type": "error", "error": {"type": "x_error", "message": "no"}},
        )

    rail = _rail("anthropic", handler)
    with pytest.raises(Exception) as raised:
        await rail.turn(HELLO, [])
    assert rail.transient(raised.value) is None


async def test_anthropic_overloaded_mid_stream_is_transient() -> None:
    chunks = [
        *anthropic_chunks()[:3],
        sse(
            {
                "type": "error",
                "error": {"type": "overloaded_error", "message": "Overloaded"},
            },
            "error",
        ),
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Bytes(chunks)
        )

    rail = _rail("anthropic", handler, streaming=True)
    with pytest.raises(Exception) as raised:
        await rail.turn(HELLO, [])
    assert rail.transient(raised.value) == Transient("overloaded")


async def test_anthropic_timeout_is_classified_through_the_sdk() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("read timed out", request=request)

    rail = _rail("anthropic", handler)
    with pytest.raises(Exception) as raised:
        await rail.turn(HELLO, [])
    assert rail.transient(raised.value) == Transient("timeout")


# The loop resends a transient failure within the caller's budget.


class Flaky:
    """Plays a script of failures and replies, recording each request."""

    def __init__(self, script: list[Reply | BaseException]) -> None:
        self.script = list(script)
        self.calls: list[tuple[list[Message], list[dict[str, Any]]]] = []
        self.constraints: list[dict[str, Any] | None] = []

    async def context_window(self) -> int | None:
        return None

    def _next(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        response_schema: dict[str, Any] | None,
    ) -> Reply:
        self.calls.append((deepcopy(messages), list(tools)))
        self.constraints.append(response_schema)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def turn(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        response_schema: dict[str, Any] | None = None,
    ) -> Reply:
        return self._next(messages, tools, response_schema)


class FlakyStructured(Flaky):
    supports_structured_output = True


class FlakyStreaming(Flaky):
    streaming = True
    supports_structured_output = True

    async def stream_turn(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        response_schema: dict[str, Any] | None = None,
    ) -> AsyncGenerator[Delta | Reply, None]:
        yield Delta("progress", "on it ")
        yield self._next(messages, tools, response_schema)


def timeout() -> httpx2.ReadTimeout:
    return httpx2.ReadTimeout("read timed out", request=REQUEST)


def answer() -> Reply:
    return Reply(
        content=[{"type": "text", "text": "the answer"}],
        tool_calls=[],
        usage=Usage(input=10, output=5),
        stop_reason="end_turn",
    )


class Sleeps:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def retry_events(events: list[Event]) -> list[dict[str, Any]]:
    return [e.detail["retry"] for e in events if "retry" in e.detail]


async def drive(
    transport: Any, retry: RetryPolicy, **kwargs: Any
) -> tuple[Session, list[Event]]:
    session = new_session("go")
    events = [
        e
        async for e in run(
            session, transport=transport, retry=retry, context_window=100000, **kwargs
        )
    ]
    return session, events


async def test_two_timeouts_then_an_answer_completes_the_run() -> None:
    sleeps = Sleeps()
    fake = Flaky([timeout(), timeout(), answer()])
    session, events = await drive(fake, RetryPolicy(sleep=sleeps))
    assert session.status == "done"
    assert session.turns == 1
    # A failed attempt adds no usage: the answer's own, only.
    assert session.usage.input == 10 and session.usage.output == 5
    assert session.retries == 2
    assert sleeps.waits == [2.0, 4.0]
    retries = retry_events(events)
    assert [r["attempt"] for r in retries] == [1, 2]
    assert {r["reason"] for r in retries} == {"timeout"}
    assert [r["wait"] for r in retries] == [2.0, 4.0]
    assert retries[0]["retries"] == 3
    assert retries[0]["status"] is None
    assert retries[0]["error"] == "ReadTimeout: read timed out"
    assert all(e.type == "progress" for e in events if "retry" in e.detail)
    # The same request each time, so a caching backend's prefix still hits.
    assert fake.calls[0] == fake.calls[1] == fake.calls[2]


@pytest.mark.parametrize("status", [401, 404])
async def test_a_permanent_refusal_is_not_retried(status: int) -> None:
    fake = Flaky([ProviderStatusError(f"HTTP {status}", status_code=status)])
    session, events = await drive(fake, RetryPolicy(sleep=Sleeps()))
    assert len(fake.calls) == 1
    assert retry_events(events) == []
    assert session.status == "error"
    assert session.stop_reason is None
    assert session.error == f"ProviderStatusError: HTTP {status}"
    assert session.retries == 0


async def test_an_exhausted_budget_ends_the_run_as_before() -> None:
    sleeps = Sleeps()
    fake = Flaky([timeout(), timeout(), timeout(), timeout()])
    session, events = await drive(fake, RetryPolicy(sleep=sleeps))
    assert len(fake.calls) == 4
    assert session.status == "error"
    assert session.stop_reason is None
    assert session.error == "ReadTimeout: read timed out"
    assert session.retries == 3
    assert session.turns == 0
    assert session.usage.total_tokens == 0
    assert sleeps.waits == [2.0, 4.0, 8.0]
    assert events[-1].type == "error"


async def test_zero_retries_turns_retrying_off() -> None:
    fake = Flaky([timeout(), answer()])
    session, events = await drive(fake, RetryPolicy(retries=0, sleep=Sleeps()))
    assert len(fake.calls) == 1
    assert session.status == "error"
    assert retry_events(events) == []


async def test_the_seconds_budget_bounds_the_waiting() -> None:
    sleeps = Sleeps()
    fake = Flaky([timeout(), timeout(), timeout(), answer()])
    session, _ = await drive(fake, RetryPolicy(retries=5, seconds=6, sleep=sleeps))
    # 2 + 4 fits in 6 seconds; the next wait of 8 does not.
    assert sleeps.waits == [2.0, 4.0]
    assert len(fake.calls) == 3
    assert session.status == "error"


async def test_waits_double_up_to_their_cap() -> None:
    sleeps = Sleeps()
    script: list[Reply | BaseException] = [timeout() for _ in range(6)]
    fake = Flaky([*script, answer()])
    session, _ = await drive(fake, RetryPolicy(retries=6, seconds=1000, sleep=sleeps))
    assert session.status == "done"
    assert sleeps.waits == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]


async def test_retry_after_lengthens_a_wait() -> None:
    sleeps = Sleeps()
    busy = ProviderStatusError("HTTP 429", status_code=429, retry_after="10")
    fake = Flaky([busy, answer()])
    session, events = await drive(fake, RetryPolicy(sleep=sleeps))
    assert session.status == "done"
    assert sleeps.waits == [10.0]
    assert retry_events(events)[0]["status"] == 429


async def test_the_budget_is_per_model_request() -> None:
    sleeps = Sleeps()
    fake = Flaky(
        [
            timeout(),
            tool_reply("ask", {"questions": ["which?"]}),
        ]
    )
    session, _ = await drive(fake, RetryPolicy(retries=1, sleep=sleeps))
    blocked: Status = session.status
    assert blocked == "blocked"
    reopen(session, "this one")
    fake.script = [timeout(), answer()]
    events = [
        e
        async for e in run(
            session,
            transport=fake,
            retry=RetryPolicy(retries=1, sleep=sleeps),
            context_window=100000,
        )
    ]
    assert session.status == "done"
    # Cumulative over the session, like turns.
    assert session.retries == 2
    assert len(retry_events(events)) == 1


async def test_a_transport_names_its_own_transient_failures() -> None:
    class Odd(Exception):
        pass

    class Picky(Flaky):
        def transient(self, exc: BaseException) -> Transient | None:
            return Transient("overloaded") if isinstance(exc, Odd) else None

    fake = Picky([Odd("busy"), answer()])
    session, events = await drive(fake, RetryPolicy(sleep=Sleeps()))
    assert session.status == "done"
    assert retry_events(events)[0]["reason"] == "overloaded"


async def test_a_streamed_turn_is_retried_after_its_partial_text() -> None:
    fake = FlakyStreaming([StreamDropped("incomplete model stream"), answer()])
    session, events = await drive(fake, RetryPolicy(sleep=Sleeps()))
    assert session.status == "done"
    assert session.retries == 1
    texts = [
        "retry" if "retry" in e.detail else e.text
        for e in events
        if e.type == "progress" and ("retry" in e.detail or e.text == "on it ")
    ]
    # The abandoned attempt's text, then the retry, then the next attempt's.
    assert texts == ["on it ", "retry", "on it "]


SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
}


def json_reply(text: str) -> Reply:
    return Reply(
        content=[{"type": "text", "text": text}],
        tool_calls=[],
        usage=Usage(input=10, output=5),
        stop_reason="end_turn",
    )


@pytest.mark.parametrize("cls", [FlakyStructured, FlakyStreaming])
async def test_the_constrained_correction_turn_is_retried_constrained(
    cls: type[Flaky],
) -> None:
    fake = cls([json_reply("not json"), timeout(), json_reply('{"ok": true}')])
    session, events = await drive(
        fake,
        RetryPolicy(sleep=Sleeps()),
        schema=SCHEMA,
        structured_output="json_schema",
    )
    assert session.status == "done"
    assert session.output == {"ok": True}
    # The first answer is free; both attempts at the correction are constrained.
    assert fake.constraints == [None, SCHEMA, SCHEMA]
    assert fake.calls[1][1] == [] and fake.calls[2][1] == []
    assert session.retries == 1


async def test_a_tool_free_constrained_run_is_retried_constrained() -> None:
    fake = FlakyStructured([timeout(), json_reply('{"ok": true}')])
    session, _ = await drive(
        fake,
        RetryPolicy(sleep=Sleeps()),
        schema=SCHEMA,
        structured_output="json_schema",
        policy=Policy(tools=frozenset()),
    )
    assert session.status == "done"
    assert fake.constraints == [SCHEMA, SCHEMA]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"retries": -1},
        {"retries": True},
        {"retries": 1.5},
        {"seconds": -1},
        {"seconds": math.nan},
        {"seconds": math.inf},
        {"seconds": True},
    ],
)
def test_an_invalid_budget_is_refused(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)
