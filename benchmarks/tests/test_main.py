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
    monkeypatch.setattr(bench, "run_check", lambda w, c, t, artifacts=None: (0, ""))
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


async def test_a_reps_checks_can_read_its_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A server the agent left running died with its container. What the run
    # left in /out is all a check can read about it.
    import benchmarks.runner.__main__ as bench

    fake_sandbox(monkeypatch, RunArtifacts(DONE_LINE, "", 0, False, 1.0))
    seen: list[str] = []

    def check(
        workdir: Path, cmd: str, timeout: int, artifacts: Path | None = None
    ) -> tuple[int, str]:
        assert artifacts is not None
        seen.append((artifacts / "session.json").read_text())
        return 0, ""

    monkeypatch.setattr(bench, "run_check", check)
    evidence = tmp_path / "results" / "stamp"
    await run_rep(sandboxed(tmp_path), CONFIG, cast(Transport, None), 0, evidence)
    assert seen == ['{"id": "s1"}']


async def test_a_rep_keeps_what_its_container_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import benchmarks.runner.__main__ as bench

    fake_sandbox(monkeypatch, RunArtifacts(DONE_LINE, "", 0, False, 1.0))

    def agent(
        case: Case, workdir: Path, artifacts: Path, config: Config
    ) -> RunArtifacts:
        (artifacts / "after.json").write_text('{"processes": []}')
        return RunArtifacts(DONE_LINE, "", 0, False, 1.0)

    monkeypatch.setattr(bench, "run_agent", agent)
    evidence = tmp_path / "results" / "stamp"
    await run_rep(sandboxed(tmp_path), CONFIG, cast(Transport, None), 0, evidence)
    assert (evidence / "demo-0" / "after.json").read_text() == '{"processes": []}'


@pytest.mark.docker
def test_verify_checks_read_what_an_idle_run_leaves(tmp_path: Path) -> None:
    """Checks that read /out run for real on the pristine fixture, against a
    run in which the agent did nothing, rather than crash on a missing file."""
    from benchmarks.runner.__main__ import pristine_check

    (tmp_path / "fixture").mkdir()
    case = replace(case_with(), directory=tmp_path)
    cmd = (
        "python3 -I -c 'import json; "
        's = json.load(open("/out/session.json")); '
        'a = json.load(open("/out/after.json")); '
        'assert s["messages"], s; '
        'assert a == {"processes": [], "database_files": []}, a\''
    )
    code, output = pristine_check(case, cmd, 60)
    assert code == 0, output


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


async def test_a_rep_records_the_agents_output_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_sandbox(monkeypatch, RunArtifacts(DONE_LINE, "", 0, False, 1.0))
    evidence = tmp_path / "results" / "stamp"
    config = replace(CONFIG, max_tokens=32000)
    rec = await run_rep(sandboxed(tmp_path), config, cast(Transport, None), 0, evidence)
    assert rec.max_tokens == 32000


@pytest.mark.parametrize(("blessed", "now"), [(None, 32000), (32000, None)])
def test_numbers_from_another_output_cap_are_not_compared(
    blessed: int | None, now: int | None
) -> None:
    from benchmarks.runner.__main__ import model_changed
    from benchmarks.runner.report import Baseline

    baseline = Baseline(
        BaselineMeta("t", "abc1234", "anthropic", "m", "m", max_tokens=blessed), {}
    )
    assert model_changed(baseline, replace(CONFIG, max_tokens=now), False)
    assert not model_changed(baseline, replace(CONFIG, max_tokens=blessed), False)
    assert not model_changed(baseline, replace(CONFIG, max_tokens=now), True)


