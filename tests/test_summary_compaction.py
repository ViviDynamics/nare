"""--compaction summary: the model's own summary of old turns, before elision.

Issue #42. The spec is docs/superpowers/specs/2026-11-05-summary-compaction-design.md.
"""

from __future__ import annotations

import copy
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from fake_provider import (
    FakeProvider,
    StreamingProvider,
    StrictArityProvider,
    text_reply,
    tool_reply,
)
from nare import loop
from nare.compact import (
    SUMMARY_PREFIX,
    context_chars,
    estimate,
    plan_summary,
    summary_instruction,
)
from nare.events import Event
from nare.loop import RetryPolicy, SummaryTier, run, step
from nare.session import Message, Session, Usage, new_session, reopen
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


def summary_events(events: list[Event]) -> list[dict[str, Any]]:
    return [
        e.detail["compaction_summary"]
        for e in events
        if "compaction_summary" in e.detail
    ]


def one_turn_to_replace() -> Session:
    """chat() with three long answers: a summary replaces one turn and keeps
    the last two."""
    s = new_session("go", session_id="o")
    for turn in range(1, 4):
        answer = {"type": "text", "text": f"answer {turn} " + "a" * 12000}
        s.messages += [
            Message("assistant", [answer]),
            Message("user", [{"type": "text", "text": "go on"}]),
        ]
    return s


def test_the_summary_statuses_are_these_in_this_order() -> None:
    assert loop.SUMMARY_STATUSES == (
        "applied",
        "failed",
        "empty",
        "tool_call",
        "refused",
        "cut_off",
        "too_long",
    )


@pytest.mark.parametrize(
    ("reply", "status"),
    [
        (summary_reply("   "), "empty"),
        (tool_reply("read", {"path": "a"}), "tool_call"),
        (summary_reply("Progress: half", stop_reason="max_tokens"), "cut_off"),
        (summary_reply("Progress: half", stop_reason="repetition"), "cut_off"),
        (summary_reply("I can't help with that.", stop_reason="refusal"), "refused"),
        (summary_reply("x" * 30000), "too_long"),
        # A reply that is several things is the first of these, in the order
        # tool_call, refused, cut_off, empty, too_long.
        (tool_reply("read", {"path": "a"}, stop_reason="refusal"), "tool_call"),
        (summary_reply("", stop_reason="refusal"), "refused"),
        (summary_reply("  ", stop_reason="max_tokens"), "cut_off"),
        (summary_reply("x" * 30000, stop_reason="refusal"), "refused"),
    ],
)
async def test_a_summary_not_applied_leaves_the_session_as_elide_would(
    reply: Reply, status: str
) -> None:
    elided, summarized = at_trigger(), at_trigger()
    plain = FakeProvider([text_reply("done")])
    fake = FakeProvider([reply, text_reply("done")])
    await drain(elided, plain)
    events = await drain(summarized, fake, compaction="summary", max_cutoffs=2)
    [detail] = summary_events(events)
    assert detail["status"] == status
    assert fake.calls[1] == plain.calls[0]
    assert replace(summarized, usage=Usage()) == replace(elided, usage=Usage())
    # A call the model made is never run, and a cut-off summary is no cut-off.
    assert summarized.tool_calls == 0 and summarized.cutoffs == 0
    assert not [e for e in events if e.type == "tool_use" or "cutoff" in e.detail]


async def test_a_refusal_is_not_applied_as_the_summary() -> None:
    s = at_trigger()
    fake = FakeProvider(
        [
            summary_reply("I can't help with that.", stop_reason="refusal"),
            text_reply("done"),
        ]
    )
    events = await drain(s, fake, compaction="summary")
    assert s.summaries == []
    assert "can't help" not in str(s.messages)
    [notice] = [e for e in events if "compaction_summary" in e.detail]
    assert notice.text == "compaction summary discarded (refused); eliding instead"
    assert s.status == "done"


async def test_a_failed_summary_request_names_its_error_and_falls_back() -> None:
    elided, summarized = at_trigger(), at_trigger()
    plain = FakeProvider([text_reply("done")])
    fake = FailsFirst(RuntimeError("connection reset"), [text_reply("done")])
    await drain(elided, plain)
    events = await drain(summarized, fake, compaction="summary")
    [detail] = summary_events(events)
    assert detail["status"] == "failed"
    assert detail["error"] == "RuntimeError: connection reset"
    assert detail["usage"] is None
    assert replace(summarized, usage=Usage()) == replace(elided, usage=Usage())


async def test_a_backend_that_refuses_tool_choice_falls_back_and_goes_on() -> None:
    s = at_trigger()
    strict = StrictArityProvider([text_reply("done")])
    events = await drain(s, strict, compaction="summary")
    [detail] = summary_events(events)
    assert detail["status"] == "failed"
    assert detail["error"].startswith("AssertionError: ")
    assert s.status == "done"


