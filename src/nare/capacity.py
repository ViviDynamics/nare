"""Adaptive in-flight concurrency per provider route (issue 289).

A provider that refuses for capacity has told the caller how many requests it
can hold. `RouteLimiter` keeps a limit per route and gates each model request
on it: a qualifying refusal cuts the limit multiplicatively, and a quiet period
raises it by one. The policy is AIMD, and it is the same whichever store holds
the state.

Two stores exist. `LocalStore` serves one process. `SharedStore` serves every
nare process on one host that names the same directory, which is how a caller
that runs many sessions in parallel gets one limit for the route. Its slots
are `flock` locks: the kernel drops a lock when its holder exits, so a process
that dies frees its slot and leaves nothing stale behind. Its state is one JSON
file, read and written under a second lock and replaced atomically.

Only the limit is shared. A limit that drops does not preempt requests already
in flight; they drain, and new ones wait for a slot under the new limit.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, Protocol, TypeVar

if sys.platform != "win32":
    import fcntl

CEILING_DEFAULT = 8
FLOOR_DEFAULT = 1
QUIET_SECONDS_DEFAULT = 60.0
# Multiplicative decrease, and the window in which refusals count as one cut.
CUT_FACTOR = 0.5
CUT_COOLDOWN = 5.0
POLL_SECONDS = 0.05

T = TypeVar("T")


@dataclass(frozen=True)
class Aimd:
    """The bounds and timing of the limit. The limit stays within floor..ceiling."""

    floor: int = FLOOR_DEFAULT
    ceiling: int = CEILING_DEFAULT
    quiet: float = QUIET_SECONDS_DEFAULT
    cooldown: float = CUT_COOLDOWN

    def __post_init__(self) -> None:
        for name, value in (("floor", self.floor), ("ceiling", self.ceiling)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"capacity {name} must be a positive integer")
        if self.floor > self.ceiling:
            raise ValueError("capacity floor must not exceed the ceiling")
        if (
            isinstance(self.quiet, bool)
            or not isinstance(self.quiet, (int, float))
            or not math.isfinite(self.quiet)
            or self.quiet < 0
        ):
            raise ValueError(
                "capacity quiet period must be a nonnegative finite number of seconds"
            )


@dataclass
class LimitState:
    """The limit and when it last moved. Times are wall-clock seconds."""

    limit: int
    last_cut: float | None = None
    last_refusal: float | None = None
    last_raise: float | None = None


@dataclass(frozen=True)
class Change:
    """One adjustment of the limit, and why it was made."""

    limit_before: int
    limit_after: int
    reason: Literal["refusal", "quiet"]


def _clamp(limit: int, policy: Aimd) -> int:
    return min(policy.ceiling, max(policy.floor, limit))


def _elapsed_at_least(now: float, since: float | None, seconds: float) -> bool:
    # A time in the future means the clock moved back, and counts as elapsed:
    # otherwise a moved clock would block every cut or raise until it caught up.
    if since is None or now < since:
        return True
    return now - since >= seconds


def cut(state: LimitState, policy: Aimd, now: float) -> Change | None:
    """A capacity refusal. Every refusal refreshes the quiet clock; the limit is
    cut at most once per cooldown, so one burst of refusals cuts once."""
    state.last_refusal = now
    if not _elapsed_at_least(now, state.last_cut, policy.cooldown):
        return None
    before = _clamp(state.limit, policy)
    after = max(policy.floor, int(before * CUT_FACTOR))
    state.limit = after
    if after == before:
        return None
    state.last_cut = now
    return Change(before, after, "refusal")


def raise_quiet(state: LimitState, policy: Aimd, now: float) -> Change | None:
    """Raise the limit by one if no refusal and no raise came within the quiet
    period. The limit never goes past the ceiling."""
    before = _clamp(state.limit, policy)
    state.limit = before
    if before >= policy.ceiling:
        return None
    marks = [m for m in (state.last_refusal, state.last_raise) if m is not None]
    since = max(marks) if marks else None
    if not _elapsed_at_least(now, since, policy.quiet):
        return None
    state.limit = before + 1
    state.last_raise = now
    return Change(before, before + 1, "quiet")


class Store(Protocol):
    """Where the limit state and the in-flight slots live."""

    def update(
        self, init: Callable[[], LimitState], apply: Callable[[LimitState], T]
    ) -> T:
        """Apply `apply` to the state, creating it with `init` when absent."""
        ...

    def take(self, limit: int) -> Callable[[], None] | None:
        """A slot under `limit`, as its release, or None when all are held."""
        ...


class LocalStore:
    """The state and slots of one process."""

    def __init__(self) -> None:
        self._state: LimitState | None = None
        self._in_flight = 0

    def update(
        self, init: Callable[[], LimitState], apply: Callable[[LimitState], T]
    ) -> T:
        if self._state is None:
            self._state = init()
        return apply(self._state)

    def take(self, limit: int) -> Callable[[], None] | None:
        if self._in_flight >= limit:
            return None
        self._in_flight += 1
        done = False

        def release() -> None:
            nonlocal done
            if done:
                return
            done = True
            self._in_flight -= 1

        return release


def route_dir(base: Path, route: str) -> Path:
    """The directory for one route under `base`. The name is a hash, so a base
    URL in the route does not show in the path."""
    return base / hashlib.sha256(route.encode("utf-8")).hexdigest()[:24]


class SharedStore:
    """The state and slots shared by every process that names `directory`.

    Each slot is `slot-N.lock`, held by an exclusive `flock`. A holder that
    crashes releases its slot with the process. The directory must be on a
    local filesystem: network filesystems do not guarantee `flock`, and this is
    not checked.
    """

    def __init__(self, directory: Path, ceiling: int) -> None:
        if sys.platform == "win32":
            raise ValueError("a shared capacity limiter needs POSIX file locks")
        self._dir = directory
        self._ceiling = ceiling
        self._dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self._dir / "state.json"
        self._lock_path = self._dir / "state.lock"

    def _read(self) -> LimitState | None:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            limit = raw["limit"]
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                return None
            stamps: dict[str, float | None] = {}
            for key in ("last_cut", "last_refusal", "last_raise"):
                value = raw.get(key)
                if value is None:
                    stamps[key] = None
                elif isinstance(value, bool) or not isinstance(value, (int, float)):
                    return None
                else:
                    stamps[key] = float(value)
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return LimitState(
            limit=limit,
            last_cut=stamps["last_cut"],
            last_refusal=stamps["last_refusal"],
            last_raise=stamps["last_raise"],
        )

    def _write(self, state: LimitState) -> None:
        tmp = self._state_path.with_name(f"{self._state_path.name}.{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps(
                {
                    "limit": state.limit,
                    "last_cut": state.last_cut,
                    "last_refusal": state.last_refusal,
                    "last_raise": state.last_raise,
                }
            ),
            encoding="utf-8",
        )
        os.replace(tmp, self._state_path)

    def update(
        self, init: Callable[[], LimitState], apply: Callable[[LimitState], T]
    ) -> T:
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            loaded = self._read()
            state = replace(loaded) if loaded is not None else init()
            result = apply(state)
            if state != loaded:
                self._write(state)
            return result
        finally:
            os.close(fd)  # closing the descriptor drops the lock

    def take(self, limit: int) -> Callable[[], None] | None:
        for index in range(min(limit, self._ceiling)):
            fd = os.open(
                self._dir / f"slot-{index}.lock", os.O_RDWR | os.O_CREAT, 0o600
            )
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                continue
            done = False

            def release(fd: int = fd) -> None:
                nonlocal done
                if done:
                    return
                done = True
                os.close(fd)

            return release
        return None


@dataclass
class Grant:
    """A request's place under the limit. Release it when the request ends."""

    limit: int
    waited: float
    changes: list[Change]
    notes: list[str]
    _release: Callable[[], None] = field(repr=False)

    def release(self) -> None:
        self._release()


