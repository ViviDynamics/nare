"""The Anthropic transport. The only vendor-shaped code in nare."""

from __future__ import annotations

import os
from typing import Any, Literal

import httpx2
from anthropic import AsyncAnthropic

DEFAULT_MAX_TOKENS = 8192

# Above this the SDK's own _calculate_nonstreaming_timeout raises, because
# 3600 * max_tokens / 128000 exceeds its ten-minute non-streaming budget.
# Streaming turn() is slice 3's, so the transport refuses rather than guessing.
NONSTREAMING_MAX_TOKENS = 21333

EFFORT_BUDGETS: dict[str, int] = {"low": 1024, "medium": 4096, "high": 16384}

Effort = Literal["low", "medium", "high"]


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

        if budget is None:
            resolved = max_tokens if max_tokens is not None else DEFAULT_MAX_TOKENS
        else:
            resolved = (
                max_tokens
                if max_tokens is not None
                else min(budget + DEFAULT_MAX_TOKENS, NONSTREAMING_MAX_TOKENS)
            )
            if resolved <= budget:
                raise ValueError(
                    f"max_tokens {resolved} must exceed the {effort} thinking "
                    f"budget of {budget}"
                )
            self.thinking = {"type": "enabled", "budget_tokens": budget}

        if resolved > NONSTREAMING_MAX_TOKENS:
            raise ValueError(
                f"max_tokens {resolved} exceeds {NONSTREAMING_MAX_TOKENS}, above "
                "which the SDK requires streaming; streaming turn() is slice 3's"
            )

        self.max_tokens = resolved
        self.model = model
        self.system = system
        self._client = AsyncAnthropic(
            api_key=api_key, base_url=base_url, http_client=http_client
        )
