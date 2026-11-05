from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from benchmarks.runner.case import Check, Judge
from benchmarks.runner.grade import (
    CheckResult,
    ContextUse,
    ResultLine,
    context_use,
    grade_bash_check,
    grade_result_check,
    infra_error,
    parse_stdout,
    rep_outcome,
)

EVENT = json.dumps({"type": "progress", "text": "working", "detail": {}})
RESULT = json.dumps(
    {
        "type": "result",
        "session_id": "abc",
        "status": "done",
        "questions": [],
        "usage": {"input": 100, "output": 20, "cache_read": 0, "cache_write": 0},
        "stop_reason": "end_turn",
        "turns": 4,
        "error": None,
    }
)


def test_reads_the_result_line_past_the_events() -> None:
    line = parse_stdout(f"{EVENT}\n{EVENT}\n{RESULT}\n")
    assert line is not None
    assert line.status == "done"
    assert line.turns == 4
    assert line.usage["input"] == 100
    assert line.stop_reason == "end_turn"
    assert line.error is None


def test_no_result_line_returns_none() -> None:
    """A run that never started emits no result line. That is an error, not
    a failure, and the caller needs to be able to tell."""
    assert parse_stdout(f"{EVENT}\n{EVENT}\n") is None


def test_empty_output_returns_none() -> None:
    assert parse_stdout("") is None


def test_non_json_noise_is_ignored() -> None:
    line = parse_stdout(f"warning: something\n{RESULT}\n")
    assert line is not None
    assert line.status == "done"


def test_a_truncated_final_line_returns_none() -> None:
    assert parse_stdout(f"{EVENT}\n{RESULT[:40]}") is None


def test_the_last_result_line_wins() -> None:
    second = RESULT.replace('"status": "done"', '"status": "blocked"')
    line = parse_stdout(f"{RESULT}\n{second}\n")
    assert line is not None
    assert line.status == "blocked"


def test_an_error_result_is_carried_through() -> None:
    raw = RESULT.replace('"status": "done"', '"status": "error"').replace(
        '"error": null', '"error": "stopped after 12 turns"'
    )
    line = parse_stdout(raw)
    assert line is not None
    assert line.status == "error"
    assert line.error == "stopped after 12 turns"


