from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import pytest

from benchmarks.runner.__main__ import (
    AB_HIGH,
    AB_LOW,
    ab_stratum,
    informative,
    main,
)
from benchmarks.runner.ab import (
    CaseAB,
    dollars_per_rep_pairs,
    mantel_haenszel,
    paired_ratio,
    pass_rate_effect,
    power,
    render,
    token_pairs,
)
from benchmarks.runner.grade import Outcome
from benchmarks.runner.report import RepRecord


def stratum(case: str, hp: int, bp: int, n: int = 10) -> CaseAB:
    return CaseAB(case=case, head_pass=hp, head_n=n, base_pass=bp, base_n=n)


def test_a_hand_computed_mantel_haenszel_odds_ratio() -> None:
    # Strata (head pass, head fail, base pass, base fail): (9,1,5,5), (8,2,4,6),
    # (7,3,3,7). MH = sum(ad/n) / sum(bc/n) = 7.10 / 1.10.
    strata = [stratum("a", 9, 5), stratum("b", 8, 4), stratum("c", 7, 3)]
    result = mantel_haenszel(strata)
    assert result.odds_ratio == pytest.approx(7.1 / 1.1)
    assert result.strata == 3
    assert result.low is not None and result.high is not None
    assert result.odds_ratio is not None
    assert result.low < result.odds_ratio < result.high


def test_no_difference_has_an_odds_ratio_of_one_and_p_of_one() -> None:
    strata = [stratum("a", 5, 5), stratum("b", 3, 3, n=6)]
    result = mantel_haenszel(strata)
    assert result.odds_ratio == pytest.approx(1.0)
    assert result.p_value == pytest.approx(1.0)
    assert result.low is not None and result.high is not None
    assert result.low < 1.0 < result.high


def test_a_consistent_difference_is_significant_and_its_interval_excludes_one() -> None:
    strata = [stratum(f"c{i}", 9, 4) for i in range(6)]
    result = mantel_haenszel(strata)
    assert result.p_value is not None and result.p_value < 0.01
    assert result.low is not None and result.low > 1.0


def test_a_difference_in_one_case_alone_is_not_pooled_into_significance() -> None:
    strata = [stratum("a", 10, 0)] + [stratum(f"c{i}", 5, 5) for i in range(5)]
    assert (mantel_haenszel(strata).p_value or 0) > 0.05


def test_undefined_strata_do_not_crash_the_test() -> None:
    result = mantel_haenszel([stratum("a", 10, 10)])
    assert result.odds_ratio is None or result.odds_ratio > 0
    assert mantel_haenszel([]).odds_ratio is None


def test_the_pass_rate_effect_averages_cases_equally_with_a_seeded_interval() -> None:
    strata = [stratum("a", 10, 5), stratum("b", 5, 5)]  # +50 and 0 points
    effect = pass_rate_effect(strata, seed=1, resamples=500)
    assert effect is not None
    mean, low, high = effect
    assert mean == pytest.approx(0.25)
    assert low <= mean <= high
    assert pass_rate_effect(strata, seed=1, resamples=500) == effect


def test_paired_ratio_is_a_geometric_mean_across_cases() -> None:
    ratio = paired_ratio([(200.0, 100.0), (50.0, 100.0)], seed=1, resamples=500)
    assert ratio is not None
    value, low, high = ratio
    assert value == pytest.approx(1.0)  # sqrt(2 * 0.5)
    assert low <= value <= high


def test_tokens_and_dollars_pair_only_where_both_arms_reported() -> None:
    strata = [
        replace(stratum("a", 5, 5), head_tokens=200, base_tokens=100),
        replace(stratum("b", 5, 5), head_tokens=None, base_tokens=100),
        replace(
            stratum("c", 5, 5),
            head_cost=1.0,
            base_cost=2.0,
            head_cost_reps=10,
            base_cost_reps=10,
        ),
        replace(stratum("d", 5, 5), head_cost=1.0, base_cost=None),
    ]
    assert token_pairs(strata) == [(200.0, 100.0)]
    assert dollars_per_rep_pairs(strata) == [(0.1, 0.2)]


def test_power_rises_with_reps_and_is_about_alpha_under_no_effect() -> None:
    rates = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    assert power(10, rates, 0.2, trials=1500) > power(5, rates, 0.2, trials=1500)
    assert power(10, rates, 0.0, trials=3000) < 0.10


