"""bin/bench with arguments for nare: the __main__ half of #156."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, cast

import pytest

from benchmarks.runner.__main__ import (
    NeverStarted,
    args_changed,
    main,
    read_results,
    run_case,
    run_rep,
    split_run_args,
    stopped_log,
    write_results,
)
from benchmarks.runner.case import Case
from benchmarks.runner.config import Config
from benchmarks.runner.nare_args import RunArgs
from benchmarks.runner.report import (
    Baseline,
    BaselineMeta,
    RepRecord,
    baseline_path,
    dumps_baseline,
    load_baseline,
)
from benchmarks.runner.sandbox import RunArtifacts
from benchmarks.tests.test_grade import COMPACTED, WINDOW
from benchmarks.tests.test_judge import FakeJudge
from benchmarks.tests.test_main import (
    CONFIG,
    DONE_LINE,
    HAIKU,
    KEPT,
    MATRIX_STAMP,
    NANO,
    SONNET,
    fake_sandbox,
    matrix,
    record,
    results_dir,
    sandboxed,
    summary,
    write_matrix_run,
)
from nare.transport import Transport

FILE_ARGS = RunArgs(
    recorded=("--system-file", "p.md"),
    files={"p.md": "sha256:aa"},
    passed=("--system-file", "/in/0-p.md"),
)


async def test_a_rep_records_its_arguments_window_and_compactions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = "\n".join([WINDOW, COMPACTED, DONE_LINE]) + "\n"
    fake_sandbox(monkeypatch, RunArtifacts(stream, "", 0, False, 1.0))
    case = replace(sandboxed(tmp_path), args=("--tools", "read"))
    config = replace(CONFIG, run_args=FILE_ARGS)
    rec = await run_rep(case, config, cast(Transport, None), 0, tmp_path / "r" / "s")
    assert rec.outcome == "pass"
    assert (rec.case_args, rec.nare_args, rec.nare_files) == (
        ["--tools", "read"],
        ["--system-file", "p.md"],
        {"p.md": "sha256:aa"},
    )
    assert (rec.context_window, rec.compactions) == (
        {"tokens": 16000, "source": "flag"},
        1,
    )


async def test_an_errored_rep_records_them_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed = json.dumps(
        {"type": "result", "status": "error", "stop_reason": None, "turns": 1,
         "usage": {}, "error": "RateLimitError: 429"}
    )  # fmt: skip
    fake_sandbox(monkeypatch, RunArtifacts(f"{WINDOW}\n{failed}\n", "", 1, False, 1.0))
    config = replace(CONFIG, run_args=FILE_ARGS)
    rec = await run_rep(
        sandboxed(tmp_path), config, cast(Transport, None), 0, tmp_path / "r" / "s"
    )
    assert rec.outcome == "error"
    assert rec.nare_args == ["--system-file", "p.md"]
    assert (rec.context_window, rec.compactions) == (
        {"tokens": 16000, "source": "flag"},
        0,
    )


async def test_a_rep_with_no_stream_records_no_compaction_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A Docker fault: nothing on stdout, so nobody knows whether nare compacted.
    fault = RunArtifacts("", "docker: Error response from daemon\n", 125, False, 1.0)
    fake_sandbox(monkeypatch, fault)
    rec = await run_rep(
        sandboxed(tmp_path), CONFIG, cast(Transport, None), 0, tmp_path / "r" / "s"
    )
    assert rec.outcome == "error"
    assert (rec.context_window, rec.compactions) == (None, None)
    path = write_results(tmp_path, [rec])
    assert json.loads(path.read_text())["compactions"] is None


def test_a_results_line_from_before_loads_as_the_default_variant(
    tmp_path: Path,
) -> None:
    payload = asdict(record("demo", model="m"))
    for key in (
        "case_args",
        "nare_args",
        "nare_files",
        "context_window",
        "compactions",
    ):
        del payload[key]
    path = tmp_path / "old.jsonl"
    path.write_text(json.dumps(payload) + "\n")
    rec = read_results(path)[0]
    assert (rec.case_args, rec.nare_args, rec.nare_files) == ([], [], {})
    assert (rec.context_window, rec.compactions) == (None, None)


def test_the_new_fields_survive_a_results_file(tmp_path: Path) -> None:
    ran = replace(
        record("demo", model="m"),
        case_args=["--tools", "read"],
        nare_args=["--effort", "low"],
        context_window={"tokens": 262144, "source": "backend"},
        compactions=2,
    )
    assert read_results(write_results(tmp_path, [ran])) == [ran]


REFUSED = "nare: error: argument --context-window: invalid int value: 'abc'\n"
STAMP = "2026-11-04T00-00-00Z"
VERDICT = '{"met": [true, true, true], "why": "ok"}'


@pytest.mark.parametrize(
    ("code", "timed_out", "stops"),
    [(2, False, True), (2, True, False), (1, False, False), (125, False, False)],
)
async def test_only_a_never_started_rep_stops_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: int,
    timed_out: bool,
    stops: bool,
) -> None:
    fake_sandbox(monkeypatch, RunArtifacts("", REFUSED, code, timed_out, 1.0))
    evidence = tmp_path / "r" / "s"
    rep = run_rep(sandboxed(tmp_path), CONFIG, cast(Transport, None), 0, evidence)
    if stops:
        with pytest.raises(NeverStarted, match="invalid int value: 'abc'") as raised:
            await rep
        log = evidence / "stopped.txt"
        assert raised.value.log == log
        assert str(log) in str(raised.value)
        assert REFUSED.rstrip() in log.read_text(encoding="utf-8")
        # The rep's own evidence is kept beside it, as for any rep.
        kept = evidence / f"{sandboxed(tmp_path).id}-0"
        assert (kept / "stderr.txt").read_text(encoding="utf-8") == REFUSED
    else:
        assert (await rep).outcome == "error"
        assert not (evidence / "stopped.txt").exists()


def test_the_stop_log_says_why_with_the_arguments_and_both_streams(
    tmp_path: Path,
) -> None:
    case = replace(sandboxed(tmp_path), args=("--tools", "read,list"))
    config = replace(
        CONFIG,
        run_args=RunArgs(
            recorded=("--context-window", "abc", "--system-file", "prompts/x.md"),
            files={"prompts/x.md": "sha256:ab12"},
            passed=("--context-window", "abc", "--system-file", "/in/0-x.md"),
        ),
    )
    result = RunArtifacts("partial line\n", REFUSED, 2, False, 0.1)
    text = stopped_log(case, 3, config, result, "s/edit-docstring-3")
    assert text.startswith("nare never started, so the run stopped.\n")
    for expected in (
        case.id,
        f"{CONFIG.model} ({CONFIG.provider})",
        "2 (never_started), no result line, not timed out",
        "--context-window abc",
        "--tools read,list",
        "s/edit-docstring-3",
        REFUSED.rstrip(),
        "partial line",
        # The args above show /in paths; the host file each came from is here.
        "prompts/x.md sha256:ab12",
    ):
        assert expected in text
    assert text.index("nare's stderr:") < text.index("nare's stdout:")


def test_the_stop_log_says_when_there_were_no_arguments_and_no_stdout(
    tmp_path: Path,
) -> None:
    text = stopped_log(
        sandboxed(tmp_path), 0, CONFIG, RunArtifacts("", REFUSED, 2, False, 0.1), "k"
    )
    assert text.count("(none)") == 2
    assert "run files" not in text
    assert text.rstrip().endswith("(empty)")


def refusing(
    monkeypatch: pytest.MonkeyPatch,
    refuse: Callable[[Config, int], bool],
    *,
    check_code: int = 0,
) -> list[str]:
    """The agent finishes with a `done` line, except where `refuse` says nare
    exits 2 with no result line; `refuse` gets the call's config and its number
    from 1. Returns the model of every agent call.

    The sandbox's other doors (the checks, the diff) are faked as `fake_sandbox`
    fakes them, so nothing here can reach Docker, even if a rep stops raising.
    """
    import benchmarks.runner.__main__ as bench

    done = RunArtifacts(DONE_LINE, "", 0, False, 1.0)
    fake_sandbox(monkeypatch, done)
    calls: list[str] = []

    def agent(
        case: Case, workdir: Path, artifacts: Path, config: Config
    ) -> RunArtifacts:
        calls.append(config.model)
        return (
            RunArtifacts("", REFUSED, 2, False, 0.1)
            if refuse(config, len(calls))
            else done
        )

    monkeypatch.setattr(bench, "run_agent", agent)
    monkeypatch.setattr(
        bench, "run_check", lambda w, c, t, artifacts=None: (check_code, "")
    )
    return calls


def test_a_never_started_rep_stops_the_run_with_exit_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import benchmarks.runner.__main__ as bench

    results_dir(tmp_path, monkeypatch)
    calls = refusing(monkeypatch, lambda config, n: True)
    monkeypatch.setattr(bench, "build_image", lambda root: None)
    monkeypatch.setattr(bench, "judge_transport", lambda config: None)
    monkeypatch.setattr(bench, "new_stamp", lambda: STAMP)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("NARE_BASE_URL", raising=False)
    argv = [
        "run",
        "--case",
        "edit-docstring",
        "--model",
        "m",
        "--provider",
        "anthropic",
    ]
    assert main(argv) == 2
    assert calls == ["m"]  # the first rep stopped the run
    err = capsys.readouterr().err
    assert "invalid int value: 'abc'" in err
    results = tmp_path / "benchmarks" / "results"
    assert not list(results.glob("*.jsonl"))
    log = results / STAMP / "stopped.txt"
    assert str(log) in err
    assert "invalid int value: 'abc'" in log.read_text(encoding="utf-8")


def test_a_stop_in_the_confirmation_sets_the_first_results_aside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The first pass wrote <stamp>.jsonl, and the newest run is what `bless`
    reads: a half run must not be it. Moved, not deleted."""
    import benchmarks.runner.__main__ as bench
    from benchmarks.runner.report import baseline_path

    baselines = results_dir(tmp_path, monkeypatch)
    baselines.mkdir()
    baseline_path(baselines, "m", "smoke").write_text(
        dumps_baseline(
            BaselineMeta("t", "abc1234", "anthropic", "m", "m"),
            [summary("edit-docstring", 1.0)],
        )
    )
    # Three reps fail their check, which the confirmation re-runs; its first
    # rep, the fourth call, is the one nare refuses.
    calls = refusing(monkeypatch, lambda config, n: n == 4, check_code=1)
    monkeypatch.setattr(bench, "build_image", lambda root: None)
    monkeypatch.setattr(bench, "judge_transport", lambda config: FakeJudge(VERDICT))
    monkeypatch.setattr(bench, "new_stamp", lambda: STAMP)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("NARE_BASE_URL", raising=False)
    argv = [
        "run",
        "--case",
        "edit-docstring",
        "--model",
        "m",
        "--provider",
        "anthropic",
    ]
    assert main(argv) == 2
    assert calls == ["m"] * 4
    captured = capsys.readouterr()
    results = tmp_path / "benchmarks" / "results"
    assert not list(results.glob("*.jsonl"))
    moved = results / STAMP / "results.partial.jsonl"
    assert [r.rep for r in read_results(moved)] == [0, 1, 2]
    assert str(moved) in captured.err
    assert "invalid int value: 'abc'" in (results / STAMP / "stopped.txt").read_text()


