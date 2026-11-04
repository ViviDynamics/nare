"""bin/bench with arguments for nare: the __main__ half of #156."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import cast

import pytest

from benchmarks.runner.__main__ import read_results, run_rep, write_results
from benchmarks.runner.nare_args import RunArgs
from benchmarks.runner.sandbox import RunArtifacts
from benchmarks.tests.test_grade import COMPACTED, WINDOW
from benchmarks.tests.test_main import (
    CONFIG,
    DONE_LINE,
    fake_sandbox,
    record,
    sandboxed,
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
