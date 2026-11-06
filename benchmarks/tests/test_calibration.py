from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.runner.calibration import (
    CalibrationError,
    calibrate,
    export_candidates,
    load_labels,
    problems,
    render_calibration,
)
from benchmarks.runner.case import Case, Check, Judge
from benchmarks.runner.report import RepRecord

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
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


def label_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "case": "fix",
        "claim": 1,
        "claim_text": CLAIMS[0],
        "diff_sha256": DIGEST_A,
        "labels": {"a": True, "b": True},
        "label": True,
        "adjudicated": False,
    }
    row.update(overrides)
    return row


def write(path: Path, *rows: object) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def rec(
    *,
    judge_model: str = "judge-x",
    claims: list[bool | None] | None,
    digest: str = DIGEST_A,
    rep: int = 0,
) -> RepRecord:
    return RepRecord(
        case="fix",
        rep=rep,
        outcome="pass",
        checks=(),
        judge_met=None,
        judge_why="",
        status="done",
        stop_reason="end_turn",
        turns=1,
        judge_model=judge_model,
        judge_claims=claims,
        diff_sha256=digest,
    )


def test_a_missing_labels_file_is_an_empty_set(tmp_path: Path) -> None:
    assert load_labels(tmp_path / "nope.jsonl") == []


def test_a_well_formed_label_loads(tmp_path: Path) -> None:
    (label,) = load_labels(write(tmp_path / "l.jsonl", label_row()))
    assert (label.case, label.claim, label.label) == ("fix", 1, True)
    assert label.labels == {"a": True, "b": True}


def test_bad_json_names_its_line(tmp_path: Path) -> None:
    path = tmp_path / "l.jsonl"
    path.write_text("\n{not json\n", encoding="utf-8")
    with pytest.raises(CalibrationError, match="line 2"):
        load_labels(path)


@pytest.mark.parametrize(
    "override, why",
    [
        ({"label": None}, "never null"),
        ({"labels": {"a": True}}, 'from each of "a" and "b"'),
        ({"labels": {"a": True, "b": False}, "label": True}, "adjudicated"),
        ({"labels": {"a": True, "b": True}, "adjudicated": True}, "not adjudicated"),
        ({"claim": 0}, "1-based"),
        ({"diff_sha256": "short"}, "64-character"),
    ],
)
def test_a_label_that_breaks_the_format_is_refused(
    tmp_path: Path, override: dict[str, object], why: str
) -> None:
    path = write(tmp_path / "l.jsonl", label_row(**override))
    with pytest.raises(CalibrationError, match=why):
        load_labels(path)


def test_disagreeing_labellers_are_settled_by_an_adjudicated_label(
    tmp_path: Path,
) -> None:
    row = label_row(labels={"a": True, "b": False}, label=False, adjudicated=True)
    (label,) = load_labels(write(tmp_path / "l.jsonl", row))
    assert label.label is False and label.adjudicated


def test_problems_finds_a_claim_the_case_no_longer_has(tmp_path: Path) -> None:
    labels = load_labels(write(tmp_path / "l.jsonl", label_row(claim=3)))
    assert any("has 2 claims" in p for p in problems(labels, CASES))


def test_problems_finds_claim_text_that_drifted_from_the_case(tmp_path: Path) -> None:
    labels = load_labels(write(tmp_path / "l.jsonl", label_row(claim_text="edited")))
    assert any("claim_text is not claim 1" in p for p in problems(labels, CASES))


def test_problems_finds_the_same_claim_labelled_twice(tmp_path: Path) -> None:
    labels = load_labels(write(tmp_path / "l.jsonl", label_row(), label_row()))
    assert any("same claim on the same diff" in p for p in problems(labels, CASES))


def test_a_clean_labels_file_has_no_problems(tmp_path: Path) -> None:
    labels = load_labels(write(tmp_path / "l.jsonl", label_row()))
    assert problems(labels, CASES) == []


