"""Adaptive concurrency wired into the loop (issue 289).

The limiter gates each model request attempt. A qualifying capacity refusal
cuts the route's limit, the refused request waits and is resent as #288
queues it, and every change of the limit is an event with its reason.
"""

from __future__ import annotations

import asyncio
from typing import Any

from nare.capacity import Aimd, LocalStore, RouteLimiter
from nare.events import Event
from nare.loop import CapacityQueue, RetryPolicy, fresh_queue, run
from nare.session import dumps, loads, new_session
from nare.tools import Policy
from nare.transport import Delta
from test_capacity_queue import Marked, MarkedStreaming, Unmarked, answered, refusal
from test_retry import Sleeps


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def limiter_for(
    *,
    ceiling: int = 8,
    declared: int | None = None,
    clock: Clock | None = None,
    poll: float = 0.001,
) -> RouteLimiter:
    return RouteLimiter(
        "route",
        store=LocalStore(),
        policy=Aimd(floor=1, ceiling=ceiling, quiet=60.0),
        declared=declared,
        now=clock or Clock(),
        poll=poll,
    )


def limit_events(events: list[Event]) -> list[dict[str, Any]]:
    return [e.detail["capacity_limit"] for e in events if "capacity_limit" in e.detail]


async def drive(
    transport: Any,
    capacity: CapacityQueue | None,
    *,
    session: Any = None,
) -> tuple[Any, list[Event]]:
    session = session or new_session("go")
    events = [
        e
        async for e in run(
            session,
            transport=transport,
            retry=RetryPolicy(retries=0, sleep=Sleeps()),
            capacity=capacity,
            policy=Policy(),
            context_window=100000,
        )
    ]
    return session, events


async def test_a_refusal_cuts_the_limit_and_the_request_is_resent() -> None:
    limiter = limiter_for()
    fake = Marked([[refusal(retry_after="0")], [answered()]])
    capacity = CapacityQueue(sleep=Sleeps(), limiter=limiter)
    session, events = await drive(fake, capacity)
    assert session.status == "done"
    assert len(fake.calls) == 2
    assert limiter.current_limit() == 4
    assert session.capacity_limit == 4
    cuts = [e for e in limit_events(events) if e["reason"] == "refusal"]
    assert cuts == [{"limit": 4, "previous": 8, "reason": "refusal"}]


async def test_a_refused_request_releases_its_slot_while_it_waits() -> None:
    # Ceiling 1: if the refused request kept its slot through the wait, the
    # acquire inside the wait would never return.
    limiter = limiter_for(ceiling=1)
    waits: list[float] = []

    async def wait_with_a_free_slot(seconds: float) -> None:
        grant = await asyncio.wait_for(limiter.acquire(), 1.0)
        grant.release()
        waits.append(seconds)

    fake = Marked([[refusal(retry_after="0")], [answered()]])
    capacity = CapacityQueue(sleep=wait_with_a_free_slot, limiter=limiter)
    session, _ = await drive(fake, capacity)
    assert session.status == "done"
    assert waits == [0.0]


async def test_a_refusal_after_a_stream_item_does_not_cut() -> None:
    limiter = limiter_for()
    fake = MarkedStreaming(
        [[Delta("progress", "partial "), refusal(retry_after="0")], [answered()]]
    )
    session, events = await drive(fake, CapacityQueue(sleep=Sleeps(), limiter=limiter))
    assert session.status == "error"
    assert limiter.current_limit() == 8
    assert limit_events(events) == []


async def test_a_transport_without_markers_never_cuts() -> None:
    limiter = limiter_for()
    fake = Unmarked([[refusal(retry_after="0")]])
    session, _ = await drive(fake, CapacityQueue(sleep=Sleeps(), limiter=limiter))
    assert session.status == "error"
    assert limiter.current_limit() == 8


async def test_a_refusal_the_queue_does_not_admit_still_cuts() -> None:
    limiter = limiter_for()
    fake = Marked([[refusal(retry_after="0")]])
    capacity = CapacityQueue(max_refusals=0, sleep=Sleeps(), limiter=limiter)
    session, _ = await drive(fake, capacity)
    assert session.status == "error"
    assert session.capacity_refusals == 0
    assert limiter.current_limit() == 4


async def test_a_quiet_period_raises_the_limit_with_its_reason() -> None:
    clock = Clock()
    limiter = limiter_for(declared=2, clock=clock)
    assert limiter.current_limit() == 2  # the start is recorded before the wait
    clock.now += 60.0
    fake = Marked([[answered()]])
    session, events = await drive(fake, CapacityQueue(sleep=Sleeps(), limiter=limiter))
    assert session.status == "done"
    assert session.capacity_limit == 3
    assert {"limit": 3, "previous": 2, "reason": "quiet"} in limit_events(events)


async def test_the_declared_limit_note_is_emitted_once_per_limiter() -> None:
    limiter = limiter_for(declared=2)
    first_session, first = await drive(
        Marked([[answered()]]), CapacityQueue(sleep=Sleeps(), limiter=limiter)
    )
    second_session, second = await drive(
        Marked([[answered()]]), CapacityQueue(sleep=Sleeps(), limiter=limiter)
    )
    notes = [
        e
        for e in first
        if e.detail.get("capacity_limit", {}).get("reason") == "declared"
    ]
    assert len(notes) == 1
    assert "declared" in notes[0].text
    assert not [
        e
        for e in second
        if "capacity_limit" in e.detail
        and e.detail["capacity_limit"]["reason"] == "declared"
    ]
    assert first_session.status == second_session.status == "done"


async def test_time_spent_waiting_for_a_slot_is_visible() -> None:
    limiter = RouteLimiter(
        "route",
        store=LocalStore(),
        policy=Aimd(floor=1, ceiling=1, quiet=60.0),
        poll=0.005,
    )
    held = await limiter.acquire()

    async def release_later() -> None:
        await asyncio.sleep(0.05)
        held.release()

    releaser = asyncio.create_task(release_later())
    session, events = await drive(
        Marked([[answered()]]), CapacityQueue(sleep=Sleeps(), limiter=limiter)
    )
    await releaser
    assert session.status == "done"
    assert session.capacity_slot_wait > 0
    waited = [e for e in limit_events(events) if e.get("reason") == "slot"]
    assert waited and waited[0]["waited"] > 0


async def test_without_a_limiter_the_new_fields_stay_at_rest() -> None:
    fake = Marked([[answered()]])
    session, events = await drive(fake, CapacityQueue(sleep=Sleeps()))
    assert session.capacity_limit is None
    assert session.capacity_slot_wait == 0.0
    assert limit_events(events) == []


async def test_fresh_queue_keeps_the_shared_limiter() -> None:
    limiter = limiter_for()
    queue = CapacityQueue(limiter=limiter)
    assert fresh_queue(queue) is not None
    refreshed = fresh_queue(queue)
    assert refreshed is not None
    assert refreshed.limiter is limiter


def test_limit_fields_round_trip_through_a_saved_session() -> None:
    session = new_session("go")
    session.capacity_limit = 3
    session.capacity_slot_wait = 1.5
    loaded = loads(dumps(session))
    assert loaded.capacity_limit == 3
    assert loaded.capacity_slot_wait == 1.5
    old = loads(dumps(new_session("go")))
    assert old.capacity_limit is None
    assert old.capacity_slot_wait == 0.0
