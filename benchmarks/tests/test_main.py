from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import cast

import pytest

from benchmarks.runner.__main__ import (
    CONFIRM_REPS,
    build_parser,
    latest_results,
    line_text,
    main,
    merge_baseline,
    read_results,
    run_rep,
    verify,
    write_results,
)
from benchmarks.runner.case import Case, Check, Judge
from benchmarks.runner.config import Config
from benchmarks.runner.report import (
    BaselineMeta,
    CaseSummary,
    RepRecord,
    dumps_baseline,
    summarize,
)
from benchmarks.runner.sandbox import RunArtifacts
from benchmarks.tests.test_judge import FakeJudge
from nare.transport import Transport


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


def test_verify_errors_when_a_check_could_not_run() -> None:
    """A timed-out or broken check never measured the fixture. Not an `ok`."""
    case = case_with(Check(kind="bash", cmd="test -f done.txt"))
    text, code = verify([case], run_check=lambda w, c, t: (124, ""))
    assert code == 1
    assert "ERROR" in text


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


def test_results_round_trip(tmp_path: Path) -> None:
    records = [
        RepRecord(
            case="demo",
            rep=0,
            outcome="pass",
            checks=(),
            judge_met=2,
            judge_why="",
            status="done",
            stop_reason="end_turn",
            turns=4,
            usage={"input": 10, "output": 5, "cache_read": 0, "cache_write": 0},
            duration_s=1.0,
            model="claude-haiku",
        )
    ]
    path = write_results(tmp_path, records)
    assert [r.case for r in read_results(path)] == ["demo"]
    assert read_results(path)[0].outcome == "pass"


def test_latest_results_picks_the_newest(tmp_path: Path) -> None:
    directory = tmp_path / "benchmarks" / "results"
    directory.mkdir(parents=True)
    (directory / "2026-09-20T00-00-00Z.jsonl").write_text("")
    (directory / "2026-09-22T00-00-00Z.jsonl").write_text("")
    newest = latest_results(tmp_path)
    assert newest is not None
    assert newest.name == "2026-09-22T00-00-00Z.jsonl"


def test_latest_results_is_none_when_nothing_has_run(tmp_path: Path) -> None:
    assert latest_results(tmp_path) is None


def test_blessing_writes_a_baseline_keyed_by_model(tmp_path: Path) -> None:
    from benchmarks.runner.report import baseline_path, load_baseline

    records = [
        RepRecord(
            case="demo",
            rep=n,
            outcome="pass",
            checks=(),
            judge_met=2,
            judge_why="",
            status="done",
            stop_reason="end_turn",
            turns=4,
            usage={"input": 1000, "output": 0, "cache_read": 0, "cache_write": 0},
            duration_s=1.0,
            model="ada/qwen3-14b",
        )
        for n in range(3)
    ]
    baselines = tmp_path / "baselines"
    baselines.mkdir()
    path = baseline_path(baselines, "ada/qwen3-14b", "smoke")
    path.write_text(
        dumps_baseline(
            BaselineMeta(
                "2026-09-22T00:00:00Z",
                "abc1234",
                "anthropic",
                "ada/qwen3-14b",
                "ada/qwen3-14b",
            ),
            summarize(records),
        )
    )
    assert path.name == "ada-qwen3-14b.smoke.toml"
    assert load_baseline(path).cases["demo"].pass_rate == 1.0


def summary(case: str, rate: float | None) -> CaseSummary:
    return CaseSummary(case, 3, 0, 0, rate, rate is None, 100, 4, None)


def test_bless_merges_into_the_baseline_instead_of_replacing_it() -> None:
    kept = {
        "a": summary("a", 1.0),
        "b": summary("b", 1.0),
        "gone": summary("gone", 1.0),
    }
    fresh = [summary("a", 0.5), summary("b", None)]
    merged = merge_baseline(kept, fresh, {"a", "b"})
    assert [(s.case, s.pass_rate) for s in merged] == [("a", 0.5), ("b", 1.0)]


