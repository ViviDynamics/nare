"""`bin/bench calibrate import`: load reviewed verdicts into the labels file.

Reviewers record a verdict per claim on a web page and export it as CSV in the
shape of `review.csv`, or as a JSON array. Each verdict is matched to a claim
instance that `calibrate export` wrote, on its case, claim number and diff
SHA-256. A file is refused as a whole when any row does not match the export,
the case or a reviewer, so nothing is written from a half-read file.

Only the human columns, the `panel` column and the JSON verdicts count. Draft
columns such as `draft_label` are never read.

A panel verdict (the CSV `panel` column, or a JSON entry whose reviewer is
`panel`) goes to the row's panel entry, with the provenance the
`--panel-provenance` file gives, and never to a human slot. The panel, or a
model named in its provenance, is refused as a human reviewer.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from benchmarks.runner.calibration import (
    ADJUDICATED,
    LABELLERS,
    PANEL,
    CalibrationError,
    Label,
    Panel,
    PanelPass,
    label_row,
    load_labels,
    not_a_human,
    parse_passes,
    parse_votes,
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
class PanelVerdict:
    """The model panel's verdict on one claim: never a human one."""

    where: str
    case: str
    claim: int
    diff_sha256: str
    verdict: bool | None  # None is the panel's unknown
    votes: Any  # the JSON entry's `votes`, checked against the passes; None if absent
    claim_text: str | None

    @property
    def key(self) -> Key:
        return (self.case, self.claim, self.diff_sha256)


@dataclass(frozen=True)
class Reviews:
    verdicts: list[Review]
    decisions: list[Decision]
    skipped: int  # CSV rows with no human or panel verdict yet
    panel: list[PanelVerdict] = field(default_factory=list)


@dataclass(frozen=True)
class ImportResult:
    verdicts: int
    decisions: int
    skipped: int
    changed: bool
    statuses: Mapping[str, int]
    panel: int = 0  # panel verdicts read
    panel_labelled: int = 0  # rows whose panel label is true or false, after import


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


def read_reviews(
    path: Path,
    reviewers: Mapping[str, str],
    panel_models: frozenset[str] = frozenset(),
) -> Reviews:
    """The verdicts in a reviewed CSV or JSON file, or a refusal naming every
    row that is wrong. `panel_models` are the models the panel provenance names,
    whose own verdicts a JSON file may not give as a reviewer's."""
    if not path.is_file():
        raise CalibrationError(f"{path}: no such file")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _from_csv(path)
    if suffix == ".json":
        return _from_json(path, reviewers, panel_models)
    raise CalibrationError(f"{path}: import reads a .csv or a .json file")


