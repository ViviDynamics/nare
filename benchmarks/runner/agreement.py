"""Every number compare-judges prints. Pure: handed rows, does arithmetic.

Agreement is with the reference model, not with ground truth: nobody
hand-labels anything, so every figure here says how close a judge gets to the
reference, never whether either is right.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from benchmarks.runner.case import Case
from benchmarks.runner.grade import rep_outcome
from benchmarks.runner.report import RepRecord

TaskName = Literal["claims", "triage"]
JudgeName = Literal["jev", "llm", "reference", "code"]
CUTOFFS = (0.5, 0.6, 0.7, 0.8, 0.9)
BANDS = (0.6, 0.7, 0.8, 0.9)
TOO_LARGE = "too_large"
GROUND_TRUTH = "Agreement is with the reference model, not ground truth."


@dataclass(frozen=True)
class Row:
    """One line of compare-<cstamp>.jsonl: one repetition, task, and judge."""

    case: str
    rep: int
    task: TaskName
    judge: JudgeName
    model: str | None  # for Jev, the pinned version it reported
    verdicts: list[float] | list[bool] | None = None  # claims: P(yes), or booleans
    cause: str | None = None  # triage
    probabilities: dict[str, float] | None = None  # triage, Jev only
    confidence: float | None = None  # triage, Jev only
    claimed_done: bool | None = None  # triage
    tokens: int = 0
    cost: float | None = None  # None when unknown
    cost_estimated: bool = False
    latency_s: float = 0.0
    attempts: int = 0
    error: str | None = None


@dataclass(frozen=True)
class Skip:
    case: str
    rep: int
    reason: str


@dataclass(frozen=True)
class Header:
    stamp: str
    model: str
    judge_model: str
    reference_model: str


@dataclass(frozen=True)
class ClaimPair:
    """One claim both a judge and the reference answered."""

    case: str
    index: int
    value: float  # P(yes) for Jev; 1.0 or 0.0 for the LLM judge
    reference: bool


def _answered(
    rows: Sequence[Row], task: TaskName, judge: JudgeName
) -> dict[tuple[str, int], Row]:
    return {
        (r.case, r.rep): r
        for r in rows
        if r.task == task and r.judge == judge and r.error is None
    }


def claim_pairs(rows: Sequence[Row], judge: JudgeName) -> list[ClaimPair]:
    """A judge that could not answer is dropped, never counted as wrong. A
    reference that could not answer drops the repetition for every judge."""
    reference = _answered(rows, "claims", "reference")
    other = _answered(rows, "claims", judge)
    pairs: list[ClaimPair] = []
    for key in sorted(reference.keys() & other.keys()):
        mine, theirs = other[key].verdicts or [], reference[key].verdicts or []
        for index, (value, truth) in enumerate(zip(mine, theirs, strict=True)):
            pairs.append(ClaimPair(key[0], index, float(value), bool(truth)))
    return pairs


def agreement(pairs: Sequence[ClaimPair], cutoff: float = 0.5) -> tuple[int, int]:
    """(agreeing, n), reading P(yes) >= cutoff as true."""
    return sum((p.value >= cutoff) == p.reference for p in pairs), len(pairs)


def band(pairs: Sequence[ClaimPair], t: float) -> tuple[int, int, int]:
    """(answered, n, agreeing among answered). Claims strictly between 1 - t
    and t are left unanswered. Rounded so 1 - 0.9 is 0.1, not 0.0999..."""
    low = round(1 - t, 9)
    answered = [p for p in pairs if p.value >= t or p.value <= low]
    agreed = sum((p.value >= t) == p.reference for p in answered)
    return len(answered), len(pairs), agreed


def outcome_flips(
    rows: Sequence[Row],
    judge: JudgeName,
    cases: Mapping[str, Case],
    records: Sequence[RepRecord],
    cutoff: float = 0.5,
) -> tuple[int, int]:
    """(flipped, n): gated repetitions whose rep_outcome, from the recorded
    checks and this judge's verdicts, differs from the reference's."""
    reference = _answered(rows, "claims", "reference")
    other = _answered(rows, "claims", judge)
    recorded = {(r.case, r.rep): r for r in records}
    flipped = n = 0
    for key in sorted(reference.keys() & other.keys()):
        case, rec = cases.get(key[0]), recorded.get(key)
        if rec is None or case is None or case.judge is None:
            continue
        if case.judge.min_met is None:
            continue
        mine = [float(v) >= cutoff for v in other[key].verdicts or []]
        theirs = [bool(v) for v in reference[key].verdicts or []]
        n += 1
        if rep_outcome(rec.checks, case.judge, mine) != rep_outcome(
            rec.checks, case.judge, theirs
        ):
            flipped += 1
    return flipped, n


def triage_pairs(rows: Sequence[Row]) -> list[tuple[Row, Row]]:
    """(jev, reference) for every failure both labelled."""
    reference = _answered(rows, "triage", "reference")
    jev = _answered(rows, "triage", "jev")
    return [(jev[k], reference[k]) for k in sorted(jev.keys() & reference.keys())]


def confusion(rows: Sequence[Row]) -> Counter[tuple[str, str]]:
    """Counts keyed (reference label, Jev label)."""
    return Counter((str(r.cause), str(j.cause)) for j, r in triage_pairs(rows))


def confidence_split(rows: Sequence[Row]) -> tuple[list[float], list[float]]:
    """Jev's confidence where it agrees with the reference, and where it does not."""
    agree: list[float] = []
    disagree: list[float] = []
    for jev, reference in triage_pairs(rows):
        if jev.confidence is not None:
            (agree if jev.cause == reference.cause else disagree).append(jev.confidence)
    return agree, disagree


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest rank: the smallest value with at least q of the values at or below."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