def test_the_power_table_reproduces_the_issue_figures() -> None:
    # #153: six cells at mid-range rates, a uniform 0.2 drop: 0.51, 0.78, 0.90
    # at 5, 10 and 15 reps per arm.
    rates = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    assert power(5, rates, 0.2, trials=4000) == pytest.approx(0.51, abs=0.04)
    assert power(10, rates, 0.2, trials=4000) == pytest.approx(0.78, abs=0.04)
    assert power(15, rates, 0.2, trials=4000) == pytest.approx(0.90, abs=0.04)


def rec(
    case: str, outcome: Outcome, tokens: int = 100, cost: float | None = 0.1
) -> RepRecord:
    return RepRecord(
        case=case,
        rep=0,
        outcome=outcome,
        checks=(),
        judge_met=None,
        judge_why="",
        status="done",
        stop_reason="end_turn",
        turns=2,
        usage={"input": tokens, "output": 0, "cache_read": 0, "cache_write": 0},
        cost=cost,
    )


def test_an_arm_mostly_errors_drops_its_case() -> None:
    base = [rec("c", "pass"), rec("c", "error"), rec("c", "error")]
    head = [rec("c", "pass"), rec("c", "pass"), rec("c", "fail")]
    assert ab_stratum("c", base, head) is None


def test_a_stratum_counts_decided_reps_and_cost_reps() -> None:
    base = [rec("c", "pass"), rec("c", "fail", cost=None)]
    head = [rec("c", "pass"), rec("c", "pass")]
    found = ab_stratum("c", base, head)
    assert found is not None
    assert (found.base_pass, found.base_n) == (1, 2)
    assert (found.head_pass, found.head_n) == (2, 2)
    assert found.base_cost_reps == 1 and found.head_cost_reps == 2


def test_informative_cases_are_those_blessed_between_the_bounds(tmp_path: Path) -> None:
    from benchmarks.runner.case import load_cases
    from benchmarks.runner.report import (
        BaselineMeta,
        CaseSummary,
        baseline_path,
        dumps_baseline,
    )
    from benchmarks.runner.sandbox import repo_root

    cases = load_cases(repo_root() / "benchmarks" / "cases", tier="smoke")
    assert cases, "the smoke tier has cases"
    first, last = cases[0].id, cases[-1].id

    def summary(case: str, passes: int) -> CaseSummary:
        return CaseSummary(
            case=case,
            passes=passes,
            fails=3 - passes,
            errors=0,
            pass_rate=passes / 3,
            inconclusive=False,
            tokens_median=None,
            turns_median=None,
            judge_met_median=None,
        )

    meta = BaselineMeta("t", "c", "p", "m", "j")
    baselines = tmp_path / "benchmarks" / "baselines"
    baselines.mkdir(parents=True)
    baseline_path(baselines, "m", "smoke").write_text(
        dumps_baseline(meta, [summary(first, 2), summary(last, 3)])
    )
    chosen, how = informative(tmp_path, "smoke", "m", cases, set(), all_cases=False)
    ids = {c.id for c in chosen}
    assert first in ids  # 2/3 is between the bounds
    assert last not in ids  # 3/3 is a guard
    assert "between" in how and str(AB_LOW) in how and str(AB_HIGH) in how
    everything, _ = informative(tmp_path, "smoke", "m", cases, set(), all_cases=True)
    assert len(everything) == len(cases)


def test_the_render_names_counts_and_the_pooled_figures() -> None:
    strata = [stratum("a", 9, 4), stratum("b", 8, 4), stratum("c", 7, 3)]
    text = render(
        base_label="x (aaa)",
        head_label="y (bbb)",
        reps=10,
        how="test",
        strata=strata,
        skipped=["d"],
        mantel=mantel_haenszel(strata),
        effect=pass_rate_effect(strata, resamples=200),
        tokens=None,
        dollars=None,
    )
    assert "9/10" in text and "skipped, an arm was mostly errors: d" in text
    assert "Mantel-Haenszel odds ratio" in text
    assert "two-sided p" in text


def test_an_unknown_ref_is_refused_before_any_build(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("NARE_MODEL", "m")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("NARE_BENCH_JUDGE_MODEL", "j")
    code = main(["ab", "--base", "no-such-ref-for-bench-ab", "--head", "HEAD"])
    assert code == 2
    assert "not a commit" in capsys.readouterr().err


def test_ab_takes_its_reps_from_the_flag() -> None:
    from benchmarks.runner.__main__ import AB_REPS, build_parser

    args = build_parser().parse_args(["ab", "--base", "HEAD~1", "--head", "HEAD"])
    assert args.reps == AB_REPS == 10
    assert args.all_cases is False
    assert isinstance(args, argparse.Namespace)