def test_a_never_started_rep_stops_the_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import benchmarks.runner.__main__ as bench

    matrix(tmp_path, monkeypatch, HAIKU, NANO)
    # The matrix helper fakes run_case; the real one runs the reps, so the log
    # that is written is the one run_rep writes.
    monkeypatch.setattr(bench, "run_case", run_case)
    monkeypatch.setattr(bench, "judge_transport", lambda config: FakeJudge(VERDICT))
    calls = refusing(monkeypatch, lambda config, n: config.model == NANO.model)
    assert main(["run", "--matrix", "--case", "edit-docstring"]) == 2
    # Three reps of the first model, then the second model's first rep.
    assert calls == [HAIKU.model] * 3 + [NANO.model]
    err = capsys.readouterr().err
    results = tmp_path / "benchmarks" / "results"
    log = results / f"{MATRIX_STAMP}.gpt-5-nano" / "stopped.txt"
    assert str(log) in err
    assert "invalid int value: 'abc'" in log.read_text(encoding="utf-8")
    # No half run is left as the newest results: the first model's file is
    # moved into its evidence directory, not deleted.
    assert not list(results.glob("*.jsonl"))
    moved = results / f"{MATRIX_STAMP}.claude-haiku-5-5" / "results.partial.jsonl"
    assert [r.model for r in read_results(moved)] == [HAIKU.model] * 3
    assert str(moved) in err


