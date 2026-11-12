"""Admission queueing of provider capacity refusals (issue 288).

A capacity refusal is raised before dispatch, so it is waited out and the same
request resent. It is not a retry: it has its own limits and counters, and
the retry budget, `--request-retries 0` included, keeps its meaning.
"""

from __future__ import annotations

import json
import random
from collections.abc import AsyncGenerator
from copy import deepcopy
from typing import Any

import httpx2
import pytest

from nare.cli import build_parser, main
from nare.contract import describe
from nare.events import Event
from nare.loop import (
    CAPACITY_REFUSALS_DEFAULT,
    CAPACITY_WAIT_DEFAULT,
    CapacityQueue,
    RetryPolicy,
    fresh_queue,
    run,
)
from nare.session import Message, Session, Usage, dumps, loads, new_session
from nare.tools import Policy
from nare.transport import Capacity, Delta, ProviderStatusError, Reply, capacity_refusal
from nare.transport.openai import OpenAITransport
from test_retry import Sleeps, _rail, timeout

MARKER = "max_parallel_requests"
MARKERS = (MARKER,)


def refusal(retry_after: str | None = None, text: str = MARKER) -> ProviderStatusError:
    return ProviderStatusError(
        f"chat completion failed with HTTP 429: {text} reached",
        status_code=429,
        retry_after=retry_after,
    )


class Attempts:
    """Plays one list of items per attempt, recording each request it is sent.

    An item is a Reply, a Delta (streamed turns only) or an exception. Each
    attempt's list is consumed in order, so a refusal can come after deltas.
    """

    def __init__(self, attempts: list[list[Any]], *, streaming: bool = False) -> None:
        self.attempts = [list(a) for a in attempts]
        self.calls: list[tuple[list[Message], list[dict[str, Any]]]] = []
        self.streaming = streaming

    async def context_window(self) -> int | None:
        return None

    def _take(self, messages: list[Message], tools: list[dict[str, Any]]) -> list[Any]:
        self.calls.append((deepcopy(messages), list(tools)))
        return self.attempts.pop(0)

    async def turn(
        self, messages: list[Message], tools: list[dict[str, Any]], **_: Any
    ) -> Reply:
        (item,) = self._take(messages, tools)
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, Reply)
        return item

    async def stream_turn(
        self, messages: list[Message], tools: list[dict[str, Any]], **_: Any
    ) -> AsyncGenerator[Delta | Reply, None]:
        for item in self._take(messages, tools):
            if isinstance(item, BaseException):
                raise item
            yield item


class StreamingAttempts(Attempts):
    def __init__(self, attempts: list[list[Any]]) -> None:
        super().__init__(attempts, streaming=True)


class MarkedStreaming(StreamingAttempts):
    capacity_markers = MARKERS


class Marked(Attempts):
    """A transport that declares the capacity marker, as the openai rail does."""

    capacity_markers = MARKERS


class Unmarked(Attempts):
    """A transport that declares no capacity markers, so it never queues."""


def answered(text: str = "the answer") -> Reply:
    return Reply(
        content=[{"type": "text", "text": text}],
        tool_calls=[],
        usage=Usage(input=10, output=5),
        stop_reason="end_turn",
    )


def queued_events(events: list[Event]) -> list[dict[str, Any]]:
    return [e.detail["capacity"] for e in events if "capacity" in e.detail]


def retry_events(events: list[Event]) -> list[dict[str, Any]]:
    return [e.detail["retry"] for e in events if "retry" in e.detail]


async def drive(
    transport: Any,
    *,
    retry: RetryPolicy | None = None,
    capacity: CapacityQueue | None = None,
    session: Session | None = None,
) -> tuple[Session, list[Event]]:
    session = session or new_session("go")
    events = [
        e
        async for e in run(
            session,
            transport=transport,
            retry=retry or RetryPolicy(retries=0, sleep=Sleeps()),
            capacity=capacity,
            policy=Policy(),
            context_window=100000,
        )
    ]
    return session, events


# The classifier -------------------------------------------------------------


def test_a_429_with_the_marker_qualifies() -> None:
    assert capacity_refusal(refusal(), MARKERS, emitted=False) == Capacity(MARKER)


def test_retry_after_is_read_from_the_refusal() -> None:
    found = capacity_refusal(refusal(retry_after="7"), MARKERS, emitted=False)
    assert found == Capacity(MARKER, retry_after=7.0)


def test_a_marker_in_a_wrapped_cause_qualifies() -> None:
    outer = RuntimeError("request failed")
    outer.__cause__ = refusal()
    assert capacity_refusal(outer, MARKERS, emitted=False) == Capacity(MARKER)


