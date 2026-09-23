"""The OpenAI-compatible transport: Chat Completions, and anything that speaks
it (a proxy, vLLM, llama.cpp, Ollama).

It exists because a rail is not a preference for reasoning models. Measured
against the org's LiteLLM proxy, one model answered correctly on
/v1/chat/completions and returned an empty content list through the Anthropic
translation: the reasoning and the answer arrive together there and only one
survives. A caller whose model looks silent has no way to tell that apart from
a model that said nothing.

No SDK. nare carries one runtime dependency so it stays easy to vendor, and
`httpx2` already arrives with `anthropic`, so this rail costs nothing new.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Literal

import httpx2

from nare.session import Message, Usage
from nare.transport import Reply, StopReason, ToolCall

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT = 600.0

Effort = Literal["low", "medium", "high"]

_STOP_REASONS: dict[str, StopReason] = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    # The pre-tools name for the same thing, still emitted by some servers.
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


def stop_reason_from(raw: str | None) -> StopReason:
    """Normalize. An unknown value degrades to end_turn with a warning rather
    than killing a run, matching the anthropic transport: the loop decides when
    to stop from tool_calls, not from this field.
    """
    if raw in _STOP_REASONS:
        return _STOP_REASONS[raw]
    if raw is not None:
        log.warning("unknown finish_reason %r; reporting end_turn", raw)
    return "end_turn"


def usage_from(raw: dict[str, Any] | None) -> Usage:
    """input EXCLUDES cache reads, which is nare's convention across rails.

    OpenAI counts cached tokens INSIDE prompt_tokens, so they are subtracted
    here. Leaving them in would inflate every cost a caller records, and
    silently: the number would simply be too big.
    """
    if not raw:
        return Usage()
    cached = int((raw.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    prompt = int(raw.get("prompt_tokens") or 0)
    return Usage(
        input=max(prompt - cached, 0),
        output=int(raw.get("completion_tokens") or 0),
        cache_read=cached,
    )


def tools_from(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {}),
            },
        }
        for tool in tools
    ]


def messages_from(messages: list[Message]) -> list[dict[str, Any]]:
    """nare's transcript (Anthropic-shaped blocks) as Chat Completions messages.

    Three shapes differ and one is dropped:
      - a tool result is its own `tool` message, not a block inside a user turn
      - a tool call is `tool_calls` on the assistant message, arguments as a
        JSON string rather than an object
      - thinking is not sent back. Reasoning is server-side state that no
        provider accepts as input here, and inventing a text block out of it
        would put the model's private reasoning into its own transcript.
    """
    out: list[dict[str, Any]] = []
    for message in messages:
        texts: list[str] = []
        calls: list[dict[str, Any]] = []
        for block in message.content:
            kind = block.get("type")
            if kind == "text":
                texts.append(block.get("text", ""))
            elif kind == "tool_result":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": str(block.get("content", "")),
                    }
                )
            elif kind == "tool_use":
                calls.append(
                    {
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input") or {}),
                        },
                    }
                )
        if calls:
            out.append(
                {
                    "role": message.role,
                    "content": "\n".join(texts) if texts else None,
                    "tool_calls": calls,
                }
            )
        elif texts:
            out.append({"role": message.role, "content": "\n".join(texts)})
    return out


def _arguments(raw: str) -> dict[str, Any]:
    """A tool call's arguments, or an empty dict.

    A model that emits malformed JSON gets an is_error tool_result it can
    correct; raising here would end the run over a mistake the model is able
    to fix on the next turn.
    """
    try:
        parsed = json.loads(raw or "{}")
    except ValueError:
        log.warning("tool call arguments were not JSON; passing an empty object")
        return {}
    return parsed if isinstance(parsed, dict) else {}


class OpenAITransport:
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
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError(
                "no API key: set OPENAI_API_KEY in the environment (there is no "
                "--api-key flag; argv is world-readable)"
            )
        self.model = model
        self.system = system
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.effort = effort
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._key = key
        self._client = http_client or httpx2.AsyncClient(timeout=DEFAULT_TIMEOUT)

    async def turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> Reply:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages_from(messages),
        }
        if self.system is not None:
            payload["messages"] = [
                {"role": "system", "content": self.system},
                *payload["messages"],
            ]
        if tools:
            payload["tools"] = tools_from(tools)
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        # Omitted rather than defaulted: a server's own default is a better
        # guess than nare's, and sending one would overwrite it invisibly.
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.effort is not None:
            payload["reasoning_effort"] = self.effort

        response = await self._client.post(
            f"{self._base_url}/chat/completions",
            headers={"authorization": f"Bearer {self._key}"},
            json=payload,
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code >= 400:
            # The body carries the reason a proxy refused, which is the part a
            # caller needs: a scoped key naming the model it may not reach.
            raise RuntimeError(
                f"chat completion failed with HTTP {response.status_code}: "
                f"{response.text}"
            )
        return self._reply(response.json())

    def _reply(self, payload: dict[str, Any]) -> Reply:
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}

        content: list[dict[str, Any]] = []
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        if reasoning:
            content.append({"type": "thinking", "thinking": str(reasoning)})
        text = message.get("content")
        if text:
            content.append({"type": "text", "text": str(text)})

        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") or {}
            args = _arguments(function.get("arguments", ""))
            calls.append(
                ToolCall(id=raw.get("id", ""), name=function.get("name", ""), args=args)
            )
            content.append(
                {
                    "type": "tool_use",
                    "id": raw.get("id", ""),
                    "name": function.get("name", ""),
                    "input": args,
                }
            )

        return Reply(
            content=content,
            tool_calls=calls,
            usage=usage_from(payload.get("usage")),
            stop_reason=stop_reason_from(choice.get("finish_reason")),
        )