TWO_CASES = ["--case", "edit-docstring", "--case", "fix-failing-test"]


def stopping_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *cases: str) -> Path:
    """A checkout with the real cases, a judge that meets every assertion, and
    model m's baseline at a pass rate of 1 on `cases`, when there are any.
    Returns benchmarks/results."""
    import benchmarks.runner.__main__ as bench

    baselines = results_dir(tmp_path, monkeypatch)
    if cases:
        baselines.mkdir()
        baseline_path(baselines, "m", "smoke").write_text(
            dumps_baseline(
                BaselineMeta("t", "abc1234", "anthropic", "m", "m"),
                [summary(case, 1.0) for case in cases],
            )
        )
    monkeypatch.setattr(bench, "build_image", lambda root: None)
    monkeypatch.setattr(bench, "judge_transport", lambda config: FakeJudge(VERDICT))
    monkeypatch.setattr(bench, "new_stamp", lambda: STAMP)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("NARE_BASE_URL", raising=False)
    return tmp_path / "benchmarks" / "results"


def test_a_stop_keeps_the_records_of_each_case_its_pass_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The judge verdicts already paid for are kept, out of the newest run."""
    results = stopping_run(tmp_path, monkeypatch)
    # edit-docstring's three reps finish; fix-failing-test's first, the fourth
    # call, is the one nare refuses.
    calls = refusing(monkeypatch, lambda config, n: n == 4)
    assert main([*RUN, "--provider", "anthropic", *TWO_CASES]) == 2
    assert len(calls) == 4
    err = capsys.readouterr().err
    assert not list(results.glob("*.jsonl"))
    kept = results / STAMP / "results.partial.jsonl"
    records = read_results(kept)
    assert [(r.case, r.rep) for r in records] == [
        ("edit-docstring", n) for n in range(3)
    ]
    assert [r.judge_met for r in records] == [3, 3, 3]
    assert str(kept) in err
    assert err.index("nare refused its invocation") < err.index(str(kept))


def test_a_stop_in_the_confirmation_keeps_its_records_beside_the_first_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both cases drop, so both are re-run. edit-docstring's re-run finishes;
    fix-failing-test's first re-run rep, the fourteenth call, is refused."""
    results = stopping_run(tmp_path, monkeypatch, "edit-docstring", "fix-failing-test")
    calls = refusing(monkeypatch, lambda config, n: n == 14, check_code=1)
    assert main([*RUN, "--provider", "anthropic", *TWO_CASES]) == 2
    assert len(calls) == 3 + 3 + 7 + 1
    err = capsys.readouterr().err
    assert not list(results.glob("*.jsonl"))
    first = results / STAMP / "results.partial.jsonl"
    assert [(r.case, r.rep) for r in read_results(first)] == [
        (case, n) for case in ("edit-docstring", "fix-failing-test") for n in range(3)
    ]
    rerun = results / STAMP / "results.confirm.partial.jsonl"
    records = read_results(rerun)
    assert [(r.case, r.rep) for r in records] == [
        ("edit-docstring", n) for n in range(7)
    ]
    assert [r.judge_met for r in records] == [3] * 7
    assert str(first) in err
    assert str(rerun) in err


ARGUED = """\
id = "argued"
tier = "smoke"
prompt = "Say what bar.py holds."
{args}

[[check]]
kind = "result"
status = "done"
"""
RUN = ["run", "--model", "m"]


@dataclass
class Ran:
    builds: int = 0
    reps: list[tuple[Case, Config]] = field(default_factory=list)


