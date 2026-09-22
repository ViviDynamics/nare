"""Grading. Pure: it is handed what happened and decides what it means.

Nothing here runs a container or calls a model. `sandbox` produces exit codes
and stdout, `judge` produces booleans, and this module turns both into a
verdict -- which is what makes the whole grading policy unit-testable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

_USAGE_FIELDS = ("input", "output", "cache_read", "cache_write")


@dataclass(frozen=True)
class ResultLine:
    """nare's terminal `result` line, which is state rather than an event."""

    status: str
    turns: int
    usage: dict[str, int]
    stop_reason: str | None
    error: str | None


def _usage_from(raw: Any) -> dict[str, int]:
    source = raw if isinstance(raw, dict) else {}
    return {field: int(source.get(field, 0)) for field in _USAGE_FIELDS}


def parse_stdout(text: str) -> ResultLine | None:
    """Return the last `result` line, or None if the run emitted none.

    None is load-bearing: nare emits no result line when a run never started,
    which the caller turns into an `error` outcome rather than a `fail`.
    """
    found: ResultLine | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            # Noise on stdout, or a line cut off by a kill. Neither is a
            # result line, and neither should crash the run.
            continue
        if not isinstance(payload, dict) or payload.get("type") != "result":
            continue
        found = ResultLine(
            status=str(payload.get("status", "")),
            turns=int(payload.get("turns", 0)),
            usage=_usage_from(payload.get("usage")),
            stop_reason=payload.get("stop_reason"),
            error=payload.get("error"),
        )
    return found
