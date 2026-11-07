"""A human-labelled calibration set for the LLM judge. Pure: reads labels and
rows, does arithmetic. Nothing here calls a model.

A claim instance is one assertion of one judged case, on one diff. Two people
label each instance blind to the judge (`export` leaves out what the judge said),
and the labels file keeps both labels, so a disagreement is visible and is
settled by an `adjudicated` label rather than by overwriting one of them.

A row also has a status. A reviewer who cannot tell says `unknown`, which is no
label: the row is kept, and it stays out of the rates. `calibration_import`
writes these rows from the reviewers' verdicts.

The judge's false-positive and false-negative rates are then read against the
labels, for every judge model the labelled diffs were judged with.
"""

from __future__ import annotations

import json
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmarks.runner.case import Case
from benchmarks.runner.report import RepRecord

LABELS_FILE = Path("benchmarks/calibration/labels.jsonl")
LABELLERS = ("a", "b")

# What a row's verdicts settle to. Only AGREED and ADJUDICATED rows carry a label.
AGREED = "agreed"  # both labellers said the same
ADJUDICATED = "adjudicated"  # they disagreed, and the decision is recorded
DISAGREE = "disagree"  # they disagreed, and nobody has decided yet
UNKNOWN = "unknown"  # a labeller could not tell: no label
PENDING = "pending"  # a labeller has not answered yet
STATUSES = (AGREED, ADJUDICATED, DISAGREE, UNKNOWN, PENDING)


class CalibrationError(Exception):
    """A labels file that cannot be read, or does not follow the format."""


def settle(
    labels: Mapping[str, bool | None],
    unknown_from: Sequence[str],
    decision: bool | None,
) -> tuple[str, bool | None, bool]:
    """The (status, label, adjudicated) that a row's verdicts settle to.

    An unknown from either labeller is no label, whatever the other one said.
    Otherwise both must have answered: agreement is the label, and a
    disagreement takes the adjudicator's decision.
    """
    if unknown_from:
        return UNKNOWN, None, False
    first, second = labels["a"], labels["b"]
    if first is None or second is None:
        return PENDING, None, False
    if first == second:
        return AGREED, first, False
    if decision is None:
        return DISAGREE, None, False
    return ADJUDICATED, decision, True


@dataclass(frozen=True)
class Label:
    line: int
    case: str
    claim: int  # 1-based, as the case lists its assertions
    claim_text: str
    diff_sha256: str
    label: bool | None
    labels: dict[str, bool | None]
    adjudicated: bool
    status: str = AGREED
    unknown_from: tuple[str, ...] = ()
    reviewers: dict[str, str] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.case, self.claim, self.diff_sha256)


