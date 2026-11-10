"""The scripted transport a caller tests against: what it accepts, and how it
replays it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest

from nare.session import Message, Usage
from nare.transport import Delta, Reply, ToolCall, Transport, make_transport
from nare.transport.replay import ReplayTransport, load_script
from test_summary_compaction import at_trigger, drain

HELLO = [Message("user", [{"type": "text", "text": "hello"}])]

_check: Transport = ReplayTransport({"replies": [{"text": "ok"}]})


def turn(transport: ReplayTransport) -> Reply:
    return asyncio.run(transport.turn(HELLO, []))


@pytest.mark.parametrize(
    ("script", "message"),
    [
        ([], "a JSON object"),
        ({}, "replies"),
        ({"replies": []}, "at least one reply"),
        ({"replies": "ok"}, "replies must be a list"),
        ({"replies": [{"text": "ok"}], "extra": 1}, "unknown key 'extra'"),
        ({"replies": ["ok"]}, "reply 1 must be a JSON object"),
        ({"replies": [{"txt": "ok"}]}, "reply 1: unknown key 'txt'"),
        ({"replies": [{"text": 3}]}, "reply 1: text must be a string"),
        ({"replies": [{"thinking": 3}]}, "reply 1: thinking must be a string"),
        (
            {"replies": [{"text": "ok"}, {"stop_reason": "finished"}]},
            "reply 2: stop_reason must be one of",
        ),
        ({"replies": [{"error": "boom", "text": "x"}]}, "reply 1: error stands alone"),
        ({"replies": [{"error": 1}]}, "reply 1: error must be a string"),
        ({"replies": [{"tool_calls": {}}]}, "reply 1: tool_calls must be a list"),
        ({"replies": [{"tool_calls": [{}]}]}, "reply 1: tool call 1 needs a name"),
        (
            {"replies": [{"tool_calls": [{"name": "read", "args": []}]}]},
            "reply 1: tool call 1: args must be a JSON object",
        ),
        (
            {"replies": [{"tool_calls": [{"name": "read", "input": {}}]}]},
            "reply 1: tool call 1: unknown key 'input'",
        ),
        (
            {"replies": [{"tool_calls": [{"name": "a", "id": 1}]}]},
            "reply 1: tool call 1: id must be a string",
        ),
        (
            {
                "replies": [
                    {
                        "tool_calls": [
                            {"name": "a", "id": "x"},
                            {"name": "b", "id": "x"},
                        ]
                    }
                ]
            },
            "reply 1: tool call 2: id 'x' is already used",
        ),
        ({"replies": [{"usage": {"input": -1}}]}, "reply 1: usage.input"),
        ({"replies": [{"usage": {"input": True}}]}, "reply 1: usage.input"),
        ({"replies": [{"usage": {"tokens": 1}}]}, "reply 1: usage: unknown key"),
        ({"replies": [{"usage": 5}]}, "reply 1: usage must be a JSON object"),
        ({"replies": [{"cost": -0.5}]}, "reply 1: cost"),
        ({"replies": [{"cost": "free"}]}, "reply 1: cost"),
    ],
)
def test_a_malformed_script_is_refused_naming_where(script: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message.replace("(", r"\(")):
        load_script(script)


def test_replies_replay_in_order_with_defaults_filled_in() -> None:
    transport = ReplayTransport(
        {
            "replies": [
                {
                    "text": "reading",
                    "tool_calls": [{"name": "read", "args": {"path": "a"}}],
                },
                {"thinking": "hmm", "text": "done"},
            ]
        }
    )

    first = turn(transport)
    assert first.content == [
        {"type": "text", "text": "reading"},
        {"type": "tool_use", "id": "call_1_1", "name": "read", "input": {"path": "a"}},
    ]
    assert first.tool_calls == [
        ToolCall(id="call_1_1", name="read", args={"path": "a"})
    ]
    assert first.stop_reason == "tool_use"
    assert first.usage == Usage()
    assert first.cost is None

    second = turn(transport)
    assert second.content == [
        {"type": "thinking", "thinking": "hmm"},
        {"type": "text", "text": "done"},
    ]
    assert second.tool_calls == []
    assert second.stop_reason == "end_turn"


def test_a_reply_says_its_own_stop_reason_usage_cost_and_ids() -> None:
    transport = ReplayTransport(
        {
            "replies": [
                {
                    "text": "cut off mid",
                    "stop_reason": "max_tokens",
                    "usage": {"input": 7, "output": 3, "cache_read": 2},
                    "cost": 0.25,
                    "tool_calls": [{"id": "mine", "name": "list"}],
                }
            ]
        }
    )
    reply = turn(transport)
    assert reply.stop_reason == "max_tokens"
    assert reply.usage == Usage(input=7, output=3, cache_read=2)
    assert reply.cost == 0.25
    assert reply.tool_calls == [ToolCall(id="mine", name="list", args={})]


def test_a_scripted_error_fails_that_turn_as_a_transport_error_does() -> None:
    transport = ReplayTransport({"replies": [{"error": "connection reset"}]})
    with pytest.raises(RuntimeError, match="^connection reset$"):
        turn(transport)


def test_running_past_the_script_fails_the_turn_and_says_so() -> None:
    transport = ReplayTransport({"replies": [{"text": "only"}]})
    turn(transport)
    with pytest.raises(RuntimeError, match="no reply for turn 2: it holds 1"):
        turn(transport)


def test_streaming_sends_thinking_and_text_as_deltas_before_the_reply() -> None:
    transport = ReplayTransport(
        {"replies": [{"thinking": "hmm", "text": "hi"}]}, streaming=True
    )
    assert transport.streaming is True

    async def collect() -> list[Delta | Reply]:
        stream: AsyncGenerator[Delta | Reply, None] = transport.stream_turn(HELLO, [])
        return [item async for item in stream]

    items = asyncio.run(collect())
    assert items[:2] == [Delta("thinking", "hmm"), Delta("progress", "hi")]
    assert isinstance(items[2], Reply)
    assert items[2].content[-1] == {"type": "text", "text": "hi"}


def test_no_window_is_claimed_so_the_run_uses_its_flag_or_default() -> None:
    transport = ReplayTransport({"replies": [{"text": "ok"}]})
    assert asyncio.run(transport.context_window()) is None


def test_from_file_reads_json_and_make_transport_builds_one(tmp_path: Path) -> None:
    path = tmp_path / "script.json"
    path.write_text(json.dumps({"replies": [{"text": "from disk"}]}), encoding="utf-8")

    built = make_transport("fake", model="any", replay=path, streaming=True)
    assert isinstance(built, ReplayTransport)
    assert built.model == "any"
    assert built.streaming is True
    assert asyncio.run(built.turn(HELLO, [])).content == [
        {"type": "text", "text": "from disk"}
    ]


def test_make_transport_refuses_fake_without_a_script() -> None:
    with pytest.raises(ValueError, match="requires a replay script"):
        make_transport("fake", model="any")


def test_make_transport_refuses_a_script_for_a_real_provider(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="replay requires the fake provider"):
        make_transport("openai", model="any", replay=tmp_path / "s.json")


def test_a_file_that_is_not_json_is_refused_naming_it(tmp_path: Path) -> None:
    path = tmp_path / "script.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="script.json"):
        ReplayTransport.from_file(path)


def test_public_api_exports_it() -> None:
    import nare

    assert nare.ReplayTransport is ReplayTransport


async def test_a_summary_request_takes_the_next_reply() -> None:
    s = at_trigger()
    transport = ReplayTransport({"replies": [{"text": "S"}, {"text": "done"}]})
    await drain(s, transport, compaction="summary")
    assert s.status == "done"
    assert s.summaries[0]["summary"] == "S"


async def test_a_script_one_reply_short_names_the_request_it_lacks() -> None:
    s = at_trigger()
    transport = ReplayTransport({"replies": [{"text": "S"}]})
    await drain(s, transport, compaction="summary")
    assert (s.status, s.stop_reason) == ("error", None)
    assert s.error == "RuntimeError: replay script has no reply for turn 2: it holds 1"
    assert s.summaries[0]["summary"] == "S"


async def test_an_error_at_a_summary_request_fails_only_that_request() -> None:
    s = at_trigger()
    transport = ReplayTransport({"replies": [{"error": "boom"}, {"text": "done"}]})
    events = await drain(s, transport, compaction="summary")
    [asked] = [
        e.detail["compaction_summary"]
        for e in events
        if e.type == "progress" and "compaction_summary" in e.detail
    ]
    assert (asked["status"], asked["error"]) == ("failed", "RuntimeError: boom")
    # No reply arrived, so no cost: the one cost event is the turn's.
    assert [e.detail.get("call") for e in events if e.type == "cost"] == [None]
    assert (s.status, s.summaries) == ("done", [])


async def test_a_script_exhausted_at_a_summary_request_fails_the_turn_after() -> None:
    s = at_trigger()
    transport = ReplayTransport({"replies": [{"text": "used"}]})
    await transport.turn(HELLO, [])  # an earlier request took the only reply
    events = await drain(s, transport, compaction="summary")
    [asked] = [
        e.detail["compaction_summary"]
        for e in events
        if e.type == "progress" and "compaction_summary" in e.detail
    ]
    lacking = "RuntimeError: replay script has no reply for turn 2: it holds 1"
    assert (asked["status"], asked["error"]) == ("failed", lacking)
    assert [e for e in events if e.type == "cost"] == []
    assert (s.status, s.stop_reason, s.error) == ("error", None, lacking)
