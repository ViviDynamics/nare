from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from benchmarks.runner.case import Case, Check, Judge
from benchmarks.runner.judge import JudgeError, build_prompt, parse_reply, score
from nare.session import Message, Usage
from nare.transport import Reply

CASE = Case(
    id="fix-failing-test",
    tier="smoke",
    prompt="Fix bar.py so the suite passes.",
    directory=Path("/nowhere"),
    max_turns=12,
    reps=3,
    timeout=600,
    checks=(Check(kind="bash", cmd="pytest -q"),),
    judge=Judge(
        assertions=("The fix is in bar.py.", "The test was not weakened."),
        min_met=2,
    ),
)


class FakeJudge:
    """A scripted transport, in the FakeProvider idiom the repo already uses."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[list[Message]] = []

    async def turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> Reply:
        self.calls.append(messages)
        assert tools == [], "the judge asks for no tools"
        return Reply(
            content=[{"type": "text", "text": self.text}],
            tool_calls=[],
            usage=Usage(input=10, output=5),
            stop_reason="end_turn",
        )


def test_prompt_carries_the_task_the_diff_and_the_assertions() -> None:
    prompt = build_prompt(CASE, "diff --git a/bar.py", "I fixed it.")
    assert "Fix bar.py so the suite passes." in prompt
    assert "diff --git a/bar.py" in prompt
    assert "The fix is in bar.py." in prompt
    assert "The test was not weakened." in prompt
    assert "I fixed it." in prompt


def test_parses_a_clean_json_reply() -> None:
    assert parse_reply('{"met": [true, false], "why": "second failed"}', 2) == [
        True,
        False,
    ]


def test_parses_json_wrapped_in_prose() -> None:
    """Small models pad their answers. That is not a judge failure."""
    reply = 'Here is my assessment:\n{"met": [true, true], "why": "ok"}\nDone.'
    assert parse_reply(reply, 2) == [True, True]


def test_parses_json_in_a_fenced_block() -> None:
    reply = '```json\n{"met": [false, true], "why": "x"}\n```'
    assert parse_reply(reply, 2) == [False, True]


def test_a_wrong_length_array_is_a_judge_failure() -> None:
    with pytest.raises(JudgeError, match="2"):
        parse_reply('{"met": [true], "why": "x"}', 2)


def test_unparseable_output_is_a_judge_failure() -> None:
    with pytest.raises(JudgeError):
        parse_reply("I think it looks fine, honestly.", 2)


def test_non_boolean_entries_are_a_judge_failure() -> None:
    with pytest.raises(JudgeError):
        parse_reply('{"met": ["yes", "no"], "why": "x"}', 2)


async def test_score_returns_the_booleans_and_the_reason() -> None:
    transport = FakeJudge('{"met": [true, false], "why": "the test was edited"}')
    met, why = await score(CASE, "a diff", "final text", transport=transport)
    assert met == [True, False]
    assert why == "the test was edited"
    assert len(transport.calls) == 1


async def test_score_raises_on_an_unusable_reply() -> None:
    with pytest.raises(JudgeError):
        await score(CASE, "d", "f", transport=FakeJudge("no json here"))