def argued(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: str = "") -> Ran:
    """A checkout whose one case, `argued`, has `args` (TOML), over a fake
    container in which every rep is done. No judge, no bash check."""
    import benchmarks.runner.__main__ as bench

    case = tmp_path / "benchmarks" / "cases" / "argued"
    (case / "fixture").mkdir(parents=True)
    (case / "fixture" / "bar.py").write_text("x = 1\n")
    (case / "case.toml").write_text(
        ARGUED.format(args=f"args = {args}" if args else "")
    )
    ran = Ran()

    def agent(
        case: Case, workdir: Path, artifacts: Path, config: Config
    ) -> RunArtifacts:
        ran.reps.append((case, config))
        return RunArtifacts(DONE_LINE, "", 0, False, 1.0)

    def build(root: Path) -> None:
        ran.builds += 1

    monkeypatch.setattr(bench, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bench, "run_agent", agent)
    monkeypatch.setattr(bench, "git_diff", lambda w: "")
    monkeypatch.setattr(bench, "build_image", build)
    monkeypatch.setattr(bench, "judge_transport", lambda config: None)
    monkeypatch.setattr(bench, "new_stamp", lambda: STAMP)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    for var in (
        "NARE_PROVIDER",
        "NARE_BASE_URL",
        "NARE_BENCH_JUDGE_MODEL",
        "NARE_BENCH_MAX_TOKENS",
    ):
        monkeypatch.delenv(var, raising=False)
    return ran


def results_of(tmp_path: Path) -> list[RepRecord]:
    return read_results(tmp_path / "benchmarks" / "results" / f"{STAMP}.jsonl")


def test_everything_after_the_first_double_dash_is_for_nare() -> None:
    assert split_run_args(["run", "--tier", "full", "--", "--effort", "low"]) == (
        ["run", "--tier", "full"],
        ["--effort", "low"],
    )
    assert split_run_args(["run", "--", "--", "x"]) == (["run"], ["--", "x"])
    assert split_run_args(["run"]) == (["run"], None)


def test_run_passes_its_arguments_then_the_cases_and_records_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = argued(tmp_path, monkeypatch, '["--tools", "read,list,search"]')
    assert main([*RUN, "--", "--effort", "low"]) == 0
    assert ran.builds == 1
    assert {(c.args, cfg.run_args.passed) for c, cfg in ran.reps} == {
        (("--tools", "read,list,search"), ("--effort", "low"))
    }
    lines = results_of(tmp_path)
    assert len(lines) == len(ran.reps) == 3
    assert {(tuple(r.case_args), tuple(r.nare_args)) for r in lines} == {
        (("--tools", "read,list,search"), ("--effort", "low"))
    }


def test_run_reads_a_system_file_once_and_every_line_records_its_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = argued(tmp_path, monkeypatch)
    prompts = tmp_path / "benchmarks" / "prompts"
    prompts.mkdir()
    (prompts / "terse.md").write_text("Be terse.\n")
    # Read from where bench runs, the checkout's root, and recorded from it.
    monkeypatch.chdir(tmp_path)
    recorded = "benchmarks/prompts/terse.md"
    assert main([*RUN, "--", "--system-file", recorded]) == 0
    inputs = tmp_path / "benchmarks" / "results" / STAMP / "inputs"
    assert {cfg.run_args.passed for _, cfg in ran.reps} == {
        ("--system-file", "/in/0-terse.md")
    }
    staged = {cfg.run_args.inputs for _, cfg in ran.reps}
    assert staged == {inputs}
    # docker -v reads a relative path as a named volume, not a directory.
    assert all(path is not None and path.is_absolute() for path in staged)
    assert (inputs / "0-terse.md").read_text() == "Be terse.\n"
    digest = "sha256:" + hashlib.sha256(b"Be terse.\n").hexdigest()
    assert {
        (tuple(r.nare_args), tuple(r.nare_files.items())) for r in results_of(tmp_path)
    } == {(("--system-file", recorded), ((recorded, digest),))}


def test_a_double_dash_with_nothing_after_it_is_the_default_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = argued(tmp_path, monkeypatch)
    assert main([*RUN, "--"]) == 0
    assert ran.reps
    assert all(cfg.run_args == RunArgs() for _, cfg in ran.reps)


@pytest.mark.parametrize(
    ("case_args", "run_args", "named"),
    [
        ('["--tools", "read,teleport"]', [], "case argued: --tools names 'teleport'"),
        ('["--model", "x"]', [], "case argued: --model is reserved for"),
        ('["--system", "Be terse."]', [], "case argued: --system is a run argument"),
        (
            "",
            ["--schema", "a.json"],
            "run arguments: --schema names a path in the work tree",
        ),
        ("", ["--max-tok", "5"], "run arguments: unknown flag '--max-tok'"),
        (
            '["--context-window", "8000"]',
            ["--context-window", "16000"],
            "--context-window is in the run arguments and in case argued's args",
        ),
        ("", ["--system-file", "absent.md"], "--system-file absent.md: cannot read it"),
        ("", ["--system", "a\udcffb"], "run arguments: 'a\\udcffb' is not valid UTF-8"),
    ],
)  # fmt: skip
def test_run_refuses_arguments_before_building_anything(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case_args: str,
    run_args: list[str],
    named: str,
) -> None:
    ran = argued(tmp_path, monkeypatch, case_args)
    monkeypatch.chdir(tmp_path)
    assert main([*RUN, "--", *run_args]) == 2
    assert (ran.builds, ran.reps) == (0, [])
    assert named in capsys.readouterr().err