@dataclass(frozen=True)
class Spend:
    calls: int
    tokens: int
    dollars: float
    estimated: bool
    unknown: int  # rows that made a call but whose cost is unknown
    median_s: float | None
    p95_s: float | None


def spend(rows: Sequence[Row], judge: JudgeName) -> Spend:
    """Rows that made no call, such as too_large, cost nothing and take no time."""
    called = [r for r in rows if r.judge == judge and r.attempts]
    latencies = [r.latency_s for r in called]
    return Spend(
        calls=sum(r.attempts for r in called),
        tokens=sum(r.tokens for r in called),
        dollars=sum(r.cost for r in called if r.cost is not None),
        estimated=any(r.cost_estimated for r in called),
        unknown=sum(r.cost is None for r in called),
        median_s=statistics.median(latencies) if latencies else None,
        p95_s=percentile(latencies, 0.95),
    )


def pct(k: int, n: int) -> str:
    return f"{k / n:.0%} ({k}/{n})" if n else "- (0/0)"


def _seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _dollars(s: Spend) -> str:
    if s.unknown and not s.dollars:
        return "-"
    return f"{'~' if s.estimated else ''}${s.dollars:.6f}"


def _header(header: Header, rows: Sequence[Row], skips: Sequence[Skip]) -> list[str]:
    pinned = sorted({str(r.model) for r in rows if r.judge == "jev" and not r.error})
    compared = {(r.case, r.rep) for r in rows}
    lines = [
        f"compare-judges over results {header.stamp}",
        f"model under test: {header.model}",
        f"llm judge: {header.judge_model}",
        f"reference: {header.reference_model}",
        f"jev: {', '.join(pinned) or 'no answer'}",
        f"repetitions compared: {len(compared)}",
    ]
    for reason, count in sorted(Counter(s.reason for s in skips).items()):
        lines.append(f"skipped, {reason}: {count}")
    too_large = Counter(f"{r.judge} {r.task}" for r in rows if r.error == TOO_LARGE)
    failed = Counter(
        f"{r.judge} {r.task}" for r in rows if r.error and r.error != TOO_LARGE
    )
    for what, counts in (("too_large", too_large), ("judge errors", failed)):
        if counts:
            listed = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
            lines.append(f"{what}: {listed}")
    lines.append(
        "A judge's error drops that repetition from its own figures; "
        "a reference error drops it from every judge's."
    )
    lines.append(GROUND_TRUTH)
    return lines


