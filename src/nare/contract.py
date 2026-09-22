"""The machine contract: what a caller may rely on, and how it finds out.

A caller turns nare's event stream, session file and exit code into decisions.
Before this module, a change to any of those shapes reached the caller as a
wrong decision rather than as a loud failure. The version here is the thing a
caller checks once, and `nare run --contract N` is how it refuses to guess.

The policy is stated in docs/contract.md and enforced by a test: one contract
version at a time, additive changes keep the number, anything a caller could
mis-read bumps it.
"""

from __future__ import annotations

from typing import Any, get_args

from nare.events import EventType
from nare.session import Status

CONTRACT_VERSION = 1

# One code per terminal status, and these three are the whole set. done and
# blocked both exit 0 because both are runs that did what was asked: blocked
# carries questions, which is an outcome, not a failure.
EXIT_CODES: dict[Status, int] = {"done": 0, "blocked": 0, "error": 1}

# A run that never started: no session existed, so no result line is emitted
# and nothing reached stdout. A caller that sees this knows its invocation was
# wrong rather than its task.
NEVER_STARTED = 2


def describe() -> dict[str, Any]:
    """The contract as data, for a caller to read before it spawns a run."""
    return {
        "contract": CONTRACT_VERSION,
        "exit_codes": dict(EXIT_CODES),
        "never_started": NEVER_STARTED,
        "statuses": list(get_args(Status)),
        "event_types": list(get_args(EventType)),
    }
