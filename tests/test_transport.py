import dataclasses
from typing import Any, get_args

import pytest

from fake_provider import FakeProvider, text_reply
from nare.session import Message, Usage
from nare.transport import Reply, StopReason, ToolCall
from nare.transport.anthropic import (
    DEFAULT_MAX_TOKENS,
    EFFORT_BUDGETS,
    NONSTREAMING_MAX_TOKENS,
    AnthropicTransport,
)


def test_stop_reason_vocabulary_is_exactly_the_five() -> None:
    assert set(get_args(StopReason)) == {
        "end_turn",
        "tool_use",
        "max_tokens",
        "stop_sequence",
        "refusal",
    }


async def test_fake_provider_replays_in_order() -> None:
    fake = FakeProvider([text_reply("one"), text_reply("two")])
    first = await fake.turn([Message("user", [])], [])
    second = await fake.turn([Message("user", [])], [])
    assert first.content[0]["text"] == "one"
    assert second.content[0]["text"] == "two"
    assert len(fake.calls) == 2


async def test_fake_provider_raises_when_exhausted() -> None:
    with pytest.raises(AssertionError, match="ran out"):
        await FakeProvider([]).turn([], [])


def test_reply_is_frozen() -> None:
    reply = Reply(content=[], tool_calls=[], usage=Usage(), stop_reason="end_turn")
    with pytest.raises(dataclasses.FrozenInstanceError):
        reply.__setattr__("stop_reason", "tool_use")


def test_tool_call_carries_id_name_and_args() -> None:
    call = ToolCall(id="c1", name="read", args={"path": "x.py"})
    assert (call.id, call.name, call.args) == ("c1", "read", {"path": "x.py"})


async def test_recorded_calls_are_snapshots_not_live_references() -> None:
    messages = [Message("user", [{"type": "text", "text": "one"}])]
    tools = [{"name": "read"}]
    fake = FakeProvider([text_reply("ok")])
    await fake.turn(messages, tools)
    # Mutating the caller's objects after the call must not rewrite history:
    # later tasks assert on fake.calls to prove what a transport really saw.
    messages[0].content.append({"type": "text", "text": "two"})
    tools.append({"name": "write"})
    recorded_messages, recorded_tools = fake.calls[0]
    assert recorded_messages[0].content == [{"type": "text", "text": "one"}]
    assert recorded_tools == [{"name": "read"}]


def build(**kwargs: Any) -> AnthropicTransport:
    params: dict[str, Any] = {"model": "claude-sonnet-5", "api_key": "test-key"}
    params.update(kwargs)
    return AnthropicTransport(**params)


def test_max_tokens_defaults_to_8192() -> None:
    assert build().max_tokens == DEFAULT_MAX_TOKENS


def test_explicit_max_tokens_is_bound() -> None:
    assert build(max_tokens=512).max_tokens == 512


def test_no_effort_means_no_thinking_block() -> None:
    assert build().thinking is None


@pytest.mark.parametrize("effort,budget", sorted(EFFORT_BUDGETS.items()))
def test_effort_renders_a_thinking_budget(effort: str, budget: int) -> None:
    t = build(effort=effort)
    assert t.thinking == {"type": "enabled", "budget_tokens": budget}
    assert t.max_tokens == min(budget + DEFAULT_MAX_TOKENS, NONSTREAMING_MAX_TOKENS)


def test_effort_high_clamps_to_the_nonstreaming_ceiling() -> None:
    # budget + 8192 would be 24576, which the SDK refuses without streaming.
    t = build(effort="high")
    assert t.thinking == {"type": "enabled", "budget_tokens": 16384}
    assert t.max_tokens == NONSTREAMING_MAX_TOKENS == 21333


def test_temperature_is_refused_outright() -> None:
    # anthropic 1.6.0 removed temperature from messages.create. Refusing beats
    # silently dropping a sampling parameter the caller explicitly asked for.
    with pytest.raises(ValueError, match="temperature"):
        build(temperature=0.2)


def test_temperature_is_refused_with_effort_too() -> None:
    with pytest.raises(ValueError, match="temperature"):
        build(effort="high", temperature=0.2)


def test_effort_with_max_tokens_at_or_below_the_budget_raises() -> None:
    with pytest.raises(ValueError, match="16384"):
        build(effort="high", max_tokens=16384)


def test_effort_with_max_tokens_above_the_budget_is_accepted() -> None:
    assert build(effort="high", max_tokens=20000).max_tokens == 20000


def test_max_tokens_above_the_nonstreaming_ceiling_raises() -> None:
    with pytest.raises(ValueError, match="streaming"):
        build(max_tokens=NONSTREAMING_MAX_TOKENS + 1)


def test_missing_api_key_raises_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        AnthropicTransport(model="claude-sonnet-5")
