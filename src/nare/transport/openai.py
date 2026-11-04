"""The OpenAI-compatible transport: Chat Completions, and anything that speaks
it (a proxy, vLLM, llama.cpp, Ollama).

It exists because a rail is not a preference for reasoning models. Measured
against the org's LiteLLM proxy, one model answered correctly on
/v1/chat/completions and returned an empty content list through the Anthropic
translation: the reasoning and the answer arrive together there and only one
survives. A caller whose model looks silent has no way to tell that apart from
a model that said nothing.

No provider SDK on this rail. `httpx2` already arrives with `anthropic`,
so this rail adds no dependency.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any, Literal

import httpx2

from nare.accounting import Prices
from nare.session import Message, Usage
from nare.transport import (
    Delta,
    ModelMetadata,
    ProviderStatusError,
    Reply,
    StopReason,
    StreamDropped,
    ToolCall,
    checked_timeout,
    reported_cost,
)

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


def messages_from(
    messages: list[Message],
    *,
    image_input: bool = False,
    image_model: str = "OpenAITransport/model unspecified",
) -> list[dict[str, Any]]:
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
        images: list[dict[str, Any]] = []
        for block in message.content:
            kind = block.get("type")
            if kind == "text":
                texts.append(block.get("text", ""))
            elif kind == "tool_result":
                text = str(block.get("content", ""))
                image = block.get("image")
                if image is not None:
                    if image_input:
                        source = image["source"]
                        images += [
                            {
                                "type": "text",
                                "text": (
                                    f"Image from read tool result "
                                    f"{block.get('tool_use_id')}: {text}"
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": (
                                        f"data:{source['media_type']};base64,"
                                        f"{source['data']}"
                                    ),
                                },
                            },
                        ]
                    else:
                        text += (
                            f" [image withheld: {image_model} "
                            "does not accept image input]"
                        )
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": text,
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
        if images:
            out.append({"role": "user", "content": images})
    return out


CACHE_CONTROL = {"type": "ephemeral"}

CACHE_BREAKPOINTS_HINT = (
    "; this request carried cache_control breakpoints, and a server that "
    "rejects unknown fields refuses them: turn off --cache-breakpoints "
    "(NARE_CACHE_BREAKPOINTS)"
)


def with_cache_breakpoints(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Mark the newest block and the last tool, as the anthropic rail does.

    Chat Completions has no breakpoint of its own, but a proxy such as LiteLLM
    forwards `cache_control` to a backend that honours it: on a content part,
    on a tool call, and on a tool's `function`. The newest block is the end of
    the last wire message, because thinking is never sent back and a tool
    result's image travels after its tool message. Only that message changes
    shape, a string content becoming one text part, so everything before it
    is the same bytes as an unmarked request and stays a cacheable prefix.
    `messages` and `tools` must be fresh copies, which this marks in place.
    """
    if messages:
        newest = messages[-1]
        content = newest.get("content")
        if newest.get("tool_calls"):
            newest["tool_calls"][-1]["cache_control"] = dict(CACHE_CONTROL)
        elif isinstance(content, str):
            newest["content"] = [
                {"type": "text", "text": content, "cache_control": dict(CACHE_CONTROL)}
            ]
        elif isinstance(content, list) and content:
            content[-1]["cache_control"] = dict(CACHE_CONTROL)
    if tools:
        tools[-1]["function"]["cache_control"] = dict(CACHE_CONTROL)
    return messages, tools


