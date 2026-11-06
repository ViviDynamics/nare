"""--compaction summary: the model's own summary of old turns, before elision.

Issue #42. The spec is docs/superpowers/specs/2026-11-05-summary-compaction-design.md.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from fake_provider import FakeProvider, StreamingProvider, text_reply
from nare.compact import (
    SUMMARY_PREFIX,
    context_chars,
    estimate,
    summary_instruction,
)
from nare.events import Event
from nare.loop import RetryPolicy, run, step
from nare.session import Message, Session, Usage, new_session
from nare.tools import Policy, approve_all
from nare.transport import ProviderStatusError, Reply, StopReason
from test_compact import THINKING, transcript, without_thinking

WINDOW = 10000
IMAGE = {
    "type": "image",
    "width": 8,
    "height": 8,
    "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="},
}


def at_trigger() -> Session:
    """transcript() as if its last request sent messages 0 to 10 and measured
    7500 tokens: the estimate is 8571.5, past 80% of WINDOW. A summary replaces
    messages 1 to 8 (turns 1 to 4) and keeps turns 5 and 6."""
    s = new_session("go", session_id="s")
    s.messages = transcript()
    s.last_input_tokens = 7500
    s.last_input_messages = 11
    s.last_input_chars = context_chars(s.messages[:11])
    return s


def chat() -> Session:
    """A tool-free session grown by resumes: six long answers to "go on". Its
    estimate, 10727.25, reaches WINDOW, and elision has nothing to elide."""
    s = new_session("go", session_id="c")
    for turn in range(1, 7):
        answer = {"type": "text", "text": f"answer {turn} " + "a" * 7000}
        s.messages += [
            Message("assistant", [answer]),
            Message("user", [{"type": "text", "text": "go on"}]),
        ]
    return s


def summary_reply(
    text: str = "Progress: read a five times.",
    *,
    stop_reason: StopReason = "end_turn",
) -> Reply:
    return Reply(
        content=[{"type": "text", "text": text}],
        tool_calls=[],
        usage=Usage(input=4000, output=60, cache_read=500),
        stop_reason=stop_reason,
    )


class FailsFirst(FakeProvider):
    """Raises `error` on its first request, then replays as FakeProvider."""

    def __init__(self, error: Exception, replies: list[Reply]) -> None:
        super().__init__(replies)
        self.error: Exception | None = error

    async def turn(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        tool_choice: str | None = None,
    ) -> Reply:
        if self.error is not None:
            error, self.error = self.error, None
            self.calls.append(
                ([Message(m.role, list(m.content)) for m in messages], list(tools))
            )
            self.choices.append(tool_choice)
            raise error
        return await super().turn(messages, tools, tool_choice=tool_choice)


async def no_sleep(seconds: float) -> None:
    return None


async def drain(s: Session, transport: Any, **options: Any) -> list[Event]:
    options.setdefault("context_window", WINDOW)
    return [event async for event in run(s, transport=transport, **options)]


async def test_elide_mode_sends_the_requests_it_sends_today() -> None:
    default = FakeProvider([text_reply("done")])
    elide = FakeProvider([text_reply("done")])
    first, second = at_trigger(), at_trigger()
    await drain(first, default)
    events = await drain(second, elide, compaction="elide")
    assert elide.calls == default.calls
    assert elide.choices == default.choices == [None]
    assert second.messages == first.messages
    [notice] = [e for e in events if "compaction" in e.detail]
    assert notice.detail["compaction"]["mode"] == "elide"
    assert notice.detail["compaction"]["summarized"] == 0
    assert notice.text.startswith("compacted: elided 2 tool results, dropped")


async def test_an_unknown_mode_is_refused_before_any_call() -> None:
    fake = FakeProvider([text_reply("done")])
    with pytest.raises(ValueError, match="compaction must be one of elide, summary"):
        await drain(at_trigger(), fake, compaction="hybrid")
    assert fake.calls == []


async def test_the_summary_request_is_the_old_turns_and_the_instruction() -> None:
    s = at_trigger()
    before = copy.deepcopy(s.messages)
    fake = FakeProvider([summary_reply(), text_reply("done")])
    await drain(s, fake, compaction="summary")
    request, tools = fake.calls[0]
    assert request[:8] == before[:8]
    instruction = {"type": "text", "text": summary_instruction(WINDOW // 10)}
    assert request[8].content == [*before[8].content, instruction]
    # The turn's own tools, with calls forbidden.
    assert tools == fake.calls[1][1] != []
    assert fake.choices == ["none", None]


async def test_the_summary_request_keeps_the_signed_thinking_it_was_sent() -> None:
    s = at_trigger()
    sent = copy.deepcopy(s.messages[: s.last_input_messages])
    fake = FakeProvider([summary_reply(), text_reply("done")])
    await drain(s, fake, compaction="summary")
    request = fake.calls[0][0]
    assert len([b for m in request for b in m.content if b.get("signature")]) == 4
    assert request[:8] == sent[:8]
    assert request[8].content[:-1] == sent[8].content


async def test_the_next_request_is_the_task_the_summary_and_the_last_turns() -> None:
    s = at_trigger()
    before = copy.deepcopy(s.messages)
    fake = FakeProvider([summary_reply("S"), text_reply("done")])
    events = await drain(s, fake, compaction="summary")
    request = fake.calls[1][0]
    assert request[0].content[0] == {"type": "text", "text": "task"}
    assert request[0].content[1]["text"].startswith(SUMMARY_PREFIX)
    assert request[0].content[1]["text"].endswith("\n\nS")
    assert request[1:] == [without_thinking(m) for m in before[9:]]
    assert not [b for m in request for b in m.content if b["type"] in THINKING]
    [notice] = [e for e in events if "compaction" in e.detail]
    assert notice.detail["compaction"]["mode"] == "summary"
    assert notice.detail["compaction"]["summarized"] == 4
    assert notice.detail["compaction"]["elided"] == 0
    assert notice.text == (
        "compacted: summarized 4 turns, dropped 2 thinking blocks, "
        "~8572 -> ~4300 tokens"
    )
    assert s.status == "done"


async def test_a_run_with_no_tools_sends_none_and_no_tool_choice() -> None:
    s = chat()
    assert estimate(s) >= WINDOW  # elide mode would stop with context
    fake = FakeProvider([summary_reply("S"), text_reply("done")])
    await drain(s, fake, compaction="summary", policy=Policy(tools=frozenset()))
    assert [tools for _, tools in fake.calls] == [[], []]
    assert fake.choices == [None, None]
    texts = [b["text"] for b in s.messages[0].content]
    assert texts[:5] == ["go", "go on", "go on", "go on", "go on"]
    assert s.status == "done"


async def test_the_summary_counts_in_usage_and_cost_but_is_not_a_turn() -> None:
    s = at_trigger()
    fake = FakeProvider([summary_reply(), text_reply("done")])
    events = await drain(s, fake, compaction="summary")
    assert s.turns == 1
    assert s.usage == Usage(input=4010, output=65, cache_read=500, cost=None)
    costs = [e for e in events if e.type == "cost"]
    assert costs[0].detail == {
        "input": 4000,
        "output": 60,
        "cache_read": 500,
        "cache_write": 0,
        "cost": None,
        "estimated": False,
        "total": 4560,
        "call": "compaction_summary",
    }
    assert "call" not in costs[1].detail
    # The summary measured another request: the boundaries are the turn's.
    assert s.last_input_tokens == 10


async def test_a_resent_summary_request_counts_in_retries() -> None:
    s = at_trigger()
    fake = FailsFirst(
        ProviderStatusError("HTTP 503", status_code=503),
        [summary_reply("S"), text_reply("done")],
    )
    retry = RetryPolicy(retries=1, seconds=10, sleep=no_sleep)
    await drain(s, fake, compaction="summary", retry=retry)
    assert s.retries == 1
    assert fake.choices == ["none", "none", None]
    assert s.summaries[0]["summary"] == "S"


async def test_a_streamed_run_sends_no_delta_from_the_summary() -> None:
    s = at_trigger()
    fake = StreamingProvider([summary_reply("S"), text_reply("done")])
    events = await drain(s, fake, compaction="summary")
    streamed = "".join(e.text for e in events if e.type == "progress" and not e.detail)
    assert streamed.count("on it") == 1  # the turn's delta, none from the summary
    assert s.summaries[0]["summary"] == "S"


async def test_the_turn_limit_summary_makes_no_summary_request() -> None:
    s = at_trigger()
    fake = FakeProvider([text_reply("what was done")])
    await step(
        s,
        fake,
        approve_all,
        Policy(),
        context_window=WINDOW,
        summary=True,
        compaction="summary",
    )
    assert fake.choices == ["none"]  # the turn-limit summary itself
    assert s.summaries == []


async def test_a_model_without_image_input_gets_no_image_in_the_request() -> None:
    s = at_trigger()
    s.messages[2].content[0]["image"] = copy.deepcopy(IMAGE)
    fake = FakeProvider([summary_reply("S"), text_reply("done")])
    await drain(s, fake, compaction="summary")
    [result] = fake.calls[0][0][2].content
    assert "image" not in result
    assert result["content"].endswith("does not accept image input]")
    assert "iVBORw0KGgo=" not in str(s.summaries)


class Capped(FakeProvider):
    """A transport with an output cap, as both vendor rails can have."""

    max_tokens = 300


async def test_the_bound_follows_the_transports_output_cap() -> None:
    fake = Capped([summary_reply(), text_reply("done")])
    await drain(at_trigger(), fake, compaction="summary")
    instruction = fake.calls[0][0][8].content[-1]["text"]
    assert instruction.endswith("Use at most 225 words.")


async def test_only_the_replys_text_becomes_the_summary() -> None:
    thought = {"type": "thinking", "thinking": "a plan", "signature": "sig"}
    reply = Reply(
        content=[thought, {"type": "text", "text": "S"}],
        tool_calls=[],
        usage=Usage(input=4000, output=60),
        stop_reason="end_turn",
    )
    s = at_trigger()
    fake = FakeProvider([reply, text_reply("done")])
    events = await drain(s, fake, compaction="summary")
    assert s.messages[0].content[-1]["text"].endswith("from the user.]\n\nS")
    assert "a plan" not in str(s.messages)
    assert not [e for e in events if e.type == "thinking"]