def test_missing_usage_fields_default_to_zero() -> None:
    raw = json.dumps({"type": "result", "status": "done", "turns": 1, "usage": {}})
    line = parse_stdout(raw)
    assert line is not None
    assert line.usage == {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


DONE = ResultLine(
    status="done",
    turns=4,
    usage={"input": 1, "output": 1, "cache_read": 0, "cache_write": 0},
    stop_reason="end_turn",
    error=None,
)


def test_result_check_matches_status() -> None:
    result = grade_result_check(Check(kind="result", status="done"), DONE)
    assert result.passed


def test_result_check_rejects_a_different_status() -> None:
    result = grade_result_check(Check(kind="result", status="blocked"), DONE)
    assert not result.passed
    assert "done" in result.detail


def test_result_check_enforces_a_turn_ceiling() -> None:
    assert grade_result_check(Check(kind="result", max_turns=4), DONE).passed
    assert not grade_result_check(Check(kind="result", max_turns=3), DONE).passed


def test_result_check_requires_both_when_both_are_given() -> None:
    check = Check(kind="result", status="done", max_turns=3)
    assert not grade_result_check(check, DONE).passed


def test_bash_check_passes_on_exit_zero() -> None:
    assert grade_bash_check(Check(kind="bash", cmd="pytest -q"), 0, "ok").passed


def test_bash_check_fails_on_nonzero_and_keeps_the_output() -> None:
    result = grade_bash_check(Check(kind="bash", cmd="pytest -q"), 1, "E   assert 0")
    assert not result.passed
    assert "assert 0" in result.detail


def test_rep_passes_when_checks_pass_and_there_is_no_judge() -> None:
    assert rep_outcome([CheckResult("bash: x", True, "")], None, None) == "pass"


def test_rep_fails_when_any_check_fails() -> None:
    checks = [CheckResult("a", True, ""), CheckResult("b", False, "")]
    assert rep_outcome(checks, None, None) == "fail"


def test_a_failed_check_is_not_rescued_by_a_perfect_judge() -> None:
    """Programmatic correctness gates. The judge can only ever make it worse."""
    judge = Judge(assertions=("a", "b"), min_met=2)
    assert rep_outcome([CheckResult("x", False, "")], judge, [True, True]) == "fail"


def test_judge_gate_can_fail_a_rep_whose_checks_passed() -> None:
    judge = Judge(assertions=("a", "b"), min_met=2)
    passed = [CheckResult("x", True, "")]
    assert rep_outcome(passed, judge, [True, False]) == "fail"
    assert rep_outcome(passed, judge, [True, True]) == "pass"


def test_a_judge_without_min_met_reports_but_does_not_gate() -> None:
    judge = Judge(assertions=("a", "b"), min_met=None)
    assert rep_outcome([CheckResult("x", True, "")], judge, [False, False]) == "pass"


def test_an_unanswerable_judge_is_an_error_only_when_it_gates() -> None:
    """A judge that cannot answer must not look like a bad answer."""
    gating = Judge(assertions=("a",), min_met=1)
    reporting = Judge(assertions=("a",), min_met=None)
    passed = [CheckResult("x", True, "")]
    assert rep_outcome(passed, gating, None) == "error"
    assert rep_outcome(passed, reporting, None) == "pass"


def test_a_caught_transport_failure_is_an_infra_error() -> None:
    def line(status: str, stop_reason: str | None) -> ResultLine:
        return ResultLine(status, 1, {}, stop_reason, "boom")

    assert infra_error(line("error", None))
    assert not infra_error(line("error", "max_turns"))
    assert not infra_error(line("error", "max_tokens"))
    assert not infra_error(line("done", "end_turn"))


def test_reads_the_questions_a_blocked_run_asked() -> None:
    blocked = json.loads(RESULT) | {
        "status": "blocked",
        "questions": ["Raise TIMEOUT from 30 to what?"],
    }
    line = parse_stdout(json.dumps(blocked))
    assert line is not None
    assert line.questions == ["Raise TIMEOUT from 30 to what?"]


def test_a_result_line_without_questions_reads_as_none_asked() -> None:
    for questions in ({}, {"questions": None}):
        payload = {k: v for k, v in json.loads(RESULT).items() if k != "questions"}
        line = parse_stdout(json.dumps(payload | questions))
        assert line is not None
        assert line.questions == []


WINDOW = json.dumps(
    {
        "type": "progress",
        "text": "context window 16000 (flag)",
        "detail": {"context_window": {"tokens": 16000, "source": "flag"}},
    }
)
COMPACTED = json.dumps(
    {
        "type": "progress",
        "text": "compacted: elided 3 tool results, ~13000 -> ~6000 tokens",
        "detail": {"compaction": {"elided": 3, "before": 13000.0, "after": 6000.0}},
    }
)


def test_the_window_and_the_compactions_come_from_the_stream() -> None:
    stream = "\n".join([WINDOW, EVENT, COMPACTED, "not json", COMPACTED, RESULT])
    assert context_use(stream) == ContextUse(
        window={"tokens": 16000, "source": "flag"}, compactions=2
    )


def test_the_window_and_the_compactions_come_from_real_nares_stream(
    tmp_path: Path,
) -> None:
    """WINDOW and COMPACTED are written by hand, and nare's contract does not
    describe progress details: a renamed key would read as no compaction, which
    looks like a variant that never compacted. So real nare runs here, on the
    fake provider, with no key and no network. Four reads of a 4000-character
    file put the estimate past 80% of a 4000-token window before the fifth
    turn, which is when nare compacts."""
    (tmp_path / "notes.txt").write_text("note " * 800, encoding="utf-8")
    read = {"tool_calls": [{"name": "read", "args": {"path": "notes.txt"}}]}
    script = tmp_path / "script.json"
    script.write_text(json.dumps({"replies": [read] * 4 + [{"text": "read it"}]}))
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from nare.cli import main; raise SystemExit(main())",
            "run",
            "--provider",
            "fake",
            "--replay",
            str(script),
            "--jsonl",
            "--yes",
            "--context-window",
            "4000",
            "--tools",
            "read",
            "--session",
            str(tmp_path / "session.json"),
            "read notes.txt",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={
            key: value
            for key, value in os.environ.items()
            if not key.endswith("_API_KEY") and not key.startswith("NARE_")
        },
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    line = parse_stdout(proc.stdout)
    assert line is not None and line.status == "done"
    use = context_use(proc.stdout)
    assert use.window == {"tokens": 4000, "source": "flag"}
    assert use.compactions >= 1


def test_a_stream_without_them_has_no_window_and_no_compactions() -> None:
    assert context_use(f"{EVENT}\n{RESULT}\n") == ContextUse(window=None, compactions=0)
    assert context_use("") == ContextUse(window=None, compactions=0)


def test_a_malformed_window_is_not_recorded() -> None:
    bad = json.dumps(
        {
            "type": "progress",
            "detail": {"context_window": {"tokens": "many", "source": "x"}},
        }
    )
    assert context_use(bad).window is None