def _arguments(raw: str) -> dict[str, Any]:
    """A tool call's arguments, or an empty dict when they are not a JSON
    object. _argument_error() then says so on the call, streamed or not, so
    the model gets an is_error result it can correct; raising here would end
    the run over a mistake the model is able to fix on the next turn.
    """
    try:
        parsed = json.loads(raw or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _argument_error(raw: str) -> str | None:
    try:
        if isinstance(json.loads(raw or "{}"), dict):
            return None
    except ValueError:
        pass
    return "tool arguments are not a complete JSON object; tool not run"


def response_format_from(schema: dict[str, Any]) -> dict[str, Any]:
    """The json_schema response format for a constrained answer turn.

    `strict` is true so a backend that cannot enforce the schema refuses the
    request rather than answering unconstrained. vLLM enforces the schema at
    decode time either way.
    """
    return {
        "type": "json_schema",
        "json_schema": {"name": "answer", "schema": schema, "strict": True},
    }


class OpenAITransport:
    supports_image_input = True
    # A constrained turn carries no tools: measured on vLLM, a request with
    # both cannot call a tool, and the model invents an answer instead.
    supports_structured_output = True

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
        streaming: bool = False,
        image_input: bool = False,
        proxy_num_retries: int | None = None,
        cache_breakpoints: bool = False,
        request_timeout: float | None = None,
    ) -> None:
        timeout = checked_timeout(request_timeout)
        if proxy_num_retries is not None and (
            type(proxy_num_retries) is not int or proxy_num_retries < 0
        ):
            raise ValueError("proxy_num_retries must be a nonnegative integer")
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError(
                "no API key: set OPENAI_API_KEY in the environment (there is no "
                "--api-key flag; argv is world-readable)"
            )
        self.streaming = streaming
        self.image_input = image_input
        self._discovery_base = base_url
        self.model = model
        self.system = system
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.effort = effort
        self.proxy_num_retries = proxy_num_retries
        # Off by default: some OpenAI-compatible servers reject a field they
        # do not know, and an unmarked request must stay what it always was.
        self.cache_breakpoints = cache_breakpoints
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._key = key
        # Each wait for bytes, not a whole response: a streamed turn that
        # keeps sending is never cut off by it.
        self.timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
        self._client = http_client or httpx2.AsyncClient(timeout=self.timeout)

        self._metadata = ModelMetadata(
            self._client,
            self._discovery_base,
            self.model,
            {"authorization": f"Bearer {self._key}"},
        )

    async def context_window(self) -> int | None:
        return await self._metadata.context_window()

    async def prices(self) -> Prices | None:
        return await self._metadata.prices()

    def _payload(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        wire_messages = messages_from(
            messages,
            image_input=self.image_input,
            image_model=f"OpenAITransport/model={self.model}",
        )
        # A constrained turn sends no tools, so none are marked.
        wire_tools = tools_from(tools) if response_schema is None else []
        if self.cache_breakpoints:
            wire_messages, wire_tools = with_cache_breakpoints(
                wire_messages, wire_tools
            )
        payload: dict[str, Any] = {"model": self.model, "messages": wire_messages}
        if self.system is not None:
            payload["messages"] = [
                {"role": "system", "content": self.system},
                *payload["messages"],
            ]
        if response_schema is not None:
            payload["response_format"] = response_format_from(response_schema)
        elif wire_tools:
            payload["tools"] = wire_tools
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        # Omitted rather than defaulted: a server's own default is a better
        # guess than nare's, and sending one would overwrite it invisibly.
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.effort is not None:
            payload["reasoning_effort"] = self.effort
        if self.proxy_num_retries is not None:
            payload["num_retries"] = self.proxy_num_retries

        return payload

    async def turn(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        response_schema: dict[str, Any] | None = None,
    ) -> Reply:
        if self.streaming:
            async for item in self.stream_turn(
                messages, tools, response_schema=response_schema
            ):
                if isinstance(item, Reply):
                    return item
            raise RuntimeError("stream ended without a reply")
        payload = self._payload(messages, tools, response_schema)
        response = await self._client.post(
            f"{self._base_url}/chat/completions",
            headers={"authorization": f"Bearer {self._key}"},
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            # The body carries the reason a proxy refused, which is the part a
            # caller needs: a scoped key naming the model it may not reach.
            raise self._failure(response)
        return self._reply(response.json(), cost=reported_cost(response.headers))

    async def stream_turn(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        response_schema: dict[str, Any] | None = None,
    ) -> AsyncGenerator[Delta | Reply, None]:
        payload = {
            **self._payload(messages, tools, response_schema),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        async with self._client.stream(
            "POST",
            f"{self._base_url}/chat/completions",
            headers={"authorization": f"Bearer {self._key}"},
            json=payload,
            timeout=self.timeout,
        ) as response:
            if response.status_code >= 400:
                await response.aread()
                raise self._failure(response)
            text: list[str] = []
            reasoning: list[str] = []
            calls: dict[int, dict[str, Any]] = {}
            finish: str | None = None
            usage: dict[str, Any] | None = None
            done = False
            data: list[str] = []
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    data.append(line[5:].lstrip(" "))
                    continue
                if line or not data:
                    continue
                frame = "\n".join(data)
                data = []
                if frame == "[DONE]":
                    done = True
                    break
                raw = json.loads(frame)
                if raw.get("error"):
                    raise RuntimeError(f"stream provider error: {raw['error']}")
                if raw.get("usage") is not None:
                    usage = raw["usage"]
                for choice in raw.get("choices", []):
                    if choice.get("index", 0) != 0:
                        continue
                    if choice.get("finish_reason") is not None:
                        finish = choice["finish_reason"]
                    delta = choice.get("delta") or {}
                    value = delta.get("reasoning_content") or delta.get("reasoning")
                    if value:
                        reasoning.append(str(value))
                        yield Delta("thinking", str(value))
                    if delta.get("content"):
                        text.append(str(delta["content"]))
                        yield Delta("progress", str(delta["content"]))
                    for part in delta.get("tool_calls") or []:
                        index = part["index"]
                        call = calls.setdefault(
                            index, {"id": "", "function": {"name": "", "arguments": ""}}
                        )
                        call["id"] += part.get("id") or ""
                        function = part.get("function") or {}
                        for key in ("name", "arguments"):
                            call["function"][key] += function.get(key) or ""
            if not done or finish is None or usage is None:
                # No final marker is a connection that ended mid-response,
                # which resending can cure; a finished stream that lacks the
                # rest is the server's shape, which it cannot.
                failure = StreamDropped if not done else RuntimeError
                raise failure(
                    "incomplete model stream: "
                    "final marker, stop reason or usage missing"
                )
            if not all(
                isinstance(usage.get(key), int)
                and not isinstance(usage.get(key), bool)
                and usage[key] >= 0
                for key in ("prompt_tokens", "completion_tokens")
            ):
                raise RuntimeError("incomplete model stream: invalid final usage")
            message = {
                "content": "".join(text),
                "reasoning_content": "".join(reasoning),
                "tool_calls": [calls[i] for i in sorted(calls)],
            }
            reply = self._reply(
                {
                    "choices": [{"message": message, "finish_reason": finish}],
                    "usage": usage,
                },
                cost=reported_cost(response.headers, streamed=True),
            )
        yield reply

    def _failure(self, response: httpx2.Response) -> ProviderStatusError:
        """The proxy's own reason, plus the option to turn off when the
        request carried breakpoints and the server refused it as a request.
        The status and Retry-After travel with it, for the retry decision.
        """
        status = response.status_code
        hint = CACHE_BREAKPOINTS_HINT if self.cache_breakpoints and status < 500 else ""
        return ProviderStatusError(
            f"chat completion failed with HTTP {status}: {response.text}{hint}",
            status_code=status,
            retry_after=response.headers.get("retry-after"),
        )

    def _reply(
        self,
        payload: dict[str, Any],
        *,
        cost: float | None = None,
    ) -> Reply:
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
                ToolCall(
                    id=raw.get("id", ""),
                    name=function.get("name", ""),
                    args=args,
                    error=_argument_error(function.get("arguments", "")),
                )
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
            cost=cost,
        )
