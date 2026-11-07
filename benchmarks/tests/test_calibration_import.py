"""`calibrate import`, offline. Every fixture is small and made here; no real
draft or label file is read."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.runner.calibration import (
    ADJUDICATED,
    AGREED,
    DISAGREE,
    PENDING,
    UNKNOWN,
    CalibrationError,
    calibrate,
    load_labels,
    problems,
)
from benchmarks.runner.calibration_import import ImportRefused, import_reviews
from benchmarks.runner.case import Case, Check, Judge
from benchmarks.runner.report import RepRecord

DIGEST = "a" * 64
OTHER = "b" * 64
CLAIMS = ("The fix is in bar.py.", "The test was not weakened.")
CASE = Case(
    id="fix",
    tier="smoke",
    prompt="p",
    directory=Path("/nowhere"),
    max_turns=1,
    reps=1,
    timeout=1,
    checks=(Check(kind="bash", cmd="true"),),
    judge=Judge(assertions=CLAIMS, min_met=1),
)
CASES = {CASE.id: CASE}
NAMES = {"a": "Jason Berry", "b": "Sam Madinya"}

# The columns of review.csv, as the drafter wrote them.
REVIEW_COLUMNS = [
    "claim_id",
    "case",
    "claim",
    "claim_text",
    "diff_sha256",
    "source",
    "outcome",
    "diff_pointer",
    "closing_pointer",
    "excerpt",
    "draft_label",
    "draft_reason",
    "confidence",
    "flag",
    "human_a",
    "human_b",
    "label",
    "adjudicated",
]


def export_row(claim: int, digest: str) -> dict[str, Any]:
    return {
        "case": "fix",
        "claim": claim,
        "claim_text": CLAIMS[claim - 1],
        "diff_sha256": digest,
        "evidence": "/nowhere",
        "labels": {"a": None, "b": None},
        "label": None,
        "adjudicated": False,
    }


@pytest.fixture
def export(tmp_path: Path) -> Path:
    path = tmp_path / "candidates.jsonl"
    rows = [export_row(1, DIGEST), export_row(2, DIGEST), export_row(1, OTHER)]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


@pytest.fixture
def labels(tmp_path: Path) -> Path:
    return tmp_path / "labels.jsonl"


def review_csv(
    tmp_path: Path, *rows: dict[str, str | int], name: str = "r.csv"
) -> Path:
    path = tmp_path / name
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        for row in rows:
            full: dict[str, str | int] = {column: "" for column in REVIEW_COLUMNS}
            claim = int(row.get("claim", 1))
            full.update(
                case="fix",
                claim=claim,
                claim_text=CLAIMS[claim - 1],
                diff_sha256=DIGEST,
            )
            full.update(row)
            writer.writerow(full)
    return path


def json_file(tmp_path: Path, *entries: dict[str, Any], name: str = "r.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(list(entries)), encoding="utf-8")
    return path


def verdict(
    reviewer: str, verdict_value: Any, claim: int = 1, **extra: Any
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "case": "fix",
        "claim": claim,
        "diff_sha256": DIGEST,
        "reviewer": reviewer,
        "verdict": verdict_value,
    }
    entry.update(extra)
    return entry


def run(
    source: Path,
    export: Path,
    labels: Path,
    *,
    adjudicate: bool = False,
) -> Any:
    return import_reviews(
        source=source,
        export_path=export,
        labels_path=labels,
        reviewers={"a": NAMES["a"], "b": NAMES["b"]},
        adjudicate=adjudicate,
        cases=CASES,
    )


def rows_by_claim(labels: Path) -> dict[tuple[int, str], dict[str, Any]]:
    rows = [json.loads(line) for line in labels.read_text().splitlines()]
    return {(r["claim"], r["diff_sha256"]): r for r in rows}


def test_agreed_verdicts_give_the_label(
    tmp_path: Path, export: Path, labels: Path
) -> None:
    source = review_csv(
        tmp_path,
        {"human_a": "true", "human_b": "true", "label": ""},
    )
    run(source, export, labels)
    (row,) = [json.loads(line) for line in labels.read_text().splitlines()]
    assert row["status"] == AGREED
    assert row["label"] is True and row["adjudicated"] is False
    assert row["reviewers"] == NAMES
    assert problems(load_labels(labels), CASES) == []


def test_a_disagreement_waits_for_adjudication(
    tmp_path: Path, export: Path, labels: Path
) -> None:
    source = review_csv(tmp_path, {"human_a": "true", "human_b": "false"})
    run(source, export, labels)
    (lab,) = load_labels(labels)
    assert lab.status == DISAGREE and lab.label is None and lab.adjudicated is False


def test_an_adjudication_settles_a_disagreement(
    tmp_path: Path, export: Path, labels: Path
) -> None:
    source = review_csv(
        tmp_path, {"human_a": "true", "human_b": "false", "label": "false"}
    )
    run(source, export, labels, adjudicate=True)
    (lab,) = load_labels(labels)
    assert lab.status == ADJUDICATED
    assert lab.label is False and lab.adjudicated is True


def test_a_decision_without_the_flag_is_refused(
    tmp_path: Path, export: Path, labels: Path
) -> None:
    source = review_csv(
        tmp_path, {"human_a": "true", "human_b": "false", "label": "true"}
    )
    with pytest.raises(ImportRefused, match="needs --adjudicate"):
        run(source, export, labels)
    assert not labels.exists()


def test_a_decision_on_agreed_verdicts_is_refused(
    tmp_path: Path, export: Path, labels: Path
) -> None:
    source = review_csv(
        tmp_path, {"human_a": "true", "human_b": "true", "label": "true"}
    )
    with pytest.raises(ImportRefused, match="only for two verdicts that disagree"):
        run(source, export, labels, adjudicate=True)


def test_unknown_is_no_label_and_stays_out_of_the_rates(
    tmp_path: Path, export: Path, labels: Path
) -> None:
    source = review_csv(tmp_path, {"human_a": "unknown", "human_b": "true"})
    run(source, export, labels)
    (lab,) = load_labels(labels)
    assert lab.status == UNKNOWN and lab.label is None
    assert lab.unknown_from == ("a",) and lab.labels == {"a": None, "b": True}
    judged = [
        RepRecord(
            case="fix",
            rep=0,
            outcome="pass",
            checks=(),
            judge_met=None,
            judge_why="",
            status="done",
            stop_reason="end_turn",
            turns=1,
            judge_model="judge-x",
            judge_claims=[False, True],
            diff_sha256=DIGEST,
        )
    ]
    result = calibrate(load_labels(labels), judged)
    assert result.labelled == 0 and result.by_judge == {}


def test_a_pending_row_is_kept_until_the_other_reviewer_answers(
    tmp_path: Path, export: Path, labels: Path
) -> None:
    run(review_csv(tmp_path, {"human_a": "true"}), export, labels)
    (lab,) = load_labels(labels)
    assert lab.status == PENDING and lab.label is None

    run(
        review_csv(tmp_path, {"human_b": "true"}, name="b.csv"),
        export,
        labels,
    )
    (lab,) = load_labels(labels)
    assert lab.status == AGREED and lab.label is True
    assert lab.reviewers == NAMES


def test_importing_each_reviewer_separately_matches_importing_both(
    tmp_path: Path, export: Path
) -> None:
    both = review_csv(
        tmp_path,
        {"human_a": "true", "human_b": "false", "claim": 1},
        {"human_a": "false", "human_b": "false", "claim": 2},
    )
    together = tmp_path / "together.jsonl"
    run(both, export, together)

    split = tmp_path / "split.jsonl"
    run(
        review_csv(
            tmp_path,
            {"human_a": "true", "claim": 1},
            {"human_a": "false", "claim": 2},
            name="a.csv",
        ),
        export,
        split,
    )
    run(
        review_csv(
            tmp_path,
            {"human_b": "false", "claim": 1},
            {"human_b": "false", "claim": 2},
            name="b.csv",
        ),
        export,
        split,
    )
    assert split.read_bytes() == together.read_bytes()


def test_a_json_import_gives_the_same_rows_as_the_csv(
    tmp_path: Path, export: Path
) -> None:
    csv_labels, json_labels = tmp_path / "c.jsonl", tmp_path / "j.jsonl"
    run(
        review_csv(
            tmp_path,
            {"human_a": "true", "human_b": "unknown"},
            {"claim": 2, "human_a": "false", "human_b": "false"},
        ),
        export,
        csv_labels,
    )
    run(
        json_file(
            tmp_path,
            verdict(NAMES["a"], True, note="it changed bar.py"),
            verdict(NAMES["b"], "unknown"),
            verdict(NAMES["a"], False, claim=2),
            verdict(NAMES["b"], False, claim=2),
        ),
        export,
        json_labels,
    )
    assert rows_by_claim(csv_labels).keys() == rows_by_claim(json_labels).keys()
    for key, row in rows_by_claim(json_labels).items():
        assert row["status"] == rows_by_claim(csv_labels)[key]["status"]
        assert row["label"] == rows_by_claim(csv_labels)[key]["label"]
    assert rows_by_claim(json_labels)[(1, DIGEST)]["notes"] == {
        "a": "it changed bar.py"
    }


def test_a_row_whose_diff_hash_is_not_in_the_export_refuses_the_file(
    tmp_path: Path, export: Path, labels: Path
) -> None:
    source = json_file(
        tmp_path,
        verdict(NAMES["a"], True),
        verdict(NAMES["b"], True) | {"diff_sha256": "c" * 64},
    )
    with pytest.raises(ImportRefused) as refused:
        run(source, export, labels)
    assert any("not in the export" in text for text in refused.value.found)
    assert not labels.exists()


def test_a_changed_claim_text_refuses_the_file(
    tmp_path: Path, export: Path, labels: Path
) -> None:
    source = review_csv(
        tmp_path,
        {"human_a": "true", "human_b": "true", "claim_text": "an edited claim"},
    )
    with pytest.raises(ImportRefused, match="claim_text is not the text"):
        run(source, export, labels)
    assert not labels.exists()


def test_a_refused_file_leaves_an_existing_labels_file_alone(
    tmp_path: Path, export: Path, labels: Path
) -> None:
    run(review_csv(tmp_path, {"human_a": "true", "human_b": "true"}), export, labels)
    before = labels.read_bytes()
    bad = json_file(
        tmp_path,
        verdict(NAMES["a"], False),
        verdict(NAMES["b"], False) | {"diff_sha256": "d" * 64},
    )
    with pytest.raises(ImportRefused):
        run(bad, export, labels)
    assert labels.read_bytes() == before


def test_a_reviewer_name_that_is_neither_refuses_the_file(
    tmp_path: Path, export: Path, labels: Path
) -> None:
    source = json_file(tmp_path, verdict("someone-else", True))
    with pytest.raises(ImportRefused, match="neither"):
        run(source, export, labels)


def test_a_labeller_recorded_under_another_name_is_refused(
    tmp_path: Path, export: Path, labels: Path
) -> None:
    run(review_csv(tmp_path, {"human_a": "true"}), export, labels)
    swapped = json_file(tmp_path, verdict(NAMES["b"], True))
    with pytest.raises(ImportRefused, match="recorded as"):
        import_reviews(
            source=swapped,
            export_path=export,
            labels_path=labels,
            reviewers={"a": NAMES["b"], "b": NAMES["a"]},
            adjudicate=False,
            cases=CASES,
        )


def test_draft_labels_are_never_imported(
    tmp_path: Path, export: Path, labels: Path
) -> None:
    # The drafter's guess is true; the humans say false, so the label is false.
    source = review_csv(
        tmp_path,
        {
            "human_a": "false",
            "human_b": "false",
            "draft_label": "true",
            "draft_reason": "the draft",
            "confidence": "high",
        },
        {
            "claim": 2,
            "draft_label": "true",
        },  # not reviewed: a draft alone counts for nothing
    )
    result = run(source, export, labels)
    assert result.skipped == 1
    (lab,) = load_labels(labels)
    assert lab.label is False and lab.status == AGREED
    assert "the draft" not in labels.read_text()


def test_a_row_with_only_a_draft_writes_nothing(
    tmp_path: Path, export: Path, labels: Path
) -> None:
    source = review_csv(tmp_path, {"draft_label": "true", "confidence": "high"})
    result = run(source, export, labels)
    assert result.skipped == 1 and result.verdicts == 0
    assert not labels.exists()


def test_reimporting_the_same_file_is_idempotent(
    tmp_path: Path, export: Path, labels: Path
) -> None:
    source = review_csv(
        tmp_path,
        {"human_a": "true", "human_b": "false", "label": "true"},
        {"claim": 2, "human_a": "unknown", "human_b": "true"},
    )
    run(source, export, labels, adjudicate=True)
    first = labels.read_bytes()
    again = run(source, export, labels, adjudicate=True)
    assert labels.read_bytes() == first
    assert again.changed is False


def test_the_csv_shape_needs_its_columns(
    tmp_path: Path, export: Path, labels: Path
) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("case,claim,human_a\nfix,1,true\n", encoding="utf-8")
    with pytest.raises(CalibrationError, match="missing column"):
        run(path, export, labels)


def test_a_verdict_must_be_true_false_or_unknown(
    tmp_path: Path, export: Path, labels: Path
) -> None:
    source = review_csv(tmp_path, {"human_a": "yes", "human_b": "true"})
    with pytest.raises(ImportRefused, match="must be true, false or unknown"):
        run(source, export, labels)


def test_the_check_sees_an_imported_file_as_clean(
    tmp_path: Path, export: Path, labels: Path
) -> None:
    run(
        review_csv(tmp_path, {"human_a": "true", "human_b": "true"}),
        export,
        labels,
    )
    assert problems(load_labels(labels), CASES) == []


def test_the_scores_read_the_imported_labels(
    tmp_path: Path, export: Path, labels: Path
) -> None:
    run(
        review_csv(
            tmp_path,
            {"human_a": "true", "human_b": "true"},  # FN if judged false
            {"claim": 2, "human_a": "false", "human_b": "false"},  # FP if judged true
        ),
        export,
        labels,
    )
    record = RepRecord(
        case="fix",
        rep=0,
        outcome="pass",
        checks=(),
        judge_met=None,
        judge_why="",
        status="done",
        stop_reason="end_turn",
        turns=1,
        judge_model="judge-x",
        judge_claims=[False, True],
        diff_sha256=DIGEST,
    )
    rates = calibrate(load_labels(labels), [record]).by_judge["judge-x"]
    assert (rates.false_neg, rates.positives) == (1, 1)
    assert (rates.false_pos, rates.negatives) == (1, 1)


def test_bin_bench_calibrate_import_runs_end_to_end(
    tmp_path: Path, export: Path, labels: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from benchmarks.runner.__main__ import main
    from benchmarks.runner.case import load_cases

    real = {c.id: c for c in load_cases(Path("benchmarks/cases"))}
    judged = next(c for c in real.values() if c.judge and c.judge.assertions)
    assert judged.judge is not None
    text = judged.judge.assertions[0]
    export_real = tmp_path / "real-export.jsonl"
    export_real.write_text(
        json.dumps(
            {
                "case": judged.id,
                "claim": 1,
                "claim_text": text,
                "diff_sha256": DIGEST,
                "evidence": "/nowhere",
                "labels": {"a": None, "b": None},
                "label": None,
                "adjudicated": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    source = tmp_path / "review.csv"
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        row = {c: "" for c in REVIEW_COLUMNS}
        row.update(
            case=judged.id,
            claim=1,
            claim_text=text,
            diff_sha256=DIGEST,
            human_a="true",
            human_b="true",
        )
        writer.writerow(row)
    code = main(
        [
            "calibrate",
            "import",
            str(source),
            "--export",
            str(export_real),
            "--reviewer-a",
            NAMES["a"],
            "--reviewer-b",
            NAMES["b"],
            "--labels",
            str(labels),
        ]
    )
    assert code == 0
    assert "imported 2 verdict(s)" in capsys.readouterr().out
    assert [lab.status for lab in load_labels(labels)] == [AGREED]
