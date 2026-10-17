"""Running every judge over kept evidence, one repetition at a time.

The judges see the same task, diff and closing message the benchmark's own
judge saw, read back from the files run_rep keeps. Nothing here decides what
the answers mean: that is agreement.py's job.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from benchmarks.runner.agreement import TOO_LARGE, JudgeName, Row, Skip
from benchmarks.runner.case import Case
from benchmarks.runner.jev import (
    JEV_STATE_LIMIT,
    Answer,
    Choice,
    Jev,
    JevError,
    Noul,
    estimated_tokens,
)
from benchmarks.runner.judge import JudgeError, score
from benchmarks.runner.report import RepRecord
from benchmarks.runner.triage import (
    cause_question,
    claimed_done,
    code_cause,
    triage_reference,
    triage_state,
)
from nare.session import Message
from nare.transport import Reply, Transport

# JUDGE_SYSTEM's rules, restated against the state's field names.
CLAIM_RULES = (
    "Judge only what `diff` shows. A claim about a file's contents is true only "
    "if `diff` shows it. `closing_message` is evidence only for claims about "
    "what the agent said or asked."
)
CLAIM_CRITERIA = {
    "true": "The evidence shows the claim holds.",
    "false": (
        "The evidence does not show the claim holds, including when it says "
        "nothing about it."
    ),
}


@dataclass(frozen=True)
class Evidence:
    diff: str
    closing: str
    messages: list[Any]  # session.json's messages; empty without one


def _exact(path: Path) -> str:
    # Bytes, not read_text: universal newlines would turn a \r the judge read
    # into a \n, and the evidence would no longer be what it judged.
    return path.read_bytes().decode("utf-8")


def _messages(path: Path) -> list[Any]:
    """A missing or unreadable session leaves the transcript empty."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    messages = raw.get("messages") if isinstance(raw, dict) else None
    return messages if isinstance(messages, list) else []


def load_evidence(results_dir: Path, rec: RepRecord) -> Evidence | None:
    """`results_dir` is benchmarks/results/, which `artifacts` is relative to."""
    if not rec.artifacts:
        return None
    kept = results_dir / rec.artifacts
    diff, closing = kept / "diff.patch", kept / "closing.txt"
    if not (diff.is_file() and closing.is_file()):
        return None
    return Evidence(_exact(diff), _exact(closing), _messages(kept / "session.json"))


def select_work(
    records: Sequence[RepRecord],
    cases: Mapping[str, Case],
    results_dir: Path,
    only: Collection[str] | None = None,
) -> tuple[list[tuple[Case, RepRecord, Evidence]], list[Skip]]:
    """The repetitions with something to judge or triage, and why the rest
    were skipped. A passing rep of an unjudged case has neither, so it is
    neither compared nor skipped."""
    work: list[tuple[Case, RepRecord, Evidence]] = []
    skips: list[Skip] = []
    for rec in records:
        if only and rec.case not in only:
            continue
        case = cases.get(rec.case)
        if case is None:
            skips.append(Skip(rec.case, rec.rep, "case no longer exists"))
            continue
        if case.judge is None and rec.outcome != "fail":
            continue
        evidence = load_evidence(results_dir, rec)
        if evidence is None:
            reason = (
                "errored before its diff was taken"
                if rec.outcome == "error"
                else "no evidence files"
            )
            skips.append(Skip(rec.case, rec.rep, reason))
            continue
        work.append((case, rec, evidence))
    return work, skips


def claims_state(case: Case, evidence: Evidence) -> dict[str, str]:
    return {
        "task": case.prompt,
        "diff": evidence.diff,
        "closing_message": evidence.closing,
    }


def claims_questions(case: Case) -> dict[str, dict[str, Any]]:
    """One noul per claim, in order, with ids c1 to cN."""
    assert case.judge is not None, "only judged cases have claims"
    return {
        f"c{n}": {
            "type": "noul",
            "instructions": f"{claim}\n\n{CLAIM_RULES}",
            "criteria": dict(CLAIM_CRITERIA),
        }
        for n, claim in enumerate(case.judge.assertions, start=1)
    }


