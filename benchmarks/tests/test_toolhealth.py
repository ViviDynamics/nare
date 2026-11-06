from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from benchmarks.runner.report import RepRecord
from benchmarks.runner.toolhealth import (
    COUNTED,
    model_health,
    read_evidence,
    render_health,
    rep_health,
    score_events,
    score_session,
)


def call(id_: str, name: str, input_: Any = None) -> dict[str, Any]:
    return {"type": "tool_use", "id": id_, "name": name, "input": input_ or {}}


def result(id_: str, text: str, *, is_error: bool = True) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": id_,
        "content": text,
        "is_error": is_error,
    }


def turn(*blocks: dict[str, Any]) -> dict[str, Any]:
    return {"role": "assistant", "content": list(blocks)}


def answers(*blocks: dict[str, Any]) -> dict[str, Any]:
    return {"role": "user", "content": list(blocks)}


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": value}


def session(*messages: dict[str, Any]) -> dict[str, Any]:
    return {"messages": list(messages)}


def count(messages: list[dict[str, Any]], kind: str) -> int:
    return score_session(session(*messages))[kind]


def test_a_malformed_argument_reply_is_an_argument_error() -> None:
    messages = [
        turn(call("1", "read")),
        answers(
            result("1", "tool arguments are not a complete JSON object; tool not run")
        ),
    ]
    counts = score_session(session(*messages))
    assert counts["argument_errors"] == 1
    assert counts["empty_arguments"] == 0  # a malformed call is not `{}` to a tool


def test_an_invalid_duplicate_key_reply_is_an_argument_error() -> None:
    messages = [
        turn(call("1", "read")),
        answers(
            result("1", "tool arguments are invalid: duplicate key path; tool not run")
        ),
    ]
    assert count(messages, "argument_errors") == 1


def test_a_failed_argument_check_is_an_argument_error() -> None:
    messages = [
        turn(call("1", "read", {"path": "a", "bogus": 1})),
        answers(result("1", "read: unknown parameter bogus; read takes path")),
    ]
    assert count(messages, "argument_errors") == 1


def test_empty_arguments_to_a_tool_with_required_parameters_are_counted() -> None:
    messages = [
        turn(call("1", "edit", {})),
        answers(
            result(
                "1",
                "edit: missing required parameter path; missing required parameter old",
            )
        ),
    ]
    counts = score_session(session(*messages))
    assert counts["argument_errors"] == 1
    assert counts["empty_arguments"] == 1


def test_a_missing_parameter_with_arguments_present_is_not_empty_arguments() -> None:
    messages = [
        turn(call("1", "read", {"other": 1})),
        answers(result("1", "read: missing required parameter path")),
    ]
    counts = score_session(session(*messages))
    assert counts["argument_errors"] == 1
    assert counts["empty_arguments"] == 0


def test_an_unknown_tool_name_is_counted() -> None:
    messages = [
        turn(call("1", "grep_files", {"q": "x"})),
        answers(
            result("1", "unknown tool 'grep_files'; this session allows: bash, read")
        ),
    ]
    counts = score_session(session(*messages))
    assert counts["unknown_tools"] == 1
    assert counts["argument_errors"] == 0


def test_a_disallowed_but_known_tool_is_not_counted_as_unknown() -> None:
    messages = [
        turn(call("1", "write", {"path": "a", "content": "b"})),
        answers(result("1", "write is not allowed in this session; allowed: read")),
    ]
    counts = score_session(session(*messages))
    assert counts["unknown_tools"] == 0 and counts["argument_errors"] == 0


def test_a_tool_failing_at_runtime_is_not_an_argument_error() -> None:
    messages = [
        turn(call("1", "read", {"path": "missing.py"})),
        answers(result("1", "no such file: missing.py")),
    ]
    counts = score_session(session(*messages))
    assert counts["argument_errors"] == 0


def test_markup_left_in_the_reply_text_is_counted() -> None:
    messages = [
        turn(text('<tool_call>{"name": "read"}</tool_call>')),
        turn(text('{"name": "read", "arguments": {"path": "a.py"}}')),
    ]
    assert count(messages, "markup_in_text") == 2


def test_prose_and_a_real_call_are_not_markup() -> None:
    messages = [
        turn(text("I will read the file now."), call("1", "read", {"path": "a"}))
    ]
    assert count(messages, "markup_in_text") == 0
    assert count(messages, "tool_calls") == 1


def test_a_json_object_that_is_not_shaped_like_a_call_is_not_markup() -> None:
    messages = [turn(text('The config is {"name": "api", "port": 8080}.'))]
    assert count(messages, "markup_in_text") == 0


