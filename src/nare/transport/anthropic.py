"""The Anthropic transport. The only vendor-shaped code in nare."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import aclosing
from copy import deepcopy
from dataclasses import asdict, replace
from typing import Any, cast

import httpx2
from anthropic import (
    DEFAULT_TIMEOUT,
    APIStatusError,
    AsyncAnthropic,
    omit,
    transform_schema,
)
from anthropic.types import MessageParam, ToolParam

from nare.accounting import Prices
from nare.repetition import RepetitionWatch, degenerate, looping
from nare.session import Message, Usage, sent_back
from nare.transport import (
    EFFORTS,
    Delta,
    Effort,
    ModelMetadata,
    Reply,
    StopReason,
    StreamDropped,
    ToolCall,
    Transient,
    checked_timeout,
    reported_cost,
    retry_after_seconds,
    transient,
)

log = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 8192

# Above this the SDK's own _calculate_nonstreaming_timeout raises, because
# 3600 * max_tokens / 128000 exceeds its ten-minute non-streaming budget.
# Streaming removes this SDK-only ceiling; nonstreaming refuses explicitly.
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


def nonstreaming_ceiling(model: str) -> int:
    """The largest max_tokens this model takes without streaming."""
    return min(
        NONSTREAMING_MAX_TOKENS,
        MODEL_NONSTREAMING_TOKENS.get(model, NONSTREAMING_MAX_TOKENS),
    )


# Adaptive thinking counts toward max_tokens, and at high effort and above
# Anthropic advises a large cap: 64k is its suggested start for xhigh and max.
# A cap is a ceiling, not a spend, so a lower level costs only what it uses.
EFFORT_MAX_TOKENS = 64000

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


def messages_from(
    messages: list[Message],
    *,
    image_input: bool = False,
    image_model: str = "AnthropicTransport/model unspecified",
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for turn in messages:
        raw = asdict(turn)
        raw["content"] = [block for block in raw["content"] if sent_back(block)]
        if not raw["content"] and turn.role == "assistant":
            continue
        if output and output[-1]["role"] == raw["role"]:
            # A dropped assistant turn leaves two user turns side by side, and
            # roles must alternate.
            output[-1]["content"] += raw["content"]
        else:
            output.append(raw)
    for message in output:
        for block in message["content"]:
            image = (
                block.pop("image", None) if block.get("type") == "tool_result" else None
            )
            if image is None:
                continue
            text = block.get("content", "")
            if image_input:
                block["content"] = [
                    {"type": "text", "text": text},
                    {"type": "image", "source": image["source"]},
                ]
            else:
                block["content"] = (
                    f"{text} [image withheld: {image_model} "
                    "does not accept image input]"
                )
    return output


def _with_cache_breakpoints(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Mark the newest block and the last tool as prompt-cache breakpoints.

    The practice run re-sent 1.82M tokens with 0 cache reads. The newest
    block's marker moves forward each turn, and the API finds the previous
    turn's entry behind it, so the history is read back at the cache price.
    The tool marker keeps a long tool list, such as one with MCP tools, cached
    when compaction rewrites old messages. (nare's 5 built-in schemas are about
    600 tokens, under the minimum cacheable prefix on most models.) `messages`
    must be messages_from's fresh copy, which this marks in place. `tools` is
    the caller's own list, so it is copied, not marked.
    """
    # The API takes no marker on a thinking block, and `nare run --resume` with
    # no prompt can end the request on a reply of thinking only, so skip those.
    newest = next(
        (
            block
            for message in reversed(messages)
            for block in reversed(message["content"])
            if block.get("type") not in ("thinking", "redacted_thinking")
        ),
        None,
    )
    if newest is not None:
        newest["cache_control"] = {"type": "ephemeral"}
    if tools:
        tools = [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}]
    return messages, tools


# Anthropic's documented ceilings across the strict schemas of one request.
MAX_OPTIONAL_PROPERTIES = 24
MAX_UNION_PROPERTIES = 16


def response_format_from(schema: dict[str, Any]) -> dict[str, Any]:
    """The output_config.format for a constrained answer turn.

    Anthropic answers a keyword it cannot enforce with a 400 rather than
    ignoring it, so the schema goes through the SDK's own transform: bounds,
    lengths, patterns and the like move into descriptions, every object is
    closed, and oneOf becomes anyOf. nare's validator still checks the answer
    against the caller's schema, which this does not touch.
    """
    return {"type": "json_schema", "schema": transform_schema(deepcopy(schema))}


