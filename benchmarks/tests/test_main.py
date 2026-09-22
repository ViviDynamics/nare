from __future__ import annotations

import json
from pathlib import Path

from benchmarks.runner.__main__ import (
    CONFIRM_REPS,
    build_parser,
    main,
    verify,
    write_results,
)
from benchmarks.runner.case import Case, Check
from benchmarks.runner.report import RepRecord


def case_with(*checks: Check, case_id: str = "demo") -> Case:
    return Case(
        id=case_id,
        tier="smoke",
        prompt="do a thing",
        directory=Path("/nowhere"),
        max_turns=4,
        reps=3,
        timeout=60,
        checks=checks,
        judge=None,
    )


def test_parser_offers_the_four_subcommands() -> None:
    parser = build_parser()
    for command in ("run", "compare", "bless", "verify"):
        assert parser.parse_args([command]).command == command


def test_run_accepts_the_configuration_flags() -> None:
    args = build_parser().parse_args(
        ["run", "--tier", "smoke", "--model", "claude-haiku", "--strict-tokens"]
    )
    assert args.tier == "smoke"
    assert args.model == "claude-haiku"
    assert args.strict_tokens


def test_verify_passes_when_a_check_fails_on_the_pristine_fixture() -> None:
    case = case_with(Check(kind="bash", cmd="test -f done.txt"))
    text, code = verify([case], run_check=lambda w, c, t: (1, ""))
    assert code == 0
    assert "demo" in text


def test_verify_fails_a_case_whose_checks_already_pass() -> None:
    """The worst benchmark bug: a check that is green before the agent runs."""
    case = case_with(Check(kind="bash", cmd="true"))
    text, code = verify([case], run_check=lambda w, c, t: (0, ""))
    assert code == 1
    assert "already passes" in text


def test_verify_exempts_a_case_with_only_result_checks() -> None:
    """verify never runs the agent, so there is no result line to check."""
    case = case_with(Check(kind="result", status="blocked"))
    text, code = verify([case], run_check=lambda w, c, t: (0, ""))
    assert code == 0
    assert "exempt" in text


def test_verify_needs_only_one_failing_check_of_several() -> None:
    case = case_with(
        Check(kind="bash", cmd="true"),
        Check(kind="bash", cmd="test -f done.txt"),
    )
    codes = iter([0, 1])
    _, code = verify([case], run_check=lambda w, c, t: (next(codes), ""))
    assert code == 0


def test_main_without_a_subcommand_exits_two() -> None:
    assert main([]) == 2


def test_results_are_written_as_one_json_line_per_rep(tmp_path: Path) -> None:
    records = [
        RepRecord(
            case="demo",
            rep=0,
            outcome="pass",
            checks=(),
            judge_met=2,
            judge_why="fine",
            status="done",
            stop_reason="end_turn",
            turns=4,
            usage={"input": 10, "output": 5, "cache_read": 0, "cache_write": 0},
            duration_s=1.5,
            model="claude-haiku",
        )
    ]
    path = write_results(tmp_path, records)
    assert path.parent == tmp_path / "benchmarks" / "results"
    payload = json.loads(path.read_text().splitlines()[0])
    assert payload["case"] == "demo"
    assert payload["outcome"] == "pass"
    assert payload["usage"]["input"] == 10
    assert payload["model"] == "claude-haiku"


def test_results_are_gitignored(tmp_path: Path) -> None:
    from benchmarks.runner.sandbox import repo_root

    assert "benchmarks/results/" in (repo_root() / ".gitignore").read_text()


def test_confirmation_uses_seven_reps() -> None:
    assert CONFIRM_REPS == 7
