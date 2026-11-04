"""bin/bench with arguments for nare: the __main__ half of #156."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import cast

import pytest

from benchmarks.runner.__main__ import (
    NeverStarted,
    main,
    read_results,
    run_case,
    run_rep,
    stopped_log,
    write_results,
)
from benchmarks.runner.case import Case
from benchmarks.runner.config import Config
from benchmarks.runner.nare_args import RunArgs
from benchmarks.runner.report import BaselineMeta, dumps_baseline
from benchmarks.runner.sandbox import RunArtifacts
from benchmarks.tests.test_grade import COMPACTED, WINDOW
from benchmarks.tests.test_judge import FakeJudge
from benchmarks.tests.test_main import (
    CONFIG,
    DONE_LINE,
    HAIKU,
    MATRIX_STAMP,
    NANO,
    fake_sandbox,
    matrix,
    record,
    results_dir,
    sandboxed,
    summary,
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