def too_large(state: Any, questions: Mapping[str, Mapping[str, Any]]) -> bool:
    """Nothing is truncated to fit: a truncated diff asks a different question."""
    longest = max(
        (str(q.get("instructions", "")) for q in questions.values()),
        key=len,
        default="",
    )
    return estimated_tokens(state, longest) > JEV_STATE_LIMIT


class RecordingTransport:
    """Keeps each reply's usage, which score() does not return, and counts the
    calls that raised, which score() swallows. judge.py is not changed to make
    room for either."""

    def __init__(self, inner: Transport) -> None:
        self.inner = inner
        self.replies: list[Reply] = []
        self.raised = 0

    async def context_window(self) -> int | None:
        return await self.inner.context_window()

    async def turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> Reply:
        try:
            reply = await self.inner.turn(messages, tools)
        except Exception:
            self.raised += 1
            raise
        self.replies.append(reply)
        return reply

    def take(self) -> tuple[int, float | None]:
        """Tokens and cost since the last take. Cost is None when any call
        raised or any reply had none: a call that timed out may still have been
        billed, and a partial sum would read as the whole. No calls at all is a
        true zero."""
        replies, self.replies = self.replies, []
        raised, self.raised = self.raised, 0
        tokens = sum(r.usage.total_tokens for r in replies)
        costs = [r.cost for r in replies]
        if raised or any(c is None for c in costs):
            return tokens, None
        return tokens, sum(c for c in costs if c is not None)


@dataclass(frozen=True)
class Judges:
    jev: Jev
    llm: RecordingTransport
    llm_model: str
    reference: RecordingTransport  # JUDGE_SYSTEM, for claims
    reference_triage: RecordingTransport  # TRIAGE_SYSTEM
    reference_model: str
    clock: Callable[[], float] = time.monotonic


def _jev_error(exc: JevError) -> str:
    return f"{exc}: {exc.body}" if exc.body else str(exc)


def _p_yes(answer: Answer) -> float:
    assert isinstance(answer, Noul), "the client checked every answer's type"
    return answer.p_yes


async def _jev_claims(
    rec: RepRecord, state: Any, questions: dict[str, dict[str, Any]], judges: Judges
) -> Row:
    model = judges.jev.model
    if too_large(state, questions):
        return Row(rec.case, rec.rep, "claims", "jev", model, error=TOO_LARGE)
    try:
        reply = await judges.jev.ask(state, questions)
    except JevError as exc:
        return Row(
            rec.case,
            rec.rep,
            "claims",
            "jev",
            model,
            latency_s=exc.latency_s,
            attempts=exc.attempts,
            error=_jev_error(exc),
        )
    return Row(
        rec.case,
        rec.rep,
        "claims",
        "jev",
        reply.model,
        verdicts=[_p_yes(reply.answers[qid]) for qid in questions],
        tokens=reply.input_tokens,
        cost=reply.cost,
        cost_estimated=reply.cost_estimated,
        latency_s=reply.latency_s,
        attempts=reply.attempts,
    )


async def _scored(
    case: Case,
    rec: RepRecord,
    evidence: Evidence,
    judge: JudgeName,
    transport: RecordingTransport,
    model: str,
    clock: Callable[[], float],
) -> Row:
    started = clock()
    try:
        met, _, attempts = await score(
            case, evidence.diff, evidence.closing, transport=transport
        )
    except JudgeError as exc:
        tokens, cost = transport.take()
        return Row(
            rec.case,
            rec.rep,
            "claims",
            judge,
            model,
            tokens=tokens,
            cost=cost,
            latency_s=clock() - started,
            attempts=len(exc.attempts),
            error=f"judge failed: {exc}",
        )
    tokens, cost = transport.take()
    return Row(
        rec.case,
        rec.rep,
        "claims",
        judge,
        model,
        verdicts=met,
        tokens=tokens,
        cost=cost,
        latency_s=clock() - started,
        attempts=len(attempts),
    )


async def judge_claims(
    case: Case, rec: RepRecord, evidence: Evidence, judges: Judges
) -> list[Row]:
    """Jev, then the LLM judge, then the reference: always in that order."""
    state = claims_state(case, evidence)
    return [
        await _jev_claims(rec, state, claims_questions(case), judges),
        await _scored(
            case, rec, evidence, "llm", judges.llm, judges.llm_model, judges.clock
        ),
        await _scored(
            case,
            rec,
            evidence,
            "reference",
            judges.reference,
            judges.reference_model,
            judges.clock,
        ),
    ]