@pytest.mark.parametrize(
    ("exc", "markers", "emitted"),
    [
        (refusal(), MARKERS, True),  # a response has started
        (refusal(), (), False),  # the transport declares no markers
        (refusal(text="rate limited"), MARKERS, False),  # no capacity marker
        (ProviderStatusError("max_parallel_requests", status_code=503), MARKERS, False),
        (ProviderStatusError("max_parallel_requests", status_code=400), MARKERS, False),
        (RuntimeError("max_parallel_requests"), MARKERS, False),  # no status
        (httpx2.ReadTimeout("slow"), MARKERS, False),
    ],
)
def test_everything_else_is_not_a_capacity_refusal(
    exc: BaseException, markers: tuple[str, ...], emitted: bool
) -> None:
    assert capacity_refusal(exc, markers, emitted=emitted) is None


def test_the_openai_rail_declares_the_marker() -> None:
    assert OpenAITransport.capacity_markers == (MARKER,)


# The queue ------------------------------------------------------------------


def test_a_retry_after_replaces_the_backoff() -> None:
    queue = CapacityQueue(sleep=Sleeps())
    assert queue.wait(1, 12.5) == 12.5
    assert queue.wait(4, 0.0) == 0.0


@pytest.mark.parametrize(
    ("queued", "low", "high"),
    [(1, 1.0, 2.0), (2, 2.0, 4.0), (3, 4.0, 8.0), (6, 30.0, 60.0), (20, 30.0, 60.0)],
)
def test_backoff_doubles_to_a_cap_with_jitter(
    queued: int, low: float, high: float
) -> None:
    queue = CapacityQueue(sleep=Sleeps(), rng=random.Random(288))
    for _ in range(50):
        assert low <= queue.wait(queued, None) <= high


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_wait": -1.0},
        {"max_wait": float("inf")},
        {"max_wait": True},
        {"max_refusals": -1},
        {"max_refusals": 1.5},
        {"max_refusals": True},
    ],
)
def test_invalid_limits_are_refused(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        CapacityQueue(**kwargs)


def test_defaults_are_the_documented_limits() -> None:
    queue = CapacityQueue()
    assert queue.max_wait == CAPACITY_WAIT_DEFAULT == 1800.0
    assert queue.max_refusals == CAPACITY_REFUSALS_DEFAULT == 50


def test_a_run_gets_fresh_counters() -> None:
    queue = CapacityQueue(max_refusals=3)
    queue.refused = 3
    fresh = fresh_queue(queue)
    assert fresh is not None and fresh is not queue
    assert fresh.refused == 0
    assert fresh.max_refusals == 3
    assert queue.refused == 3
    assert fresh_queue(None) is None


# The loop -------------------------------------------------------------------


async def test_a_refusal_is_waited_out_and_the_same_request_resent() -> None:
    sleeps = Sleeps()
    fake = Marked([[refusal(retry_after="5")], [answered()]])
    session, events = await drive(fake, capacity=CapacityQueue(sleep=sleeps))
    assert session.status == "done"
    assert sleeps.waits == [5.0]
    # Accounted apart from requests, retries and usage.
    assert session.capacity_refusals == 1
    assert session.capacity_wait == 5.0
    assert session.retries == 0
    assert session.turns == 1
    assert session.usage.input == 10 and session.usage.output == 5
    # The byte-identical request: the same messages and tools each time.
    assert fake.calls[0] == fake.calls[1]
    assert retry_events(events) == []
    [record] = queued_events(events)
    assert record == {
        "attempt": 1,
        "wait": 5.0,
        "waited": 5.0,
        "refusals": 1,
        "refusals_limit": CAPACITY_REFUSALS_DEFAULT,
        "status": 429,
        "reason": MARKER,
    }
    [event] = [e for e in events if "capacity" in e.detail]
    assert event.type == "progress"
    assert event.timestamp


async def test_without_the_queue_the_refusal_is_a_failed_request() -> None:
    sleeps = Sleeps()
    fake = Marked([[refusal()], [answered()]])
    session, events = await drive(fake, retry=RetryPolicy(retries=0, sleep=sleeps))
    assert len(fake.calls) == 1
    assert session.status == "error"
    assert session.capacity_refusals == 0
    assert queued_events(events) == []


async def test_without_the_queue_a_429_is_still_retried_as_before() -> None:
    sleeps = Sleeps()
    fake = Marked([[refusal(retry_after="3")], [answered()]])
    session, events = await drive(fake, retry=RetryPolicy(sleep=sleeps))
    assert session.status == "done"
    assert session.capacity_refusals == 0
    assert [r["reason"] for r in retry_events(events)] == ["status"]
    assert sleeps.waits == [3.0]


async def test_request_retries_zero_still_queues_a_refusal() -> None:
    fake = Marked([[refusal(retry_after="1")], [answered()]])
    session, _ = await drive(
        fake,
        retry=RetryPolicy(retries=0, sleep=Sleeps()),
        capacity=CapacityQueue(sleep=Sleeps()),
    )
    assert session.status == "done"
    assert session.capacity_refusals == 1
    assert session.retries == 0


async def test_queued_refusals_do_not_spend_the_retry_budget() -> None:
    fake = Marked(
        [
            [refusal(retry_after="1")],
            [refusal(retry_after="1")],
            [timeout()],
            [answered()],
        ]
    )
    session, events = await drive(
        fake,
        retry=RetryPolicy(retries=1, sleep=Sleeps()),
        capacity=CapacityQueue(sleep=Sleeps()),
    )
    assert session.status == "done"
    assert session.capacity_refusals == 2
    assert session.retries == 1
    assert [r["reason"] for r in retry_events(events)] == ["timeout"]


async def test_a_refusal_past_the_wait_limit_surfaces_as_before() -> None:
    sleeps = Sleeps()
    fake = Marked([[refusal(retry_after="10")], [answered()]])
    session, events = await drive(
        fake, capacity=CapacityQueue(max_wait=5, sleep=sleeps)
    )
    assert len(fake.calls) == 1
    assert sleeps.waits == []
    assert session.status == "error"
    assert session.capacity_refusals == 0
    assert queued_events(events) == []


async def test_the_wait_limit_is_cumulative_for_one_request() -> None:
    sleeps = Sleeps()
    fake = Marked([[refusal(retry_after="2")] for _ in range(3)] + [[answered()]])
    session, _ = await drive(fake, capacity=CapacityQueue(max_wait=5, sleep=sleeps))
    # Two waits of 2 stay within 5 seconds; a third would take the request to 6.
    assert sleeps.waits == [2.0, 2.0]
    assert session.capacity_refusals == 2
    assert session.capacity_wait == 4.0
    assert session.status == "error"


async def test_the_refusal_limit_surfaces_as_before() -> None:
    fake = Marked([[refusal(retry_after="0")] for _ in range(3)] + [[answered()]])
    session, _ = await drive(
        fake, capacity=CapacityQueue(max_refusals=2, sleep=Sleeps())
    )
    assert len(fake.calls) == 3
    assert session.status == "error"
    assert session.capacity_refusals == 2


async def test_the_refusal_limit_is_fresh_for_each_run() -> None:
    queue = CapacityQueue(max_refusals=1, sleep=Sleeps())
    first, _ = await drive(
        Marked([[refusal(retry_after="0")], [answered()]]), capacity=queue
    )
    second, _ = await drive(
        Marked([[refusal(retry_after="0")], [answered()]]), capacity=queue
    )
    assert first.status == second.status == "done"
    assert first.capacity_refusals == second.capacity_refusals == 1
    assert queue.refused == 0


async def test_a_429_without_the_marker_is_not_queued() -> None:
    fake = Marked([[refusal(text="slow down")], [answered()]])
    session, events = await drive(
        fake,
        retry=RetryPolicy(retries=0, sleep=Sleeps()),
        capacity=CapacityQueue(sleep=Sleeps()),
    )
    assert len(fake.calls) == 1
    assert session.status == "error"
    assert session.capacity_refusals == 0
    assert queued_events(events) == []


async def test_a_transport_declaring_no_markers_never_queues() -> None:
    fake = Unmarked([[refusal(retry_after="0")], [answered()]])
    session, _ = await drive(
        fake,
        retry=RetryPolicy(retries=0, sleep=Sleeps()),
        capacity=CapacityQueue(sleep=Sleeps()),
    )
    assert len(fake.calls) == 1
    assert session.status == "error"
    assert session.capacity_refusals == 0


async def test_a_refusal_before_the_first_stream_item_is_queued() -> None:
    fake = MarkedStreaming(
        [[refusal(retry_after="0")], [Delta("progress", "ok "), answered("ok ")]]
    )
    session, events = await drive(fake, capacity=CapacityQueue(sleep=Sleeps()))
    assert session.status == "done"
    assert session.capacity_refusals == 1
    assert [e.text for e in events if e.type == "progress" and e.text == "ok "] == [
        "ok "
    ]


async def test_a_refusal_after_a_stream_item_is_never_queued() -> None:
    fake = MarkedStreaming(
        [[Delta("progress", "partial "), refusal(retry_after="0")], [answered()]]
    )
    session, events = await drive(fake, capacity=CapacityQueue(sleep=Sleeps()))
    assert len(fake.calls) == 1
    assert session.status == "error"
    assert session.capacity_refusals == 0
    assert queued_events(events) == []


# The openai rail, end to end ----------------------------------------------


def _completion(text: str) -> httpx2.Response:
    body = {
        "id": "resp",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 4, "completion_tokens": 2},
    }
    return httpx2.Response(200, json=body)