def test_results_from_another_model_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import benchmarks.runner.__main__ as bench
    from benchmarks.runner.sandbox import repo_root

    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "cases").symlink_to(repo_root() / "benchmarks" / "cases")
    write_results(tmp_path, [record("edit-docstring", model="other-model")])
    monkeypatch.setattr(bench, "repo_root", lambda: tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert main(["bless", "--model", "ada/qwen3-14b"]) == 2
    assert not (tmp_path / "benchmarks" / "baselines").exists()


def test_bless_needs_no_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It reads local files and calls no model, so it must not demand a key."""
    import benchmarks.runner.__main__ as bench
    from benchmarks.runner.sandbox import repo_root

    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "cases").symlink_to(repo_root() / "benchmarks" / "cases")
    write_results(tmp_path, [record("edit-docstring", model="ada/qwen3-14b")])
    monkeypatch.setattr(bench, "repo_root", lambda: tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main(["bless", "--model", "ada/qwen3-14b"]) == 0


async def test_a_transport_failure_in_the_agent_is_an_error_not_a_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import benchmarks.runner.__main__ as bench

    (tmp_path / "fixture").mkdir()
    line = json.dumps(
        {
            "type": "result",
            "status": "error",
            "stop_reason": None,
            "turns": 1,
            "usage": {},
            "error": "RateLimitError: 429",
        }
    )
    monkeypatch.setattr(
        bench, "run_agent", lambda *a: RunArtifacts(line, "", 1, False, 1.0)
    )
    case = replace(case_with(Check(kind="bash", cmd="true")), directory=tmp_path)
    config = Config("m", "anthropic", None, "m", "k", "ANTHROPIC_API_KEY")
    rec = await run_rep(case, config, cast(Transport, None), 0, tmp_path / "r" / "s")
    assert rec.outcome == "error"
    assert rec.judge_why == "RateLimitError: 429"


def record(case: str, *, model: str) -> RepRecord:
    return RepRecord(
        case=case,
        rep=0,
        outcome="pass",
        checks=(),
        judge_met=None,
        judge_why="",
        status="done",
        stop_reason="end_turn",
        turns=4,
        usage={"input": 10, "output": 5, "cache_read": 0, "cache_write": 0},
        duration_s=1.0,
        model=model,
    )


OUTPUT = json.dumps({"type": "output", "text": "Done: raised it to 60."})


def test_the_closing_message_is_the_output_event() -> None:
    assert line_text(f"{OUTPUT}\n") == "Done: raised it to 60."


def test_the_closing_message_carries_a_blocked_runs_questions() -> None:
    """nare emits no output event on a blocked run, only the questions."""
    text = line_text("", ["Raise TIMEOUT from 30 to what?", "Which file?"])
    assert text == (
        "The agent stopped to ask:\n- Raise TIMEOUT from 30 to what?\n- Which file?"
    )


def test_questions_follow_the_output_when_there_is_both() -> None:
    text = line_text(f"{OUTPUT}\n", ["Which file?"])
    assert text == (
        "Done: raised it to 60.\n\nThe agent stopped to ask:\n- Which file?"
    )


DONE_LINE = json.dumps(
    {
        "type": "result",
        "status": "done",
        "stop_reason": "end_turn",
        "turns": 1,
        "usage": {},
        "error": None,
        "questions": [],
    }
)
CONFIG = Config("m", "anthropic", None, "m", "k", "ANTHROPIC_API_KEY")


def sandboxed(tmp_path: Path, judge: Judge | None = None) -> Case:
    (tmp_path / "fixture").mkdir(exist_ok=True)
    return replace(
        case_with(Check(kind="bash", cmd="true")), directory=tmp_path, judge=judge
    )


def fake_sandbox(monkeypatch: pytest.MonkeyPatch, result: RunArtifacts) -> None:
    import benchmarks.runner.__main__ as bench

    def agent(
        case: Case, workdir: Path, artifacts: Path, config: Config
    ) -> RunArtifacts:
        if not result.timed_out:
            (artifacts / "session.json").write_text('{"id": "s1"}')
        return result

    monkeypatch.setattr(bench, "run_agent", agent)
    monkeypatch.setattr(bench, "run_check", lambda w, c, t: (0, ""))
    monkeypatch.setattr(bench, "git_diff", lambda w: "a diff")


async def test_a_rep_keeps_its_session_output_and_judge_replies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_sandbox(monkeypatch, RunArtifacts(DONE_LINE, "a warning", 0, False, 1.0))
    case = sandboxed(tmp_path, Judge(("x",), 1))
    evidence = tmp_path / "results" / "stamp"
    judge = FakeJudge('{"met": [true], "why": "ok"}')
    rec = await run_rep(case, CONFIG, judge, 0, evidence)
    kept = evidence / "demo-0"
    assert rec.outcome == "pass"
    assert rec.artifacts == "stamp/demo-0"
    assert json.loads((kept / "session.json").read_text()) == {"id": "s1"}
    assert (kept / "stdout.jsonl").read_text() == DONE_LINE
    assert (kept / "stderr.txt").read_text() == "a warning"
    assert "stop_reason=end_turn" in (kept / "judge.txt").read_text()


async def test_a_judge_that_fails_twice_still_leaves_both_replies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_sandbox(monkeypatch, RunArtifacts(DONE_LINE, "", 0, False, 1.0))
    case = sandboxed(tmp_path, Judge(("x",), 1))
    evidence = tmp_path / "results" / "stamp"
    rec = await run_rep(case, CONFIG, FakeJudge("", "not json"), 0, evidence)
    judged = (evidence / "demo-0" / "judge.txt").read_text()
    assert rec.outcome == "error"
    assert "attempt 1" in judged
    assert "attempt 2: stop_reason=end_turn\nnot json" in judged


async def test_a_timed_out_rep_still_keeps_its_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No result line and no session: the rep section 2 exists to explain."""
    fake_sandbox(
        monkeypatch, RunArtifacts('{"type": "prog', "killed", 124, True, 600.0)
    )
    evidence = tmp_path / "results" / "stamp"
    rec = await run_rep(sandboxed(tmp_path), CONFIG, cast(Transport, None), 0, evidence)
    kept = evidence / "demo-0"
    assert rec.outcome == "error"
    assert rec.judge_why == "timed out"
    assert rec.artifacts == "stamp/demo-0"
    assert (kept / "stdout.jsonl").read_text() == '{"type": "prog'
    assert (kept / "stderr.txt").read_text() == "killed"
    assert not (kept / "session.json").exists()


async def test_a_rerun_rep_clears_what_the_first_pass_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_sandbox(monkeypatch, RunArtifacts("", "", 124, True, 600.0))
    evidence = tmp_path / "results" / "stamp"
    (evidence / "demo-0").mkdir(parents=True)
    (evidence / "demo-0" / "judge.txt").write_text("from the first pass")
    await run_rep(sandboxed(tmp_path), CONFIG, cast(Transport, None), 0, evidence)
    assert not (evidence / "demo-0" / "judge.txt").exists()


def test_a_results_line_without_artifacts_still_loads(tmp_path: Path) -> None:
    payload = asdict(record("demo", model="m"))
    del payload["artifacts"]
    path = tmp_path / "old.jsonl"
    path.write_text(json.dumps(payload) + "\n")
    assert read_results(path)[0].artifacts == ""


def test_a_confirmation_rerun_leaves_one_results_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two stamps would mean two files, and `compare` would read the first."""
    import benchmarks.runner.__main__ as bench
    from benchmarks.runner.report import baseline_path
    from benchmarks.runner.sandbox import repo_root

    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "cases").symlink_to(repo_root() / "benchmarks" / "cases")
    baselines = tmp_path / "benchmarks" / "baselines"
    baselines.mkdir()
    baseline_path(baselines, "m", "smoke").write_text(
        dumps_baseline(
            BaselineMeta("t", "abc1234", "anthropic", "m", "m"),
            [summary("edit-docstring", 1.0)],
        )
    )

    async def failing(
        case: Case, config: Config, transport: Transport, reps: int, evidence: Path
    ) -> list[RepRecord]:
        base = replace(record(case.id, model="m"), outcome="fail")
        return [replace(base, rep=n) for n in range(reps)]

    # Distinct stamps on every call, so a second stamp cannot hide by landing
    # in the same second as the first.
    stamps = iter(f"2026-09-24T00-00-0{n}Z" for n in range(10))
    monkeypatch.setattr(bench, "new_stamp", lambda: next(stamps))
    monkeypatch.setattr(bench, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bench, "build_image", lambda root: None)
    monkeypatch.setattr(bench, "judge_transport", lambda config: None)
    monkeypatch.setattr(bench, "run_case", failing)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    for var in ("NARE_PROVIDER", "NARE_BASE_URL", "NARE_BENCH_JUDGE_MODEL"):
        monkeypatch.delenv(var, raising=False)

    code = main(
        ["run", "--case", "edit-docstring", "--model", "m", "--provider", "anthropic"]
    )
    assert code == 1  # the drop was confirmed at seven reps
    results = tmp_path / "benchmarks" / "results"
    assert [p.name for p in results.glob("*.jsonl")] == ["2026-09-24T00-00-00Z.jsonl"]
