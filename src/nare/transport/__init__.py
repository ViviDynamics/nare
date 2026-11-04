"""The transport layer: nare's bottom layer.

Everything vendor-shaped lives below this line — wire format, tool schema
translation, sampling parameters, retry policy, and stop_reason and usage
normalization. Everything above it is the loop, the tools, approval, and events.
"""

from __future__ import annotations

import logging
import math
import os
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


# Statuses that say "not now" rather than "not this": rate limited, a gateway
# that lost its upstream or timed out, a service unavailable or overloaded
# (529 is Anthropic's). 500 is not here: behind a proxy it is as often a
# translation failure that resending repeats.
RETRY_STATUSES = frozenset({429, 502, 503, 504, 529})

TransientReason = Literal["timeout", "connection", "stream", "status", "overloaded"]


@dataclass(frozen=True)
class Transient:
    """Why a failed model request is worth resending as it was."""

    reason: TransientReason
    status: int | None = None
    retry_after: float | None = None


class ProviderStatusError(RuntimeError):
    """A provider answered a model request with an HTTP error status."""

    def __init__(
        self, message: str, *, status_code: int, retry_after: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class StreamDropped(RuntimeError):
    """A streamed response ended before its completion marker arrived."""


def retry_after_seconds(value: str | None) -> float | None:
    """A Retry-After given in seconds. The HTTP-date form is ignored."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def transient(exc: BaseException) -> Transient | None:
    """Whether a model request's failure is transient, and why.

    Walks the explicit cause chain, because an SDK raises its own error from
    the transport's. Anything not named here is permanent: resending a request
    the provider refused for what it is gets the same refusal.
    """
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, StreamDropped):
            return Transient("stream")
        if isinstance(current, httpx2.TimeoutException):
            return Transient("timeout")
        if isinstance(current, (httpx2.NetworkError, httpx2.RemoteProtocolError)):
            return Transient("connection")
        status = getattr(current, "status_code", None)
        if isinstance(status, int) and status in RETRY_STATUSES:
            return Transient("status", status=status, retry_after=_retry_after(current))
        current = current.__cause__
    return None


def _retry_after(exc: BaseException) -> float | None:
    raw = getattr(exc, "retry_after", None)
    if isinstance(raw, str):
        return retry_after_seconds(raw)
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    return retry_after_seconds(headers.get("retry-after"))


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
    cache_breakpoints: bool = False,
    replay: str | os.PathLike[str] | None = None,
) -> Transport:
    """Build a transport from configuration.

    Three arms. `openai` reaches anything speaking Chat Completions, which is a
    proxy, vLLM, llama.cpp or Ollama as much as it is OpenAI, and `--base-url`
    is how a caller says which. A registry is slice 4, with the rest of the
    extension surface.

    `cache_breakpoints` reaches the openai arm only. The anthropic arm always
    marks its breakpoints, so the option asks it for nothing it lacks.

    `fake` replays the JSON script at `replay` and talks to nothing, which is
    how a caller tests its integration with no key and no network. The
    vendor options mean nothing to it and are ignored.
    """
    if replay is not None and kind != "fake":
        raise ValueError("replay requires the fake provider")
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
                cache_breakpoints=cache_breakpoints,
            )
        case "fake":
            if replay is None:
                raise ValueError("the fake provider requires a replay script")
            from nare.transport.replay import ReplayTransport

            return ReplayTransport.from_file(replay, model=model, streaming=streaming)
        case _:
            raise ValueError(
                f"unknown provider {kind!r}; nare supports: anthropic, openai, fake"
            )


__all__ = [
    "ProviderStatusError",
    "Reply",
    "StopReason",
    "StreamDropped",
    "ToolCall",
    "Transient",
    "Transport",
    "make_transport",
    "transient",
]


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
