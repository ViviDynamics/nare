from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.runner.grade import Outcome
from benchmarks.runner.report import (
    Baseline,
    BaselineMeta,
    CaseSummary,
    RepRecord,
    baseline_path,
    compare,
    dumps_baseline,
    load_baseline,
    median_int,
    render,
    slugify,
    summarize,
)


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


def test_tokens_median_counts_cached_input() -> None:
    """The proxy serves most input from cache; leaving it out hides a longer
    prompt, which is exactly what a harness change tends to add."""
    rec = record(tokens=1)
    usage = {"input": 1, "output": 50, "cache_read": 640, "cache_write": 9}
    rec = RepRecord(**{**rec.__dict__, "usage": usage})
    assert summarize([rec])[0].tokens_median == 700


def test_judge_median_is_none_when_no_rep_was_judged() -> None:
    assert summarize([record(judge_met=None)])[0].judge_met_median is None


def test_summaries_are_sorted_by_case() -> None:
    records = [record(case="zeta"), record(case="alpha")]
    assert [s.case for s in summarize(records)] == ["alpha", "zeta"]


META = BaselineMeta(
    blessed="2026-09-22T14:02:00Z",
    commit="237a678",
    provider="anthropic",
    model="claude-haiku",
    judge_model="claude-haiku",
)


def test_slugify_makes_a_model_name_filesystem_safe() -> None:
    assert slugify("claude-haiku") == "claude-haiku"
    assert slugify("ada/qwen3-14b") == "ada-qwen3-14b"
    assert slugify("spark/glm-5.3-flash") == "spark-glm-5-3-flash"


def test_baseline_path_keys_by_model_and_tier(tmp_path: Path) -> None:
    assert baseline_path(tmp_path, "ada/qwen3-14b", "smoke") == (
        tmp_path / "ada-qwen3-14b.smoke.toml"
    )


def test_a_blessed_baseline_round_trips(tmp_path: Path) -> None:
    summaries = summarize([record(case="c", tokens=1000, turns=4, judge_met=3)])
    path = tmp_path / "claude-haiku.smoke.toml"
    path.write_text(dumps_baseline(META, summaries))

    baseline = load_baseline(path)
    assert baseline.meta == META
    assert baseline.cases["c"].pass_rate == 1.0
    assert baseline.cases["c"].tokens_median == 1000
    assert baseline.cases["c"].judge_met_median == 3


def test_inconclusive_cases_are_not_blessed(tmp_path: Path) -> None:
    """A baseline is a claim about what should happen. 'We don't know' isn't one."""
    summaries = summarize(
        [record(case="c", outcome="error"), record(case="c", outcome="error")]
    )
    path = tmp_path / "b.toml"
    path.write_text(dumps_baseline(META, summaries))
    assert load_baseline(path).cases == {}


def test_loading_a_missing_baseline_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_baseline(tmp_path / "absent.toml")


def test_a_model_name_with_quotes_survives_the_round_trip(tmp_path: Path) -> None:
    meta = BaselineMeta(
        blessed=META.blessed,
        commit=META.commit,
        provider="anthropic",
        model='weird"name',
        judge_model="claude-haiku",
    )
    path = tmp_path / "b.toml"
    path.write_text(dumps_baseline(meta, summarize([record(case="c")])))
    assert load_baseline(path).meta.model == 'weird"name'


def summary(
    case: str = "c",
    pass_rate: float | None = 1.0,
    tokens: int | None = 1000,
    judge: int | None = 3,
    inconclusive: bool = False,
) -> CaseSummary:
    return CaseSummary(
        case=case,
        passes=3,
        fails=0,
        errors=0,
        pass_rate=pass_rate,
        inconclusive=inconclusive,
        tokens_median=tokens,
        turns_median=4,
        judge_met_median=judge,
    )


def baseline_of(*summaries: CaseSummary) -> Baseline:
    return Baseline(meta=META, cases={s.case: s for s in summaries})


def test_matching_numbers_are_ok_and_exit_zero() -> None:
    comparison = compare([summary()], baseline_of(summary()))
    assert comparison.deltas[0].verdict == "ok"
    assert comparison.exit_code == 0


def test_a_pass_rate_drop_is_suspect_before_confirmation() -> None:
    comparison = compare([summary(pass_rate=0.67)], baseline_of(summary()))
    assert comparison.deltas[0].verdict == "suspect"
    assert comparison.exit_code == 0


def test_a_confirmed_pass_rate_drop_is_a_regression() -> None:
    comparison = compare(
        [summary(pass_rate=0.67)], baseline_of(summary()), confirmed=True
    )
    assert comparison.deltas[0].verdict == "regression"
    assert comparison.exit_code == 1


def test_a_higher_pass_rate_is_an_improvement_not_a_failure() -> None:
    comparison = compare([summary(pass_rate=1.0)], baseline_of(summary(pass_rate=0.5)))
    assert comparison.deltas[0].verdict == "improved"
    assert comparison.exit_code == 0


def test_tokens_inside_the_band_do_not_register() -> None:
    comparison = compare([summary(tokens=1030)], baseline_of(summary(tokens=1000)))
    assert comparison.deltas[0].verdict == "ok"
    assert comparison.exit_code == 0


def test_tokens_above_the_band_warn_but_exit_zero() -> None:
    comparison = compare([summary(tokens=1150)], baseline_of(summary(tokens=1000)))
    assert comparison.exit_code == 0
    assert any("token" in note for note in comparison.deltas[0].notes)


def test_tokens_above_the_band_fail_under_strict() -> None:
    comparison = compare(
        [summary(tokens=1150)], baseline_of(summary(tokens=1000)), strict_tokens=True
    )
    assert comparison.exit_code == 1


def test_tokens_below_the_band_are_never_a_failure() -> None:
    comparison = compare(
        [summary(tokens=500)], baseline_of(summary(tokens=1000)), strict_tokens=True
    )
    assert comparison.exit_code == 0


def test_a_judge_drop_warns() -> None:
    comparison = compare([summary(judge=1)], baseline_of(summary(judge=3)))
    assert comparison.exit_code == 0
    assert any("judge" in note for note in comparison.deltas[0].notes)


def test_an_inconclusive_case_exits_one() -> None:
    comparison = compare(
        [summary(pass_rate=None, inconclusive=True)], baseline_of(summary())
    )
    assert comparison.deltas[0].verdict == "inconclusive"
    assert comparison.exit_code == 1


def test_a_case_absent_from_the_baseline_is_new_and_never_fails() -> None:
    comparison = compare([summary(case="fresh")], baseline_of(summary(case="old")))
    verdicts = {d.case: d.verdict for d in comparison.deltas}
    assert verdicts["fresh"] == "new"
    assert comparison.exit_code == 0


def test_a_case_absent_from_the_run_is_missing_and_never_fails() -> None:
    comparison = compare([], baseline_of(summary(case="old")))
    assert comparison.deltas[0].verdict == "missing"
    assert comparison.exit_code == 0


def test_render_names_the_regression() -> None:
    comparison = compare(
        [summary(pass_rate=0.67)], baseline_of(summary()), confirmed=True
    )
    text = render(comparison)
    assert "REGRESSION" in text
    assert "c" in text
