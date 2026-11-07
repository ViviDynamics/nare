from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import replace
from pathlib import Path

import pytest

from benchmarks.runner.__main__ import case_changed
from benchmarks.runner.case import load_case
from benchmarks.runner.grade import Outcome, parse_stdout
from benchmarks.runner.judge import JudgeAttempt, JudgeError
from benchmarks.runner.provenance import (
    case_hash,
    judge_cause,
    session_tool_calls,
    tool_calls_by_tool,
)
from benchmarks.runner.report import (
    Baseline,
    BaselineMeta,
    CaseSummary,
    RepRecord,
    changed_cases,
    compare,
    dumps_baseline,
    load_baseline,
    render,
    summarize,
    token_z,
)

META = BaselineMeta(
    blessed="2026-11-06T00:00:00+00:00",
    commit="abc1234",
    provider="anthropic",
    model="m",
    judge_model="j",
)


def rec(
    rep: int = 0,
    outcome: Outcome = "pass",
    tokens: int = 1000,
    cost: float | None = 0.01,
    estimated: bool = False,
    case_hash_value: str = "sha256:aa",
    error_cause: str | None = None,
) -> RepRecord:
    return RepRecord(
        case="c",
        rep=rep,
        outcome=outcome,
        checks=(),
        judge_met=None,
        judge_why="",
        status="done",
        stop_reason="end_turn",
        turns=3,
        usage={"input": tokens, "output": 0, "cache_read": 0, "cache_write": 0},
        cost=cost,
        cost_estimated=estimated,
        case_hash=case_hash_value,
        error_cause=error_cause,
    )


def summary(*records: RepRecord) -> CaseSummary:
    (found,) = summarize(list(records))
    return found


# grade: the cost nare reports


def test_the_result_line_carries_its_cost_and_whether_it_was_estimated() -> None:
    line = json.dumps(
        {
            "type": "result",
            "status": "done",
            "usage": {"input": 1, "cost": 0.25, "estimated": True},
        }
    )
    parsed = parse_stdout(line)
    assert parsed is not None
    assert (parsed.cost, parsed.cost_estimated) == (0.25, True)


def test_a_null_or_absent_cost_is_unknown_not_zero() -> None:
    for usage in ({"input": 1, "cost": None}, {"input": 1}):
        line = json.dumps({"type": "result", "status": "done", "usage": usage})
        parsed = parse_stdout(line)
        assert parsed is not None
        assert parsed.cost is None


def test_a_boolean_cost_is_not_a_cost() -> None:
    line = json.dumps({"type": "result", "status": "done", "usage": {"cost": True}})
    parsed = parse_stdout(line)
    assert parsed is not None
    assert parsed.cost is None


# summaries: dollars, token range and spread


def test_a_case_sums_its_known_costs_and_counts_the_unknown() -> None:
    found = summary(
        rec(rep=0, cost=0.02),
        rec(rep=1, cost=None),
        rec(rep=2, cost=0.04, estimated=True),
    )
    assert found.cost == pytest.approx(0.06)
    assert found.cost_unknown == 1
    assert found.cost_estimated is True


def test_a_case_with_no_reported_cost_has_no_dollars() -> None:
    found = summary(rec(rep=0, cost=None), rec(rep=1, cost=None))
    assert found.cost is None
    assert found.cost_unknown == 2


def test_tokens_have_a_range_and_a_log_spread_once_there_are_two_reps() -> None:
    found = summary(rec(rep=0, tokens=900), rec(rep=1, tokens=1100))
    assert (found.tokens_min, found.tokens_max) == (900, 1100)
    assert found.tokens_log_sd == pytest.approx(
        statistics.stdev([math.log(900), math.log(1100)])
    )


def test_one_rep_has_no_spread() -> None:
    assert summary(rec(rep=0)).tokens_log_sd is None


def test_the_case_hash_comes_from_its_records() -> None:
    assert summary(rec(case_hash_value="sha256:bb")).case_hash == "sha256:bb"


# render: dollars per rep and per solved task


def test_render_shows_dollars_per_solved_task_and_marks_estimates() -> None:
    comparison = compare(
        [
            summarize(
                [
                    rec(rep=0, cost=0.5, estimated=True),
                    rec(rep=1, cost=0.5, estimated=True),
                ]
            )[0]
        ],
        Baseline(meta=META, cases={}),
    )
    text = render(comparison)
    assert "~$0.5000" in text  # $1.00 over two passes
    assert "~ dollars estimated" in text


def test_render_marks_reps_with_no_cost() -> None:
    comparison = compare(
        [summarize([rec(rep=0, cost=0.5), rec(rep=1, cost=None)])[0]],
        Baseline(meta=META, cases={}),
    )
    text = render(comparison)
    assert "$0.2500*" in text
    assert "some reps reported no cost" in text


# the token test


def test_a_significant_token_rise_is_detected() -> None:
    now = replace(
        summary(rec(rep=0, tokens=1150), rec(rep=1, tokens=1150)), tokens_log_sd=0.02
    )
    then = replace(
        summary(rec(rep=0, tokens=1000), rec(rep=1, tokens=1000)), tokens_log_sd=0.02
    )
    z = token_z(now, then)
    assert z is not None and z > 1.6449


def test_noise_within_the_spread_is_not_a_token_rise() -> None:
    # A 5% rise over reps that vary by 30% is nothing the test can see.
    now = replace(summary(rec(rep=0), rec(rep=1)), tokens_log_sd=0.3)
    then = replace(summary(rec(rep=0), rec(rep=1)), tokens_log_sd=0.3)
    now = replace(now, tokens_median=1050)
    z = token_z(now, then)
    assert z is not None and z < 1.6449


