"""Grading. Pure: it is handed what happened and decides what it means.

Nothing here runs a container or calls a model. `sandbox` produces exit codes
and stdout, `judge` produces booleans, and this module turns both into a
verdict -- which is what makes the whole grading policy unit-testable.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from benchmarks.runner.case import Check, Judge

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


Outcome = Literal["pass", "fail", "error"]


@dataclass(frozen=True)
class CheckResult:
    label: str
    passed: bool
    detail: str


def grade_result_check(check: Check, line: ResultLine) -> CheckResult:
    """Every condition a result check names must hold, not just one."""
    failures: list[str] = []
    if check.status is not None and line.status != check.status:
        failures.append(f"status {line.status!r}, wanted {check.status!r}")
    if check.max_turns is not None and line.turns > check.max_turns:
        failures.append(f"took {line.turns} turns, wanted at most {check.max_turns}")
    wanted = " and ".join(
        part
        for part in (
            f"status={check.status}" if check.status is not None else "",
            f"max_turns={check.max_turns}" if check.max_turns is not None else "",
        )
        if part
    )
    return CheckResult(
        label=f"result: {wanted}",
        passed=not failures,
        detail="; ".join(failures),
    )


def grade_bash_check(check: Check, exit_code: int, output: str) -> CheckResult:
    return CheckResult(
        label=f"bash: {check.cmd}",
        passed=exit_code == 0,
        detail="" if exit_code == 0 else f"exit {exit_code}\n{output}".strip(),
    )


def rep_outcome(
    checks: Sequence[CheckResult],
    judge: Judge | None,
    met: list[bool] | None,
) -> Outcome:
    """Decide one repetition.

    `error` means the repetition could not be judged at all, and is counted in
    neither the numerator nor the denominator. It is returned here only when a
    gating judge produced no answer; every other `error` -- a dead container, a
    missing result line -- is decided by the caller before grading starts.
    """
    if judge is not None and judge.min_met is not None and met is None:
        return "error"
    if not all(check.passed for check in checks):
        return "fail"
    if judge is not None and judge.min_met is not None:
        assert met is not None  # narrowed by the guard above
        if sum(met) < judge.min_met:
            return "fail"
    return "pass"
