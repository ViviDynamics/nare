"""`bin/bench calibrate import`: load reviewed verdicts into the labels file.

Reviewers record a verdict per claim on a web page and export it as CSV in the
shape of `review.csv`, or as a JSON array. Each verdict is matched to a claim
instance that `calibrate export` wrote, on its case, claim number and diff
SHA-256. A file is refused as a whole when any row does not match the export,
the case or a reviewer, so nothing is written from a half-read file.

Only the human columns and the JSON verdicts count. Draft columns such as
`draft_label` are never read.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from benchmarks.runner.calibration import (
    ADJUDICATED,
    LABELLERS,
    CalibrationError,
    Label,
    label_row,
    load_labels,
    problems,
    settle,
)
from benchmarks.runner.case import Case

CSV_REQUIRED = ("case", "claim", "diff_sha256", "human_a", "human_b")
JSON_REQUIRED = ("case", "claim", "diff_sha256", "reviewer", "verdict")
Key = tuple[str, int, str]
_VERDICTS = {"true": True, "false": False, "unknown": None}


class ImportRefused(CalibrationError):
    """Verdicts the export, the cases or the labels file do not accept. Raised
    before anything is written."""

    def __init__(self, found: Sequence[str]) -> None:
        super().__init__("\n".join(found))
        self.found = list(found)


@dataclass(frozen=True)
class Review:
    where: str  # "line 3" or "entry 2", for messages
    case: str
    claim: int
    diff_sha256: str
    labeller: str  # "a" or "b"
    verdict: bool | None  # None is unknown
    note: str
    claim_text: str | None  # from a CSV row only, to check against the export

    @property
    def key(self) -> Key:
        return (self.case, self.claim, self.diff_sha256)


@dataclass(frozen=True)
class Decision:
    """The adjudicator's answer, from a CSV row's `label` column."""

    where: str
    case: str
    claim: int
    diff_sha256: str
    value: bool
    claim_text: str | None

    @property
    def key(self) -> Key:
        return (self.case, self.claim, self.diff_sha256)


@dataclass(frozen=True)
class Reviews:
    verdicts: list[Review]
    decisions: list[Decision]
    skipped: int  # CSV rows with no human verdict yet


@dataclass(frozen=True)
class ImportResult:
    verdicts: int
    decisions: int
    skipped: int
    changed: bool
    statuses: Mapping[str, int]


def _parse_verdict(raw: object) -> tuple[bool, bool | None]:
    """(valid, verdict). A valid verdict of None is unknown."""
    if isinstance(raw, bool):
        return True, raw
    if isinstance(raw, str) and raw.strip().lower() in _VERDICTS:
        return True, _VERDICTS[raw.strip().lower()]
    return False, None


def _cell(row: Mapping[str, Any], name: str) -> str:
    return str(row.get(name) or "").strip()


def _parse_claim(raw: object) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        number = raw
    elif isinstance(raw, str) and raw.strip().isdigit():
        number = int(raw.strip())
    else:
        return None
    return number if number >= 1 else None


def read_reviews(path: Path, reviewers: Mapping[str, str]) -> Reviews:
    """The verdicts in a reviewed CSV or JSON file, or a refusal naming every
    row that is wrong."""
    if not path.is_file():
        raise CalibrationError(f"{path}: no such file")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _from_csv(path)
    if suffix == ".json":
        return _from_json(path, reviewers)
    raise CalibrationError(f"{path}: import reads a .csv or a .json file")


def _from_csv(path: Path) -> Reviews:
    found: list[str] = []
    verdicts: list[Review] = []
    decisions: list[Decision] = []
    skipped = 0
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        missing = [name for name in CSV_REQUIRED if name not in columns]
        if missing:
            raise CalibrationError(f"{path}: missing column(s) {', '.join(missing)}")
        for raw in reader:
            where = f"line {reader.line_num}"

            human = {name: _cell(raw, f"human_{name}") for name in LABELLERS}
            decision_text = _cell(raw, "label").lower()
            if not any(human.values()) and not decision_text:
                skipped += 1  # not reviewed yet; nothing here is a label
                continue
            claim = _parse_claim(_cell(raw, "claim"))
            digest = _cell(raw, "diff_sha256")
            case = _cell(raw, "case")
            if claim is None or not case or not digest:
                found.append(f"{where}: needs case, a claim number and diff_sha256")
                continue
            claim_text = _cell(raw, "claim_text") or None
            for name in LABELLERS:
                if not human[name]:
                    continue
                valid, verdict = _parse_verdict(human[name])
                if not valid:
                    found.append(
                        f"{where}: human_{name} must be true, false or unknown"
                    )
                    continue
                verdicts.append(
                    Review(
                        where=where,
                        case=case,
                        claim=claim,
                        diff_sha256=digest,
                        labeller=name,
                        verdict=verdict,
                        note=_cell(raw, f"note_{name}"),
                        claim_text=claim_text,
                    )
                )
            if decision_text:
                if decision_text not in ("true", "false"):
                    found.append(f"{where}: label must be true or false")
                else:
                    decisions.append(
                        Decision(
                            where=where,
                            case=case,
                            claim=claim,
                            diff_sha256=digest,
                            value=decision_text == "true",
                            claim_text=claim_text,
                        )
                    )
    if found:
        raise ImportRefused(found)
    return Reviews(verdicts=verdicts, decisions=decisions, skipped=skipped)


def _from_json(path: Path, reviewers: Mapping[str, str]) -> Reviews:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"{path}: not JSON ({exc.msg})") from exc
    if not isinstance(data, list):
        raise CalibrationError(f"{path}: a JSON import is an array of verdicts")
    by_name = {name: labeller for labeller, name in reviewers.items()}
    found: list[str] = []
    verdicts: list[Review] = []
    for number, entry in enumerate(data, 1):
        where = f"entry {number}"
        if not isinstance(entry, dict):
            found.append(f"{where}: not a JSON object")
            continue
        if "label" in entry:
            found.append(
                f"{where}: JSON takes verdicts only; record the decision in the "
                "CSV label column"
            )
            continue
        missing = [name for name in JSON_REQUIRED if name not in entry]
        if missing:
            found.append(f"{where}: missing {', '.join(missing)}")
            continue
        case, digest = entry["case"], entry["diff_sha256"]
        claim = _parse_claim(entry["claim"])
        if not isinstance(case, str) or not case or claim is None:
            found.append(f"{where}: needs a case and a claim number")
            continue
        if not isinstance(digest, str) or not digest:
            found.append(f"{where}: needs diff_sha256")
            continue
        labeller = by_name.get(str(entry["reviewer"]))
        if labeller is None:
            found.append(
                f"{where}: reviewer {entry['reviewer']!r} is neither "
                f"{', '.join(repr(n) for n in reviewers.values())}"
            )
            continue
        valid, verdict = _parse_verdict(entry["verdict"])
        if not valid:
            found.append(f"{where}: verdict must be true, false or unknown")
            continue
        note = entry.get("note") or ""
        if not isinstance(note, str):
            found.append(f"{where}: note must be text")
            continue
        verdicts.append(
            Review(
                where=where,
                case=case,
                claim=claim,
                diff_sha256=digest,
                labeller=labeller,
                verdict=verdict,
                note=note,
                claim_text=None,
            )
        )
    if found:
        raise ImportRefused(found)
    return Reviews(verdicts=verdicts, decisions=[], skipped=0)


def read_export(path: Path) -> dict[Key, dict[str, Any]]:
    """The rows `calibrate export` wrote, keyed by what a review matches on."""
    if not path.is_file():
        raise CalibrationError(f"{path}: no such export")
    rows: dict[Key, dict[str, Any]] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CalibrationError(
                f"{path}: line {number}: not JSON ({exc.msg})"
            ) from exc
        if not isinstance(row, dict) or not all(
            name in row for name in ("case", "claim", "claim_text", "diff_sha256")
        ):
            raise CalibrationError(f"{path}: line {number}: not an export row")
        rows[(row["case"], row["claim"], row["diff_sha256"])] = row
    return rows


def apply_reviews(
    existing: Sequence[Label],
    reviews: Reviews,
    export: Mapping[Key, Mapping[str, Any]],
    cases: Mapping[str, Case],
    *,
    reviewers: Mapping[str, str],
    adjudicate: bool,
) -> list[Label]:
    """The labels file after the reviews, or a refusal. Pure.

    A reviewer's verdict replaces their earlier one on the same claim, and a
    labeller not in this file keeps theirs, so importing one reviewer's file
    and then the other's gives the same rows as importing both at once.
    """
    found: list[str] = []
    votes: dict[Key, dict[str, Review]] = {}
    order: list[Key] = []
    for review in reviews.verdicts:
        if review.key not in export:
            found.append(_not_in_export(review.where, review.key))
            continue
        _check_claim_text(found, review.where, review.claim_text, export[review.key])
        previous = votes.setdefault(review.key, {}).get(review.labeller)
        if previous is not None and previous.verdict != review.verdict:
            found.append(
                f"{review.where}: a second, different verdict from {review.labeller}"
            )
            continue
        if review.key not in order:
            order.append(review.key)
        votes[review.key][review.labeller] = review

    decisions: dict[Key, Decision] = {}
    for entry in reviews.decisions:
        if entry.key not in export:
            found.append(_not_in_export(entry.where, entry.key))
            continue
        _check_claim_text(found, entry.where, entry.claim_text, export[entry.key])
        if entry.key in decisions and decisions[entry.key].value != entry.value:
            found.append(f"{entry.where}: a second, different decision for this claim")
        decisions.setdefault(entry.key, entry)
        if entry.key not in order:
            order.append(entry.key)

    index: dict[Key, int] = {}
    for position, lab in enumerate(existing):
        index.setdefault(lab.key, position)
    out = list(existing)

    for key in order:
        group = votes.get(key, {})
        decision = decisions.get(key)
        base = out[index[key]] if key in index else None
        labels: dict[str, bool | None] = {
            name: base.labels.get(name) if base else None for name in LABELLERS
        }
        unknown = set(base.unknown_from) if base else set()
        names = dict(base.reviewers) if base else {}
        notes = dict(base.notes) if base else {}
        for labeller, review in sorted(group.items()):
            name = reviewers[labeller]
            if names.get(labeller, name) != name:
                found.append(
                    f"{review.where}: labeller {labeller} is recorded as "
                    f"{names[labeller]!r} on this claim, not {name!r}"
                )
                continue
            names[labeller] = name
            labels[labeller] = review.verdict
            if review.verdict is None:
                unknown.add(labeller)
            else:
                unknown.discard(labeller)
            if review.note:
                notes[labeller] = review.note
        prior = base.label if base is not None and base.adjudicated else None
        if decision is not None and not adjudicate:
            found.append(
                f"{decision.where}: a decision in the label column needs --adjudicate"
            )
            continue
        value = decision.value if decision is not None else prior
        status, label, adjudicated = settle(labels, tuple(sorted(unknown)), value)
        if decision is not None and status != ADJUDICATED:
            found.append(
                f"{decision.where}: the label column is only for two verdicts that "
                f"disagree, and this claim is {status}"
            )
            continue
        row = export[key]
        lab = Label(
            line=0,
            case=key[0],
            claim=key[1],
            claim_text=str(row["claim_text"]),
            diff_sha256=key[2],
            label=label,
            labels=labels,
            adjudicated=adjudicated,
            status=status,
            unknown_from=tuple(sorted(unknown)),
            reviewers=names,
            notes=notes,
        )
        if base is not None:
            out[index[key]] = lab
        else:
            index[key] = len(out)
            out.append(lab)

    if found:
        raise ImportRefused(found)
    final = [replace(lab, line=number) for number, lab in enumerate(out, 1)]
    clash = problems(final, cases)
    if clash:
        raise ImportRefused(clash)
    return final


def _not_in_export(where: str, key: Key) -> str:
    case, claim, digest = key
    return (
        f"{where}: {case} claim {claim} on diff {digest[:12]} is not in the export. "
        "The claim or the diff has changed since the export; export again and review "
        "the new rows"
    )


def _check_claim_text(
    found: list[str], where: str, text: str | None, row: Mapping[str, Any]
) -> None:
    """A CSV row that names its claim text must name the export's."""
    if text is not None and text != row["claim_text"]:
        found.append(
            f"{where}: claim_text is not the text the export holds for this claim"
        )