def test_without_a_spread_on_both_sides_nothing_is_tested() -> None:
    now = replace(summary(rec(rep=0)), tokens_median=5000)
    then = replace(summary(rec(rep=0)), tokens_median=1000)
    assert token_z(now, then) is None


def test_the_token_test_is_one_sided_and_never_fails_a_drop() -> None:
    now = replace(
        summary(rec(rep=0), rec(rep=1)), tokens_median=500, tokens_log_sd=0.05
    )
    then = replace(
        summary(rec(rep=0), rec(rep=1)), tokens_median=1000, tokens_log_sd=0.05
    )
    comparison = compare(
        [now], Baseline(meta=META, cases={"c": then}), strict_tokens=True
    )
    assert comparison.exit_code == 0


# baselines: the hash and the spread are stored and read back


def test_a_baseline_stores_the_case_hash_and_spread(tmp_path: Path) -> None:
    found = replace(summary(rec(rep=0, tokens=900), rec(rep=1, tokens=1100)))
    path = tmp_path / "b.toml"
    path.write_text(dumps_baseline(META, [found]))
    loaded = load_baseline(path).cases["c"]
    assert loaded.case_hash == "sha256:aa"
    assert loaded.tokens_log_sd == pytest.approx(found.tokens_log_sd)


def test_a_baseline_from_before_this_loads_without_them(tmp_path: Path) -> None:
    path = tmp_path / "b.toml"
    path.write_text(
        '[meta]\nblessed = "t"\ncommit = "c"\nprovider = "p"\n'
        'model = "m"\njudge_model = "j"\n\n[case.c]\npasses = 3\nreps = 3\n'
    )
    loaded = load_baseline(path).cases["c"]
    assert loaded.case_hash == "" and loaded.tokens_log_sd is None


# refusals: a changed case is not the case the baseline measured


def baseline_with_hash(digest: str) -> Baseline:
    return Baseline(
        meta=META,
        cases={"c": replace(summary(rec(case_hash_value=digest)), case_hash=digest)},
    )


def test_a_changed_case_is_named() -> None:
    baseline = baseline_with_hash("sha256:old")
    assert changed_cases({"c": "sha256:new"}, baseline) == ["c"]


def test_an_unchanged_or_unhashed_case_is_not_named() -> None:
    assert changed_cases({"c": "sha256:old"}, baseline_with_hash("sha256:old")) == []
    assert changed_cases({"c": ""}, baseline_with_hash("sha256:old")) == []
    assert changed_cases({"c": "sha256:new"}, baseline_with_hash("")) == []


def test_run_refuses_a_changed_case_unless_told_to() -> None:
    baseline = baseline_with_hash("sha256:old")
    refuse = argparse.Namespace(allow_case_change=False)
    allow = argparse.Namespace(allow_case_change=True)
    assert case_changed(baseline, {"c": "sha256:new"}, refuse) is True
    assert case_changed(baseline, {"c": "sha256:new"}, allow) is False


# provenance: the case hash and the tool calls


def make_case(root: Path) -> Path:
    body = (
        'id = "c"\ntier = "smoke"\nprompt = "x"\n\n'
        '[[check]]\nkind = "result"\nstatus = "done"\n'
    )
    directory = root / "c"
    (directory / "fixture").mkdir(parents=True)
    (directory / "case.toml").write_text(body)
    (directory / "fixture" / "a.py").write_text("x = 1\n")
    return directory


def test_the_case_hash_changes_with_the_task_or_its_fixture(tmp_path: Path) -> None:
    directory = make_case(tmp_path)
    first = case_hash(load_case(directory))
    assert first.startswith("sha256:") and first == case_hash(load_case(directory))
    (directory / "fixture" / "a.py").write_text("x = 2\n")
    assert case_hash(load_case(directory)) != first


def test_the_case_hash_is_empty_when_the_files_are_not_on_disk(tmp_path: Path) -> None:
    case = load_case(make_case(tmp_path))
    gone = replace(case, directory=tmp_path / "missing")
    assert case_hash(gone) == ""


def test_tool_calls_are_counted_by_tool() -> None:
    session = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "go"}]},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "1", "name": "read"},
                    {"type": "tool_use", "id": "2", "name": "bash"},
                ],
            },
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "3", "name": "read"}],
            },
        ]
    }
    assert tool_calls_by_tool(session) == {"bash": 1, "read": 2}


def test_a_rep_without_a_session_file_has_no_tool_calls(tmp_path: Path) -> None:
    assert session_tool_calls(tmp_path) is None


# typed error causes


def test_a_judge_transport_failure_is_an_http_error() -> None:
    exc = JudgeError("429", [JudgeAttempt("transport error", "429")])
    assert judge_cause(exc) == "judge_http_error"


def test_an_empty_judge_reply_is_typed_empty() -> None:
    exc = JudgeError("no JSON", [JudgeAttempt("end_turn", "   ")])
    assert judge_cause(exc) == "judge_empty"


def test_a_garbled_judge_reply_is_typed_unparsed() -> None:
    exc = JudgeError("no JSON", [JudgeAttempt("end_turn", "I think yes")])
    assert judge_cause(exc) == "judge_unparsed"


def test_a_judge_that_never_ran_is_typed_unanswered() -> None:
    assert judge_cause(JudgeError("no judge model is configured")) == "judge_unanswered"