def _label_from(raw: Any, line: int) -> Label:
    def fail(why: str) -> CalibrationError:
        return CalibrationError(f"line {line}: {why}")

    if not isinstance(raw, dict):
        raise fail("not a JSON object")
    case, claim = raw.get("case"), raw.get("claim")
    text, digest = raw.get("claim_text"), raw.get("diff_sha256")
    label, labels = raw.get("label"), raw.get("labels")
    adjudicated = raw.get("adjudicated", False)
    status = raw.get("status")
    unknown_from = raw.get("unknown_from", [])
    reviewers = raw.get("reviewers", {})
    notes = raw.get("notes", {})
    if not isinstance(case, str) or not case:
        raise fail("case must be a case id")
    if not isinstance(claim, int) or isinstance(claim, bool) or claim < 1:
        raise fail("claim must be the 1-based number of an assertion")
    if not isinstance(text, str) or not text:
        raise fail("claim_text must be the assertion's text")
    if not isinstance(digest, str) or len(digest) != 64:
        raise fail("diff_sha256 must be the 64-character digest of the diff")
    if not isinstance(labels, dict) or set(labels) != set(LABELLERS):
        raise fail('labels must hold a true, false or null from each of "a" and "b"')
    if not isinstance(adjudicated, bool):
        raise fail("adjudicated must be true or false")
    if not isinstance(reviewers, dict) or not (
        set(reviewers) <= set(LABELLERS)
        and all(isinstance(v, str) and v for v in reviewers.values())
    ):
        raise fail('reviewers must name the person behind "a" and/or "b"')
    if not isinstance(notes, dict) or not (
        set(notes) <= set(LABELLERS) and all(isinstance(v, str) for v in notes.values())
    ):
        raise fail('notes must hold text for "a" and/or "b"')

    if status is None:
        # The original format, which has no status: both labellers answered.
        if not isinstance(label, bool):
            raise fail("label must be true or false, never null")
        if not all(isinstance(v, bool) for v in labels.values()):
            raise fail('labels must hold a true or false from each of "a" and "b"')
        agreed = labels["a"] == labels["b"]
        if agreed and (label != labels["a"] or adjudicated):
            raise fail(
                "both labellers agree, so label must be their answer and not "
                "adjudicated"
            )
        if not agreed and not adjudicated:
            raise fail("the labellers disagree, so the label needs adjudicated: true")
        status = ADJUDICATED if adjudicated else AGREED
        unknown_from = []
    else:
        if status not in STATUSES:
            raise fail(f"status must be one of {', '.join(STATUSES)}")
        if label is not None and not isinstance(label, bool):
            raise fail("label must be true, false or null")
        if not all(v is None or isinstance(v, bool) for v in labels.values()):
            raise fail('labels must hold true, false or null for "a" and "b"')
        if not isinstance(unknown_from, list) or not set(unknown_from) <= set(
            LABELLERS
        ):
            raise fail("unknown_from must name the labellers who said unknown")
        if any(labels[name] is not None for name in unknown_from):
            raise fail("a labeller who said unknown has no true or false")
        expected = settle(labels, tuple(unknown_from), label if adjudicated else None)
        if expected != (status, label, adjudicated):
            raise fail(
                f"status {status} does not follow from the labels; it should be "
                f"{expected[0]}"
            )
    return Label(
        line=line,
        case=case,
        claim=claim,
        claim_text=text,
        diff_sha256=digest,
        label=label,
        labels={k: v for k, v in labels.items()},
        adjudicated=adjudicated,
        status=status,
        unknown_from=tuple(unknown_from),
        reviewers=dict(reviewers),
        notes=dict(notes),
    )


def label_row(lab: Label) -> dict[str, Any]:
    """A label as one line of the labels file. Fixed key order, so writing the
    same labels twice gives the same bytes."""
    row: dict[str, Any] = {
        "case": lab.case,
        "claim": lab.claim,
        "claim_text": lab.claim_text,
        "diff_sha256": lab.diff_sha256,
        "labels": {name: lab.labels.get(name) for name in LABELLERS},
        "label": lab.label,
        "adjudicated": lab.adjudicated,
        "status": lab.status,
    }
    if lab.unknown_from:
        row["unknown_from"] = [n for n in LABELLERS if n in lab.unknown_from]
    if lab.reviewers:
        row["reviewers"] = {
            n: lab.reviewers[n] for n in LABELLERS if n in lab.reviewers
        }
    if lab.notes:
        row["notes"] = {n: lab.notes[n] for n in LABELLERS if n in lab.notes}
    return row


def load_labels(path: Path) -> list[Label]:
    """The labels in a JSONL file. A missing file is an empty set, not an error."""
    if not path.is_file():
        return []
    labels: list[Label] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CalibrationError(f"line {number}: not JSON ({exc.msg})") from exc
        labels.append(_label_from(payload, number))
    return labels


def problems(labels: Sequence[Label], cases: Mapping[str, Case]) -> list[str]:
    """What is wrong with a labels file against the cases, or none."""
    found: list[str] = []
    first_seen: dict[tuple[str, int, str], int] = {}
    for lab in labels:
        case = cases.get(lab.case)
        if case is None or case.judge is None:
            found.append(f"line {lab.line}: {lab.case!r} is not a judged case")
            continue
        assertions = case.judge.assertions
        if lab.claim > len(assertions):
            found.append(
                f"line {lab.line}: {lab.case} has {len(assertions)} claims, "
                f"not {lab.claim}"
            )
        elif assertions[lab.claim - 1] != lab.claim_text:
            found.append(
                f"line {lab.line}: claim_text is not claim {lab.claim} of {lab.case} "
                "as the case now states it"
            )
        key = (lab.case, lab.claim, lab.diff_sha256)
        if key in first_seen:
            found.append(
                f"line {lab.line}: the same claim on the same diff as line "
                f"{first_seen[key]}"
            )
        else:
            first_seen[key] = lab.line
    return found


