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
from contextlib import aclosing
from dataclasses import replace
from typing import Any

import httpx2

from nare.accounting import Prices
from nare.repetition import RepetitionWatch, degenerate, looping
from nare.schema import DuplicateKeyError, loads_unique
from nare.session import Message, Usage
from nare.transport import (
    Delta,
    Effort,
    ModelMetadata,
    ProviderStatusError,
    Reply,
    RequestClock,
    StopReason,
    StreamDropped,
    ToolCall,
    checked_timeout,
    reported_cost,
    usage_cost,
)

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT = 600.0


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


def function_answer(data: dict[str, Any]) -> dict[str, Any]:
    """Decode one forced answer function; never turn it into an action tool.

    Keep raw argument JSON so duplicate keys and malformed data reach the normal
    answer validator. Shape failures spend the same correction allowance. Usage,
    refusal and cutoff metadata stay attached to the physical reply.
    """
    choices = data.get("choices") or []
    if not choices:
        return data
    choice = choices[0]
    message = choice.get("message") or {}
    calls = message.get("tool_calls") or []
    text = "Expected exactly one nare_final_answer function response."
    if (
        isinstance(calls, list)
        and len(calls) == 1
        and isinstance(calls[0], dict)
        and not message.get("content")
    ):
        function = calls[0].get("function")
        if (
            isinstance(function, dict)
            and function.get("name") == "nare_final_answer"
            and isinstance(function.get("arguments"), str)
        ):
            text = function["arguments"]
    finish = choice.get("finish_reason")
    if finish in {"tool_calls", "function_call"}:
        finish = "stop"
    return {
        **data,
        "choices": [
            {
                **choice,
                "message": {**message, "content": text, "tool_calls": []},
                "finish_reason": finish,
            }
        ],
    }