async def test_the_compaction_summary_event_describes_the_request() -> None:
    fake = FakeProvider([summary_reply(), text_reply("done")])
    events = await drain(at_trigger(), fake, compaction="summary")
    [notice] = [e for e in events if "compaction_summary" in e.detail]
    assert notice.type == "progress"
    assert notice.text == "compaction summary: 4 turns into ~7 tokens"
    assert notice.detail["compaction_summary"] == {
        "status": "applied",
        "error": None,
        "turns": 4,
        "messages": 8,
        "replaced_chars": 17142,
        "chars": 28,
        "usage": {
            "input": 4000,
            "output": 60,
            "cache_read": 500,
            "cache_write": 0,
            "cost": None,
            "estimated": False,
            "total": 4560,
        },
        "text": "Progress: read a five times.",
    }


async def test_a_discarded_summary_says_so() -> None:
    fake = FakeProvider([summary_reply("  "), text_reply("done")])
    events = await drain(at_trigger(), fake, compaction="summary")
    [notice] = [e for e in events if "compaction_summary" in e.detail]
    assert notice.text == "compaction summary discarded (empty); eliding instead"
    assert notice.detail["compaction_summary"]["text"] is None
    assert notice.detail["compaction_summary"]["chars"] == 0


async def test_one_turn_is_summarized_in_the_singular() -> None:
    s = one_turn_to_replace()
    fake = FakeProvider([summary_reply(), text_reply("done")])
    events = await drain(s, fake, compaction="summary")
    [asked] = [e for e in events if "compaction_summary" in e.detail]
    assert asked.detail["compaction_summary"]["turns"] == 1
    assert asked.text == "compaction summary: 1 turn into ~7 tokens"
    [notice] = [e for e in events if "compaction" in e.detail]
    assert notice.detail["compaction"]["summarized"] == 1
    assert notice.text.startswith("compacted: summarized 1 turn, ~")


async def test_a_summary_that_falls_back_writes_the_elision_text_of_today() -> None:
    elided, summarized = at_trigger(), at_trigger()
    plain = await drain(elided, FakeProvider([text_reply("done")]))
    fake = FakeProvider([summary_reply("  "), text_reply("done")])
    events = await drain(summarized, fake, compaction="summary")
    [today] = [e for e in plain if "compaction" in e.detail]
    [notice] = [e for e in events if "compaction" in e.detail]
    assert notice.text == today.text
    assert notice.text.startswith("compacted: elided 2 tool results, dropped")
    assert "summarized" not in notice.text
    # The report still says which mode asked for it, and that nothing was summarized.
    assert notice.detail["compaction"] == {
        **today.detail["compaction"],
        "mode": "summary",
    }
    assert notice.detail["compaction"]["summarized"] == 0


async def test_the_events_of_a_summary_come_in_order() -> None:
    fake = FailsFirst(
        ProviderStatusError("HTTP 503", status_code=503),
        [summary_reply("S"), text_reply("done")],
    )
    retry = RetryPolicy(retries=1, seconds=10, sleep=no_sleep)
    events = await drain(at_trigger(), fake, compaction="summary", retry=retry)

    def first(found: Any) -> int:
        return next(i for i, e in enumerate(events) if found(e))

    order = [
        first(lambda e: "retry" in e.detail),
        first(lambda e: e.detail.get("call") == "compaction_summary"),
        first(lambda e: "compaction_summary" in e.detail),
        first(lambda e: "compaction" in e.detail),
        first(lambda e: e.type == "output"),
    ]
    assert order == sorted(order)


async def test_one_failure_turns_the_summary_off_for_the_invocation() -> None:
    tier = SummaryTier()
    failing = FakeProvider([summary_reply("  "), text_reply("done")])
    await step(
        at_trigger(),
        failing,
        approve_all,
        Policy(),
        context_window=WINDOW,
        compaction="summary",
        summary_tier=tier,
    )
    assert tier.failed
    s = at_trigger()
    assert plan_summary(s, WINDOW) is not None  # a summary would be asked for
    fake = FakeProvider([text_reply("done")])
    await step(
        s,
        fake,
        approve_all,
        Policy(),
        context_window=WINDOW,
        compaction="summary",
        summary_tier=tier,
    )
    assert fake.choices == [None]


