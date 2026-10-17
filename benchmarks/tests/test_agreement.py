from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from benchmarks.runner.agreement import (
    GROUND_TRUTH,
    TOO_LARGE,
    ClaimPair,
    Header,
    Row,
    Skip,
    agreement,
    band,
    claim_pairs,
    confidence_split,
    confusion,
    outcome_flips,
    percentile,
    render_summary,
    spend,
)
from benchmarks.runner.grade import CheckResult
from benchmarks.runner.report import RepRecord
from benchmarks.tests.test_judge import CASE

PASSED = CheckResult("bash: pytest -q", True, "")
FAILED = CheckResult("bash: pytest -q", False, "exit 1")
CASES = {CASE.id: CASE}  # two claims, min_met 2


def claims(
    judge: Any, rep: int, verdicts: Any, model: str | None = "m", **extra: Any
) -> Row:
    return Row(CASE.id, rep, "claims", judge, model, verdicts=verdicts, **extra)


def record(rep: int, checks: tuple[CheckResult, ...] = (PASSED,)) -> RepRecord:
    return RepRecord(CASE.id, rep, "pass", checks, 2, "", "done", "end_turn", 3)


def test_claim_pairs_line_up_each_claim_with_the_reference() -> None:
    rows = [
        claims("reference", 0, [True, False]),
        claims("jev", 0, [0.9, 0.7]),
    ]
    assert claim_pairs(rows, "jev") == [
        ClaimPair(CASE.id, 0, 0.9, True),
        ClaimPair(CASE.id, 1, 0.7, False),
    ]


def test_a_judge_error_drops_only_that_judges_pairs() -> None:
    rows = [
        claims("reference", 0, [True, True]),
        claims("jev", 0, None, error=TOO_LARGE),
        claims("llm", 0, [True, False]),
    ]
    assert claim_pairs(rows, "jev") == []
    assert len(claim_pairs(rows, "llm")) == 2


def test_a_reference_error_drops_the_rep_for_every_judge() -> None:
    rows = [
        claims("reference", 0, None, error="judge failed"),
        claims("jev", 0, [0.9, 0.1]),
        claims("llm", 0, [True, False]),
    ]
    assert claim_pairs(rows, "jev") == []
    assert claim_pairs(rows, "llm") == []


PAIRS = [
    ClaimPair("c", 0, 0.95, True),
    ClaimPair("c", 1, 0.65, True),
    ClaimPair("c", 2, 0.55, False),
    ClaimPair("c", 3, 0.10, False),
]


def test_agreement_at_a_cutoff() -> None:
    assert agreement(PAIRS, 0.5) == (3, 4)  # 0.55 reads as true, wrongly
    assert agreement(PAIRS, 0.6) == (4, 4)
    assert agreement(PAIRS, 0.7) == (3, 4)  # 0.65 now reads as false


def test_the_llm_judges_booleans_agree_at_one_half() -> None:
    pairs = [ClaimPair("c", 0, 1.0, True), ClaimPair("c", 1, 0.0, True)]
    assert agreement(pairs) == (1, 2)


def test_a_band_leaves_unsure_claims_unanswered() -> None:
    # At 0.7, 0.65 and 0.55 sit between 0.3 and 0.7.
    assert band(PAIRS, 0.7) == (2, 4, 2)
    # At 0.6, only 0.55 is unsure; 0.65 answers true and agrees.
    assert band(PAIRS, 0.6) == (3, 4, 3)


def test_a_band_edge_counts_as_answered() -> None:
    """1 - 0.9 is 0.0999... in floating point; 0.1 must still answer false."""
    assert band([ClaimPair("c", 0, 0.1, False)], 0.9) == (1, 1, 1)


def test_an_outcome_flips_when_the_gate_reads_differently() -> None:
    rows = [
        claims("reference", 0, [True, True]),  # 2 met: pass
        claims("jev", 0, [0.9, 0.55]),  # at 0.5: pass; at 0.6: fail
        claims("llm", 0, [True, False]),  # fail
    ]
    records = [record(0)]
    assert outcome_flips(rows, "llm", CASES, records) == (1, 1)
    assert outcome_flips(rows, "jev", CASES, records, 0.5) == (0, 1)
    assert outcome_flips(rows, "jev", CASES, records, 0.6) == (1, 1)


def test_a_rep_whose_checks_failed_cannot_flip() -> None:
    rows = [claims("reference", 0, [True, True]), claims("llm", 0, [False, False])]
    assert outcome_flips(rows, "llm", CASES, [record(0, (FAILED,))]) == (0, 1)