def answer_fragment_valid(part: Any) -> bool:
    """A malformed forced-answer fragment must still drain final usage."""
    if not isinstance(part, dict):
        return False
    index = part.get("index")
    function = {} if part.get("function") is None else part["function"]
    return (
        isinstance(index, int)
        and not isinstance(index, bool)
        and index >= 0
        and (part.get("id") is None or isinstance(part["id"], str))
        and isinstance(function, dict)
        and all(
            function.get(key) is None or isinstance(function[key], str)
            for key in ("name", "arguments")
        )
    )


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
        parsed = loads_unique(raw or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _argument_error(raw: str) -> str | None:
    try:
        if isinstance(loads_unique(raw or "{}"), dict):
            return None
    except DuplicateKeyError as exc:
        return f"tool arguments are invalid: {exc}; tool not run"
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
    # The proxy's capacity markers (see nare.transport.capacity_refusal): a 429
    # whose body names one of these was refused before dispatch, and may queue.
    capacity_markers: tuple[str, ...] = ("max_parallel_requests",)
    # A constrained turn carries no tools: measured on vLLM, a request with
    # both cannot call a tool, and the model invents an answer instead.
    supports_structured_output = True
    structured_output_modes = ("json_schema", "json_object", "tool_call")

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

    def declared_concurrency(self) -> int | None:
        """No provider-agnostic limit can be read from a Chat Completions endpoint.
        A proxy's max_parallel_requests appears only in a refusal, and each
        proxy exposes its limits through its own API, so none is discovered here
        (issue 289 reports this gap). The adaptive limiter starts at its ceiling
        and learns from refusals."""
        return None

    async def context_window(self) -> int | None:
        return await self._metadata.context_window()

    async def prices(self) -> Prices | None:
        return await self._metadata.prices()

    def _payload(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        response_schema: dict[str, Any] | None = None,
        tool_choice: str | None = None,
        response_mode: str = "json_schema",
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
            if response_mode == "json_schema":
                payload["response_format"] = response_format_from(response_schema)
            elif response_mode == "json_object":
                payload["response_format"] = {"type": "json_object"}
            elif response_mode == "tool_call":
                payload["tools"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": "nare_final_answer",
                            "description": "Return the final structured answer.",
                            "parameters": response_schema,
                        },
                    }
                ]
                payload["parallel_tool_calls"] = False
                payload["tool_choice"] = {
                    "type": "function",
                    "function": {"name": "nare_final_answer"},
                }
            else:
                raise ValueError(f"Unsupported answer encoding: {response_mode}")
        elif wire_tools:
            payload["tools"] = wire_tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
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
        tool_choice: str | None = None,
        response_mode: str = "json_schema",
    ) -> Reply:
        if self.streaming:
            # Closed explicitly: a reply stopped early returns from inside the
            # stream, and the connection must not wait on garbage collection.
            async with aclosing(
                self.stream_turn(
                    messages,
                    tools,
                    response_schema=response_schema,
                    tool_choice=tool_choice,
                    response_mode=response_mode,
                )
            ) as items:
                async for item in items:
                    if isinstance(item, Reply):
                        return item
            raise RuntimeError("stream ended without a reply")
        payload = self._payload(
            messages, tools, response_schema, tool_choice, response_mode
        )
        # Sent as a stream and read whole, so the headers can be timed apart
        # from the body; the request on the wire is the same.
        clock = RequestClock()
        async with self._client.stream(
            "POST",
            f"{self._base_url}/chat/completions",
            headers={"authorization": f"Bearer {self._key}"},
            json=payload,
            timeout=self.timeout,
        ) as response:
            clock.headers()
            await response.aread()
            clock.done()
        if response.status_code >= 400:
            # The body carries the reason a proxy refused, which is the part a
            # caller needs: a scoped key naming the model it may not reach.
            raise self._failure(response)
        data = response.json()
        if response_schema is not None and response_mode == "tool_call":
            data = function_answer(data)
        reply = self._reply(data, cost=reported_cost(response.headers))
        reply = replace(reply, timing=clock.timing())
        if looping(reply.content):
            return replace(reply, stop_reason="repetition")
        return reply

    async def stream_turn(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        response_schema: dict[str, Any] | None = None,
        tool_choice: str | None = None,
        response_mode: str = "json_schema",
    ) -> AsyncGenerator[Delta | Reply, None]:
        payload = {
            **self._payload(
                messages, tools, response_schema, tool_choice, response_mode
            ),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        clock = RequestClock()
        async with self._client.stream(
            "POST",
            f"{self._base_url}/chat/completions",
            headers={"authorization": f"Bearer {self._key}"},
            json=payload,
            timeout=self.timeout,
        ) as response:
            clock.headers()
            if response.status_code >= 400:
                await response.aread()
                raise self._failure(response)
            text: list[str] = []
            reasoning: list[str] = []
            calls: dict[int, dict[str, Any]] = {}
            finish: str | None = None
            usage: dict[str, Any] | None = None
            done = False
            # A reply whose text or reasoning starts to loop is stopped here:
            # the rest of the stream would only repeat it to the output cap.
            looped = False
            text_watch = RepetitionWatch()
            thought_watch = RepetitionWatch()
            answer_watch = RepetitionWatch()
            forced_answer = response_schema is not None and response_mode == "tool_call"
            malformed_answer = False
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
                    if (
                        delta.get("content")
                        or delta.get("reasoning_content")
                        or delta.get("reasoning")
                        or delta.get("tool_calls")
                    ):
                        # Generated content of any kind: a tool-only turn's
                        # first argument fragment counts as its first token.
                        clock.token()
                    value = delta.get("reasoning_content") or delta.get("reasoning")
                    if value:
                        reasoning.append(str(value))
                        yield Delta("thinking", str(value))
                        looped = thought_watch.feed(str(value)) or looped
                    if delta.get("content"):
                        text.append(str(delta["content"]))
                        yield Delta("progress", str(delta["content"]))
                        looped = text_watch.feed(str(delta["content"])) or looped
                    parts = delta.get("tool_calls") or []
                    if forced_answer and not isinstance(parts, list):
                        malformed_answer = True
                        parts = []
                    for part in parts:
                        if forced_answer and not answer_fragment_valid(part):
                            malformed_answer = True
                            continue
                        index = part["index"]
                        call = calls.setdefault(
                            index, {"id": "", "function": {"name": "", "arguments": ""}}
                        )
                        call["id"] += part.get("id") or ""
                        function = part.get("function") or {}
                        for key in ("name", "arguments"):
                            call["function"][key] += function.get(key) or ""
                        if forced_answer:
                            looped = (
                                answer_watch.feed(function.get("arguments") or "")
                                or looped
                            )
                if looped:
                    break
            clock.done()
            if not looped and not done:
                # A connection that ended on a looping reply is the loop, not a
                # failure to resend: the resend would loop the same way.
                looped = degenerate("".join(text)) or degenerate("".join(reasoning))
            if not looped:
                if not done or finish is None or usage is None:
                    # No final marker is a connection that ended mid-response,
                    # which resending can cure; a finished stream that lacks
                    # the rest is the server's shape, which it cannot.
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
                "tool_calls": []
                if malformed_answer
                else [calls[i] for i in sorted(calls)],
            }
            reply_data = {
                "choices": [{"message": message, "finish_reason": finish}],
                "usage": usage,
            }
            if response_schema is not None and response_mode == "tool_call":
                reply_data = function_answer(reply_data)
            reply = self._reply(reply_data, cost=usage_cost(response.headers, usage))
            reply = replace(reply, timing=clock.timing())
            if looped:
                # The stop was ours, so the provider's finish reason says nothing.
                reply = replace(reply, stop_reason="repetition")
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