async def test_run_gives_every_step_of_an_invocation_one_tier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[SummaryTier | None] = []
    real = loop.step

    async def spy(*args: Any, **kwargs: Any) -> Session:
        seen.append(kwargs.get("summary_tier"))
        return await real(*args, **kwargs)

    monkeypatch.setattr(loop, "step", spy)
    policy = Policy(tools=frozenset({"list"}), root=tmp_path)
    s = new_session("go")
    two_turns = FakeProvider([tool_reply("list", {}), text_reply("done")])
    await drain(s, two_turns, compaction="summary", policy=policy)
    assert len(seen) == 2 and seen[0] is seen[1] is not None
    reopen(s, "again")
    one_turn = FakeProvider([text_reply("done")])
    await drain(s, one_turn, compaction="summary", policy=policy)
    assert seen[2] is not None and seen[2] is not seen[0]
    await drain(new_session("go"), FakeProvider([text_reply("done")]), policy=policy)
    assert seen[3] is None


async def test_a_streamed_run_gives_its_steps_the_tier_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[SummaryTier | None] = []
    real = loop._live_step

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("summary_tier"))
        return real(*args, **kwargs)

    monkeypatch.setattr(loop, "_live_step", spy)
    fake = StreamingProvider([summary_reply("  "), text_reply("done")])
    s = at_trigger()
    await drain(s, fake, compaction="summary")
    [tier] = seen
    assert tier is not None and tier.failed
    assert s.status == "done"


async def test_the_context_stop_after_a_discarded_summary_is_todays() -> None:
    elided, summarized = chat(), chat()
    no_tools = Policy(tools=frozenset())
    await drain(elided, FakeProvider([]), policy=no_tools)
    fake = FakeProvider([summary_reply("  ")])
    await drain(summarized, fake, compaction="summary", policy=no_tools)
    assert (summarized.status, summarized.stop_reason) == ("error", "context")
    assert summarized.error == elided.error
    assert elided.error == "context estimate 10727 reaches window 10000"
    assert len(fake.calls) == 1


async def test_a_token_budget_without_room_skips_the_summary() -> None:
    plan = plan_summary(at_trigger(), WINDOW)
    assert plan is not None
    fake = FakeProvider([text_reply("done")])
    limit = math.floor(plan.request_tokens + plan.bound)
    events = await drain(at_trigger(), fake, compaction="summary", budget_tokens=limit)
    assert fake.choices == [None]
    assert summary_events(events) == []


async def test_a_summary_that_crosses_the_budget_is_kept_and_ends_the_step() -> None:
    plan = plan_summary(at_trigger(), WINDOW)
    assert plan is not None
    limit = math.floor(plan.request_tokens + plan.bound) + 1
    crossing = Reply(
        content=[{"type": "text", "text": "S"}],
        tool_calls=[],
        usage=Usage(input=limit),
        stop_reason="end_turn",
    )
    s = at_trigger()
    measured = (s.last_input_tokens, s.last_input_chars)
    fake = FakeProvider([crossing, text_reply("never sent")])
    events = await drain(s, fake, compaction="summary", budget_tokens=limit)
    assert len(fake.calls) == 1 and s.turns == 0
    assert (s.status, s.stop_reason) == ("error", "budget")
    assert len(s.summaries) == 1
    # The summary reply measured no turn's request. (The message count is
    # rebased onto the shorter transcript by the summary, not by the reply.)
    assert (s.last_input_tokens, s.last_input_chars) == measured
    errors = [e for e in events if e.type == "error"]
    assert len(errors) == 1 and "budget" in errors[0].detail
    compacted = next(i for i, e in enumerate(events) if "compaction" in e.detail)
    assert compacted < events.index(errors[0])
    # A resume with a larger budget continues from the summary.
    reopen(s)
    resumed = FakeProvider([text_reply("done")])
    await drain(s, resumed, compaction="summary", budget_tokens=limit * 10)
    assert s.status == "done"
    assert resumed.calls[0][0][0].content[-1]["text"].endswith("\n\nS")


async def test_a_summary_of_unknown_cost_stops_a_dollar_budget() -> None:
    s = at_trigger()
    fake = FakeProvider([summary_reply("S"), text_reply("never sent")])
    await drain(s, fake, compaction="summary", budget_usd=1.0)
    assert len(fake.calls) == 1
    assert (s.status, s.stop_reason) == ("error", "budget")
    assert s.error is not None and "cannot be enforced" in s.error


async def test_a_budget_stop_is_reported_before_the_context_stop() -> None:
    s = chat()
    fake = FakeProvider([summary_reply("no", stop_reason="refusal")])
    events = await drain(
        s, fake, compaction="summary", policy=Policy(tools=frozenset()), budget_usd=1.0
    )
    assert len(fake.calls) == 1
    assert (s.status, s.stop_reason) == ("error", "budget")
    # One error, the budget's: the context stop did not come first.
    [error] = [e for e in events if e.type == "error"]
    assert "budget" in error.detail