def _event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "text": "", "detail": {}, **fields}


def test_a_turn_with_reasoning_and_no_answer_and_no_call_is_counted() -> None:
    events = [_event("thinking", text="hmm"), _event("cost", text="1 in / 2 out")]
    assert score_events(events) == {"turns": 1, "reasoning_no_answer": 1}


def test_reasoning_followed_by_an_answer_or_a_call_is_not_counted() -> None:
    events = [
        _event("thinking"),
        _event("progress", text="Done."),
        _event("cost"),
        _event("thinking"),
        _event("tool_use", text="read", tool_use_id="1"),
        _event("cost"),
    ]
    assert score_events(events) == {"turns": 2, "reasoning_no_answer": 0}


def test_a_turn_without_reasoning_is_not_counted_however_empty() -> None:
    assert score_events([_event("cost")]) == {"turns": 1, "reasoning_no_answer": 0}


def test_evidence_of_a_rep_is_read_from_its_directory(tmp_path: Path) -> None:
    (tmp_path / "session.json").write_text(
        json.dumps(
            session(
                turn(call("1", "edit", {})),
                answers(result("1", "edit: missing required parameter path")),
            )
        ),
        encoding="utf-8",
    )
    (tmp_path / "stdout.jsonl").write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                _event("thinking"),
                _event("tool_use", text="edit", tool_use_id="1"),
                _event("cost"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    health = rep_health(tmp_path)
    assert health is not None
    assert health["argument_errors"] == 1 and health["empty_arguments"] == 1
    assert health["turns"] == 1 and health["reasoning_no_answer"] == 0
    assert health["session"] == 1 and health["events"] == 1


def test_a_rep_with_no_evidence_is_unread_not_zero(tmp_path: Path) -> None:
    assert rep_health(tmp_path / "missing") is None


def test_a_rep_whose_transcript_never_saved_still_counts_its_turns(
    tmp_path: Path,
) -> None:
    (tmp_path / "stdout.jsonl").write_text(
        json.dumps(_event("cost")) + "\n", encoding="utf-8"
    )
    health = rep_health(tmp_path)
    assert health is not None
    assert health["session"] == 0 and health["events"] == 1 and health["turns"] == 1


def test_a_torn_last_line_of_the_stream_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "stdout.jsonl").write_text(
        json.dumps(_event("cost")) + '\n{"type": "thin', encoding="utf-8"
    )
    health = rep_health(tmp_path)
    assert health is not None and health["turns"] == 1


def _record(model: str, health: dict[str, int] | None, rep: int = 0) -> RepRecord:
    return RepRecord(
        case="c",
        rep=rep,
        outcome="pass",
        checks=(),
        judge_met=None,
        judge_why="",
        status="done",
        stop_reason="end_turn",
        turns=1,
        model=model,
        tool_health=health,
    )


def _health(**overrides: int) -> dict[str, int]:
    base = dict.fromkeys(COUNTED, 0) | {"session": 1, "events": 1}
    return base | overrides


def test_counts_are_summed_per_model_and_unread_reps_are_named() -> None:
    records = [
        _record("a", _health(argument_errors=2, tool_calls=5), rep=0),
        _record("a", None, rep=1),
        _record("b", _health(unknown_tools=1), rep=0),
    ]
    rows = {row.model: row for row in model_health(records)}
    assert rows["a"].reps == 2 and rows["a"].read == 1
    assert rows["a"].counts["argument_errors"] == 2
    assert rows["b"].counts["unknown_tools"] == 1


def test_render_names_each_kind_beside_its_model() -> None:
    text_out = render_health(model_health([_record("m", _health(markup_in_text=3))]))
    assert "tool-call health: m, 1 of 1 reps read" in text_out
    assert "tool-call markup in reply text 3" in text_out


def test_read_evidence_fills_only_the_records_without_counts(tmp_path: Path) -> None:
    rep_dir = tmp_path / "stamp" / "c-0"
    rep_dir.mkdir(parents=True)
    (rep_dir / "stdout.jsonl").write_text(
        json.dumps(_event("cost")) + "\n", encoding="utf-8"
    )
    bare = replace(_record("m", None), artifacts="stamp/c-0")
    kept = replace(_record("m", _health(turns=9)), artifacts="stamp/c-0")
    out = read_evidence(tmp_path, [bare, kept])
    assert out[0].tool_health is not None and out[0].tool_health["turns"] == 1
    assert out[1].tool_health is not None and out[1].tool_health["turns"] == 9