def results_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import benchmarks.runner.__main__ as bench
    from benchmarks.runner.sandbox import repo_root

    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "cases").symlink_to(repo_root() / "benchmarks" / "cases")
    monkeypatch.setattr(bench, "repo_root", lambda: tmp_path)
    for var in ("NARE_BENCH_MAX_TOKENS", "NARE_BENCH_JUDGE_MODEL", "NARE_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path / "benchmarks" / "baselines"


def test_bless_records_the_cap_the_results_were_run_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Read from the results, not the shell: bless never runs the agent.
    from benchmarks.runner.report import baseline_path, load_baseline

    baselines = results_dir(tmp_path, monkeypatch)
    capped = replace(record("edit-docstring", model="m"), max_tokens=32000)
    write_results(tmp_path, [capped])
    assert main(["bless", "--model", "m"]) == 0
    assert load_baseline(baseline_path(baselines, "m", "smoke")).meta.max_tokens == (
        32000
    )


def test_compare_refuses_results_run_at_another_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.runner.report import baseline_path

    baselines = results_dir(tmp_path, monkeypatch)
    baselines.mkdir()
    baseline_path(baselines, "m", "smoke").write_text(
        dumps_baseline(
            BaselineMeta("t", "abc1234", "anthropic", "m", "m"),
            [summary("edit-docstring", 1.0)],
        )
    )
    capped = replace(record("edit-docstring", model="m"), max_tokens=32000)
    write_results(tmp_path, [capped])
    assert main(["compare", "--model", "m"]) == 2


async def test_a_judged_rep_keeps_the_diff_and_closing_message_it_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_sandbox(monkeypatch, RunArtifacts(f"{OUTPUT}\n{DONE_LINE}", "", 0, False, 1.0))
    judge = FakeJudge('{"met": [true], "why": "ok"}')
    evidence = tmp_path / "results" / "stamp"
    await run_rep(sandboxed(tmp_path, Judge(("x",), 1)), CONFIG, judge, 0, evidence)
    kept = evidence / "demo-0"
    assert (kept / "diff.patch").read_text() == "a diff"
    assert (kept / "closing.txt").read_text() == "Done: raised it to 60."
    prompt = judge.calls[0][0].content[0]["text"]
    assert "a diff" in prompt
    assert "Done: raised it to 60." in prompt


async def test_an_unjudged_rep_keeps_them_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Triage reads the diff of every failure, judged case or not."""
    fake_sandbox(monkeypatch, RunArtifacts(DONE_LINE, "", 0, False, 1.0))
    evidence = tmp_path / "results" / "stamp"
    await run_rep(sandboxed(tmp_path), CONFIG, cast(Transport, None), 0, evidence)
    kept = evidence / "demo-0"
    assert (kept / "diff.patch").read_text() == "a diff"
    assert (kept / "closing.txt").read_text() == ""
    assert not (kept / "judge.txt").exists()


async def test_a_rep_without_a_result_line_keeps_neither(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_sandbox(monkeypatch, RunArtifacts('{"type": "prog', "", 124, True, 600.0))
    evidence = tmp_path / "results" / "stamp"
    await run_rep(sandboxed(tmp_path), CONFIG, cast(Transport, None), 0, evidence)
    assert not (evidence / "demo-0" / "diff.patch").exists()
    assert not (evidence / "demo-0" / "closing.txt").exists()


async def test_an_infrastructure_error_keeps_neither(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    line = json.dumps(
        {"type": "result", "status": "error", "stop_reason": None, "turns": 1}
    )
    fake_sandbox(monkeypatch, RunArtifacts(line, "", 1, False, 1.0))
    evidence = tmp_path / "results" / "stamp"
    rec = await run_rep(sandboxed(tmp_path), CONFIG, cast(Transport, None), 0, evidence)
    assert rec.outcome == "error"
    assert not (evidence / "demo-0" / "diff.patch").exists()
    assert not (evidence / "demo-0" / "closing.txt").exists()


async def test_a_rerun_rep_clears_the_first_passs_diff_and_closing_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_sandbox(monkeypatch, RunArtifacts("", "", 124, True, 600.0))
    evidence = tmp_path / "results" / "stamp"
    (evidence / "demo-0").mkdir(parents=True)
    (evidence / "demo-0" / "diff.patch").write_text("from the first pass")
    (evidence / "demo-0" / "closing.txt").write_text("from the first pass")
    await run_rep(sandboxed(tmp_path), CONFIG, cast(Transport, None), 0, evidence)
    assert not (evidence / "demo-0" / "diff.patch").exists()
    assert not (evidence / "demo-0" / "closing.txt").exists()


def test_compare_judges_accepts_its_flags() -> None:
    args = build_parser().parse_args(
        [
            "compare-judges",
            "--results",
            "r.jsonl",
            "--case",
            "a",
            "--case",
            "b",
            "--jev-model",
            "jev-1.13.0",
            "--reference-model",
            "opus",
        ]
    )
    assert args.command == "compare-judges"
    assert args.case == ["a", "b"]
    assert (args.jev_model, args.reference_model) == ("jev-1.13.0", "opus")


async def test_a_rep_records_its_provider_and_judge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_sandbox(monkeypatch, RunArtifacts(DONE_LINE, "", 0, False, 1.0))
    evidence = tmp_path / "results" / "stamp"
    config = replace(CONFIG, provider="openai", judge_model="claude-sonnet-5-5")
    rec = await run_rep(sandboxed(tmp_path), config, cast(Transport, None), 0, evidence)
    assert (rec.provider, rec.judge_model) == ("openai", "claude-sonnet-5-5")


def test_bless_writes_the_provider_and_judge_the_results_were_run_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A matrix run takes both from the baseline, not the shell, so bless must
    # read them back from the results, as it reads the cap.
    from benchmarks.runner.report import baseline_path, load_baseline

    baselines = results_dir(tmp_path, monkeypatch)
    ran = replace(
        record("edit-docstring", model="m"),
        provider="openai",
        judge_model="claude-sonnet-5-5",
    )
    write_results(tmp_path, [ran])
    assert main(["bless", "--model", "m"]) == 0
    meta = load_baseline(baseline_path(baselines, "m", "smoke")).meta
    assert (meta.provider, meta.judge_model) == ("openai", "claude-sonnet-5-5")


def test_bless_takes_the_flags_for_results_written_before_they_were_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.runner.report import baseline_path, load_baseline

    baselines = results_dir(tmp_path, monkeypatch)
    write_results(tmp_path, [record("edit-docstring", model="m")])
    flags = ["--provider", "openai", "--judge-model", "j"]
    assert main(["bless", "--model", "m", *flags]) == 0
    meta = load_baseline(baseline_path(baselines, "m", "smoke")).meta
    assert (meta.provider, meta.judge_model) == ("openai", "j")


def test_compare_reads_the_judge_from_the_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.runner.report import baseline_path

    baselines = results_dir(tmp_path, monkeypatch)
    baselines.mkdir()
    baseline_path(baselines, "m", "smoke").write_text(
        dumps_baseline(
            BaselineMeta("t", "abc1234", "openai", "m", "claude-sonnet-5-5"),
            [summary("edit-docstring", 1.0)],
        )
    )
    judged = replace(
        record("edit-docstring", model="m"), judge_model="claude-sonnet-5-5"
    )
    write_results(tmp_path, [judged])
    # No --judge-model: the default, the model under test, is not the judge.
    assert main(["compare", "--model", "m"]) == 0


MATRIX_STAMP = "2026-10-26T00-00-00Z"


def test_bless_reads_its_models_file_from_a_matrix_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from benchmarks.runner.report import baseline_path, load_baseline

    baselines = results_dir(tmp_path, monkeypatch)
    for model in ("claude-haiku-5-5", "gpt-5-nano"):
        ran = record("edit-docstring", model=model)
        write_results(tmp_path, [ran], f"{MATRIX_STAMP}.{model}")
    for model in ("claude-haiku-5-5", "gpt-5-nano"):
        assert main(["bless", "--model", model]) == 0
        assert load_baseline(baseline_path(baselines, model, "smoke")).meta.model == (
            model
        )
    assert f"from {MATRIX_STAMP}.claude-haiku-5-5.jsonl" in capsys.readouterr().out


def test_a_matrix_run_without_the_model_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    results_dir(tmp_path, monkeypatch)
    ran = record("edit-docstring", model="gpt-5-nano")
    write_results(tmp_path, [ran], f"{MATRIX_STAMP}.gpt-5-nano")
    assert main(["bless", "--model", "claude-haiku-5-5"]) == 2
    assert "no results for 'claude-haiku-5-5'" in capsys.readouterr().err


def test_a_single_run_after_a_matrix_run_is_the_newest_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    results_dir(tmp_path, monkeypatch)
    write_results(tmp_path, [record("edit-docstring", model="m")], f"{MATRIX_STAMP}.m")
    write_results(
        tmp_path, [record("edit-docstring", model="m")], "2026-10-26T01-00-00Z"
    )
    assert main(["bless", "--model", "m"]) == 0
    assert "from 2026-10-26T01-00-00Z.jsonl" in capsys.readouterr().out


def test_the_newest_matrix_run_skips_a_newer_single_run(tmp_path: Path) -> None:
    from benchmarks.runner.__main__ import latest_run

    directory = tmp_path / "benchmarks" / "results"
    directory.mkdir(parents=True)
    for name in (
        "2026-10-25T00-00-00Z.a.jsonl",
        f"{MATRIX_STAMP}.a.jsonl",
        f"{MATRIX_STAMP}.b.jsonl",
        "2026-10-26T01-00-00Z.jsonl",
    ):
        (directory / name).write_text("")
    assert [p.name for p in latest_run(tmp_path)] == ["2026-10-26T01-00-00Z.jsonl"]
    assert [p.name for p in latest_run(tmp_path, matrix=True)] == [
        f"{MATRIX_STAMP}.a.jsonl",
        f"{MATRIX_STAMP}.b.jsonl",
    ]


def endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *served: str
) -> list[tuple[str, str]]:
    """A checkout and an endpoint listing `served`; returns each listing call."""
    import benchmarks.runner.__main__ as bench
    from benchmarks.runner.models import Listing, ServedModel

    calls: list[tuple[str, str]] = []

    def listing(base_url: str, api_key: str) -> Listing:
        calls.append((base_url, api_key))
        return Listing(tuple(ServedModel(name) for name in served), detailed=True)

    results_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(bench, "list_models", listing)
    monkeypatch.setenv("NARE_BASE_URL", "https://proxy.example")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    return calls


def test_models_lists_what_the_endpoint_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = endpoint(tmp_path, monkeypatch, "gpt-5-nano")
    assert main(["models"]) == 0
    assert calls == [("https://proxy.example", "k")]
    assert "gpt-5-nano" in capsys.readouterr().out


def test_models_without_a_base_url_exits_two_with_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = endpoint(tmp_path, monkeypatch, "gpt-5-nano")
    monkeypatch.delenv("NARE_BASE_URL")
    assert main(["models"]) == 2
    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) == 1
    assert "no base URL" in err
    assert calls == []


def test_models_reports_a_listing_that_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import benchmarks.runner.__main__ as bench
    from benchmarks.runner.models import Listing, ListingError

    endpoint(tmp_path, monkeypatch)

    def refuse(base_url: str, api_key: str) -> Listing:
        raise ListingError("/v1/models: HTTP 401")

    monkeypatch.setattr(bench, "list_models", refuse)
    assert main(["models"]) == 1
    assert "HTTP 401" in capsys.readouterr().err


SONNET = "claude-sonnet-5-5"
HAIKU = BaselineMeta("t", "abc1234", "anthropic", "claude-haiku-5-5", SONNET)
NANO = BaselineMeta("t", "abc1234", "openai", "gpt-5-nano", SONNET)


def matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *metas: BaselineMeta,
    failing: str = "",
    served: tuple[str, ...] | None = None,
) -> list[Config]:
    """A checkout whose baselines are `metas`, each passing edit-docstring.

    Every model passes every rep except `failing`, which fails them all. The
    endpoint lists `served`, by default every baseline's model and judge.
    Returns the config of every run_case call; the image build is one too.
    """
    import benchmarks.runner.__main__ as bench
    from benchmarks.runner.report import baseline_path

    listed = (
        served
        if served is not None
        else tuple({name for meta in metas for name in (meta.model, meta.judge_model)})
    )
    endpoint(tmp_path, monkeypatch, *listed)
    baselines = tmp_path / "benchmarks" / "baselines"
    baselines.mkdir()
    for meta in metas:
        baseline_path(baselines, meta.model, "smoke").write_text(
            dumps_baseline(meta, [summary("edit-docstring", 1.0)])
        )
    ran: list[Config] = []

    async def reps(
        case: Case, config: Config, transport: Transport, n: int, evidence: Path
    ) -> list[RepRecord]:
        ran.append(config)
        base = replace(
            record(case.id, model=config.model),
            outcome="fail" if config.model == failing else "pass",
            provider=config.provider,
            judge_model=config.judge_model,
        )
        return [replace(base, rep=rep) for rep in range(n)]

    monkeypatch.setattr(bench, "new_stamp", lambda: MATRIX_STAMP)
    monkeypatch.setattr(bench, "build_image", lambda root: ran.append(CONFIG))
    monkeypatch.setattr(bench, "judge_transport", lambda config: None)
    monkeypatch.setattr(bench, "run_case", reps)
    monkeypatch.setenv("NARE_MODEL", "ada/qwen3-14b")  # the matrix ignores it
    return ran


def table_row(out: str, case: str) -> list[str]:
    line = next(row for row in out.splitlines() if row.startswith(f"| {case} "))
    return [cell.strip() for cell in line.strip("|").split("|")]


def test_the_matrix_runs_each_model_on_its_own_provider_and_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = matrix(tmp_path, monkeypatch, HAIKU, NANO)
    assert main(["run", "--matrix", "--case", "edit-docstring"]) == 0
    assert [(c.model, c.provider, c.base_url, c.judge_model) for c in ran[1:]] == [
        ("claude-haiku-5-5", "anthropic", "https://proxy.example", SONNET),
        ("gpt-5-nano", "openai", "https://proxy.example/v1", SONNET),
    ]
    results = tmp_path / "benchmarks" / "results"
    assert sorted(p.name for p in results.glob("*.jsonl")) == [
        f"{MATRIX_STAMP}.claude-haiku-5-5.jsonl",
        f"{MATRIX_STAMP}.gpt-5-nano.jsonl",
    ]
    out = capsys.readouterr().out
    assert table_row(out, "case") == ["case", "claude-haiku-5-5", "gpt-5-nano"]
    assert table_row(out, "edit-docstring") == ["edit-docstring", "3/3", "3/3"]
    assert f"Judge: {SONNET}, for every model." in out


def test_a_matrix_regression_on_any_model_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    matrix(tmp_path, monkeypatch, HAIKU, NANO, failing="gpt-5-nano")
    assert main(["run", "--matrix", "--case", "edit-docstring"]) == 1
    row = table_row(capsys.readouterr().out, "edit-docstring")
    assert row == ["edit-docstring", "3/3", f"0/{CONFIRM_REPS} regression"]


def test_a_matrix_with_two_judges_exits_two_before_any_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = matrix(tmp_path, monkeypatch, HAIKU, replace(NANO, judge_model="gpt-5-nano"))
    assert main(["run", "--matrix"]) == 2
    assert ran == []
    err = capsys.readouterr().err
    assert "claude-haiku-5-5.smoke.toml" in err
    assert "gpt-5-nano.smoke.toml" in err


def test_a_matrix_model_without_its_key_exits_two_before_any_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = matrix(tmp_path, monkeypatch, HAIKU, NANO)
    monkeypatch.delenv("OPENAI_API_KEY")
    assert main(["run", "--matrix"]) == 2
    assert ran == []
    err = capsys.readouterr().err
    assert "gpt-5-nano.smoke.toml" in err
    assert "OPENAI_API_KEY" in err


def test_a_model_the_endpoint_does_not_list_stops_the_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = matrix(
        tmp_path, monkeypatch, HAIKU, NANO, served=("claude-haiku-5-5", SONNET)
    )
    assert main(["run", "--matrix"]) == 2
    assert ran == []
    err = capsys.readouterr().err
    assert "'gpt-5-nano' (gpt-5-nano.smoke.toml)" in err


def test_a_judge_the_endpoint_does_not_list_stops_the_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The judge answers on every baseline's provider, so a judge the endpoint
    # does not serve would fail every judged rep of every model.
    served = ("claude-haiku-5-5", "gpt-5-nano")
    ran = matrix(tmp_path, monkeypatch, HAIKU, NANO, served=served)
    assert main(["run", "--matrix"]) == 2
    assert ran == []
    err = capsys.readouterr().err
    assert f"'{SONNET}' (the judge" in err
    assert err.count(SONNET) == 1  # one key, so one listing and one miss


@pytest.mark.parametrize("failing_key", ["ka", "ko"])
def test_a_listing_that_fails_for_one_key_still_checks_the_others(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failing_key: str,
) -> None:
    # A proxy key can be limited to some models, so each key lists on its own,
    # and one key's failed listing must not discard another key's miss.
    import benchmarks.runner.__main__ as bench
    from benchmarks.runner.models import Listing, ListingError, ServedModel

    ran = matrix(tmp_path, monkeypatch, HAIKU, NANO)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ka")
    monkeypatch.setenv("OPENAI_API_KEY", "ko")

    def listing(base_url: str, api_key: str) -> Listing:
        if api_key == failing_key:
            raise ListingError("/v1/models: HTTP 503")
        return Listing((ServedModel(SONNET),), detailed=True)

    monkeypatch.setattr(bench, "list_models", listing)
    assert main(["run", "--matrix"]) == 2
    assert ran == []
    err = capsys.readouterr().err
    listed, skipped = (
        ("claude-haiku-5-5", "gpt-5-nano")
        if failing_key == "ko"
        else ("gpt-5-nano", "claude-haiku-5-5")
    )
    assert f"'{listed}' ({listed}.smoke.toml)" in err
    assert f"'{skipped}' ({skipped}.smoke.toml)" not in err
    assert "warning" in err


def test_a_listing_that_fails_warns_and_the_matrix_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import benchmarks.runner.__main__ as bench
    from benchmarks.runner.models import Listing, ListingError

    ran = matrix(tmp_path, monkeypatch, HAIKU)

    def refuse(base_url: str, api_key: str) -> Listing:
        raise ListingError("/v1/models: HTTP 503")

    monkeypatch.setattr(bench, "list_models", refuse)
    assert main(["run", "--matrix", "--case", "edit-docstring"]) == 0
    assert [c.model for c in ran[1:]] == ["claude-haiku-5-5"]
    assert "warning" in capsys.readouterr().err


def test_a_matrix_without_a_base_url_warns_and_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = matrix(tmp_path, monkeypatch, HAIKU, NANO)
    monkeypatch.delenv("NARE_BASE_URL")
    assert main(["run", "--matrix", "--case", "edit-docstring"]) == 0
    assert [c.base_url for c in ran[1:]] == [None, None]
    assert "no base URL" in capsys.readouterr().err


def test_the_matrix_takes_no_model_provider_or_judge_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = matrix(tmp_path, monkeypatch, HAIKU)
    for flag in (["--model", "m"], ["--provider", "openai"], ["--judge-model", "j"]):
        assert main(["run", "--matrix", *flag]) == 2
    assert ran == []


def test_an_empty_matrix_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = matrix(tmp_path, monkeypatch)
    assert main(["run", "--matrix"]) == 2
    assert ran == []


def test_bless_stays_per_model() -> None:
    # Blessing the whole matrix at once would make it too easy to bless away
    # a regression.
    with pytest.raises(SystemExit):
        build_parser().parse_args(["bless", "--matrix"])


def test_compare_matrix_prints_the_last_matrix_run_without_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = matrix(tmp_path, monkeypatch, HAIKU, NANO, failing="gpt-5-nano")
    assert main(["run", "--matrix", "--case", "edit-docstring"]) == 1
    printed = capsys.readouterr().out
    # A later single run is not part of the matrix run.
    write_results(
        tmp_path, [record("edit-docstring", model="m")], "2026-10-27T00-00-00Z"
    )
    del ran[:]
    assert main(["compare", "--matrix", "--case", "edit-docstring"]) == 1
    out = capsys.readouterr().out
    assert ran == []
    assert table_row(out, "edit-docstring") == table_row(printed, "edit-docstring")


def test_compare_matrix_without_a_matrix_run_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    matrix(tmp_path, monkeypatch, HAIKU)
    write_results(tmp_path, [record("edit-docstring", model="m")])
    assert main(["compare", "--matrix"]) == 2


def test_compare_matrix_baselines_prints_the_tiers_baselines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = matrix(tmp_path, monkeypatch, HAIKU, NANO)
    assert main(["compare", "--matrix", "--baselines"]) == 0
    assert ran == []
    out = capsys.readouterr().out
    assert table_row(out, "edit-docstring") == ["edit-docstring", "3/3", "3/3"]


def test_baselines_goes_with_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    matrix(tmp_path, monkeypatch, HAIKU)
    assert main(["compare", "--baselines", "--model", "claude-haiku-5-5"]) == 2


def test_run_refuses_a_baseline_blessed_on_another_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The same model on the other route can lose a reasoning model's thinking
    # on the way, so the numbers are not comparable.
    matrix(tmp_path, monkeypatch, NANO)
    flags = [
        "--model",
        "gpt-5-nano",
        "--provider",
        "anthropic",
        "--judge-model",
        SONNET,
    ]
    assert main(["run", "--case", "edit-docstring", *flags]) == 2
    err = capsys.readouterr().err
    assert "provider 'openai'" in err
    assert "'anthropic'" in err


def provider_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Model m, blessed on openai and judged by j."""
    from benchmarks.runner.report import baseline_path

    baselines = results_dir(tmp_path, monkeypatch)
    baselines.mkdir()
    baseline_path(baselines, "m", "smoke").write_text(
        dumps_baseline(
            BaselineMeta("t", "abc1234", "openai", "m", "j"),
            [summary("edit-docstring", 1.0)],
        )
    )


def test_compare_refuses_results_run_on_another_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    provider_baseline(tmp_path, monkeypatch)
    ran = replace(record("edit-docstring", model="m"), provider="anthropic")
    write_results(tmp_path, [replace(ran, judge_model="j")])
    assert main(["compare", "--model", "m"]) == 2
    err = capsys.readouterr().err
    assert "provider 'openai'" in err
    assert "'anthropic'" in err


@pytest.mark.parametrize(
    ("flags", "code"),
    [
        ([], 0),
        (["--provider", "openai"], 0),
        (["--provider", "anthropic"], 2),
    ],
)
def test_an_unrecorded_provider_is_unknown_unless_a_flag_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: list[str], code: int
) -> None:
    # Results written before the provider was recorded say nothing about the
    # route they took, and the shell's NARE_PROVIDER says nothing either.
    provider_baseline(tmp_path, monkeypatch)
    monkeypatch.setenv("NARE_PROVIDER", "anthropic")
    write_results(tmp_path, [record("edit-docstring", model="m")])
    assert main(["compare", "--model", "m", "--judge-model", "j", *flags]) == code