def write_labels(path: Path, labels: Sequence[Label]) -> bool:
    """Write the labels file atomically. False when it already holds these rows."""
    text = "".join(json.dumps(label_row(lab)) + "\n" for lab in labels)
    if path.is_file():
        if path.read_text(encoding="utf-8") == text:
            return False
    elif not text:
        return False  # nothing to keep, and no file to create
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".labels-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return True


def import_reviews(
    *,
    source: Path,
    export_path: Path,
    labels_path: Path,
    reviewers: Mapping[str, str],
    adjudicate: bool,
    cases: Mapping[str, Case],
) -> ImportResult:
    """Read a reviewed file and merge it into the labels file. Writes nothing and
    raises if any row is refused."""
    if len(set(reviewers.values())) != 2 or set(reviewers) != set(LABELLERS):
        raise CalibrationError("--reviewer-a and --reviewer-b must name two people")
    reviews = read_reviews(source, reviewers)
    export = read_export(export_path)
    existing = load_labels(labels_path)
    final = apply_reviews(
        existing, reviews, export, cases, reviewers=reviewers, adjudicate=adjudicate
    )
    changed = write_labels(labels_path, final)
    statuses = Counter(lab.status for lab in final)
    return ImportResult(
        verdicts=len(reviews.verdicts),
        decisions=len(reviews.decisions),
        skipped=reviews.skipped,
        changed=changed,
        statuses=dict(statuses),
    )