def _claims(rows: Sequence[Row], cases: Mapping[str, Case]) -> list[str]:
    jev, llm = claim_pairs(rows, "jev"), claim_pairs(rows, "llm")
    lines = ["", "Claim agreement with the reference"]
    lines.append(f"  {'llm judge':<34} {pct(*agreement(llm))}")
    for t in CUTOFFS:
        lines.append(f"  {f'jev, P(yes) >= {t}':<34} {pct(*agreement(jev, t))}")
    for t in BANDS:
        answered, n, agreed = band(jev, t)
        label = f"jev, unsure between {round(1 - t, 9)} and {t}"
        lines.append(
            f"  {label:<34} coverage {pct(answered, n)}, "
            f"agreement {pct(agreed, answered)}"
        )
    lines.append("  Jev gives no reason for a verdict; that is not counted against it.")
    lines.append("")
    lines.append("  per claim: jev at 0.5, then the llm judge")
    keys = sorted({(p.case, p.index) for p in jev + llm})
    for case_id, index in keys:
        mine = [p for p in jev if (p.case, p.index) == (case_id, index)]
        theirs = [p for p in llm if (p.case, p.index) == (case_id, index)]
        case = cases.get(case_id)
        text = case.judge.assertions[index] if case and case.judge else "?"
        lines.append(
            f"  {case_id} #{index + 1}  jev {pct(*agreement(mine))}  "
            f"llm {pct(*agreement(theirs))}"
        )
        lines.append(f"    {text}")
    return lines


def _flips(
    rows: Sequence[Row], cases: Mapping[str, Case], records: Sequence[RepRecord]
) -> list[str]:
    lines = ["", "Outcome flips against the reference, gated repetitions only"]
    flipped, n = outcome_flips(rows, "llm", cases, records)
    lines.append(f"  {'llm judge':<34} {flipped} of {n}")
    for t in CUTOFFS:
        flipped, n = outcome_flips(rows, "jev", cases, records, t)
        lines.append(f"  {f'jev, P(yes) >= {t}':<34} {flipped} of {n}")
    return lines


def _triage(rows: Sequence[Row]) -> list[str]:
    lines = ["", "Triage"]
    by_code = Counter(str(r.cause) for r in rows if r.judge == "code")
    coded = ", ".join(f"{k} {v}" for k, v in sorted(by_code.items()))
    lines.append(f"  labelled by code: {coded or 'none'}")
    triaged = {
        (r.case, r.rep): bool(r.claimed_done) for r in rows if r.task == "triage"
    }
    lines.append(
        f"  claimed done with a failing check: "
        f"{sum(triaged.values())} of {len(triaged)} triaged failures"
    )
    table = confusion(rows)
    labels = sorted({label for pair in table for label in pair})
    if labels:
        lines.append("  jev (columns) against the reference (rows), counts:")
        lines.append(
            f"    {'':<20}" + "".join(f"{n:>4}" for n in range(1, len(labels) + 1))
        )
        for n, ref in enumerate(labels, start=1):
            counts = "".join(f"{table[(ref, j)]:>4}" for j in labels)
            lines.append(f"  {n:>2} {ref:<19}{counts}")
    agree, disagree = confidence_split(rows)
    lines.append(
        "  jev's median confidence: "
        f"{_median(agree)} agreeing (n={len(agree)}), "
        f"{_median(disagree)} disagreeing (n={len(disagree)})"
    )
    return lines


def _median(values: Sequence[float]) -> str:
    return f"{statistics.median(values):.2f}" if values else "-"


def _spend(rows: Sequence[Row]) -> list[str]:
    lines = ["", "Cost and speed"]
    lines.append(
        f"  {'judge':<10}{'calls':>6}{'tokens':>10}{'dollars':>12}"
        f"{'no cost':>9}{'median s':>10}{'p95 s':>7}"
    )
    estimated = unknown = False
    judges: tuple[JudgeName, ...] = ("jev", "llm", "reference")
    for judge in judges:
        s = spend(rows, judge)
        estimated = estimated or s.estimated
        unknown = unknown or bool(s.unknown)
        lines.append(
            f"  {judge:<10}{s.calls:>6}{s.tokens:>10,}{_dollars(s):>12}"
            f"{s.unknown:>9}{_seconds(s.median_s):>10}{_seconds(s.p95_s):>7}"
        )
    if unknown:
        lines.append("  no cost: answers whose cost the backend did not report")
    if estimated:
        lines.append(
            "  ~ estimated from Jev's input tokens: the proxy reported no cost"
        )
    return lines


def render_summary(
    header: Header,
    rows: Sequence[Row],
    skips: Sequence[Skip],
    cases: Mapping[str, Case],
    records: Sequence[RepRecord],
) -> str:
    return "\n".join(
        _header(header, rows, skips)
        + _claims(rows, cases)
        + _flips(rows, cases, records)
        + _triage(rows)
        + _spend(rows)
    )
