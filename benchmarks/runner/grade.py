"""Grading. Pure: it is handed what happened and decides what it means.

Nothing here runs a container or calls a model. `sandbox` produces exit codes
and stdout, `judge` produces booleans, and this module turns both into a
verdict -- which is what makes the whole grading policy unit-testable.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from benchmarks.runner.case import Check, Judge

USAGE_FIELDS = ("input", "output", "cache_read", "cache_write")


@dataclass(frozen=True)
class ResultLine:
    """nare's terminal `result` line, which is state rather than an event."""

    status: str
    turns: int
    usage: dict[str, int]
    stop_reason: str | None
    error: str | None
    questions: list[str] = field(default_factory=list)


def _usage_from(raw: Any) -> dict[str, int]:
    source = raw if isinstance(raw, dict) else {}
    return {field: int(source.get(field, 0)) for field in USAGE_FIELDS}


def _questions_from(raw: Any) -> list[str]:
    return [str(q) for q in raw] if isinstance(raw, list) else []


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
            questions=_questions_from(payload.get("questions")),
        )
    return found


@dataclass(frozen=True)
class ContextUse:
    """What nare reported about its context: the window and the compactions.

    Read from progress events, on every rep, error reps included: a rep that
    died after compacting still compacted. `compactions` is None, unknown,
    when the stream carried neither a window nor a compaction: nare emits its
    window before its first turn, so such a rep never reached one, and a 0
    would read as a rep that ran and never compacted.
    """

    window: dict[str, int | str] | None
    compactions: int | None


def _window_from(raw: Any) -> dict[str, int | str] | None:
    if not isinstance(raw, dict):
        return None
    tokens, source = raw.get("tokens"), raw.get("source")
    # `bool` is an `int`, and a window of `True` tokens is not a window.
    if not isinstance(tokens, int) or isinstance(tokens, bool):
        return None
    if not isinstance(source, str):
        return None
    return {"tokens": tokens, "source": source}


def context_use(text: str) -> ContextUse:
    """The last `context_window` progress event and the count of `compaction` ones."""
    window: dict[str, int | str] | None = None
    compactions = 0
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("type") != "progress":
            continue
        detail = payload.get("detail")
        if not isinstance(detail, dict):
            continue
        seen = _window_from(detail.get("context_window"))
        if seen is not None:
            window = seen
        if "compaction" in detail:
            compactions += 1
    if window is None and compactions == 0:
        return ContextUse(window=None, compactions=None)
    return ContextUse(window=window, compactions=compactions)


Outcome = Literal["pass", "fail", "error"]


def infra_error(line: ResultLine) -> bool:
    """A result line reporting that the run broke underneath the agent.

    nare catches every transport exception (a 429, a 5xx, a refused
    connection) and still emits a result line, with status `error` and no
    stop_reason. Running out of turns or tokens keeps a stop_reason: that is
    the agent's doing, and stays a failure.
    """
    return line.status == "error" and line.stop_reason is None


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
    met: Sequence[bool | None] | None,
) -> Outcome:
    """Decide one repetition.

    `met` holds one verdict per claim, None where the judge said "unknown".
    An unknown is never a false: it can only settle the rep as a pass when
    the true claims already reach `min_met`, and as a fail when even counting
    every unknown as true would not reach it. Between those, the rep is an
    `error`, because the judge did not settle it.

    `error` means the repetition could not be judged at all, and is counted
    against the pass rate, never silently dropped. It is returned here only when a
    gating judge produced no answer or could not settle the rep; every other
    `error` -- a dead container, a missing result line, an `infra_error` -- is
    decided by the caller before grading starts.
    """
    if judge is not None and judge.min_met is not None and met is None:
        return "error"
    if not all(check.passed for check in checks):
        return "fail"
    if judge is not None and judge.min_met is not None:
        assert met is not None  # narrowed by the guard above
        true_count = sum(1 for value in met if value is True)
        open_count = sum(1 for value in met if value is None)
        if true_count >= judge.min_met:
            return "pass"
        if true_count + open_count < judge.min_met:
            return "fail"
        return "error"
    return "pass"
