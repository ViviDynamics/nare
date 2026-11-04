"""A reply the model cut off, at its output cap or by repeating itself, is
corrected within a budget of cut-offs, and ends the run once that is spent.
"""

from __future__ import annotations

import json
from typing import Any

from fake_provider import FakeProvider, StreamingProvider, text_reply
from nare.contract import describe
from nare.events import Event
from nare.loop import run
from nare.repetition import MAX_PERIOD, REPEAT_SPAN
from nare.session import Session, Usage, new_session, reopen
from nare.tools import Policy
from nare.transport import Reply, ToolCall


def test_the_contract_lists_repetition_as_a_limit_that_ends_in_error() -> None:
    rows = {row["stop_reason"]: row for row in describe()["stop_reasons"]}

    assert rows["repetition"] == {
        "stop_reason": "repetition",
        "kind": "limit",
        "statuses": ["error"],
    }
    assert rows["max_tokens"]["kind"] == "limit"


def test_the_contract_describes_the_cut_off_budget() -> None:
    cut_offs = describe()["cut_off_replies"]

    assert cut_offs == {
        "flag": "--max-cutoffs",
        "default": 2,
        "per": "invocation",
        "stop_reasons": ["max_tokens", "repetition"],
        "event": "progress",
        "event_detail": "cutoff",
        "result_field": "cutoffs",
        "session_field": "cutoffs",
        "budget_keys": ["cutoffs", "used_cutoffs"],
    }


def test_the_repetition_span_holds_at_least_eight_copies_of_the_longest_period() -> (
    None
):
    assert REPEAT_SPAN >= 8 * MAX_PERIOD


LOOPED = '{"findings":[' + " | 1" * 800


def looped_reply() -> Reply:
    return Reply(
        content=[{"type": "text", "text": LOOPED}],
        tool_calls=[],
        usage=Usage(input=10, output=4000),
        stop_reason="repetition",
    )


def empty_cut(output: int = 24576) -> Reply:
    return Reply(
        content=[],
        tool_calls=[],
        usage=Usage(input=10, output=output),
        stop_reason="max_tokens",
    )


def cut_off_events(events: list[Event]) -> list[dict[str, Any]]:
    return [e.detail["cutoff"] for e in events if "cutoff" in e.detail]


async def drive(
    session: Session, replies: list[Reply], **options: Any
) -> tuple[list[Event], FakeProvider]:
    fake = FakeProvider(replies)
    events = [event async for event in run(session, transport=fake, **options)]
    return events, fake


async def test_a_cut_off_reply_is_corrected_and_the_run_finishes() -> None:
    session = new_session("write it")

    events, fake = await drive(
        session,
        [text_reply("half an ans", stop_reason="max_tokens"), text_reply("the answer")],
        max_cutoffs=2,
    )

    assert (session.status, session.stop_reason) == ("done", "end_turn")
    assert session.cutoffs == 1
    assert cut_off_events(events) == [
        {
            "kind": "max_tokens",
            "output_tokens": 5,
            "attempt": 1,
            "limit": 2,
            "corrected": True,
        }
    ]
    # The second request carried the guidance, merged into the user turn.
    last_user = fake.calls[1][0][-1]
    assert last_user.role == "user"
    assert "cut off at the output limit" in last_user.content[-1]["text"]


async def test_a_repeating_reply_runs_none_of_its_tool_calls() -> None:
    session = new_session("fix the file")
    looping_with_call = Reply(
        content=[
            {"type": "text", "text": LOOPED},
            {"type": "tool_use", "id": "call_1", "name": "bash", "input": {}},
        ],
        tool_calls=[ToolCall(id="call_1", name="bash", args={"command": "rm -rf ."})],
        usage=Usage(input=10, output=4000),
        stop_reason="repetition",
    )

    await drive(
        session,
        [looping_with_call, text_reply("the file is unchanged")],
        max_cutoffs=2,
        policy=Policy(),
    )

    assert session.status == "done"
    results = [
        block
        for message in session.messages
        for block in message.content
        if block.get("type") == "tool_result"
    ]
    assert results[0]["is_error"] is True
    assert "not run" in results[0]["content"]
    assert session.tool_calls == 0


async def test_a_spent_budget_ends_the_run_with_the_cut_off_stop_reason() -> None:
    session = new_session("write it")

    events, _ = await drive(session, [looped_reply(), looped_reply()], max_cutoffs=1)

    assert session.status == "error"
    assert session.stop_reason == "repetition"
    assert "repeated" in (session.error or "")
    assert session.cutoffs == 2
    assert [c["corrected"] for c in cut_off_events(events)] == [True, False]


async def test_a_length_cut_with_no_content_counts_against_the_budget() -> None:
    session = new_session("write it")

    events, _ = await drive(session, [empty_cut(), text_reply("ok")], max_cutoffs=1)

    assert session.status == "done"
    assert cut_off_events(events)[0]["output_tokens"] == 24576


async def test_an_empty_cut_off_reply_leaves_no_empty_assistant_turn() -> None:
    session = new_session("write it")

    await drive(session, [empty_cut(), text_reply("ok")], max_cutoffs=1)

    assert not [m for m in session.messages if m.role == "assistant" and not m.content]
    assert session.status == "done"


async def test_zero_cut_offs_reproduces_the_error_on_the_first_cut() -> None:
    session = new_session("write it")

    events, fake = await drive(
        session,
        [text_reply("half", stop_reason="max_tokens"), text_reply("never asked")],
        max_cutoffs=0,
    )

    assert (session.status, session.stop_reason) == ("error", "max_tokens")
    assert "max_tokens" in (session.error or "")
    assert len(fake.calls) == 1
    assert [c["corrected"] for c in cut_off_events(events)] == [False]


async def test_without_a_budget_the_run_keeps_its_error_and_no_event() -> None:
    session = new_session("write it")

    events, fake = await drive(
        session, [text_reply("half", stop_reason="max_tokens"), text_reply("never")]
    )

    assert (session.status, session.stop_reason) == ("error", "max_tokens")
    assert len(fake.calls) == 1
    assert cut_off_events(events) == []


async def test_the_budget_is_per_invocation_and_the_count_is_the_session_total() -> (
    None
):
    session = new_session("write it")
    await drive(
        session,
        [text_reply("cut", stop_reason="max_tokens"), text_reply("ok")],
        max_cutoffs=1,
    )
    reopen(session, "again")

    await drive(
        session,
        [text_reply("cut", stop_reason="max_tokens"), text_reply("ok")],
        max_cutoffs=1,
    )

    assert session.status == "done"
    assert session.cutoffs == 2


async def test_a_valid_reply_with_eight_identical_items_is_not_cut() -> None:
    session = new_session("list them")

    await drive(session, [text_reply(json.dumps({"items": ["a"] * 8}))], max_cutoffs=2)

    assert session.status == "done"
    assert session.cutoffs == 0


async def test_a_streamed_cut_off_reply_is_corrected() -> None:
    session = new_session("write it")
    fake = StreamingProvider(
        [text_reply("cut", stop_reason="max_tokens"), text_reply("ok")]
    )

    events = [event async for event in run(session, transport=fake, max_cutoffs=2)]

    assert session.status == "done"
    assert [c["corrected"] for c in cut_off_events(events)] == [True]
