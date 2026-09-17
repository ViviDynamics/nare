"""The transport layer: nare's bottom layer.

Everything vendor-shaped lives below this line — wire format, tool schema
translation, sampling parameters, retry policy, and stop_reason and usage
normalization. Everything above it is the loop, the tools, approval, and events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from nare.session import Message, Usage

StopReason = Literal["end_turn", "tool_use", "max_tokens", "stop_sequence", "refusal"]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Reply:
    content: list[dict[str, Any]]
    tool_calls: list[ToolCall]
    usage: Usage
    stop_reason: StopReason


class Transport(Protocol):
    """One method. Everything else is bound at construction, which keeps the
    loop free of vendor parameters entirely.

    Structural, not nominal: implementations inherit nothing and import nothing
    from here, and mypy still checks them.
    """

    async def turn(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> Reply: ...


__all__ = ["Reply", "StopReason", "ToolCall", "Transport"]
