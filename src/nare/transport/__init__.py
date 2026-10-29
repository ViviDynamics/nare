"""The transport layer: nare's bottom layer.

Everything vendor-shaped lives below this line — wire format, tool schema
translation, sampling parameters, retry policy, and stop_reason and usage
normalization. Everything above it is the loop, the tools, approval, and events.
"""

from __future__ import annotations

import logging
import math
from collections.abc import AsyncGenerator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

import httpx2

from nare.session import Message, Usage

if TYPE_CHECKING:
    from nare.accounting import Prices

StopReason = Literal["end_turn", "tool_use", "max_tokens", "stop_sequence", "refusal"]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class Reply:
    content: list[dict[str, Any]]
    tool_calls: list[ToolCall]
    usage: Usage
    stop_reason: StopReason
    cost: float | None = None


@dataclass(frozen=True)
class Delta:
    kind: Literal["progress", "thinking"]
    text: str


class StreamingTransport(Protocol):
    streaming: bool

    def stream_turn(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncGenerator[Delta | Reply, None]: ...


class StructuredTransport(Protocol):
    """A transport that can constrain an answer turn to a JSON Schema.

    Declared with `supports_structured_output = True`; the loop calls it with
    `response_schema` only on a constrained turn, and with no tools.
    """

    supports_structured_output: bool

    async def turn(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        response_schema: dict[str, Any] | None = None,
    ) -> Reply: ...


class StreamingStructuredTransport(Protocol):
    def stream_turn(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        response_schema: dict[str, Any] | None = None,
    ) -> AsyncGenerator[Delta | Reply, None]: ...


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
    streaming: bool = False,
    image_input: bool = False,
    proxy_num_retries: int | None = None,
) -> Transport:
    """Build a transport from configuration.

    Two arms. `openai` reaches anything speaking Chat Completions, which is a
    proxy, vLLM, llama.cpp or Ollama as much as it is OpenAI, and `--base-url`
    is how a caller says which. A registry is slice 4, with the rest of the
    extension surface.
    """
    match kind:
        case "anthropic":
            if proxy_num_retries is not None:
                raise ValueError("proxy_num_retries requires the openai provider")
            from nare.transport.anthropic import AnthropicTransport

            return AnthropicTransport(
                model=model,
                base_url=base_url,
                api_key=api_key,
                temperature=temperature,
                max_tokens=max_tokens,
                effort=effort,
                system=system,
                streaming=streaming,
                image_input=image_input,
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
                streaming=streaming,
                image_input=image_input,
                proxy_num_retries=proxy_num_retries,
            )
        case _:
            raise ValueError(
                f"unknown provider {kind!r}; nare supports: anthropic, openai"
            )


__all__ = ["Reply", "StopReason", "ToolCall", "Transport", "make_transport"]


def reported_cost(
    headers: Mapping[str, str], *, streamed: bool = False
) -> float | None:
    # Stream headers precede final usage, even when a proxy reports zero.
    if streamed:
        return None
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


class ModelMetadata:
    """One best-effort discovery shared by window sizing and price estimates."""

    def __init__(
        self,
        client: httpx2.AsyncClient,
        base_url: str | None,
        model: str,
        headers: dict[str, str],
    ) -> None:
        self.client = client
        self.base_url = base_url
        self.model = model
        self.headers = headers
        self.loaded = False
        self.info: dict[str, Any] = {}

    async def load(self) -> None:
        if self.loaded:
            return
        self.loaded = True
        if self.base_url is None:
            return
        base = self.base_url.rstrip("/").removesuffix("/v1")
        try:
            response = await self.client.get(
                f"{base}/v1/model/info", headers=self.headers, timeout=5.0
            )
            response.raise_for_status()
            for entry in response.json().get("data", []):
                if entry.get("model_name") == self.model:
                    info = entry.get("model_info")
                    if isinstance(info, dict):
                        self.info = info
                        return
            raise ValueError("model info missing/invalid")
        except (httpx2.HTTPError, ValueError, TypeError, AttributeError) as exc:
            logging.getLogger(__name__).warning(
                "context window discovery failed: %s", exc
            )

    async def context_window(self) -> int | None:
        await self.load()
        value = self.info.get("max_input_tokens")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        if self.base_url is not None:
            logging.getLogger(__name__).warning("context window missing/invalid")
        return None

    async def prices(self) -> Prices | None:
        from nare.accounting import Prices

        await self.load()
        values: dict[str, float] = {}
        for category, key in (
            ("input", "input_cost_per_token"),
            ("output", "output_cost_per_token"),
            ("cache_read", "cache_read_input_token_cost"),
            ("cache_write", "cache_creation_input_token_cost"),
        ):
            value = self.info.get(key)
            if value is None and category.startswith("cache_"):
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                return None
            values[category] = value * 1_000_000
        try:
            return Prices(**values)
        except ValueError:
            return None
