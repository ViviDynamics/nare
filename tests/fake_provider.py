"""A scripted Transport. Inherits nothing and imports no vendor code, which is
what keeps the seam honestly duck-typed.
"""

from __future__ import annotations

from typing import Any

from nare.session import Message, Usage
from nare.transport import Reply, StopReason, ToolCall, Transport


class FakeProvider:
    def __init__(self, replies: list[Reply]) -> None:
        self._replies = list(replies)
        self.calls: list[tuple[list[Message], list[dict[str, Any]]]] = []

    async def turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> Reply:
        self.calls.append(([Message(m.role, list(m.content)) for m in messages], tools))
        if not self._replies:
            raise AssertionError("FakeProvider ran out of scripted replies")
        return self._replies.pop(0)


# The whole cost of keeping the double and the protocol honest: mypy fails here
# if Transport ever drifts away from FakeProvider.
_check: Transport = FakeProvider([])


def text_reply(text: str, *, stop_reason: StopReason = "end_turn") -> Reply:
    return Reply(
        content=[{"type": "text", "text": text}],
        tool_calls=[],
        usage=Usage(input=10, output=5),
        stop_reason=stop_reason,
    )


class Exploding:
    """A Transport whose turn() always fails, for exercising the error path."""

    async def turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> Reply:
        raise RuntimeError("connection reset")


def tool_reply(name: str, args: dict[str, Any], *, call_id: str = "call_1") -> Reply:
    return Reply(
        content=[{"type": "tool_use", "id": call_id, "name": name, "input": args}],
        tool_calls=[ToolCall(id=call_id, name=name, args=args)],
        usage=Usage(input=10, output=5),
        stop_reason="tool_use",
    )
