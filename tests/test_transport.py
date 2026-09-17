import dataclasses
from typing import get_args

import pytest

from fake_provider import FakeProvider, text_reply
from nare.session import Message, Usage
from nare.transport import Reply, StopReason, ToolCall


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