@pytest.mark.parametrize("command", ["bless", "compare"])
@pytest.mark.parametrize(
    ("flag", "given", "recorded"),
    [
        ("--provider", "openai", "anthropic"),
        ("--judge-model", "claude-sonnet-5-5", "gpt-5-nano"),
    ],
)
def test_a_flag_that_contradicts_the_results_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    flag: str,
    given: str,
    recorded: str,
) -> None:
    # bless would write the flag's value over what was measured.
    from benchmarks.runner.report import baseline_path

    baselines = results_dir(tmp_path, monkeypatch)
    baselines.mkdir()
    blessed = baseline_path(baselines, "gpt-5-nano", "smoke")
    blessed.write_text(
        dumps_baseline(
            BaselineMeta("t", "abc1234", "anthropic", "gpt-5-nano", "gpt-5-nano"),
            [summary("edit-docstring", 1.0)],
        )
    )
    before = blessed.read_text()
    ran = replace(
        record("edit-docstring", model="gpt-5-nano"),
        provider="anthropic",
        judge_model="gpt-5-nano",
    )
    write_results(tmp_path, [ran])
    assert main([command, "--model", "gpt-5-nano", flag, given]) == 2
    err = capsys.readouterr().err
    assert f"'{given}'" in err
    assert f"'{recorded}'" in err
    assert blessed.read_text() == before
    # A flag that agrees with the results is fine.
    assert main([command, "--model", "gpt-5-nano", flag, recorded]) == 0