async def _jev_cause(
    rec: RepRecord, state: dict[str, Any], done: bool, judges: Judges
) -> Row:
    try:
        reply = await judges.jev.ask(state, {"cause": cause_question()})
    except JevError as exc:
        return Row(
            rec.case,
            rec.rep,
            "triage",
            "jev",
            judges.jev.model,
            claimed_done=done,
            latency_s=exc.latency_s,
            attempts=exc.attempts,
            error=_jev_error(exc),
        )
    answer = reply.answers["cause"]
    assert isinstance(answer, Choice), "the client checked every answer's type"
    return Row(
        rec.case,
        rec.rep,
        "triage",
        "jev",
        reply.model,
        cause=answer.choice,
        probabilities=answer.probabilities,
        confidence=answer.confidence,
        claimed_done=done,
        tokens=reply.input_tokens,
        cost=reply.cost,
        cost_estimated=reply.cost_estimated,
        latency_s=reply.latency_s,
        attempts=reply.attempts,
    )


async def _reference_cause(
    rec: RepRecord, state: dict[str, Any], done: bool, judges: Judges
) -> Row:
    transport, model = judges.reference_triage, judges.reference_model
    started = judges.clock()
    try:
        cause, attempts = await triage_reference(state, transport=transport)
    except JudgeError as exc:
        tokens, cost = transport.take()
        return Row(
            rec.case,
            rec.rep,
            "triage",
            "reference",
            model,
            claimed_done=done,
            tokens=tokens,
            cost=cost,
            latency_s=judges.clock() - started,
            attempts=len(exc.attempts),
            error=f"judge failed: {exc}",
        )
    tokens, cost = transport.take()
    return Row(
        rec.case,
        rec.rep,
        "triage",
        "reference",
        model,
        cause=cause,
        claimed_done=done,
        tokens=tokens,
        cost=cost,
        latency_s=judges.clock() - started,
        attempts=len(attempts),
    )


async def triage_rep(
    case: Case, rec: RepRecord, evidence: Evidence, judges: Judges
) -> list[Row]:
    done = claimed_done(rec)
    coded = code_cause(rec, case.judge.min_met if case.judge else None)
    if coded is not None:
        return [
            Row(
                rec.case,
                rec.rep,
                "triage",
                "code",
                None,
                cause=coded,
                claimed_done=done,
            )
        ]
    state = triage_state(
        case.prompt, rec, evidence.diff, evidence.closing, evidence.messages
    )
    if state is None:
        both: tuple[tuple[JudgeName, str], ...] = (
            ("jev", judges.jev.model),
            ("reference", judges.reference_model),
        )
        return [
            Row(
                rec.case,
                rec.rep,
                "triage",
                judge,
                model,
                claimed_done=done,
                error=TOO_LARGE,
            )
            for judge, model in both
        ]
    return [
        await _jev_cause(rec, state, done, judges),
        await _reference_cause(rec, state, done, judges),
    ]


async def compare_rep(
    case: Case, rec: RepRecord, evidence: Evidence, judges: Judges
) -> list[Row]:
    rows: list[Row] = []
    if case.judge is not None:
        rows += await judge_claims(case, rec, evidence, judges)
    if rec.outcome == "fail":
        rows += await triage_rep(case, rec, evidence, judges)
    return rows


async def run_comparison(
    work: Sequence[tuple[Case, RepRecord, Evidence]], judges: Judges, out: Path
) -> list[Row]:
    """Sequential, as run_case is, so latency is measured without contention.
    Rows are written and flushed as each repetition finishes: an interrupted
    comparison keeps every finished repetition."""
    rows: list[Row] = []
    with out.open("w", encoding="utf-8") as handle:
        for case, rec, evidence in work:
            for row in await compare_rep(case, rec, evidence, judges):
                handle.write(json.dumps(asdict(row)) + "\n")
                handle.flush()
                rows.append(row)
    return rows
