"""Adaptive in-flight limiter per provider route (issue 289).

The policy is AIMD: a qualifying capacity refusal cuts the limit once per
cooldown, and a quiet period raises it by one. Two stores hold the state: an
in-process one, and a file-locked one that nare processes share. The shared
store's slots are `flock` locks, so a holder that dies frees its slot.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from nare.capacity import (
    Aimd,
    Change,
    LimitState,
    LocalStore,
    RouteLimiter,
    SharedStore,
    cut,
    raise_quiet,
    route_dir,
)

POLICY = Aimd(floor=1, ceiling=8, quiet=60.0)


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


# --- policy -----------------------------------------------------------------


def test_aimd_cut_halves_and_records_the_change() -> None:
    state = LimitState(limit=8, last_raise=0.0)
    change = cut(state, POLICY, now=100.0)
    assert change == Change(limit_before=8, limit_after=4, reason="refusal")
    assert state.limit == 4
    assert state.last_cut == 100.0
    assert state.last_refusal == 100.0


def test_aimd_cut_never_goes_below_the_floor() -> None:
    policy = Aimd(floor=2, ceiling=8, quiet=60.0)
    state = LimitState(limit=3, last_raise=0.0)
    assert cut(state, policy, now=100.0) == Change(3, 2, "refusal")
    state.last_cut = None
    assert cut(state, policy, now=200.0) is None
    assert state.limit == 2


def test_aimd_one_burst_of_refusals_cuts_once() -> None:
    state = LimitState(limit=8, last_raise=0.0)
    assert cut(state, POLICY, now=100.0) is not None
    # Eight refusals from one burst land within the cooldown: one cut.
    for offset in (0.1, 0.5, 1.0, 4.9):
        assert cut(state, POLICY, now=100.0 + offset) is None
    assert state.limit == 4
    # A refusal after the cooldown is a new signal.
    assert cut(state, POLICY, now=106.0) == Change(4, 2, "refusal")


def test_aimd_refusal_inside_cooldown_still_resets_the_quiet_clock() -> None:
    state = LimitState(limit=8, last_raise=0.0)
    cut(state, POLICY, now=100.0)
    cut(state, POLICY, now=102.0)  # deduplicated, but still a refusal
    assert state.last_refusal == 102.0
    # The quiet period counts from the last refusal, not the cut.
    assert raise_quiet(state, POLICY, now=101.0 + 60.0) is None
    assert raise_quiet(state, POLICY, now=102.0 + 60.0) == Change(4, 5, "quiet")


def test_aimd_quiet_period_raises_by_one_up_to_the_ceiling() -> None:
    state = LimitState(limit=4, last_raise=100.0)
    assert raise_quiet(state, POLICY, now=159.0) is None
    assert raise_quiet(state, POLICY, now=160.0) == Change(4, 5, "quiet")
    assert state.last_raise == 160.0
    state.limit = 8
    assert raise_quiet(state, POLICY, now=10_000.0) is None


def test_aimd_quiet_period_is_blocked_by_a_refusal() -> None:
    state = LimitState(limit=4, last_raise=0.0, last_refusal=150.0)
    assert raise_quiet(state, POLICY, now=180.0) is None
    assert raise_quiet(state, POLICY, now=210.0) == Change(4, 5, "quiet")


def test_aimd_backwards_clock_counts_as_elapsed() -> None:
    # Persisted times can come from a host whose clock moved back.
    state = LimitState(limit=8, last_raise=5_000.0)
    assert cut(state, POLICY, now=100.0) == Change(8, 4, "refusal")
    state = LimitState(limit=4, last_raise=5_000.0, last_refusal=5_000.0)
    assert raise_quiet(state, POLICY, now=100.0) == Change(4, 5, "quiet")


def test_aimd_rejects_nonsense_bounds() -> None:
    with pytest.raises(ValueError):
        Aimd(floor=0, ceiling=8, quiet=60.0)
    with pytest.raises(ValueError):
        Aimd(floor=9, ceiling=8, quiet=60.0)
    with pytest.raises(ValueError):
        Aimd(floor=1, ceiling=8, quiet=-1.0)
    with pytest.raises(ValueError):
        Aimd(floor=cast(int, True), ceiling=8, quiet=60.0)


# --- in-process store -------------------------------------------------------


async def test_local_limit_blocks_the_next_request_until_a_release() -> None:
    clock = Clock()
    limiter = RouteLimiter(
        "route", store=LocalStore(), policy=Aimd(1, 2, 60.0), now=clock, poll=0.001
    )
    first = await limiter.acquire()
    second = await limiter.acquire()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(limiter.acquire(), 0.05)
    first.release()
    third = await asyncio.wait_for(limiter.acquire(), 1.0)
    second.release()
    third.release()


async def test_local_release_twice_frees_one_slot_only() -> None:
    limiter = RouteLimiter(
        "route", store=LocalStore(), policy=Aimd(1, 1, 60.0), now=Clock(), poll=0.001
    )
    grant = await limiter.acquire()
    grant.release()
    grant.release()
    held = await limiter.acquire()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(limiter.acquire(), 0.05)
    held.release()


async def test_local_quiet_period_raises_the_limit_and_reports_it() -> None:
    clock = Clock()
    limiter = RouteLimiter(
        "route",
        store=LocalStore(),
        policy=Aimd(1, 3, 60.0),
        declared=1,
        now=clock,
        poll=0.001,
    )
    assert limiter.current_limit() == 1
    clock.now += 60.0
    grant = await limiter.acquire()
    assert grant.changes == [Change(1, 2, "quiet")]
    assert grant.limit == 2
    assert limiter.current_limit() == 2
    grant.release()


async def test_refused_cuts_the_limit_and_returns_the_change() -> None:
    limiter = RouteLimiter(
        "route", store=LocalStore(), policy=POLICY, now=Clock(), poll=0.001
    )
    assert limiter.current_limit() == 8
    assert limiter.refused() == Change(8, 4, "refusal")
    assert limiter.refused() is None
    assert limiter.current_limit() == 4


# --- declared-limit seam ----------------------------------------------------


async def test_declared_limit_starts_below_the_ceiling_and_warns_once() -> None:
    limiter = RouteLimiter(
        "route", store=LocalStore(), policy=POLICY, declared=2, now=Clock(), poll=0.001
    )
    assert limiter.current_limit() == 2
    first = await limiter.acquire()
    assert len(first.notes) == 1
    assert "8" in first.notes[0] and "2" in first.notes[0]
    second = await limiter.acquire()
    assert second.notes == []
    first.release()
    second.release()


async def test_declared_limit_at_or_above_the_ceiling_is_silent() -> None:
    limiter = RouteLimiter(
        "route", store=LocalStore(), policy=POLICY, declared=64, now=Clock(), poll=0.001
    )
    assert limiter.current_limit() == 8
    grant = await limiter.acquire()
    assert grant.notes == []
    grant.release()


# --- shared store -----------------------------------------------------------


def shared(
    tmp_path: Path, policy: Aimd = POLICY, clock: Clock | None = None
) -> RouteLimiter:
    return RouteLimiter(
        "route",
        store=SharedStore(tmp_path, policy.ceiling),
        policy=policy,
        now=clock or Clock(),
        poll=0.001,
    )


async def test_shared_slots_are_exclusive_across_handles(tmp_path: Path) -> None:
    one = shared(tmp_path, Aimd(1, 1, 60.0))
    two = shared(tmp_path, Aimd(1, 1, 60.0))
    held = await one.acquire()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(two.acquire(), 0.05)
    held.release()
    taken = await asyncio.wait_for(two.acquire(), 1.0)
    taken.release()


async def test_shared_limit_is_one_value_for_every_handle(tmp_path: Path) -> None:
    one = shared(tmp_path)
    two = shared(tmp_path)
    assert one.refused() == Change(8, 4, "refusal")
    assert two.current_limit() == 4


async def test_shared_quiet_increase_happens_once_per_period(tmp_path: Path) -> None:
    clock = Clock()
    one = shared(tmp_path, Aimd(1, 8, 60.0), clock)
    two = shared(tmp_path, Aimd(1, 8, 60.0), clock)
    one.refused()
    clock.now += 60.0
    first = await one.acquire()
    second = await two.acquire()
    assert [c.reason for c in first.changes] == ["quiet"]
    assert second.changes == []
    assert one.current_limit() == 5
    first.release()
    second.release()


async def test_shared_corrupt_state_is_reinitialized(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text("{not json", encoding="utf-8")
    limiter = shared(tmp_path)
    assert limiter.current_limit() == 8
    assert '"limit": 8' in (tmp_path / "state.json").read_text(encoding="utf-8")


async def test_shared_slot_of_a_killed_process_is_recovered(tmp_path: Path) -> None:
    # The holder takes the only slot, then is killed while holding it. Its
    # kernel-held lock must go with it, or this process would wait forever.
    script = (
        "import asyncio, sys\n"
        "from pathlib import Path\n"
        "from nare.capacity import Aimd, RouteLimiter, SharedStore\n"
        "async def main():\n"
        "    limiter = RouteLimiter('route', store=SharedStore(Path(sys.argv[1]), 1),"
        " policy=Aimd(1, 1, 60.0), poll=0.001)\n"
        "    await limiter.acquire()\n"
        "    print('held', flush=True)\n"
        "    await asyncio.sleep(60)\n"
        "asyncio.run(main())\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "held"
        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=10)
        survivor = shared(tmp_path, Aimd(1, 1, 60.0))
        grant = await asyncio.wait_for(survivor.acquire(), 5.0)
        grant.release()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def test_route_dir_is_stable_and_hides_the_route(tmp_path: Path) -> None:
    secret = "https://internal.example.test/v1|model-x"
    path = route_dir(tmp_path, secret)
    assert path == route_dir(tmp_path, secret)
    assert path.parent == tmp_path
    assert "internal" not in path.name
    assert route_dir(tmp_path, "other") != path
