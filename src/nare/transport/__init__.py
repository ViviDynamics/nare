"""The transport layer: nare's bottom layer.

Everything vendor-shaped lives below this line — wire format, tool schema
translation, sampling parameters, retry policy, and stop_reason and usage
normalization. Everything above it is the loop, the tools, approval, and events.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx2

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
    cost: float | None = None


class Transport(Protocol):
    """A turn and optional backend window discovery. Configuration is bound at
    construction, which keeps the
    loop free of vendor parameters entirely.

    Structural, not nominal: implementations inherit nothing and import nothing
    from here, and mypy still checks them.
    """

    async def context_window(self) -> int | None: ...

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

    Two arms. `openai` reaches anything speaking Chat Completions, which is a
    proxy, vLLM, llama.cpp or Ollama as much as it is OpenAI, and `--base-url`
    is how a caller says which. A registry is slice 4, with the rest of the
    extension surface.
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
        case "openai":
            from nare.transport.openai import OpenAITransport

            return OpenAITransport(
                model=model,
                base_url=base_url,
                api_key=api_key,
                temperature=temperature,
                max_tokens=max_tokens,
                effort=effort,
                system=system,
            )
        case _:
            raise ValueError(
                f"unknown provider {kind!r}; nare supports: anthropic, openai"
            )


__all__ = ["Reply", "StopReason", "ToolCall", "Transport", "make_transport"]


def reported_cost(headers: Mapping[str, str]) -> float | None:
    raw = headers.get("x-litellm-response-cost")
    if raw is None:
        raw = headers.get("x-litellm-response-cost-original")
    if raw is None:
        return None
    try:
        cost = float(raw)
    except ValueError:
        return None
    return cost if math.isfinite(cost) and cost >= 0 else None


async def discover_context_window(
    client: httpx2.AsyncClient,
    base_url: str | None,
    model: str,
    headers: dict[str, str],
) -> int | None:
    if base_url is None:
        return None
    base = base_url.rstrip("/").removesuffix("/v1")
    try:
        response = await client.get(
            f"{base}/v1/model/info", headers=headers, timeout=5.0
        )
        response.raise_for_status()
        payload = response.json()
        for entry in payload.get("data", []):
            if entry.get("model_name") == model:
                value = entry.get("model_info", {}).get("max_input_tokens")
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    return value
        raise ValueError("model or max_input_tokens missing/invalid")
    except (httpx2.HTTPError, ValueError, TypeError, AttributeError) as exc:
        logging.getLogger(__name__).warning("context window discovery failed: %s", exc)
        return None