def _from_csv(path: Path) -> Reviews:
    found: list[str] = []
    verdicts: list[Review] = []
    decisions: list[Decision] = []
    panel: list[PanelVerdict] = []
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
            panel_text = _cell(raw, PANEL)
            if not any(human.values()) and not decision_text and not panel_text:
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
            if panel_text:
                valid, value = _parse_verdict(panel_text)
                if not valid:
                    found.append(f"{where}: panel must be true, false or unknown")
                else:
                    panel.append(
                        PanelVerdict(
                            where=where,
                            case=case,
                            claim=claim,
                            diff_sha256=digest,
                            verdict=value,
                            votes=None,
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
    return Reviews(verdicts=verdicts, decisions=decisions, skipped=skipped, panel=panel)


def _from_json(
    path: Path, reviewers: Mapping[str, str], panel_models: frozenset[str]
) -> Reviews:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"{path}: not JSON ({exc.msg})") from exc
    if not isinstance(data, list):
        raise CalibrationError(f"{path}: a JSON import is an array of verdicts")
    by_name = {name: labeller for labeller, name in reviewers.items()}
    models = {m.casefold() for m in panel_models}
    found: list[str] = []
    verdicts: list[Review] = []
    panel: list[PanelVerdict] = []
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
        reviewer = str(entry["reviewer"])
        if reviewer.strip().casefold() in models:
            found.append(
                f"{where}: reviewer {reviewer!r} is a model on the panel, and one "
                "model's verdict is not a human one; give the panel's verdict as "
                f"reviewer {PANEL!r}"
            )
            continue
        if reviewer.strip().casefold() == PANEL:
            valid, value = _parse_verdict(entry["verdict"])
            if not valid:
                found.append(f"{where}: verdict must be true, false or unknown")
                continue
            panel.append(
                PanelVerdict(
                    where=where,
                    case=case,
                    claim=claim,
                    diff_sha256=digest,
                    verdict=value,
                    votes=entry.get("votes"),
                    claim_text=None,
                )
            )
            continue
        if "votes" in entry:
            found.append(f"{where}: votes are for the panel's verdict only")
            continue
        labeller = by_name.get(reviewer)
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
    return Reviews(verdicts=verdicts, decisions=[], skipped=0, panel=panel)


def read_provenance(path: Path) -> tuple[PanelPass, ...]:
    """The panel's passes from a `--panel-provenance` file:
    `{"passes": [{"name", "date", "models": [{"model", "effort"}]}]}`."""
    if not path.is_file():
        raise CalibrationError(f"{path}: no such provenance file")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"{path}: not JSON ({exc.msg})") from exc
    if not isinstance(data, dict) or set(data) != {"passes"}:
        raise CalibrationError(f"{path}: provenance is an object with passes only")
    try:
        return parse_passes(data["passes"])
    except CalibrationError as exc:
        raise CalibrationError(f"{path}: {exc}") from exc


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
    passes: Sequence[PanelPass] | None = None,
) -> list[Label]:
    """The labels file after the reviews, or a refusal. Pure.

    A reviewer's verdict replaces their earlier one on the same claim, and a
    labeller not in this file keeps theirs, so importing one reviewer's file
    and then the other's gives the same rows as importing both at once. A panel
    verdict replaces the row's panel entry, with `passes` as its provenance, and
    a row the file gives no panel verdict keeps its panel entry.
    """
    found: list[str] = []
    votes: dict[Key, dict[str, Review]] = {}
    order: list[Key] = []
    for review in reviews.verdicts:
        if review.labeller not in reviewers:
            found.append(
                f"{review.where}: a verdict from human_{review.labeller} needs "
                f"--reviewer-{review.labeller}, the name of the person behind it"
            )
            continue
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

    panels: dict[Key, Panel] = {}
    if reviews.panel and passes is None:
        found.append("panel verdicts need --panel-provenance, the panel's passes")
    for given in reviews.panel:
        if passes is None:
            break
        if given.key not in export:
            found.append(_not_in_export(given.where, given.key))
            continue
        _check_claim_text(found, given.where, given.claim_text, export[given.key])
        try:
            split = parse_votes(given.votes if given.votes is not None else {}, passes)
        except CalibrationError as exc:
            found.append(f"{given.where}: {exc}")
            continue
        made = Panel(label=given.verdict, passes=tuple(passes), votes=split)
        if given.key in panels and panels[given.key] != made:
            found.append(f"{given.where}: a second, different panel verdict")
            continue
        panels[given.key] = made
        if given.key not in order:
            order.append(given.key)

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
        panel = panels.get(key, base.panel if base is not None else None)
        clash = [
            f"labeller {slot} is recorded as {name!r}, which is the panel or one "
            "of its models and not a human reviewer"
            for slot, name in sorted(names.items())
            if not_a_human(name, panel)
        ]
        if clash:
            case, claim, digest = key
            found.extend(
                f"{case} claim {claim} on diff {digest[:12]}: {text}" for text in clash
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
            panel=panel,
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
    panel_provenance: Path | None = None,
) -> ImportResult:
    """Read a reviewed file and merge it into the labels file. Writes nothing and
    raises if any row is refused.

    `reviewers` names the person behind `a`, `b` or both; a file may hold
    verdicts only from labellers named here. `panel_provenance` is needed when
    the file holds panel verdicts."""
    if not set(reviewers) <= set(LABELLERS) or not all(reviewers.values()):
        raise CalibrationError('reviewers are named for labellers "a" and "b" only')
    if len(set(reviewers.values())) != len(reviewers):
        raise CalibrationError(
            "--reviewer-a and --reviewer-b must name two different people"
        )
    if not reviewers and panel_provenance is None:
        raise CalibrationError(
            "import needs --reviewer-a, --reviewer-b or --panel-provenance"
        )
    passes = read_provenance(panel_provenance) if panel_provenance else None
    named = Panel(label=None, passes=passes) if passes else None
    for slot, name in sorted(reviewers.items()):
        if not_a_human(name, named):
            raise CalibrationError(
                f"--reviewer-{slot} {name!r} is the panel or one of its models, "
                "not a human reviewer"
            )
    reviews = read_reviews(source, reviewers, frozenset(named.models if named else ()))
    if reviews.panel and passes is None:
        raise CalibrationError(
            f"{source}: holds panel verdicts, which need --panel-provenance"
        )
    export = read_export(export_path)
    existing = load_labels(labels_path)
    final = apply_reviews(
        existing,
        reviews,
        export,
        cases,
        reviewers=reviewers,
        adjudicate=adjudicate,
        passes=passes,
    )
    changed = write_labels(labels_path, final)
    statuses = Counter(lab.status for lab in final)
    return ImportResult(
        verdicts=len(reviews.verdicts),
        decisions=len(reviews.decisions),
        skipped=reviews.skipped,
        changed=changed,
        statuses=dict(statuses),
        panel=len(reviews.panel),
        panel_labelled=sum(
            1 for lab in final if lab.panel is not None and lab.panel.label is not None
        ),
    )