def export_candidates(
    records: Sequence[RepRecord],
    cases: Mapping[str, Case],
    *,
    sample: int | None = None,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Blind candidate rows for labelling: the claim, the diff's digest and where
    its evidence is kept. The judge's verdict and reason are left out on
    purpose, so a labeller reads the diff and not the judge."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for rec in records:
        case = cases.get(rec.case)
        if case is None or case.judge is None or not rec.diff_sha256:
            continue
        for number, text in enumerate(case.judge.assertions, start=1):
            key = (rec.case, number, rec.diff_sha256)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "case": rec.case,
                    "claim": number,
                    "claim_text": text,
                    "diff_sha256": rec.diff_sha256,
                    "evidence": rec.artifacts,
                    "labels": {name: None for name in LABELLERS},
                    "label": None,
                    "adjudicated": False,
                }
            )
    if sample is not None and sample < len(rows):
        rows = random.Random(seed).sample(rows, sample)
    return rows


@dataclass
class JudgeRates:
    judge_model: str
    scored: int = 0  # labelled instances the judge answered
    unknown: int = 0  # labelled instances it marked "unknown"
    negatives: int = 0  # labelled false and answered
    false_pos: int = 0  # labelled false, judged true
    positives: int = 0  # labelled true and answered
    false_neg: int = 0  # labelled true, judged false


@dataclass(frozen=True)
class Calibration:
    labelled: int  # rows with a label; rows without one are in no rate
    unmatched: int  # labelled claims with no judged rep on that diff
    by_judge: dict[str, JudgeRates] = field(default_factory=dict)


def calibrate(labels: Sequence[Label], records: Sequence[RepRecord]) -> Calibration:
    """Every labelled claim against each rep judged on the same case and diff.

    A diff judged by two reps counts twice, once per rep: that is how the judge's
    consistency on one diff shows. A rep with no verdicts (the judge failed)
    contributes nothing. A row with no label (unknown, or still waiting for a
    reviewer) is kept in the file and counted nowhere here.
    """
    index: dict[tuple[str, str], list[RepRecord]] = {}
    for rec in records:
        if rec.diff_sha256 and rec.judge_claims is not None:
            index.setdefault((rec.case, rec.diff_sha256), []).append(rec)

    by_judge: dict[str, JudgeRates] = {}
    labelled = 0
    unmatched = 0
    for lab in labels:
        label = lab.label
        if label is None:
            continue
        labelled += 1
        matches = index.get((lab.case, lab.diff_sha256), [])
        if not matches:
            unmatched += 1
            continue
        for rec in matches:
            assert rec.judge_claims is not None
            if lab.claim > len(rec.judge_claims):
                continue
            rates = by_judge.setdefault(rec.judge_model, JudgeRates(rec.judge_model))
            verdict = rec.judge_claims[lab.claim - 1]
            if verdict is None:
                rates.unknown += 1
                continue
            rates.scored += 1
            if label:
                rates.positives += 1
                rates.false_neg += not verdict
            else:
                rates.negatives += 1
                rates.false_pos += verdict
    return Calibration(labelled=labelled, unmatched=unmatched, by_judge=by_judge)


def _pct(part: int, whole: int) -> str:
    return "-" if whole == 0 else f"{part}/{whole} ({part / whole:.0%})"


def render_calibration(result: Calibration, source: Path) -> str:
    if result.labelled == 0:
        return f"calibration: no labelled claims yet ({source})"
    lines = [
        f"calibration: {result.labelled} labelled claim instance(s) ({source})",
        f"  {'judge':<30} {'scored':>6} {'unknown':>7} {'FP rate':>16} {'FN rate':>16}",
    ]
    for name, rates in sorted(result.by_judge.items()):
        lines.append(
            f"  {name or '(none)':<30} {rates.scored:>6} {rates.unknown:>7} "
            f"{_pct(rates.false_pos, rates.negatives):>16} "
            f"{_pct(rates.false_neg, rates.positives):>16}"
        )
    if not result.by_judge:
        lines.append("  no labelled claim has a judged rep on its diff yet")
    if result.unmatched:
        lines.append(
            f"  {result.unmatched} labelled claim(s) match no judged rep on their diff"
        )
    lines.append(
        "  FP: labelled false, judged true. FN: labelled true, judged false. "
        "Unknown is counted in neither rate."
    )
    return "\n".join(lines)
