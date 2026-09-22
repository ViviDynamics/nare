from __future__ import annotations

from benchmarks.runner.grade import Outcome
from benchmarks.runner.report import RepRecord, median_int, summarize


def record(
    case: str = "c",
    rep: int = 0,
    outcome: Outcome = "pass",
    tokens: int = 1000,
    turns: int = 4,
    judge_met: int | None = 2,
) -> RepRecord:
    return RepRecord(
        case=case,
        rep=rep,
        outcome=outcome,
        checks=(),
        judge_met=judge_met,
        judge_why="",
        status="done",
        stop_reason="end_turn",
        turns=turns,
        usage={"input": tokens, "output": 0, "cache_read": 0, "cache_write": 0},
        duration_s=1.0,
        model="claude-haiku",
    )


def test_median_of_an_empty_sequence_is_none() -> None:
    assert median_int([]) is None


def test_median_rounds_to_an_int() -> None:
    assert median_int([1, 2]) == 2
    assert median_int([10, 20, 31]) == 20


def test_pass_rate_counts_passes_over_passes_plus_fails() -> None:
    records = [record(outcome="pass"), record(outcome="pass"), record(outcome="fail")]
    summary = summarize(records)[0]
    assert (summary.passes, summary.fails, summary.errors) == (2, 1, 0)
    assert summary.pass_rate == 2 / 3


def test_errors_are_excluded_from_the_denominator() -> None:
    """An infrastructure failure must never read as a task failure."""
    records = [record(outcome="pass"), record(outcome="error")]
    summary = summarize(records)[0]
    assert summary.pass_rate == 1.0
    assert summary.errors == 1
    assert not summary.inconclusive


def test_more_than_half_errors_is_inconclusive() -> None:
    records = [record(outcome="pass"), record(outcome="error"), record(outcome="error")]
    summary = summarize(records)[0]
    assert summary.inconclusive
    assert summary.pass_rate is None


def test_all_errors_is_inconclusive_not_a_zero_pass_rate() -> None:
    summary = summarize([record(outcome="error")])[0]
    assert summary.inconclusive
    assert summary.pass_rate is None


def test_medians_ignore_errored_reps() -> None:
    records = [
        record(outcome="pass", tokens=1000, turns=4),
        record(outcome="fail", tokens=2000, turns=6),
        record(outcome="error", tokens=999_999, turns=999),
    ]
    summary = summarize(records)[0]
    assert summary.tokens_median == 1500
    assert summary.turns_median == 5


def test_one_runaway_rep_cannot_swing_the_median() -> None:
    records = [
        record(tokens=1000),
        record(tokens=1100),
        record(tokens=90_000),
    ]
    assert summarize(records)[0].tokens_median == 1100


def test_tokens_median_sums_input_and_output() -> None:
    rec = record(tokens=1000)
    rec = RepRecord(**{**rec.__dict__, "usage": {**rec.usage, "output": 500}})
    assert summarize([rec])[0].tokens_median == 1500


def test_judge_median_is_none_when_no_rep_was_judged() -> None:
    assert summarize([record(judge_met=None)])[0].judge_met_median is None


def test_summaries_are_sorted_by_case() -> None:
    records = [record(case="zeta"), record(case="alpha")]
    assert [s.case for s in summarize(records)] == ["alpha", "zeta"]