class RouteLimiter:
    """The limit for one provider route, over one store.

    `declared` is the concurrency the route's provider declares, when a
    transport can read one. The limit starts there, and a configured ceiling
    above it produces a note, which the first grant carries to the caller.
    """

    def __init__(
        self,
        route: str,
        *,
        store: Store,
        policy: Aimd,
        declared: int | None = None,
        now: Callable[[], float] = time.time,
        poll: float = POLL_SECONDS,
    ) -> None:
        self.route = route
        self.policy = policy
        self._store = store
        self._now = now
        self._poll = poll
        self._start = policy.ceiling if declared is None else _clamp(declared, policy)
        self._notes: list[str] = []
        if declared is not None and declared < policy.ceiling:
            self._notes.append(
                f"configured concurrency {policy.ceiling} exceeds the declared "
                f"limit {declared} for this route; starting at {self._start}"
            )

    def _init(self) -> LimitState:
        return LimitState(limit=self._start, last_raise=self._now())

    def current_limit(self) -> int:
        return self._store.update(self._init, lambda st: _clamp(st.limit, self.policy))

    def refused(self) -> Change | None:
        """Record a qualifying capacity refusal. Returns the cut, if one happened."""
        return self._store.update(
            self._init, lambda st: cut(st, self.policy, self._now())
        )

    async def acquire(self) -> Grant:
        """Wait for a slot under the current limit. Raises the limit first when a
        quiet period has passed."""
        notes, self._notes = self._notes, []
        changes: list[Change] = []
        started = self._now()

        def sync(state: LimitState) -> tuple[int, Change | None]:
            state.limit = _clamp(state.limit, self.policy)
            change = raise_quiet(state, self.policy, self._now())
            return state.limit, change

        while True:
            limit, change = self._store.update(self._init, sync)
            if change is not None:
                changes.append(change)
            release = self._store.take(limit)
            if release is not None:
                return Grant(
                    limit=limit,
                    waited=max(0.0, self._now() - started),
                    changes=changes,
                    notes=notes,
                    _release=release,
                )
            await asyncio.sleep(self._poll)