def test_a_typo_is_reported_before_the_key_is_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    argued(tmp_path, monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert main([*RUN, "--", "--max-tok", "5"]) == 2
    err = capsys.readouterr().err
    assert "run arguments: unknown flag '--max-tok'" in err
    assert "ANTHROPIC_API_KEY" not in err


def test_run_refuses_a_prompt_copy_it_cannot_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = argued(tmp_path, monkeypatch)
    (tmp_path / "terse.md").write_text("Be terse.\n")
    # A file where the run's evidence directory goes: the copy cannot land.
    results = tmp_path / "benchmarks" / "results"
    results.mkdir()
    (results / STAMP).write_text("")
    monkeypatch.chdir(tmp_path)
    assert main([*RUN, "--", "--system-file", "terse.md"]) == 2
    assert (ran.builds, ran.reps) == (0, [])
    err = capsys.readouterr().err
    assert (
        "bench: run arguments: cannot copy the prompt files into "
        f"{results / STAMP / 'inputs'}"
    ) in err


def test_verify_refuses_a_case_whose_args_nare_would_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = argued(tmp_path, monkeypatch, '["--no-such-flag"]')
    assert main(["verify"]) == 2
    assert ran.builds == 0
    assert "case argued: unknown flag '--no-such-flag'" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["compare", "--model", "m", "--"],
        ["bless", "--model", "m", "--", "--effort", "low"],
        ["verify", "--", "--effort", "low"],
        ["models", "--"],
        ["compare-judges", "--"],
    ],
)
def test_only_run_takes_arguments_for_nare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    ran = argued(tmp_path, monkeypatch)
    assert main(argv) == 2
    assert ran.builds == 0
    assert f"bench: {argv[0]} takes no arguments for nare" in capsys.readouterr().err


def test_run_help_shows_where_the_arguments_for_nare_go(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["run", "--help"])
    assert stopped.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: bench run [options] [-- NARE_ARGS ...]\n")
    # The options are still all listed below the usage line.
    assert "--allow-arg-change" in out
    assert (
        "Everything after -- goes to nare run as the run arguments, for every "
        "selected case"
    ) in " ".join(out.split())


def test_the_matrix_takes_no_run_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = argued(tmp_path, monkeypatch)
    assert main(["run", "--matrix", "--", "--effort", "low"]) == 2
    assert ran.builds == 0
    assert "--matrix runs each baseline's own run arguments" in capsys.readouterr().err


DIGEST = "sha256:" + "ab" * 32


def blessed_with(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *nare_args: str,
    files: dict[str, str] | None = None,
) -> Path:
    """Model m, blessed on openai and judged by j, with run arguments `nare_args`."""
    baselines = results_dir(tmp_path, monkeypatch)
    baselines.mkdir()
    path = baseline_path(baselines, "m", "smoke")
    meta = BaselineMeta(
        "t", "abc1234", "openai", "m", "j", nare_args=nare_args, nare_files=files or {}
    )
    path.write_text(dumps_baseline(meta, [summary("edit-docstring", 1.0)]))
    return path


def ran_with(
    *nare_args: str, files: dict[str, str] | None = None, **change: Any
) -> RepRecord:
    ran = replace(
        record("edit-docstring", model="m"),
        provider="openai",
        judge_model="j",
        nare_args=list(nare_args),
        nare_files=files or {},
    )
    return replace(ran, **change)


BASE = Baseline(BaselineMeta("t", "abc1234", "anthropic", "m", "m"), {})


@pytest.mark.parametrize(
    ("blessed", "now", "changed"),
    [
        ((), (), False),
        (
            ("--effort", "low", "--context-window", "16000"),
            ("--context-window=16000", "--effort=low"),
            False,
        ),
        ((), ("--effort", "low"), True),
        (("--effort", "low"), ("--effort", "high"), True),
    ],
)
def test_a_variant_change_is_by_key_not_by_text(
    blessed: tuple[str, ...], now: tuple[str, ...], changed: bool
) -> None:
    baseline = Baseline(replace(BASE.meta, nare_args=blessed), {})
    config = replace(CONFIG, run_args=RunArgs(recorded=now))
    assert args_changed(baseline, config, False) is changed
    assert args_changed(baseline, config, True) is False


def test_a_run_file_is_compared_by_digest_not_by_path() -> None:
    meta = replace(
        BASE.meta,
        nare_args=("--system-file", "a/p.md"),
        nare_files={"a/p.md": "sha256:aa"},
    )
    moved = RunArgs(recorded=("--system-file", "b/p.md"), files={"b/p.md": "sha256:aa"})
    edited = RunArgs(
        recorded=("--system-file", "a/p.md"), files={"a/p.md": "sha256:bb"}
    )
    assert not args_changed(Baseline(meta, {}), replace(CONFIG, run_args=moved), False)
    assert args_changed(Baseline(meta, {}), replace(CONFIG, run_args=edited), False)


@pytest.mark.parametrize(
    ("blessed", "files", "hint"),
    [
        (("--effort", "low", "--system", "Be terse."), {},
         "bench: to match the baseline, run with: -- --effort low --system "
         "'Be terse.'\n"),
        (("--system-file", "a/p.md"), {"a/p.md": "sha256:aa"},
         "bench: to match the baseline, run from the checkout's root with: -- "
         "--system-file a/p.md\n"),
    ],
)  # fmt: skip
def test_a_variant_refusal_says_which_arguments_match_the_baseline(
    capsys: pytest.CaptureFixture[str],
    blessed: tuple[str, ...],
    files: dict[str, str],
    hint: str,
) -> None:
    baseline = Baseline(replace(BASE.meta, nare_args=blessed, nare_files=files), {})
    assert args_changed(baseline, CONFIG, False)
    assert capsys.readouterr().err.endswith(hint)


