"""The Claude Code arm's runner, without Docker or a model: the record round
trip, the gateway paths, the cross-arm arithmetic, and the refusals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest

from benchmarks.runner import claude_arm
from benchmarks.runner.claude_code import COMPARABLE_CASES, EXCLUDED_CASES, ModelPrice
from benchmarks.runner.grade import CheckResult, Outcome
from benchmarks.runner.report import RepRecord
from benchmarks.runner.sandbox import repo_root

PRICE = ModelPrice(input=1e-07, output=5e-07, cache_read=1e-08, cache_write=1.25e-07)


def _rec(
    case: str,
    rep: int,
    outcome: str,
    *,
    cost: float | None = 999.0,
    usage: dict[str, int] | None = None,
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


def test_gateway_root_drops_one_trailing_v1_and_no_more() -> None:
    assert (
        claude_arm.gateway_root("https://llm.vividynamics.com/v1")
        == "https://llm.vividynamics.com"
    )
    assert (
        claude_arm.gateway_root("https://llm.vividynamics.com/")
        == "https://llm.vividynamics.com"
    )


def test_fetch_model_info_returns_the_named_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "data": [
                    {"model_name": "other", "model_info": {}},
                    {
                        "model_name": "claude-haiku-5-5",
                        "model_info": {"input_cost_per_token": 1e-07},
                    },
                ]
            }

    seen: dict[str, str] = {}

    def fake_get(url: str, **kwargs: Any) -> Any:
        seen["url"] = url
        seen["auth"] = kwargs["headers"]["Authorization"]
        return Response()

    monkeypatch.setattr(httpx2, "get", fake_get)
    info = claude_arm.fetch_model_info(
        "https://gw.example/v1", "KEY", "claude-haiku-5-5"
    )
    assert info == {"input_cost_per_token": 1e-07}
    assert seen["url"] == "https://gw.example/v1/model/info"
    assert seen["auth"] == "Bearer KEY"


def test_fetch_model_info_refuses_an_unlisted_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"data": []}

    monkeypatch.setattr(httpx2, "get", lambda *a, **k: Response())
    with pytest.raises(claude_arm.ArmError, match="does not list"):
        claude_arm.fetch_model_info("https://gw.example", "KEY", "claude-haiku-5-5")


def test_records_round_trip_through_a_results_file(tmp_path: Path) -> None:
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


def test_compare_prices_both_arms_from_usage_not_from_each_harness(
    tmp_path: Path,
) -> None:
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


def test_compare_gives_pooled_figures_over_cases_with_both_arms() -> None:
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


def test_a_case_run_by_one_arm_only_is_still_a_row() -> None:
    out = claude_arm.compare_arms([_rec("only-nare", 0, "pass")], [], PRICE)
    assert [r["case"] for r in out["rows"]] == ["only-nare"]
    assert out["rows"][0]["claude"]["n"] == 0
    assert out["mantel_haenszel"] is None


def test_a_case_outside_the_comparable_set_is_refused_before_any_key_is_read(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert claude_arm.main(["--case", "ambiguous-request"]) == 2
    assert "not comparable" in capsys.readouterr().out


def test_list_names_every_case_once(capsys: pytest.CaptureFixture[str]) -> None:
    assert claude_arm.main(["--list"]) == 0
    text = capsys.readouterr().out
    assert text.count("compared  ") == len(COMPARABLE_CASES)
    assert text.count("excluded  ") == len(EXCLUDED_CASES)


OPT_IN_CASES = {
    "invoice-discount-bug",
    "notes-tags-feature",
    "prefs-migration",
    "report-refactor",
}


def test_the_tier_flag_selects_the_comparable_cases_of_that_tier() -> None:
    root = repo_root()
    # No tier means full, the nine cases the arm has always run.
    assert claude_arm.selected_ids(root, None, None) == claude_arm.comparable_ids(
        root, "full"
    )
    assert "edit-docstring" in claude_arm.selected_ids(root, None, None)
    assert not OPT_IN_CASES & set(claude_arm.selected_ids(root, None, None))
    # The opt-in tier (#97) is the arm's first user: only its cases run.
    assert set(claude_arm.selected_ids(root, "extended", None)) == OPT_IN_CASES


def test_a_named_case_may_be_run_without_a_tier_but_not_outside_its_tier() -> None:
    root = repo_root()
    assert claude_arm.selected_ids(root, None, ["notes-tags-feature"]) == [
        "notes-tags-feature"
    ]
    with pytest.raises(ValueError, match="not in tier extended: edit-docstring"):
        claude_arm.selected_ids(root, "extended", ["edit-docstring"])
    with pytest.raises(ValueError, match="not comparable"):
        claude_arm.selected_ids(root, None, ["ambiguous-request"])


def test_a_case_outside_the_named_tier_is_refused_before_any_key_is_read(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert claude_arm.main(["--tier", "extended", "--case", "edit-docstring"]) == 2
    assert "not in tier extended: edit-docstring" in capsys.readouterr().out


def test_tools_seen_reads_each_reps_init_message(tmp_path: Path) -> None:
    rep = tmp_path / "edit-docstring-0"
    rep.mkdir()
    init = {"type": "system", "subtype": "init", "tools": ["Bash", "Read"]}
    (rep / "stream.jsonl").write_text(json.dumps(init) + "\n", encoding="utf-8")
    assert claude_arm._tools_seen(tmp_path) == {"edit-docstring-0": ["Bash", "Read"]}


def test_a_max_tokens_setting_is_refused_before_anything_runs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("NARE_BENCH_MAX_TOKENS", "4096")
    monkeypatch.setenv("NARE_BASE_URL", "https://gw.example")
    assert claude_arm.main(["--case", "edit-docstring", "--judge-model", "j"]) == 2
    assert "NARE_BENCH_MAX_TOKENS" in capsys.readouterr().out