def _subschemas(node: dict[str, Any], path: str) -> list[tuple[dict[str, Any], str]]:
    found: list[tuple[dict[str, Any], str]] = []
    for name, child in (node.get("properties") or {}).items():
        found.append((child, f"{path}properties.{name}"))
    if isinstance(node.get("items"), dict):
        found.append((node["items"], f"{path}items"))
    for keyword in ("anyOf", "oneOf", "allOf"):
        for index, child in enumerate(node.get(keyword) or []):
            found.append((child, f"{path}{keyword}[{index}]"))
    for name, child in (node.get("$defs") or {}).items():
        found.append((child, f"{path}$defs.{name}"))
    return [(child, f"{where}.") for child, where in found if isinstance(child, dict)]


def _refusal(node: dict[str, Any], path: str) -> str | None:
    where = path.rstrip(".") or "the root"
    if "definitions" in node:
        return f"definitions at {where}: name them under $defs"
    extra = node.get("additionalProperties")
    if isinstance(extra, dict) or (extra is True and not node.get("properties")):
        return (
            f"a map at {where} (additionalProperties): a closed object would "
            "decode with no entries"
        )
    if any(isinstance(c, dict) and "$ref" in c for c in node.get("allOf") or []):
        return f"allOf with a $ref at {where}"
    if any(isinstance(v, (dict, list)) for v in node.get("enum") or []):
        return f"an enum of objects or arrays at {where}"
    if not any(k in node for k in ("type", "anyOf", "oneOf", "allOf", "$ref")):
        return f"a schema with no type at {where}"
    return None


def _count(node: dict[str, Any]) -> tuple[int, int]:
    optional = unions = 0
    required = set(node.get("required") or [])
    for name, child in (node.get("properties") or {}).items():
        if name not in required:
            optional += 1
        if isinstance(child, dict) and (
            "anyOf" in child or isinstance(child.get("type"), list)
        ):
            unions += 1
    for child, _ in _subschemas(node, ""):
        more = _count(child)
        optional, unions = optional + more[0], unions + more[1]
    return optional, unions


def check_response_schema(schema: dict[str, Any]) -> None:
    """Refuse at startup a schema the anthropic rail cannot constrain.

    Anything Anthropic refuses would otherwise surface mid-run, as a 400 on the
    correction turn after the run had spent its first answer.
    """
    prefix = "--structured-output on the anthropic provider cannot send this schema"
    queue = [(schema, "")]
    while queue:
        node, path = queue.pop()
        reason = _refusal(node, path)
        if reason is not None:
            raise ValueError(f"{prefix}: {reason}")
        queue += _subschemas(node, path)
    optional, unions = _count(response_format_from(schema)["schema"])
    if optional > MAX_OPTIONAL_PROPERTIES:
        raise ValueError(
            f"{prefix}: {optional} optional properties, above Anthropic's "
            f"{MAX_OPTIONAL_PROPERTIES} optional limit; mark more of them required"
        )
    if unions > MAX_UNION_PROPERTIES:
        raise ValueError(
            f"{prefix}: {unions} properties with a union type (anyOf or a type "
            f"list), above Anthropic's {MAX_UNION_PROPERTIES}"
        )