async def test_the_openai_rail_resends_the_identical_body_after_refusals() -> None:
    bodies: list[bytes] = []
    replies = [
        httpx2.Response(
            429,
            headers={"retry-after": "0"},
            json={"error": {"message": f"{MARKER}: limit reached for this key"}},
        ),
        httpx2.Response(
            429,
            json={"error": {"message": f"{MARKER}: limit reached for this key"}},
        ),
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(request.content)
        if replies:
            return replies.pop(0)
        return _completion("done")

    transport = _rail("openai", handler)
    session = new_session("go")
    sleeps = Sleeps()
    events = [
        e
        async for e in run(
            session,
            transport=transport,
            retry=RetryPolicy(retries=0, sleep=sleeps),
            capacity=CapacityQueue(sleep=sleeps, rng=random.Random(288)),
            policy=Policy(),
            context_window=100000,
        )
    ]
    assert session.status == "done", session.error
    assert len(bodies) == 3
    assert bodies[0] == bodies[1] == bodies[2]
    assert json.loads(bodies[0])["messages"]
    assert session.capacity_refusals == 2
    assert session.retries == 0
    assert session.turns == 1
    assert session.usage.input == 4 and session.usage.output == 2
    assert len(queued_events(events)) == 2
    # The first wait honours Retry-After; the second is backoff for attempt 2.
    assert sleeps.waits[0] == 0.0
    assert 2.0 <= sleeps.waits[1] <= 4.0


async def test_the_openai_rail_without_the_queue_fails_on_the_refusal() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(429, json={"error": {"message": MARKER}})

    session, _ = await drive(
        _rail("openai", handler),
        retry=RetryPolicy(retries=0, sleep=Sleeps()),
    )
    assert session.status == "error"
    assert session.capacity_refusals == 0


# Session accounting ---------------------------------------------------------


async def test_the_session_round_trips_the_counters() -> None:
    fake = Marked([[refusal(retry_after="3")], [answered()]])
    session, _ = await drive(fake, capacity=CapacityQueue(sleep=Sleeps()))
    loaded = loads(dumps(session))
    assert loaded.capacity_refusals == 1
    assert loaded.capacity_wait == 3.0


async def test_an_older_session_file_loads_with_no_queued_refusals() -> None:
    session = new_session("go")
    raw = json.loads(dumps(session))
    del raw["capacity_refusals"]
    del raw["capacity_wait"]
    loaded = loads(json.dumps(raw))
    assert loaded.capacity_refusals == 0
    assert loaded.capacity_wait == 0.0


# The CLI and the contract ---------------------------------------------------


def rows(out: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.splitlines()]


def test_the_queue_is_off_by_default() -> None:
    args = build_parser().parse_args(["run", "go"])
    assert args.capacity_queue is False
    assert args.capacity_wait_seconds is None
    assert args.capacity_refusals is None


def test_the_environment_turns_the_queue_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARE_CAPACITY_QUEUE", "1")
    assert build_parser().parse_args(["run", "go"]).capacity_queue is True


def test_the_flags_reach_the_result_and_session(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = Marked([[refusal(retry_after="0")], [answered()]])
    code = main(
        ["run", "--yes", "--jsonl", "--capacity-queue", "go"],
        transport=fake,
    )
    assert code == 0
    result = rows(capsys.readouterr().out)[-1]
    assert result["status"] == "done"
    assert result["capacity_refusals"] == 1
    assert result["capacity_wait_seconds"] == 0.0
    assert result["retries"] == 0


def test_the_result_reports_zero_without_the_queue(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = Marked([[answered()]])
    assert main(["run", "--yes", "--jsonl", "go"], transport=fake) == 0
    result = rows(capsys.readouterr().out)[-1]
    assert result["capacity_refusals"] == 0
    assert result["capacity_wait_seconds"] == 0


@pytest.mark.parametrize(
    "argv",
    [
        ["--capacity-refusals", "3"],
        ["--capacity-wait-seconds", "30"],
        ["--capacity-queue", "--capacity-refusals", "-1"],
        ["--capacity-queue", "--capacity-wait-seconds", "inf"],
    ],
)
def test_an_invalid_capacity_option_exits_two(argv: list[str]) -> None:
    assert main(["run", "--yes", "--jsonl", *argv, "go"], transport=Marked([])) == 2


def test_the_contract_advertises_the_queue() -> None:
    entry = describe()["capacity_queue"]
    assert entry["flags"] == {
        "enable": "--capacity-queue",
        "wait_seconds": "--capacity-wait-seconds",
        "refusals": "--capacity-refusals",
    }
    assert entry["default"]["enable"] is False
    assert entry["providers"] == ["openai"]
    flags = describe()["flags"]
    assert flags["--capacity-queue"] == {"takes_value": False, "choices": None}
    assert flags["--capacity-refusals"]["takes_value"] is True