def test_against_the_default_variant_the_refusal_says_to_drop_the_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = replace(CONFIG, run_args=RunArgs(recorded=("--effort", "low")))
    assert args_changed(BASE, config, False)
    err = capsys.readouterr().err
    assert err.endswith(
        "bench: to match the baseline, drop the -- and the arguments after it\n"
    )
    assert "run with:" not in err


@pytest.mark.parametrize(
    ("flags", "code"),
    [([], 2), (["--allow-model-change"], 2), (["--allow-arg-change"], 0)],
)
def test_compare_refuses_results_from_another_variant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
    code: int,
) -> None:
    blessed_with(tmp_path, monkeypatch)
    write_results(tmp_path, [ran_with("--context-window", "16000")])
    assert main(["compare", "--model", "m", *flags]) == code
    captured = capsys.readouterr()
    if code == 2:
        assert (
            "baseline was blessed with run arguments none; "
            "this run used --context-window 16000"
        ) in captured.err
    else:
        assert captured.out.startswith(
            "baseline run arguments: none\n"
            "results run arguments:  --context-window 16000\n"
        )


def test_allow_arg_change_does_not_cover_a_judge_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    blessed_with(tmp_path, monkeypatch)
    write_results(tmp_path, [ran_with("--effort", "low", judge_model="other")])
    assert main(["compare", "--model", "m", "--allow-arg-change"]) == 2
    assert "baseline was blessed on provider 'openai'" in capsys.readouterr().err
    both = ["--allow-arg-change", "--allow-model-change"]
    assert main(["compare", "--model", "m", *both]) == 0


def test_results_from_before_compare_as_the_default_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    blessed_with(tmp_path, monkeypatch)
    # A line as results files were written before #156: none of its keys.
    payload = asdict(ran_with())
    for key in (
        "case_args",
        "nare_args",
        "nare_files",
        "context_window",
        "compactions",
    ):
        del payload[key]
    results = tmp_path / "benchmarks" / "results"
    results.mkdir()
    (results / f"{STAMP}.jsonl").write_text(json.dumps(payload) + "\n")
    assert main(["compare", "--model", "m"]) == 0
    # One variant on both sides, so the table carries no note.
    assert "run arguments" not in capsys.readouterr().out


MODEL_REFUSAL = "baseline was blessed on provider 'openai'"
VARIANT_REFUSAL = (
    "baseline was blessed with run arguments none; this run used --effort low"
)


def test_run_prints_both_refusals_when_the_judge_and_the_variant_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Each refusal names its own override: shown one at a time, the second
    # appears only after the first override is added.
    ran = matrix(tmp_path, monkeypatch, NANO)
    other_judge = [
        "run", "--case", "edit-docstring", "--model", "gpt-5-nano",
        "--provider", "openai", "--judge-model", "j",
    ]  # fmt: skip
    assert main([*other_judge, "--", "--effort", "low"]) == 2
    assert ran == []
    err = capsys.readouterr().err
    assert MODEL_REFUSAL in err
    assert VARIANT_REFUSAL in err


def test_compare_prints_both_refusals_when_the_judge_and_the_variant_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    blessed_with(tmp_path, monkeypatch)
    write_results(tmp_path, [ran_with("--effort", "low", judge_model="other")])
    assert main(["compare", "--model", "m"]) == 2
    err = capsys.readouterr().err
    assert MODEL_REFUSAL in err
    assert VARIANT_REFUSAL in err


def test_compare_matrix_prints_every_refusal_of_every_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = matrix(tmp_path, monkeypatch, HAIKU, NANO)
    for meta, change in (
        (HAIKU, {"nare_args": ["--effort", "low"], "max_tokens": 32000}),
        (NANO, {"judge_model": "j"}),
    ):
        rec = replace(
            record("edit-docstring", model=meta.model),
            provider=meta.provider,
            judge_model=meta.judge_model,
        )
        write_results(
            tmp_path, [replace(rec, **change)], f"{MATRIX_STAMP}.{meta.model}"
        )
    assert main(["compare", "--matrix"]) == 2
    assert ran == []
    # Each names its baseline, since one model's variant refusal does not.
    err = capsys.readouterr().err
    assert "bench: claude-haiku-5-5.smoke.toml: baseline was blessed on " in err
    assert f"bench: claude-haiku-5-5.smoke.toml: {VARIANT_REFUSAL}" in err
    assert "bench: gpt-5-nano.smoke.toml: baseline was blessed on " in err
    assert "gpt-5-nano.smoke.toml: baseline was blessed with run arguments" not in err
    # The matrix takes no --, so its way to match is to run it again.
    assert (
        "bench: claude-haiku-5-5.smoke.toml: to match the baseline, run the matrix "
        "again, which runs each baseline's own arguments\n"
    ) in err


def test_run_refuses_another_variant_before_building_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = matrix(tmp_path, monkeypatch, NANO)
    single = [
        "run", "--case", "edit-docstring", "--model", "gpt-5-nano",
        "--provider", "openai", "--judge-model", SONNET,
    ]  # fmt: skip
    assert main([*single, "--", "--effort", "low"]) == 2
    assert main([*single, "--allow-model-change", "--", "--effort", "low"]) == 2
    assert ran == []
    err = capsys.readouterr().err
    assert (
        "baseline was blessed with run arguments none; this run used --effort low"
        in err
    )
    assert main([*single, "--allow-arg-change", "--", "--effort", "low"]) == 0
    assert [c.run_args.recorded for c in ran[1:]] == [("--effort", "low")]
    assert "results run arguments:  --effort low" in capsys.readouterr().out