def test_export_is_blind_to_the_judge() -> None:
    rows = export_candidates([rec(claims=[True, False])], CASES)
    assert len(rows) == 2
    for row in rows:
        assert "judge" not in json.dumps(row)
        assert row["labels"] == {"a": None, "b": None}
        assert row["label"] is None


def test_export_skips_reps_with_no_digest_and_dedups_identical_diffs() -> None:
    records = [
        rec(claims=[True, True]),
        rec(claims=[True, True], rep=1),  # same diff: its claims are already listed
        rec(claims=None, digest=""),
    ]
    assert len(export_candidates(records, CASES)) == 2


def test_export_sampling_is_reproducible_for_a_seed() -> None:
    records = [rec(claims=[True, True], digest=f"{i:064x}", rep=i) for i in range(20)]
    first = export_candidates(records, CASES, sample=5, seed=7)
    again = export_candidates(records, CASES, sample=5, seed=7)
    assert first == again and len(first) == 5


def test_a_label_is_scored_against_the_judge_on_its_diff(tmp_path: Path) -> None:
    labels = load_labels(
        write(
            tmp_path / "l.jsonl",
            label_row(label=True),  # labelled true, judged false: an FN
            label_row(
                claim=2,
                claim_text=CLAIMS[1],
                label=False,
                labels={"a": False, "b": False},
            ),  # false, judged true: an FP
        )
    )
    result = calibrate(labels, [rec(claims=[False, True])])
    rates = result.by_judge["judge-x"]
    assert (rates.scored, rates.positives, rates.false_neg) == (2, 1, 1)
    assert (rates.negatives, rates.false_pos) == (1, 1)


def test_an_unknown_verdict_counts_in_neither_rate(tmp_path: Path) -> None:
    labels = load_labels(write(tmp_path / "l.jsonl", label_row()))
    rates = calibrate(labels, [rec(claims=[None, True])]).by_judge["judge-x"]
    assert (rates.scored, rates.unknown, rates.false_neg) == (0, 1, 0)


def test_a_diff_judged_twice_counts_once_per_rep(tmp_path: Path) -> None:
    labels = load_labels(write(tmp_path / "l.jsonl", label_row()))
    records = [rec(claims=[True, True]), rec(claims=[False, True], rep=1)]
    rates = calibrate(labels, records).by_judge["judge-x"]
    assert rates.scored == 2 and rates.false_neg == 1


def test_a_label_with_no_judged_rep_is_unmatched(tmp_path: Path) -> None:
    labels = load_labels(write(tmp_path / "l.jsonl", label_row(diff_sha256=DIGEST_B)))
    result = calibrate(labels, [rec(claims=[True, True])])
    assert result.unmatched == 1 and result.by_judge == {}


def test_rates_are_kept_per_judge_model(tmp_path: Path) -> None:
    labels = load_labels(write(tmp_path / "l.jsonl", label_row()))
    records = [
        rec(judge_model="one", claims=[True, True]),
        rec(judge_model="two", claims=[False, True], rep=1),
    ]
    result = calibrate(labels, records)
    assert set(result.by_judge) == {"one", "two"}
    assert result.by_judge["two"].false_neg == 1
    assert result.by_judge["one"].false_neg == 0


def test_render_says_so_when_there_are_no_labels() -> None:
    text = render_calibration(calibrate([], []), Path("labels.jsonl"))
    assert "no labelled claims yet" in text


def test_render_gives_the_rates_per_judge(tmp_path: Path) -> None:
    labels = load_labels(
        write(
            tmp_path / "l.jsonl",
            label_row(label=True),
            label_row(
                claim=2,
                claim_text=CLAIMS[1],
                label=False,
                labels={"a": False, "b": False},
            ),
        )
    )
    text = render_calibration(
        calibrate(labels, [rec(claims=[False, True])]), Path("labels.jsonl")
    )
    assert "judge-x" in text
    assert "1/1 (100%)" in text  # FN: one labelled true, judged false
    assert "1/1 (100%)" in text  # FP: one labelled false, judged true
