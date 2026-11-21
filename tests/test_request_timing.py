"""Per-request timing on each `cost` event and on the result line (issue 315).

Each rail reads `nare.transport.clock`, a monotonic clock, so these tests
replace it with one that only moves when the fake server says so: when its
response headers go out, and before each streamed chunk. The numbers are then
exact, and nothing here sleeps.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest

import nare.transport
from nare.cli import main
from nare.contract import CONTRACT_VERSION, describe
from nare.loop import RetryPolicy, run
from nare.session import Message, new_session
from nare.tools import Policy
from nare.transport import Delta, Reply, Timing
from nare.transport.anthropic import AnthropicTransport
from nare.transport.openai import OpenAITransport
from nare.transport.replay import ReplayTransport
from test_streaming import sse

START = 100.0


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[Clock]:
    fake = Clock()
    monkeypatch.setattr(nare.transport, "clock", fake)
    yield fake


class Timed(httpx2.AsyncByteStream):
    """A response body whose chunks arrive at set offsets on the fake clock."""

    def __init__(self, clock: Clock, chunks: list[tuple[float, bytes]]) -> None:
        self.clock = clock
        self.chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for advance, chunk in self.chunks:
            self.clock.now += advance
            yield chunk

    async def aclose(self) -> None:
        pass


def server(
    clock: Clock, headers_after: float, chunks: list[tuple[float, bytes]]
) -> httpx2.AsyncClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        clock.now += headers_after
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Timed(clock, chunks),
        )

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def hi() -> list[Message]:
    return [Message("user", [{"type": "text", "text": "hi"}])]


def exact(timing: Timing | None) -> tuple[float | None, ...]:
    assert timing is not None
    return tuple(
        None if v is None else round(v, 9)
        for v in (
            timing.headers_seconds,
            timing.first_token_seconds,
            timing.total_seconds,
        )
    )


async def collect(stream: AsyncIterator[Delta | Reply]) -> tuple[list[Delta], Reply]:
    deltas: list[Delta] = []
    reply: Reply | None = None
    async for item in stream:
        if isinstance(item, Delta):
            deltas.append(item)
        else:
            reply = item
    assert reply is not None
    return deltas, reply


def openai(client: httpx2.AsyncClient, *, streaming: bool) -> OpenAITransport:
    return OpenAITransport(
        model="m",
        api_key="k",
        base_url="https://proxy.example/v1",
        http_client=client,
        streaming=streaming,
    )


def anthropic(client: httpx2.AsyncClient, *, streaming: bool) -> AnthropicTransport:
    return AnthropicTransport(
        model="claude-sonnet-4-5",
        api_key="k",
        http_client=client,
        streaming=streaming,
    )


def completion() -> bytes:
    return json.dumps(
        {
            "id": "c",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "hello"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    ).encode()


def chunk(delta: dict[str, Any], finish: str | None = None) -> bytes:
    return sse({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})


USAGE_CHUNK = sse(
    {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
)
DONE = b"data: [DONE]\n\n"


async def test_openai_unstreamed_reports_headers_and_total_but_no_first_token(
    clock: Clock,
) -> None:
    body = completion()
    transport = openai(
        server(clock, 0.25, [(0.5, body[:20]), (0.25, body[20:])]), streaming=False
    )
    reply = await transport.turn(hi(), [])
    assert reply.content == [{"type": "text", "text": "hello"}]
    # No chunk of an unstreamed body is a token the caller could have had
    # sooner, so the first-token time is null rather than equal to the headers.
    assert exact(reply.timing) == (0.25, None, 1.0)


async def test_openai_streamed_first_token_skips_a_role_only_chunk(
    clock: Clock,
) -> None:
    transport = openai(
        server(
            clock,
            0.25,
            [
                (0.25, chunk({"role": "assistant"})),
                (0.5, chunk({"content": "hello"})),
                (1.0, chunk({"content": " world"}, "stop")),
                (0.5, USAGE_CHUNK),
                (0.5, DONE),
            ],
        ),
        streaming=True,
    )
    deltas, reply = await collect(transport.stream_turn(hi(), []))
    assert [d.text for d in deltas] == ["hello", " world"]
    assert exact(reply.timing) == (0.25, 1.0, 3.0)


async def test_openai_reasoning_is_a_first_token(clock: Clock) -> None:
    transport = openai(
        server(
            clock,
            0.5,
            [
                (0.5, chunk({"reasoning_content": "hmm"})),
                (1.0, chunk({"content": "ok"}, "stop")),
                (0.0, USAGE_CHUNK),
                (0.0, DONE),
            ],
        ),
        streaming=True,
    )
    _, reply = await collect(transport.stream_turn(hi(), []))
    assert exact(reply.timing) == (0.5, 1.0, 2.0)


async def test_openai_tool_only_stream_times_its_first_argument_chunk(
    clock: Clock,
) -> None:
    def call(arguments: str, *, first: bool = False) -> dict[str, Any]:
        part: dict[str, Any] = {"index": 0, "function": {"arguments": arguments}}
        if first:
            part["id"] = "call_1"
            part["function"]["name"] = "read"
        return {"tool_calls": [part]}

    transport = openai(
        server(
            clock,
            0.25,
            [
                (0.25, chunk({"role": "assistant"})),
                (0.5, chunk(call('{"path"', first=True))),
                (2.0, chunk(call(': "a.txt"}'), "tool_calls")),
                (0.0, USAGE_CHUNK),
                (0.0, DONE),
            ],
        ),
        streaming=True,
    )
    deltas, reply = await collect(transport.stream_turn(hi(), []))
    # The turn streams no delta, but its first token is still timed.
    assert deltas == []
    assert [c.args for c in reply.tool_calls] == [{"path": "a.txt"}]
    assert exact(reply.timing) == (0.25, 1.0, 3.0)


def anthropic_events(*blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    start = {
        "type": "message_start",
        "message": {
            "id": "m",
            "type": "message",
            "role": "assistant",
            "model": "m",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 7, "output_tokens": 0},
        },
    }
    tail: list[dict[str, Any]] = [
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]
    return [start, *[event for block in blocks for event in block], *tail]


TEXT_BLOCK = [
    {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text", "text": ""},
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "hi"},
    },
    {"type": "content_block_stop", "index": 0},
]

TOOL_BLOCK = [
    {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "tool_use", "id": "t1", "name": "read", "input": {}},
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "input_json_delta", "partial_json": '{"path": "a.txt"}'},
    },
    {"type": "content_block_stop", "index": 0},
]


def anthropic_stream(
    clock: Clock, events: list[dict[str, Any]], advances: list[float]
) -> httpx2.AsyncClient:
    assert len(events) == len(advances)
    return server(
        clock,
        0.25,
        [(a, sse(e, str(e["type"]))) for a, e in zip(advances, events, strict=True)],
    )


async def test_anthropic_streamed_first_token_is_the_first_text(clock: Clock) -> None:
    events = anthropic_events(TEXT_BLOCK)
    # message_start and an empty block start carry no token.
    advances = [0.25, 0.25, 0.25, 0.5, 0.5, 0.25]
    transport = anthropic(anthropic_stream(clock, events, advances), streaming=True)
    deltas, reply = await collect(transport.stream_turn(hi(), []))
    assert [d.text for d in deltas] == ["hi"]
    assert exact(reply.timing) == (0.25, 1.0, 2.25)


async def test_anthropic_tool_only_stream_times_the_tool_block(clock: Clock) -> None:
    events = anthropic_events(TOOL_BLOCK)
    advances = [0.25, 0.5, 1.0, 0.25, 0.25, 0.25]
    transport = anthropic(anthropic_stream(clock, events, advances), streaming=True)
    deltas, reply = await collect(transport.stream_turn(hi(), []))
    assert deltas == []
    assert [c.args for c in reply.tool_calls] == [{"path": "a.txt"}]
    assert exact(reply.timing) == (0.25, 1.0, 2.75)


async def test_anthropic_unstreamed_reports_headers_and_total(clock: Clock) -> None:
    body = json.dumps(
        {
            "id": "m",
            "type": "message",
            "role": "assistant",
            "model": "m",
            "content": [{"type": "text", "text": "hello"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 7, "output_tokens": 5},
        }
    ).encode()

    def handler(request: httpx2.Request) -> httpx2.Response:
        clock.now += 0.5
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            stream=Timed(clock, [(0.25, body[:10]), (0.25, body[10:])]),
        )

    transport = anthropic(
        httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), streaming=False
    )
    reply = await transport.turn(hi(), [])
    assert reply.content == [{"type": "text", "text": "hello"}]
    assert exact(reply.timing) == (0.5, None, 1.0)


class Sleeps:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        # The backoff passes on the clock too, and must not be counted.
        self.waits.append(seconds)
        self.clock.now += seconds


async def test_a_retried_request_reports_the_attempt_that_answered(
    clock: Clock,
) -> None:
    calls: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if not request.url.path.endswith("/chat/completions"):
            return httpx2.Response(404)
        calls.append(1)
        if len(calls) == 1:
            clock.now += 7.0
            return httpx2.Response(503, text="busy")
        clock.now += 0.25
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Timed(
                clock,
                [
                    (0.5, chunk({"content": "done"}, "stop")),
                    (0.25, USAGE_CHUNK),
                    (0.0, DONE),
                ],
            ),
        )

    transport = openai(
        httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), streaming=True
    )
    sleeps = Sleeps(clock)
    session = new_session("go")
    events = [
        e
        async for e in run(
            session,
            transport=transport,
            policy=Policy(),
            retry=RetryPolicy(sleep=sleeps),
            context_window=100000,
        )
    ]
    assert session.status == "done" and session.retries == 1
    assert sleeps.waits == [2.0]
    costs = [e.detail for e in events if e.type == "cost"]
    assert len(costs) == 1
    # Not the failed attempt's 7 seconds, nor the 2 second backoff.
    assert costs[0]["timing"] == {
        "headers_seconds": 0.25,
        "first_token_seconds": 0.75,
        "total_seconds": 1.0,
        "generation_seconds": 0.25,
    }


async def test_a_transport_that_reports_no_timing_gets_a_null_record() -> None:
    from fake_provider import FakeProvider, text_reply

    session = new_session("go")
    events = [
        e
        async for e in run(
            session,
            transport=FakeProvider([text_reply("hi")]),
            policy=Policy(),
            context_window=100000,
        )
    ]
    costs = [e.detail for e in events if e.type == "cost"]
    assert len(costs) == 1 and costs[0]["timing"] is None


SCRIPT = {
    "replies": [
        {
            "tool_calls": [{"name": "read", "args": {"path": "a.txt"}}],
            "usage": {"input": 10, "output": 5},
        },
        {"text": "done", "usage": {"input": 20, "output": 7}},
    ]
}


def run_cli(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    script: dict[str, Any],
    *extra: str,
    expect: int = 0,
) -> list[dict[str, Any]]:
    (tmp_path / "a.txt").write_text("hi\n", encoding="utf-8")
    path = tmp_path / "replay.json"
    path.write_text(json.dumps(script), encoding="utf-8")
    code = main(
        [
            "run",
            "--yes",
            "--jsonl",
            *extra,
            "--provider",
            "fake",
            "--replay",
            str(path),
            "--tools",
            "read",
            "--root",
            str(tmp_path),
            "read a.txt",
        ]
    )
    assert code == expect
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_replay_streamed_timing_is_zero_and_the_result_sums_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = run_cli(tmp_path, capsys, SCRIPT, "--stream")
    costs = [r["detail"] for r in rows if r.get("type") == "cost"]
    zero = {
        "headers_seconds": 0.0,
        "first_token_seconds": 0.0,
        "total_seconds": 0.0,
        "generation_seconds": 0.0,
    }
    assert [c["timing"] for c in costs] == [zero, zero]
    result = rows[-1]
    assert result["type"] == "result"
    assert result["request_timing"] == {
        "requests": 2,
        "timed": 2,
        "headers_seconds": 0.0,
        "total_seconds": 0.0,
        "first_tokens": 2,
        "first_token_seconds": 0.0,
        "generation_seconds": 0.0,
        "generation_output_tokens": 12,
    }


def test_replay_unstreamed_timing_has_no_first_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = run_cli(tmp_path, capsys, SCRIPT)
    costs = [r["detail"] for r in rows if r.get("type") == "cost"]
    zero = {
        "headers_seconds": 0.0,
        "first_token_seconds": None,
        "total_seconds": 0.0,
        "generation_seconds": None,
    }
    assert [c["timing"] for c in costs] == [zero, zero]
    assert rows[-1]["request_timing"] == {
        "requests": 2,
        "timed": 2,
        "headers_seconds": 0.0,
        "total_seconds": 0.0,
        "first_tokens": 0,
        "first_token_seconds": None,
        "generation_seconds": None,
        "generation_output_tokens": 0,
    }


def test_replay_script_timing_is_reported_and_aggregated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = json.loads(json.dumps(SCRIPT))
    script["replies"][0]["timing"] = {
        "headers_seconds": 0.5,
        "first_token_seconds": 1.5,
        "total_seconds": 4.0,
    }
    script["replies"][1]["timing"] = {
        "headers_seconds": 0.25,
        "first_token_seconds": 0.75,
        "total_seconds": 2.25,
    }
    rows = run_cli(tmp_path, capsys, script, "--stream")
    costs = [r["detail"] for r in rows if r.get("type") == "cost"]
    assert [c["timing"] for c in costs] == [
        {**script["replies"][0]["timing"], "generation_seconds": 2.5},
        {**script["replies"][1]["timing"], "generation_seconds": 1.5},
    ]
    assert rows[-1]["request_timing"] == {
        "requests": 2,
        "timed": 2,
        "headers_seconds": 0.75,
        "total_seconds": 6.25,
        "first_tokens": 2,
        "first_token_seconds": 2.25,
        # (4.0 - 1.5) + (2.25 - 0.75): decode is output / generation seconds.
        "generation_seconds": 4.0,
        "generation_output_tokens": 12,
    }


def test_a_run_with_no_request_reports_an_empty_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = run_cli(tmp_path, capsys, {"replies": [{"error": "boom"}]}, expect=1)
    assert rows[-1]["request_timing"] == {
        "requests": 0,
        "timed": 0,
        "headers_seconds": None,
        "total_seconds": None,
        "first_tokens": 0,
        "first_token_seconds": None,
        "generation_seconds": None,
        "generation_output_tokens": 0,
    }


@pytest.mark.parametrize(
    ("timing", "message"),
    [
        ([], "timing must be an object"),
        ({"total": 1}, "unknown key 'total'"),
        ({"total_seconds": -1}, "timing.total_seconds"),
        ({"total_seconds": True}, "timing.total_seconds"),
        ({"headers_seconds": float("inf")}, "timing.headers_seconds"),
    ],
)
def test_replay_refuses_a_malformed_timing(timing: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ReplayTransport({"replies": [{"text": "x", "timing": timing}]})


def test_contract_advertises_request_timing() -> None:
    contract = describe()
    assert contract["contract"] == CONTRACT_VERSION == 3
    entry = contract["request_timing"]
    assert entry["event"] == "cost"
    assert entry["event_detail"] == "timing"
    assert entry["fields"] == [
        "headers_seconds",
        "first_token_seconds",
        "total_seconds",
        "generation_seconds",
    ]
    assert entry["clock"] == "monotonic"
    assert entry["attempt"] == "the attempt that answered"
    assert entry["result_field"] == "request_timing"
    assert entry["result_per"] == "invocation"
    assert set(entry["providers"]) == {"anthropic", "bedrock", "openai", "fake"}
    assert entry["providers"]["openai"]["unstreamed"]["first_token_seconds"] is False
    assert entry["providers"]["openai"]["streamed"]["first_token_seconds"] is True


def test_the_documented_contract_describes_request_timing() -> None:
    doc = Path(__file__).parent.parent / "docs" / "contract.md"
    text = " ".join(doc.read_text(encoding="utf-8").split())
    assert "`request_timing`" in text
    assert "`first_token_seconds`" in text
    assert "`generation_output_tokens`" in text


def test_a_reply_stopped_for_repetition_has_no_generation_window(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # nare stopped reading the looping reply, so its final usage never came:
    # its output count must not be paired with its window, per request or in
    # the run's decode rate.
    timing = {"headers_seconds": 0.5, "first_token_seconds": 1.0, "total_seconds": 90.0}
    script = {
        "replies": [
            {
                "text": "again and again",
                "stop_reason": "repetition",
                "usage": {"input": 10, "output": 1},
                "timing": timing,
            },
            {
                "text": "done",
                "usage": {"input": 20, "output": 30},
                "timing": {
                    "headers_seconds": 0.5,
                    "first_token_seconds": 1.0,
                    "total_seconds": 4.0,
                },
            },
        ]
    }
    rows = run_cli(tmp_path, capsys, script, "--stream", "--max-cutoffs", "1")
    costs = [r["detail"] for r in rows if r.get("type") == "cost"]
    assert costs[0]["timing"] == {**timing, "generation_seconds": None}
    assert costs[1]["timing"]["generation_seconds"] == 3.0
    result = rows[-1]["request_timing"]
    assert result["first_tokens"] == 2
    assert result["first_token_seconds"] == 2.0
    assert result["total_seconds"] == 94.0
    assert result["generation_seconds"] == 3.0
    assert result["generation_output_tokens"] == 30


async def test_a_streaming_replay_turn_has_a_first_token() -> None:
    # A summary request calls turn(); on a streaming rail that streams too.
    transport = ReplayTransport({"replies": [{"text": "x"}]}, streaming=True)
    reply = await transport.turn(hi(), [])
    assert exact(reply.timing) == (0.0, 0.0, 0.0)


async def test_an_anthropic_body_read_timeout_is_the_sdk_timeout(clock: Clock) -> None:
    import anthropic as sdk

    from nare.transport import transient

    class Stalls(httpx2.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'{"id": '
            raise httpx2.ReadTimeout("read timed out")

        async def aclose(self) -> None:
            pass

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, headers={"content-type": "application/json"}, stream=Stalls()
        )

    transport = anthropic(
        httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), streaming=False
    )
    with pytest.raises(sdk.APITimeoutError) as raised:
        await transport.turn(hi(), [])
    assert transport.transient(raised.value) == transient(raised.value)
    found = transient(raised.value)
    assert found is not None and found.reason == "timeout"
