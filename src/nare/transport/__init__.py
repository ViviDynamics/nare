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


def make_transport(
    kind: str,
    *,
    model: str,
    base_url: str | None = None,
    api_key: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    effort: Literal["low", "medium", "high"] | None = None,
    system: str | None = None,
) -> Transport:
    """Build a transport from configuration.

    ponytail: one arm today. The match is the extension point — the OpenAI
    transport (and with it, locally hosted models via base_url) lands as a
    second arm. A registry is slice 4, with the rest of the extension surface.
    """
    match kind:
        case "anthropic":
            from nare.transport.anthropic import AnthropicTransport

            return AnthropicTransport(
                model=model,
                base_url=base_url,
                api_key=api_key,
                temperature=temperature,
                max_tokens=max_tokens,
                effort=effort,
                system=system,
            )
        case _:
            raise ValueError(f"unknown provider {kind!r}; nare supports: anthropic")


__all__ = ["Reply", "StopReason", "ToolCall", "Transport", "make_transport"]
