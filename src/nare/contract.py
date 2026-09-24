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

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any, get_args

from nare.events import REDACTION_RULES, REDACTION_VERSION, EventType

if TYPE_CHECKING:
    from nare.session import Status

CONTRACT_VERSION = 1

# The release that produced a run. Read from the installed distribution when
# there is one, so a wheel's metadata and what nare reports can never disagree;
# the fallback is for a source checkout, which has no distribution to read.
try:  # pragma: no cover - exercised by whichever branch the environment takes
    NARE_VERSION = version("nare")
except PackageNotFoundError:  # pragma: no cover
    NARE_VERSION = "0.0.0+source"

# One code per terminal status, and these three are the whole set. done and
# blocked both exit 0 because both are runs that did what was asked: blocked
# carries questions, which is an outcome, not a failure.
EXIT_CODES: dict[Status, int] = {"done": 0, "blocked": 0, "error": 1}

# A run that never started: no session existed, so no result line is emitted
# and nothing reached stdout. A caller that sees this knows its invocation was
# wrong rather than its task.
NEVER_STARTED = 2


def describe() -> dict[str, Any]:
    """The contract as data, for a caller to read before it spawns a run.

    The Status import is local: session.py takes CONTRACT_VERSION from here, so
    a module-level import back into session would be a cycle. One definition of
    the version is worth a function-level import.
    """
    from nare.session import Status

    return {
        "contract": CONTRACT_VERSION,
        "nare": NARE_VERSION,
        "exit_codes": dict(EXIT_CODES),
        "never_started": NEVER_STARTED,
        "statuses": list(get_args(Status)),
        "event_types": list(get_args(EventType)),
        # The redaction is part of what a caller relies on: event `text` and
        # whatever a caller pipes through `nare redact` carry it, and a rule
        # change is a change to what those mean.
        "redaction": {
            "version": REDACTION_VERSION,
            "rules": list(REDACTION_RULES),
        },
    }