def test_allow_arg_change_does_not_let_run_past_a_judge_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = matrix(tmp_path, monkeypatch, NANO)
    other_judge = [
        "run", "--case", "edit-docstring", "--model", "gpt-5-nano",
        "--provider", "openai", "--judge-model", "j",
    ]  # fmt: skip
    assert main([*other_judge, "--allow-arg-change", "--", "--effort", "low"]) == 2
    assert ran == []
    assert "baseline was blessed on provider 'openai'" in capsys.readouterr().err
    both = ["--allow-arg-change", "--allow-model-change"]
    assert main([*other_judge, *both, "--", "--effort", "low"]) == 0
    # Both sides' arguments, directly above the table they qualify.
    assert (
        "baseline run arguments: none\nresults run arguments:  --effort low\ncase "
    ) in capsys.readouterr().out


def test_bless_writes_the_variant_the_results_were_run_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baselines = results_dir(tmp_path, monkeypatch)
    terse = "benchmarks/prompts/terse.md"
    write_results(tmp_path, [ran_with("--system-file", terse, files={terse: DIGEST})])
    assert main(["bless", "--model", "m"]) == 0
    meta = load_baseline(baseline_path(baselines, "m", "smoke")).meta
    assert (meta.nare_args, meta.nare_files) == (
        ("--system-file", terse),
        {terse: DIGEST},
    )


def test_bless_will_not_keep_cases_measured_on_another_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    baselines = results_dir(tmp_path, monkeypatch)
    baselines.mkdir()
    blessed = baseline_path(baselines, "gpt-5-nano", "smoke")
    blessed.write_text(dumps_baseline(NANO, [summary(c, 1.0) for c in KEPT]))
    before = blessed.read_text()
    ran = replace(
        record("edit-docstring", model="gpt-5-nano"),
        provider="openai",
        judge_model=SONNET,
        nare_args=["--effort", "low"],
    )
    write_results(tmp_path, [ran])
    assert main(["bless", "--model", "gpt-5-nano"]) == 2
    assert main(["bless", "--model", "gpt-5-nano", "--allow-model-change"]) == 2
    assert blessed.read_text() == before
    err = capsys.readouterr().err
    assert "multi-file-rename, read-only-question" in err
    assert "--allow-arg-change" in err
    assert main(["bless", "--model", "gpt-5-nano", "--allow-arg-change"]) == 0
    assert load_baseline(blessed).meta.nare_args == ("--effort", "low")


def test_allow_arg_change_does_not_let_bless_relabel_the_cases_it_keeps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    baselines = results_dir(tmp_path, monkeypatch)
    baselines.mkdir()
    blessed = baseline_path(baselines, "gpt-5-nano", "smoke")
    blessed.write_text(dumps_baseline(NANO, [summary(c, 1.0) for c in KEPT]))
    before = blessed.read_text()
    # Another variant and, unasked for, another judge.
    ran = replace(
        record("edit-docstring", model="gpt-5-nano"),
        provider="openai",
        judge_model="j",
        nare_args=["--effort", "low"],
    )
    write_results(tmp_path, [ran])
    assert main(["bless", "--model", "gpt-5-nano", "--allow-arg-change"]) == 2
    assert blessed.read_text() == before
    assert "under these results' labels" in capsys.readouterr().err
    both = ["--allow-arg-change", "--allow-model-change"]
    assert main(["bless", "--model", "gpt-5-nano", *both]) == 0
    meta = load_baseline(blessed).meta
    assert (meta.judge_model, meta.nare_args) == ("j", ("--effort", "low"))


def test_bless_prints_both_refusals_for_the_cases_it_keeps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    baselines = results_dir(tmp_path, monkeypatch)
    baselines.mkdir()
    blessed = baseline_path(baselines, "gpt-5-nano", "smoke")
    blessed.write_text(dumps_baseline(NANO, [summary(c, 1.0) for c in KEPT]))
    before = blessed.read_text()
    ran = replace(
        record("edit-docstring", model="gpt-5-nano"),
        provider="openai",
        judge_model="j",
        nare_args=["--effort", "low"],
    )
    write_results(tmp_path, [ran])
    assert main(["bless", "--model", "gpt-5-nano"]) == 2
    assert blessed.read_text() == before
    err = capsys.readouterr().err
    assert "under these results' labels" in err
    assert "under these results' run arguments" in err
    # Matching the old variant is not what a bless is after; it says what is.
    assert "to match the baseline" not in err


def test_bless_may_move_a_baseline_to_another_variant_when_it_replaces_every_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baselines = results_dir(tmp_path, monkeypatch)
    baselines.mkdir()
    blessed = baseline_path(baselines, "gpt-5-nano", "smoke")
    blessed.write_text(dumps_baseline(NANO, [summary("edit-docstring", 1.0)]))
    ran = replace(
        record("edit-docstring", model="gpt-5-nano"),
        provider="openai",
        judge_model=SONNET,
        nare_args=["--effort", "low"],
    )
    write_results(tmp_path, [ran])
    assert main(["bless", "--model", "gpt-5-nano"]) == 0
    assert load_baseline(blessed).meta.nare_args == ("--effort", "low")


@pytest.mark.parametrize(
    "flags", [[], ["--allow-arg-change"], ["--allow-model-change"]]
)
def test_bless_refuses_a_run_file_outside_the_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
) -> None:
    baselines = results_dir(tmp_path, monkeypatch)
    outside = "/elsewhere/terse.md"
    write_results(
        tmp_path, [ran_with("--system-file", outside, files={outside: DIGEST})]
    )
    assert main(["bless", "--model", "m", *flags]) == 2
    assert "outside the checkout" in capsys.readouterr().err
    assert not baseline_path(baselines, "m", "smoke").exists()


