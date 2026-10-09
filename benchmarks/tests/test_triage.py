from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from benchmarks.runner.grade import CheckResult
from benchmarks.runner.jev import estimated_tokens
from benchmarks.runner.judge import JudgeError
from benchmarks.runner.report import RepRecord
from benchmarks.runner.triage import (
    LABELS,
    STATE_BUDGET,
    TRIAGE_SYSTEM,
    cause_question,
    claimed_done,
    code_cause,
    message_entries,
    triage_reference,
    triage_state,
)
from benchmarks.tests.test_judge import FakeJudge

PASSED = CheckResult("bash: pytest -q", True, "")
FAILED = CheckResult("bash: pytest -q", False, "exit 1\n1 failed")


def failure(**changes: Any) -> RepRecord:
    base = RepRecord(
        case="fix-failing-test",
        rep=0,
        outcome="fail",
        checks=(FAILED,),
        judge_met=1,
        judge_why="",
        status="done",
        stop_reason="end_turn",
        turns=4,
    )
    return replace(base, **changes)


@pytest.mark.parametrize("stop_reason", ["max_turns", "budget", "context"])
def test_running_out_is_labelled_by_code(stop_reason: str) -> None:
    assert code_cause(failure(stop_reason=stop_reason), 3) == "ran_out"


def test_a_judge_gate_failure_is_labelled_by_code() -> None:
    rec = failure(checks=(PASSED,), judge_met=2)
    assert code_cause(rec, 3) == "judge_gate"


def test_a_failed_check_is_left_for_the_models() -> None:
    assert code_cause(failure(judge_met=0), 3) is None


def test_an_ungated_case_is_never_a_judge_gate_failure() -> None:
    assert code_cause(failure(checks=(PASSED,), judge_met=0), None) is None


def test_claimed_done_is_done_with_a_failing_check() -> None:
    assert claimed_done(failure())
    assert not claimed_done(failure(status="blocked"))
    assert not claimed_done(failure(checks=(PASSED,)))


def test_the_transcript_clips_text_and_tool_arguments() -> None:
    message = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "x" * 1500},
            {
                "type": "tool_use",
                "id": "t1",
                "name": "bash",
                "input": {"cmd": "y" * 1500},
            },
        ],
    }
    text, call = message_entries(message)
    assert text == {"role": "assistant", "type": "text", "text": "x" * 1000}
    assert call["tool"] == "bash"
    assert call["args"].startswith('{"cmd": "yyy')
    assert len(call["args"]) == 1000


def test_the_transcript_drops_thinking_and_replaces_images() -> None:
    message = {
        "role": "user",
        "content": [
            {"type": "thinking", "thinking": "secret plan"},
            {
                "type": "tool_result",
                "tool_use_id": "t1",
                "content": "read 1 image",
                "image": {"source": {"data": "iVBORw0KGgo="}},
            },
            {"type": "image", "source": {"data": "iVBORw0KGgo="}},
        ],
    }
    entries = message_entries(message)
    assert entries == [
        {"role": "user", "type": "tool_result", "text": "read 1 image\n[image]"},
        {"role": "user", "type": "image", "text": "[image]"},
    ]
    assert "secret plan" not in str(entries)
    assert "iVBOR" not in str(entries)


def text_message(role: str, text: str) -> dict[str, Any]:
    return {"role": role, "content": [{"type": "text", "text": text}]}


def test_a_small_state_keeps_the_whole_transcript() -> None:
    messages = [text_message("user", "Fix it."), text_message("assistant", "Done.")]
    state = triage_state("Fix it.", failure(), "a diff", "Done.", messages)
    assert state is not None
    assert state["failed_checks"] == [{"label": FAILED.label, "detail": FAILED.detail}]
    assert [e["text"] for e in state["transcript"]] == ["Fix it.", "Done."]


def test_a_long_transcript_keeps_the_first_user_message_and_drops_the_oldest() -> None:
    filler = "z" * 900
    messages = [text_message("user", "Fix it.")] + [
        text_message("assistant" if n % 2 else "user", f"{n} {filler}")
        for n in range(1, 200)
    ]
    state = triage_state("Fix it.", failure(), "a diff", "Done.", messages)
    assert state is not None
    assert estimated_tokens(state) <= STATE_BUDGET
    transcript = state["transcript"]
    assert transcript[0]["text"] == "Fix it."
    omitted = transcript[1]
    assert omitted["type"] == "omitted"
    dropped = int(omitted["text"].strip("[]").split()[0])
    assert transcript[2]["text"].startswith(f"{dropped + 1} ")
    assert transcript[-1]["text"].startswith("199 ")


def test_a_diff_too_large_to_fit_is_none() -> None:
    messages = [text_message("user", "Fix it.")]
    huge = "+" * (STATE_BUDGET * 4)
    assert triage_state("Fix it.", failure(), huge, "", messages) is None


def test_no_session_means_an_empty_transcript() -> None:
    state = triage_state("Fix it.", failure(), "a diff", "", [])
    assert state is not None
    assert state["transcript"] == []


def test_jev_and_the_reference_get_the_same_labels() -> None:
    assert cause_question()["criteria"] == LABELS
    for label, text in LABELS.items():
        assert f"- {label}: {text}" in TRIAGE_SYSTEM


async def test_the_reference_reply_is_parsed() -> None:
    judge = FakeJudge('Here you go: {"cause": "wrong_change"}')
    cause, attempts = await triage_reference({"task": "t"}, transport=judge)
    assert cause == "wrong_change"
    assert len(attempts) == 1
    assert '"task": "t"' in judge.calls[0][0].content[0]["text"]


async def test_an_unknown_label_is_a_judge_error_after_two_attempts() -> None:
    # The second reply's cause is a list: unhashable, so a bare `in LABELS`
    # would raise TypeError instead of JudgeError.
    judge = FakeJudge('{"cause": "flaky"}', '{"cause": ["no_change"]}')
    with pytest.raises(JudgeError, match="unknown cause") as info:
        await triage_reference({"task": "t"}, transport=judge)
    assert len(info.value.attempts) == 2


async def test_an_empty_reply_is_retried_once() -> None:
    judge = FakeJudge("", '{"cause": "no_change"}')
    cause, attempts = await triage_reference({"task": "t"}, transport=judge)
    assert cause == "no_change"
    assert [a.text for a in attempts] == ["", '{"cause": "no_change"}']
