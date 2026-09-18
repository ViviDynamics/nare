"""The Anthropic transport. The only vendor-shaped code in nare."""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from typing import Any, Literal, cast

import httpx2
from anthropic import AsyncAnthropic, omit
from anthropic.types import MessageParam, ToolParam

from nare.session import Message, Usage
from nare.transport import Reply, StopReason, ToolCall

log = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 8192

# Above this the SDK's own _calculate_nonstreaming_timeout raises, because
# 3600 * max_tokens / 128000 exceeds its ten-minute non-streaming budget.
# Streaming turn() is slice 3's, so the transport refuses rather than guessing.
NONSTREAMING_MAX_TOKENS = 21333

# Per-model output caps, which are lower than the global ceiling: every
# opus-4 id caps at 8192. Checking only the global one let those models
# construct fine and then fail on every single turn, blaming streaming. The
# SDK's table is private but it is the same one messages.create feeds to
# _calculate_nonstreaming_timeout; a hardcoded model list here would rot.
MODEL_NONSTREAMING_TOKENS: dict[str, int] = {}
try:
    from anthropic._constants import MODEL_NONSTREAMING_TOKENS as _SDK_MODEL_CAPS
except ImportError:  # pragma: no cover - the SDK moved it; fall back to global
    log.warning(
        "anthropic SDK has no MODEL_NONSTREAMING_TOKENS; per-model output "
        "caps are not checked at construction"
    )
else:
    MODEL_NONSTREAMING_TOKENS = dict(_SDK_MODEL_CAPS)

EFFORT_BUDGETS: dict[str, int] = {"low": 1024, "medium": 4096, "high": 16384}

Effort = Literal["low", "medium", "high"]

_STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "stop_sequence": "stop_sequence",
    "refusal": "refusal",
    # The output was truncated; reporting end_turn would claim the model
    # finished when context overflow cut it off mid-run.
    "model_context_window_exceeded": "max_tokens",
    # Unreachable without server-side tools, which nare does not use. Mapping
    # it to tool_use would produce a contradictory done + tool_use result
    # line, since the loop derives status from whether tool calls are
    # present, not from this field. end_turn is at least internally
    # consistent.
    "pause_turn": "end_turn",
}


def stop_reason_from(raw: str | None) -> StopReason:
    """Normalize. An unknown value degrades to end_turn with a warning rather
    than killing a run, because the loop decides when to stop from tool_calls,
    not from this field.
    """
    if raw in _STOP_REASONS:
        return _STOP_REASONS[raw]
    log.warning("unknown anthropic stop_reason %r; reporting end_turn", raw)
    return "end_turn"


def _cache_tokens(raw: Any, field: str) -> int:
    """Read a cache-token field, warning if the vendor no longer has it.

    A null from the vendor is a legitimate zero and stays quiet. A missing
    attribute means the schema moved, and reporting a silent zero there
    would make the token accounting wrong with no way to notice.
    """
    if not hasattr(raw, field):
        log.warning(
            "anthropic usage has no %s; reporting 0 for it. Token accounting "
            "for cached requests may be understated.",
            field,
        )
        return 0
    value: int | None = getattr(raw, field)
    return value or 0


def usage_from(raw: Any) -> Usage:
    """input EXCLUDES cache reads, which is Anthropic's own convention and the
    one nare normalizes to.
    """
    return Usage(
        input=raw.input_tokens,
        output=raw.output_tokens,
        cache_read=_cache_tokens(raw, "cache_read_input_tokens"),
        cache_write=_cache_tokens(raw, "cache_creation_input_tokens"),
    )


class AnthropicTransport:
    def __init__(
        self,
        *,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        effort: Effort | None = None,
        system: str | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        if api_key is None and not os.environ.get("ANTHROPIC_API_KEY"):
            raise ValueError(
                "no Anthropic API key: set ANTHROPIC_API_KEY in the environment "
                "(there is no --api-key flag; argv is world-readable)"
            )
        if temperature is not None:
            raise ValueError(
                "anthropic no longer accepts temperature: the parameter was "
                "removed from messages.create, so nare refuses it rather than "
                "silently dropping it. Omit --temperature for this provider."
            )

        self.thinking: dict[str, Any] | None = None
        budget = EFFORT_BUDGETS[effort] if effort is not None else None
        ceiling = min(
            NONSTREAMING_MAX_TOKENS,
            MODEL_NONSTREAMING_TOKENS.get(model, NONSTREAMING_MAX_TOKENS),
        )

        if budget is None:
            resolved = max_tokens if max_tokens is not None else DEFAULT_MAX_TOKENS
        else:
            resolved = (
                max_tokens
                if max_tokens is not None
                else min(budget + DEFAULT_MAX_TOKENS, ceiling)
            )
            if resolved <= budget:
                raise ValueError(
                    f"max_tokens {resolved} must exceed the {effort} thinking "
                    f"budget of {budget}"
                )
            self.thinking = {"type": "enabled", "budget_tokens": budget}

        if resolved > ceiling:
            limit = (
                f"{ceiling}, this model's non-streaming cap"
                if ceiling < NONSTREAMING_MAX_TOKENS
                else str(NONSTREAMING_MAX_TOKENS)
            )
            raise ValueError(
                f"max_tokens {resolved} exceeds {limit}, above "
                "which the SDK requires streaming; streaming turn() is slice 3's"
            )

        self.max_tokens = resolved
        self.model = model
        self.system = system
        self._client = AsyncAnthropic(
            api_key=api_key, base_url=base_url, http_client=http_client
        )

    async def turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> Reply:
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=cast("list[MessageParam]", [asdict(m) for m in messages]),
            tools=cast("list[ToolParam]", tools),
            system=self.system if self.system is not None else omit,
            thinking=cast(Any, self.thinking) if self.thinking else omit,
        )
        content = [block.model_dump(exclude_none=True) for block in response.content]
        return Reply(
            content=content,
            tool_calls=[
                ToolCall(id=b["id"], name=b["name"], args=b.get("input") or {})
                for b in content
                if b.get("type") == "tool_use"
            ],
            usage=usage_from(response.usage),
            stop_reason=stop_reason_from(response.stop_reason),
        )