def test_an_ungated_case_has_no_flips_to_count() -> None:
    assert CASE.judge is not None
    ungated = {CASE.id: replace(CASE, judge=replace(CASE.judge, min_met=None))}
    rows = [claims("reference", 0, [True, True]), claims("llm", 0, [False, False])]
    assert outcome_flips(rows, "llm", ungated, [record(0)]) == (0, 0)


def triage(
    judge: Any, rep: int, cause: str | None, model: str | None = "m", **extra: Any
) -> Row:
    return Row(CASE.id, rep, "triage", judge, model, cause=cause, **extra)


TRIAGE = [
    triage("reference", 0, "no_change"),
    triage("jev", 0, "no_change", confidence=0.9),
    triage("reference", 1, "wrong_change"),
    triage("jev", 1, "no_change", confidence=0.4),
    triage("reference", 2, "wrong_change"),
    triage("jev", 2, "wrong_change", confidence=0.8),
    triage("reference", 3, None, error="judge failed"),
    triage("jev", 3, "other", confidence=0.7),
]


def test_the_confusion_table_counts_reference_against_jev() -> None:
    assert confusion(TRIAGE) == {
        ("no_change", "no_change"): 1,
        ("wrong_change", "no_change"): 1,
        ("wrong_change", "wrong_change"): 1,
    }


def test_confidence_is_split_by_agreement() -> None:
    assert confidence_split(TRIAGE) == ([0.9, 0.8], [0.4])


def test_percentile_is_nearest_rank() -> None:
    values = [float(n) for n in range(1, 21)]
    assert percentile(values, 0.95) == 19.0
    assert percentile([3.0], 0.95) == 3.0
    assert percentile([], 0.95) is None


def test_spend_sums_calls_tokens_and_dollars() -> None:
    rows = [
        claims("jev", 0, [0.9], tokens=300, cost=0.01, latency_s=0.2, attempts=1),
        claims("jev", 1, [0.9], tokens=100, cost=0.02, latency_s=0.4, attempts=2),
        claims("jev", 2, None, error=TOO_LARGE),
    ]
    s = spend(rows, "jev")
    assert (s.calls, s.tokens, s.unknown) == (3, 400, 0)
    assert s.dollars == pytest.approx(0.03)
    assert s.median_s == pytest.approx(0.3)
    assert s.p95_s == 0.4


def test_an_unknown_cost_is_counted_not_zeroed() -> None:
    rows = [claims("llm", 0, [True], cost=None, attempts=1, latency_s=1.0)]
    assert spend(rows, "llm").unknown == 1


def test_the_summary_names_every_section_with_its_n() -> None:
    rows = [
        claims("jev", 0, [0.9, 0.55], model="jev-1.13.0", attempts=1, latency_s=0.3),
        claims("llm", 0, [True, False], attempts=1, latency_s=2.0),
        claims("reference", 0, [True, True], attempts=1, latency_s=4.0),
        claims("jev", 1, None, error=TOO_LARGE),
        triage("code", 2, "ran_out", model=None, claimed_done=False),
        *[replace(r, claimed_done=True) for r in TRIAGE[:2]],
    ]
    text = render_summary(
        Header("2026-10-08T00-00-00Z", "m", "m", "opus"),
        rows,
        [Skip(CASE.id, 4, "no evidence files")],
        CASES,
        [record(0)],
    )
    assert "compare-judges over results 2026-10-08T00-00-00Z" in text
    assert "jev: jev-1.13.0" in text
    assert "skipped, no evidence files: 1" in text
    assert "too_large: jev claims 1" in text
    assert GROUND_TRUTH in text
    assert "llm judge                          50% (1/2)" in text
    assert "jev, P(yes) >= 0.5                 100% (2/2)" in text
    assert "coverage 50% (1/2), agreement 100% (1/1)" in text
    assert "The fix is in bar.py." in text
    assert "labelled by code: ran_out 1" in text
    assert "claimed done with a failing check: 1 of 2 triaged failures" in text
    assert "~ estimated" not in text


def test_an_estimated_cost_is_marked() -> None:
    rows = [claims("jev", 0, [0.9], cost=0.00004, cost_estimated=True, attempts=1)]
    text = render_summary(Header("s", "m", "m", "opus"), rows, [], CASES, [])
    assert "~$0.000040" in text
    assert "~ estimated from Jev's input tokens" in text
