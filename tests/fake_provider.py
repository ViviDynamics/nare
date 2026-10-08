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

    async def context_window(self) -> int | None:
        return None

    async def turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> Reply:
        self.calls.append(
            ([Message(m.role, list(m.content)) for m in messages], list(tools))
        )
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


def thinking_reply() -> Reply:
    return Reply(
        content=[{"type": "thinking", "thinking": "the answer should be..."}],
        tool_calls=[],
        usage=Usage(input=10, output=5),
        stop_reason="end_turn",
    )


class Exploding:
    """A Transport whose turn() always fails, for exercising the error path."""

    async def context_window(self) -> int | None:
        return None

    async def turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> Reply:
        raise RuntimeError("connection reset")


def tool_reply(
    name: str,
    args: dict[str, Any],
    *,
    call_id: str = "call_1",
    stop_reason: StopReason = "tool_use",
) -> Reply:
    return Reply(
        content=[{"type": "tool_use", "id": call_id, "name": name, "input": args}],
        tool_calls=[ToolCall(id=call_id, name=name, args=args)],
        usage=Usage(input=10, output=5),
        stop_reason=stop_reason,
    )


def calls_reply(
    *calls: tuple[str, dict[str, Any]], stop_reason: StopReason = "tool_use"
) -> Reply:
    """One turn carrying several tool calls, with ids call_1, call_2, ..."""
    tool_calls = [
        ToolCall(id=f"call_{n}", name=name, args=args)
        for n, (name, args) in enumerate(calls, start=1)
    ]
    return Reply(
        content=[
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.args}
            for c in tool_calls
        ],
        tool_calls=tool_calls,
        usage=Usage(input=10, output=5),
        stop_reason=stop_reason,
    )
