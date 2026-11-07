"""The Claude Code arm's runner, without Docker or a model: the record round
trip, the gateway paths, the cross-arm arithmetic, and the refusals."""

from __future__ import annotations

from typing import cast

import pytest

from benchmarks.runner import claude_arm
from benchmarks.runner.claude_code import ModelPrice
from benchmarks.runner.grade import CheckResult, Outcome
from benchmarks.runner.report import RepRecord

PRICE = ModelPrice(input=1e-07, output=5e-07, cache_read=1e-08, cache_write=1.25e-07)


def _rec(
    case: str, rep: int, outcome: str, *, cost: float | None = 999.0, usage=None
) -> RepRecord:
    return RepRecord(
        case=case,
        rep=rep,
        outcome=cast(Outcome, outcome),
        checks=(CheckResult(label="x", passed=outcome == "pass", detail=""),),
        judge_met=None,
        judge_why="",
        status="done",
        stop_reason="end_turn",
        turns=2,
        usage=usage
        or {"input": 100, "output": 10, "cache_read": 1000, "cache_write": 0},
        duration_s=5.0,
        cost=cost,
        cost_estimated=True,
    )


def test_gateway_root_drops_one_trailing_v1_and_no_more():
    assert (
        claude_arm.gateway_root("https://llm.vividynamics.com/v1")
        == "https://llm.vividynamics.com"
    )
    assert (
        claude_arm.gateway_root("https://llm.vividynamics.com/")
        == "https://llm.vividynamics.com"
    )


def test_fetch_model_info_returns_the_named_entry(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"model_name": "other", "model_info": {}},
                    {
                        "model_name": "claude-haiku-5-5",
                        "model_info": {"input_cost_per_token": 1e-07},
                    },
                ]
            }

    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["auth"] = kwargs["headers"]["Authorization"]
        return Response()

    monkeypatch.setattr(claude_arm.httpx2, "get", fake_get)
    info = claude_arm.fetch_model_info(
        "https://gw.example/v1", "KEY", "claude-haiku-5-5"
    )
    assert info == {"input_cost_per_token": 1e-07}
    assert seen["url"] == "https://gw.example/v1/model/info"
    assert seen["auth"] == "Bearer KEY"


def test_fetch_model_info_refuses_an_unlisted_model(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": []}

    monkeypatch.setattr(claude_arm.httpx2, "get", lambda *a, **k: Response())
    with pytest.raises(claude_arm.ArmError, match="does not list"):
        claude_arm.fetch_model_info("https://gw.example", "KEY", "claude-haiku-5-5")


def test_records_round_trip_through_a_results_file(tmp_path):
    records = [_rec("edit-docstring", 0, "pass"), _rec("edit-docstring", 1, "fail")]
    path = tmp_path / "results.jsonl"
    claude_arm._write_records(path, records)
    back = claude_arm.read_records(path)
    assert [(r.case, r.rep, r.outcome) for r in back] == [
        ("edit-docstring", 0, "pass"),
        ("edit-docstring", 1, "fail"),
    ]
    assert back[0].checks[0].label == "x"
    assert back[0].usage == records[0].usage


def test_compare_prices_both_arms_from_usage_not_from_each_harness(tmp_path):
    nare = [_rec("c", 0, "pass", cost=50.0), _rec("c", 1, "fail", cost=50.0)]
    claude = [_rec("c", 0, "pass", cost=0.0001), _rec("c", 1, "pass", cost=0.0001)]
    out = claude_arm.compare_arms(nare, claude, PRICE)
    row = out["rows"][0]
    per_rep = 100 * 1e-07 + 10 * 5e-07 + 1000 * 1e-08
    assert row["nare"]["dollars_total"] == pytest.approx(2 * per_rep)
    assert row["claude"]["dollars_total"] == pytest.approx(2 * per_rep)
    assert row["nare"]["passes"] == 1 and row["nare"]["n"] == 2
    assert row["claude"]["passes"] == 2
    assert row["claude"]["dollars_per_solved"] == pytest.approx(per_rep)


def test_compare_gives_pooled_figures_over_cases_with_both_arms():
    nare = [_rec("a", i, "pass") for i in range(3)] + [
        _rec("b", i, "fail") for i in range(3)
    ]
    claude = [_rec("a", i, "pass") for i in range(3)] + [
        _rec("b", i, "pass") for i in range(3)
    ]
    out = claude_arm.compare_arms(nare, claude, PRICE)
    assert out["mantel_haenszel"] is not None
    assert out["mantel_haenszel"]["strata"] == 2
    assert out["nare_over_claude_tokens_geomean"] is not None


def test_a_case_run_by_one_arm_only_is_still_a_row():
    out = claude_arm.compare_arms([_rec("only-nare", 0, "pass")], [], PRICE)
    assert [r["case"] for r in out["rows"]] == ["only-nare"]
    assert out["rows"][0]["claude"]["n"] == 0
    assert out["mantel_haenszel"] is None


def test_a_case_outside_the_comparable_set_is_refused_before_any_key_is_read(capsys):
    assert claude_arm.main(["--case", "ambiguous-request"]) == 2
    assert "not comparable" in capsys.readouterr().out


def test_list_names_every_case_once(capsys):
    assert claude_arm.main(["--list"]) == 0
    text = capsys.readouterr().out
    assert text.count("compared  ") == 9
    assert text.count("excluded  ") == 8