TERSE_PATH = "benchmarks/prompts/terse.md"
TERSE_DIGEST = "sha256:" + hashlib.sha256(b"Be terse.\n").hexdigest()


def write_terse(tmp_path: Path, text: str = "Be terse.\n") -> None:
    prompt = tmp_path / TERSE_PATH
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(text)


def test_the_matrix_runs_each_model_with_its_baselines_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    nano = replace(
        NANO,
        nare_args=("--effort", "low", "--system-file", TERSE_PATH),
        nare_files={TERSE_PATH: TERSE_DIGEST},
    )
    ran = matrix(tmp_path, monkeypatch, HAIKU, nano)
    write_terse(tmp_path)
    assert main(["run", "--matrix", "--case", "edit-docstring"]) == 0
    by_model = {c.model: c.run_args for c in ran[1:]}
    assert by_model["claude-haiku-5-5"] == RunArgs()
    staged = by_model["gpt-5-nano"]
    assert (staged.recorded, staged.files) == (nano.nare_args, nano.nare_files)
    assert staged.passed == ("--effort", "low", "--system-file", "/in/0-terse.md")
    results = tmp_path / "benchmarks" / "results"
    assert staged.inputs == results / f"{MATRIX_STAMP}.gpt-5-nano" / "inputs"
    note = capsys.readouterr().out.splitlines()[-1]
    assert note.endswith(
        "Run arguments: claude-haiku-5-5: none; gpt-5-nano: --effort low "
        f"--system-file {TERSE_PATH} ({TERSE_PATH} {TERSE_DIGEST[:19]})."
    )


@pytest.mark.parametrize(
    ("nare_args", "files", "prompt", "named"),
    [
        (("--system-file", TERSE_PATH), {TERSE_PATH: TERSE_DIGEST}, "Be verbose.\n",
         f"{TERSE_PATH} no longer has the digest the baseline measured"),
        (("--system-file", TERSE_PATH), {TERSE_PATH: TERSE_DIGEST}, None,
         f"--system-file {TERSE_PATH}: cannot read it"),
        (("--no-such-flag",), {}, None, "unknown flag '--no-such-flag'"),
    ],
)  # fmt: skip
def test_a_baseline_whose_arguments_cannot_run_stops_the_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    nare_args: tuple[str, ...],
    files: dict[str, str],
    prompt: str | None,
    named: str,
) -> None:
    nano = replace(NANO, nare_args=nare_args, nare_files=files)
    ran = matrix(tmp_path, monkeypatch, HAIKU, nano)
    if prompt is not None:
        write_terse(tmp_path, prompt)
    assert main(["run", "--matrix"]) == 2
    assert ran == []
    err = capsys.readouterr().err
    assert "gpt-5-nano.smoke.toml" in err
    assert named in err


def test_no_flag_runs_the_matrix_on_a_prompt_its_baseline_did_not_measure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # An edited prompt is a new experiment: a single run with -- measures it.
    nano = replace(
        NANO,
        nare_args=("--system-file", TERSE_PATH),
        nare_files={TERSE_PATH: TERSE_DIGEST},
    )
    ran = matrix(tmp_path, monkeypatch, HAIKU, nano)
    write_terse(tmp_path, "Be verbose.\n")
    for flags in (
        ["--allow-arg-change"],
        ["--allow-arg-change", "--allow-model-change"],
    ):
        assert main(["run", "--matrix", *flags]) == 2
        assert ran == []
        assert (
            f"bench: gpt-5-nano.smoke.toml: {TERSE_PATH} no longer has the digest "
            "the baseline measured; restore it, or bless that model again on the "
            "new file"
        ) in capsys.readouterr().err


def test_compare_matrix_refuses_results_from_another_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = matrix(tmp_path, monkeypatch, HAIKU, NANO)
    write_matrix_run(tmp_path, HAIKU, NANO, change={"nare_args": ["--effort", "low"]})
    assert main(["compare", "--matrix"]) == 2
    assert "--allow-arg-change" in capsys.readouterr().err
    assert main(["compare", "--matrix", "--allow-arg-change"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[-1].endswith(
        "Run arguments: claude-haiku-5-5: --effort low; gpt-5-nano: none."
    )
    # Both sides of the model that differs, above the table; none for the other.
    assert out.startswith(
        "claude-haiku-5-5:\n"
        "  baseline run arguments: none\n"
        "  results run arguments:  --effort low\n"
        "| case "
    )
    assert "gpt-5-nano:\n" not in out
    assert ran == []


def test_allow_model_change_does_not_let_compare_matrix_past_a_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    matrix(tmp_path, monkeypatch, HAIKU, NANO)
    write_matrix_run(tmp_path, HAIKU, NANO, change={"nare_args": ["--effort", "low"]})
    assert main(["compare", "--matrix", "--allow-model-change"]) == 2
    assert (
        "baseline was blessed with run arguments none; this run used --effort low"
    ) in capsys.readouterr().err


def test_the_baselines_table_names_each_baselines_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    matrix(tmp_path, monkeypatch, HAIKU, replace(NANO, nare_args=("--effort", "low")))
    assert main(["compare", "--matrix", "--baselines"]) == 0
    assert (
        capsys.readouterr()
        .out.splitlines()[-1]
        .endswith("Run arguments: claude-haiku-5-5: none; gpt-5-nano: --effort low.")
    )
