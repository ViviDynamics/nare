from __future__ import annotations

import json

from benchmarks.runner.grade import parse_stdout

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