class AnthropicTransport:
    supports_image_input = True
    # A constrained turn carries no tools, on this rail as on the openai one,
    # so one rule covers which turns a caller's schema constrains.
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
        thinking_budget: int | None = None,
        system: str | None = None,
        http_client: httpx2.AsyncClient | None = None,
        streaming: bool = False,
        image_input: bool = False,
        request_timeout: float | None = None,
    ) -> None:
        timeout = checked_timeout(request_timeout)
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

        self._warned_proxy_reasoning = False
        self.streaming = streaming
        self.image_input = image_input
        if thinking_budget is not None and (
            isinstance(thinking_budget, bool)
            or not isinstance(thinking_budget, int)
            or thinking_budget <= 0
        ):
            raise ValueError("thinking_budget must be a positive integer")
        if effort is not None and effort not in EFFORTS:
            raise ValueError(
                f"unknown effort {effort!r}; expected one of: {', '.join(EFFORTS)}"
            )
        self.effort = effort
        self.thinking_budget = thinking_budget
        # Effort is output_config.effort with adaptive thinking: current Claude
        # models answer a thinking budget with a 400. Adaptive is sent, not
        # left implied, because Opus 4.7 and 4.8 do not think without it. A
        # budget is the caller's explicit choice for the older models that
        # still need one; with effort too both go, as Opus 4.5 takes them.
        self.output_config: dict[str, Any] | None = (
            {"effort": effort} if effort is not None else None
        )
        self.thinking: dict[str, Any] | None = None
        ceiling = nonstreaming_ceiling(model)

        if thinking_budget is not None:
            self.thinking = {"type": "enabled", "budget_tokens": thinking_budget}
            room = thinking_budget + DEFAULT_MAX_TOKENS
            resolved = (
                max_tokens
                if max_tokens is not None
                else (room if streaming else min(room, ceiling))
            )
            if resolved <= thinking_budget:
                raise ValueError(
                    f"max_tokens {resolved} must exceed the thinking budget of "
                    f"{thinking_budget}"
                )
        elif effort is not None:
            self.thinking = {"type": "adaptive"}
            resolved = (
                max_tokens
                if max_tokens is not None
                else (
                    EFFORT_MAX_TOKENS if streaming else min(EFFORT_MAX_TOKENS, ceiling)
                )
            )
        else:
            resolved = max_tokens if max_tokens is not None else DEFAULT_MAX_TOKENS

        if not streaming and resolved > ceiling:
            limit = (
                f"{ceiling}, this model's non-streaming cap"
                if ceiling < NONSTREAMING_MAX_TOKENS
                else str(NONSTREAMING_MAX_TOKENS)
            )
            raise ValueError(
                f"max_tokens {resolved} exceeds {limit}, above "
                "which the SDK requires streaming; use --stream"
            )

        self.max_tokens = resolved
        self._discovery_base = base_url
        self.model = model
        self.system = system
        # max_retries=0: the SDK's own retries were silent and uncounted, and
        # stacked under nare's they made a caller's zero mean two.
        # Unset keeps the SDK's own 600 seconds (5 to connect), and with it
        # the SDK's nonstreaming checks, which nare repeats above anyway.
        self._client = AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
            http_client=http_client,
            max_retries=0,
            timeout=timeout if timeout is not None else DEFAULT_TIMEOUT,
        )

        self._metadata = ModelMetadata(
            self._client._client,
            self._discovery_base,
            self.model,
            {"x-api-key": self._client.api_key or ""},
        )

    def _warn_proxy_reasoning(self, content: list[dict[str, Any]]) -> None:
        # Stream starts can have an empty signature even for native thinking.
        # Inspect completed blocks, after any signature deltas have arrived.
        if not self._warned_proxy_reasoning and any(
            block.get("type") == "thinking" and not block.get("signature")
            for block in content
        ):
            self._warned_proxy_reasoning = True
            log.warning(
                "reasoning is translated by a proxy; on this route LiteLLM can drop "
                "words or whole answers. Use --provider openai for this model."
            )

    async def context_window(self) -> int | None:
        return await self._metadata.context_window()

    async def prices(self) -> Prices | None:
        return await self._metadata.prices()

    def transient(self, exc: BaseException) -> Transient | None:
        """The shared rule, plus two failures Anthropic documents as passing.

        A 500 here is Anthropic's api_error, which its SDK retried, rather
        than a proxy's translation failure. An overloaded_error can arrive
        mid-stream inside a 200, so only its body says what it is.
        """
        if isinstance(exc, APIStatusError):
            body = exc.body if isinstance(exc.body, dict) else {}
            error = body.get("error")
            if isinstance(error, dict) and error.get("type") == "overloaded_error":
                if exc.status_code < 400:
                    return Transient("overloaded")
            if exc.status_code == 500:
                return Transient(
                    "status",
                    status=500,
                    retry_after=retry_after_seconds(
                        exc.response.headers.get("retry-after")
                    ),
                )
        return transient(exc)

    def _wire(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> tuple[list[MessageParam], list[ToolParam]]:
        wire_messages, wire_tools = _with_cache_breakpoints(
            messages_from(
                messages,
                image_input=self.image_input,
                image_model=f"AnthropicTransport/model={self.model}",
            ),
            tools,
        )
        return (
            cast("list[MessageParam]", wire_messages),
            cast("list[ToolParam]", wire_tools),
        )

    def check_response_schema(self, schema: dict[str, Any]) -> None:
        check_response_schema(schema)

    def _output_config(self, response_schema: dict[str, Any] | None) -> Any:
        config = dict(self.output_config or {})
        if response_schema is not None:
            config["format"] = response_format_from(response_schema)
        return cast(Any, config) if config else omit

    async def turn(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        response_schema: dict[str, Any] | None = None,
    ) -> Reply:
        if self.streaming:
            # Closed explicitly: a reply stopped early returns from inside the
            # stream, and the connection must not wait on garbage collection.
            async with aclosing(
                self.stream_turn(messages, tools, response_schema=response_schema)
            ) as items:
                async for item in items:
                    if isinstance(item, Reply):
                        return item
            raise RuntimeError("stream ended without a reply")
        wire_messages, wire_tools = self._wire(
            messages, tools if response_schema is None else []
        )
        raw = await self._client.messages.with_raw_response.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=wire_messages,
            tools=wire_tools,
            system=self.system if self.system is not None else omit,
            thinking=cast(Any, self.thinking) if self.thinking else omit,
            output_config=self._output_config(response_schema),
        )
        response = await raw.parse()
        content = [block.model_dump(exclude_none=True) for block in response.content]
        self._warn_proxy_reasoning(content)
        reply = Reply(
            content=content,
            tool_calls=[
                ToolCall(id=b["id"], name=b["name"], args=b.get("input") or {})
                for b in content
                if b.get("type") == "tool_use"
            ],
            usage=usage_from(response.usage),
            stop_reason=stop_reason_from(response.stop_reason),
            cost=reported_cost(raw.headers),
        )
        if looping(content):
            return replace(reply, stop_reason="repetition")
        return reply

    async def stream_turn(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        response_schema: dict[str, Any] | None = None,
    ) -> AsyncGenerator[Delta | Reply, None]:
        wire_messages, wire_tools = self._wire(
            messages, tools if response_schema is None else []
        )
        async with self._client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=wire_messages,
            tools=wire_tools,
            system=self.system if self.system is not None else omit,
            thinking=cast(Any, self.thinking) if self.thinking else omit,
            output_config=self._output_config(response_schema),
        ) as stream:
            finished = False
            complete_usage = False
            fragments: dict[int, list[str]] = {}
            # The reply's text, kept apart from the stream's own snapshot so a
            # stopped stream still has what arrived. A reply whose text or
            # reasoning starts to loop is stopped here, not read to the cap.
            answer: list[str] = []
            looped = False
            text_watch = RepetitionWatch()
            thought_watch = RepetitionWatch()
            async for event in stream:
                if event.type == "content_block_start":
                    if event.content_block.type == "text" and event.content_block.text:
                        answer.append(event.content_block.text)
                        looped = text_watch.feed(event.content_block.text) or looped
                        yield Delta("progress", event.content_block.text)
                    elif (
                        event.content_block.type == "thinking"
                        and event.content_block.thinking
                    ):
                        looped = (
                            thought_watch.feed(event.content_block.thinking) or looped
                        )
                        yield Delta("thinking", event.content_block.thinking)
                elif event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        answer.append(event.delta.text)
                        looped = text_watch.feed(event.delta.text) or looped
                        yield Delta("progress", event.delta.text)
                    elif event.delta.type == "thinking_delta":
                        looped = thought_watch.feed(event.delta.thinking) or looped
                        yield Delta("thinking", event.delta.thinking)
                    elif event.delta.type == "input_json_delta":
                        fragments.setdefault(event.index, []).append(
                            event.delta.partial_json
                        )
                elif event.type == "message_delta":
                    count = getattr(event.usage, "output_tokens", None)
                    complete_usage = (
                        isinstance(count, int)
                        and not isinstance(count, bool)
                        and count >= 0
                    )
                elif event.type == "message_stop":
                    finished = True
                if looped:
                    break
            if not finished and not looped:
                # A connection that ended on a looping reply is the loop, not a
                # failure to resend: the resend would loop the same way.
                looped = degenerate("".join(answer))
            if looped:
                # The reasoning block is left out: a stream stopped part way has
                # no signature for it, and the reply's text is what the loop
                # produced. The snapshot gives the usage that did arrive.
                snapshot = stream.current_message_snapshot
                yield Reply(
                    content=[{"type": "text", "text": "".join(answer)}]
                    if answer
                    else [],
                    tool_calls=[],
                    usage=usage_from(snapshot.usage)
                    if snapshot is not None
                    else Usage(),
                    stop_reason="repetition",
                    cost=None,
                )
                return
            if not finished:
                raise StreamDropped("incomplete model stream: message_stop missing")
            if not complete_usage:
                raise RuntimeError("incomplete model stream: final usage missing")
            response = await stream.get_final_message()
            if response.stop_reason is None:
                raise RuntimeError("incomplete model stream: stop reason missing")
            content = [
                block.model_dump(exclude_none=True) for block in response.content
            ]
            self._warn_proxy_reasoning(content)
            calls: list[ToolCall] = []
            for index, block in enumerate(content):
                if block.get("type") != "tool_use":
                    continue
                error = None
                if index in fragments:
                    try:
                        args = json.loads("".join(fragments[index]))
                        if not isinstance(args, dict):
                            raise ValueError("arguments must be a JSON object")
                        block["input"] = args
                    except ValueError:
                        block["input"] = {}
                        error = "incomplete or nonobject JSON arguments; tool not run"
                calls.append(
                    ToolCall(
                        id=block["id"],
                        name=block["name"],
                        args=block.get("input") or {},
                        error=error,
                    )
                )
            reply = Reply(
                content=content,
                tool_calls=calls,
                usage=usage_from(response.usage),
                stop_reason=stop_reason_from(response.stop_reason),
                cost=reported_cost(stream.response.headers, streamed=True),
            )
        yield reply
