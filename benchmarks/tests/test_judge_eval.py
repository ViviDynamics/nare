"""The pure figures of the judge evaluation: labels, agreement, self-preference."""

from __future__ import annotations

from pathlib import Path

from benchmarks.judge_eval import (
    EXPLOIT_ANSWERS,
    Call,
    agreement,
    designed_answers,
    jev_unknown,
    llm_base,
    loo_label,
    majority,
    percentile,
    self_preference,
)
from benchmarks.runner.case import load_cases

CASES = {c.id: c for c in load_cases(Path(__file__).parents[1] / "cases")}


def _item(case_id: str, kind: str):
    from benchmarks.judge_eval import Item
    from benchmarks.runner.compare import Evidence

    return Item(
        id=f"ref:{case_id}:{kind}",
        group="reference",
        case=CASES[case_id],
        evidence=Evidence("", "", []),
        kind=kind,
    )


def test_solution_answers_every_claim_true() -> None:
    item = _item("edit-docstring", "solution")
    assert designed_answers(item) == {1: True, 2: True}


def test_exploit_answers_only_the_claims_meant_to_catch_it() -> None:
    # The contested edit-docstring claim 1 is deliberately left unscored.
    item = _item("edit-docstring", "exploit")
    assert designed_answers(item) == {2: True}
    assert ("edit-docstring", 1) not in EXPLOIT_ANSWERS
    bootstrap = _item("bootstrap-without-database", "exploit")
    assert designed_answers(bootstrap) == {1: False, 2: False, 3: False}


def test_majority_ties_and_unknowns_are_no_label() -> None:
    assert majority([True, True, False]) is True
    assert majority([True, False]) is None
    assert majority([None, None]) is None
    assert majority([None, True]) is True


def test_agreement_ignores_unanswered_and_unlabelled() -> None:
    result = agreement([(True, True), (False, True), (None, True), (True, None)])
    assert (result.agree, result.answered, result.labelled) == (1, 2, 3)
    assert result.rate == 0.5


def test_loo_label_excludes_the_judge_being_scored() -> None:
    def call(name: str, verdict: bool) -> Call:
        return Call(name, "gen:x", 0, verdicts=[verdict])

    calls = {"a": call("a", True), "b": call("b", False), "c": call("c", True)}
    assert loo_label(calls, "gen:x", 1, excluding="a") is None  # b vs c: tie
    assert loo_label(calls, "gen:x", 1, excluding="b") is True  # a and c agree


def test_self_preference_is_own_minus_other_deviation() -> None:
    # Own diffs: judge says true where the panel says false (+1), twice.
    # Other diffs: no deviation.
    value, own_n = self_preference([(True, 1.0), (True, 1.0), (False, 0.0)])
    assert own_n == 2
    assert value == 1.0


def test_self_preference_needs_both_sides() -> None:
    assert self_preference([(True, 1.0)]) == (None, 1)


def test_percentile_nearest_rank() -> None:
    assert percentile([1, 2, 3, 4, 5], 0.95) == 5
    assert percentile([], 0.95) is None


def test_jev_unknown_band_counts_middling_probabilities() -> None:
    call = Call("noul (Jev)", "x", 0, verdicts=[True, False], p_yes=[0.5, 0.1])
    assert jev_unknown(call) == 1


def test_anthropic_routes_sit_on_the_root_and_openai_under_v1() -> None:
    assert llm_base("anthropic", "https://h/") == "https://h"
    assert llm_base("openai", "https://h/") == "https://h/v1"
    assert llm_base("openai", "https://h/v1") == "https://h/v1"
