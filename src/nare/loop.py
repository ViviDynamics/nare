"""The agent loop. A plain Session advanced by an explicit step(), surfaced as
an async generator: resumable by construction, deterministic under test.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import (
    AsyncGenerator,
    Awaitable,
    Callable,
    Iterable,
    Mapping,
    Sequence,
)
from contextlib import aclosing
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal, cast

from nare.accounting import Prices, finite_number, turn_usage
from nare.caller_tools import Tool, external_tools
from nare.compact import compact, context_chars, estimate
from nare.corrections import (
    SCHEMA_CORRECTIONS_DEFAULT,
    charge,
    check_max_corrections,
    correction_text,
    record,
)
from nare.events import Event, LiveText, redact
from nare.mcp import Server, connect
from nare.reads import NO_READS, ReadRequirement, unmet
from nare.reads import build as build_reads
from nare.schema import DuplicateKeyError, SchemaError, extract_json, validate
from nare.session import Message, Session, append_user_text
from nare.tools import (
    Approve,
    Policy,
    approve_all,
    dispatch,
    questions_from,
    tool_result,
)
from nare.transport import (
    Delta,
    Reply,
    StopReason,
    StreamingStructuredTransport,
    StreamingTransport,
    StructuredTransport,
    Transient,
    Transport,
    transient,
)

MAX_TURNS_DEFAULT = 50

# The constrained-answer modes a transport can be asked for. One today: the
# OpenAI-compatible json_schema response format.
StructuredOutput = Literal["json_schema"]
STRUCTURED_OUTPUT_MODES: tuple[str, ...] = ("json_schema",)

# What an unanswered call says when a person stopped the turn, so the model is
# told what happened instead of that a tool broke.
INTERRUPTED = "interrupted by the user"

# Cut-off replies, the output cap or a repetition, each get a correction within
# a per-invocation budget. The library default is none, which is what an
# embedder that never asked for the budget has always got; the CLI default is
# CUTOFFS_DEFAULT.
CUTOFFS_DEFAULT = 2
CUTOFF_REASONS: tuple[StopReason, ...] = ("max_tokens", "repetition")

RETRIES_DEFAULT = 3
RETRY_SECONDS_DEFAULT = 60.0
RETRY_FIRST_WAIT = 2.0
RETRY_MAX_WAIT = 30.0


@dataclass(frozen=True)
class RetryPolicy:
    """How often one model request is resent after a transient failure.

    Per request, not per run: a long run that meets a failure every few turns
    should not run out on turn three. `seconds` bounds the waiting between
    attempts, not the attempts, because one timed-out attempt alone can take
    the whole request timeout. The transport says what is transient; the loop
    decides how often, because a retry is an event and a count.
    """

    retries: int = RETRIES_DEFAULT
    seconds: float = RETRY_SECONDS_DEFAULT
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    def __post_init__(self) -> None:
        if (
            isinstance(self.retries, bool)
            or not isinstance(self.retries, int)
            or self.retries < 0
        ):
            raise ValueError("retries must be a nonnegative integer")
        if (
            isinstance(self.seconds, bool)
            or not isinstance(self.seconds, (int, float))
            or not math.isfinite(self.seconds)
            or self.seconds < 0
        ):
            raise ValueError("retry seconds must be a nonnegative finite number")

    def wait(self, attempt: int, found: Transient) -> float:
        """Exponential from 2 seconds, capped at 30; Retry-After lengthens it.

        No jitter, so the wait an event reports is the wait nare took.
        """
        wait = min(RETRY_FIRST_WAIT * 2 ** (attempt - 1), RETRY_MAX_WAIT)
        if found.retry_after is not None:
            wait = max(wait, found.retry_after)
        return float(wait)


NO_RETRIES = RetryPolicy(retries=0, seconds=0)


def budget_record(
    s: Session,
    *,
    max_tool_calls: int | None = None,
    tool_calls_from: int | None = None,
    max_corrections: int | None = None,
    max_cutoffs: int | None = None,
    cutoffs_from: int | None = None,
) -> dict[str, Any]:
    """The limits a run had and what it used. The tool call cap and its count
    are per invocation, so only a caller that knows where the invocation
    started (`tool_calls_from`) reports them. The cut-off count is the same:
    used_cutoffs counts every cut-off reply the invocation saw, the last one
    included, where the cap counts the corrections it allowed.
    """
    record = {**s.budget, "used_tokens": s.usage.total_tokens, "used_usd": s.usage.cost}
    if max_tool_calls is not None:
        record["tool_calls"] = max_tool_calls
    if tool_calls_from is not None:
        record["used_tool_calls"] = s.tool_calls - tool_calls_from
    if max_corrections is not None:
        record["schema_corrections"] = max_corrections
        record["used_schema_corrections"] = s.schema_corrections
    if max_cutoffs is not None:
        record["cutoffs"] = max_cutoffs
    if cutoffs_from is not None:
        record["used_cutoffs"] = s.cutoffs - cutoffs_from
    return record


@dataclass
class ToolCallLimit:
    """--max-tool-calls for one invocation: the cap, the calls run against it,
    and how many turns had a call refused at it."""

    limit: int
    used: int = 0
    refused_turns: int = 0

    def message(self) -> str:
        return (
            f"not run: the tool call limit for this run ({self.limit}) is reached. "
            "Tools are disabled: answer now with what you have, and say what "
            "remains undone."
        )


def check_max_tool_calls(value: int | None) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
    ):
        raise ValueError("max_tool_calls must be a positive integer")


def configure_budgets(
    s: Session,
    tokens: int | None = None,
    usd: float | None = None,
) -> None:
    """Resolve overrides over saved ceilings without resetting usage."""
    tokens = tokens if tokens is not None else s.budget.get("tokens")
    usd = usd if usd is not None else s.budget.get("usd")
    if tokens is not None:
        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens <= 0:
            raise ValueError("budget_tokens must be a positive integer")
        s.budget["tokens"] = tokens
    if usd is not None:
        s.budget["usd"] = finite_number(usd, "budget_usd")


def check_budget(s: Session) -> bool:
    token_limit = s.budget.get("tokens")
    usd_limit = s.budget.get("usd")
    reasons: list[str] = []
    if token_limit is not None and s.usage.total_tokens >= token_limit:
        reasons.append(
            f"token budget exhausted: used {s.usage.total_tokens}, limit {token_limit}"
        )
    if usd_limit is not None:
        if s.usage.cost is None:
            reasons.append(
                "--budget-usd cannot be enforced: cost for this backend is unknown; "
                "set NARE_PRICE_IN and NARE_PRICE_OUT"
            )
        elif s.usage.cost >= usd_limit:
            reasons.append(
                f"USD budget exhausted: used {s.usage.cost}, limit {usd_limit}"
            )
    if not reasons:
        return False
    s.status = "error"
    s.stop_reason = "budget"
    s.error = "; ".join(reasons)
    s.events.append(
        Event("error", s.error, {"budget": budget_record(s), "usage": s.usage.wire()})
    )
    return True


# A turn the vendor cut off, or that the model refused, is not a finished one.
# A caller reads done as "the task succeeded", so reporting either as done
# would tell it something false. The transport already takes care to map
# model_context_window_exceeded to max_tokens for exactly this reason; the
# loop is where that distinction was being thrown away.
_UNFINISHED: dict[StopReason, str] = {
    "max_tokens": "the model's turn was cut off at max_tokens",
    "repetition": "the model's reply repeated one span until it was cut off",
    "refusal": "the model refused to continue",
}

# What a corrected cut-off says to the model. A turn that carried tool calls
# is told they did not run, since its calls were never sent to the tools.
_CUTOFF_GUIDANCE: dict[str, str] = {
    "repetition": (
        "Your last reply repeated the same text until it was cut off. Reason "
        "briefly, write each record once, and return the complete answer."
    ),
    "max_tokens": (
        "Your last reply was cut off at the output limit before it finished. "
        "Reason briefly, and return the complete answer."
    ),
}
_CALLS_NOT_RUN = " Its tool calls did not run."


@dataclass
class CutoffLimit:
    """--max-cutoffs for one invocation: the budget, and every cut-off reply
    the invocation has seen, the last one included."""

    limit: int
    used: int = 0


def check_max_cutoffs(value: int | None) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value < 0
    ):
        raise ValueError("max_cutoffs must be a nonnegative integer")


# An ask beside other calls is not a question about the request: the turn has
# already acted on one reading of it. Honouring it told the caller that work
# was waiting on them when it was finished.
ASK_NOT_ALONE = (
    "ask was not recorded: call ask on its own, before making any change. "
    "The other calls in this turn ran."
)
ASK_NOT_DELIVERED = (
    "ask was not recorded: another ask in this turn failed, so nothing was "
    "sent. Call ask again, once, with all your questions."
)


def _final_text(content: list[dict[str, Any]]) -> str:
    return "\n".join(b.get("text", "") for b in content if b.get("type") == "text")


def final_text(s: Session) -> str | None:
    """The final reply of a run that ended done, redacted as events are.

    None for any other status: a blocked, failed or stopped run has no final
    reply, whatever its last message says.
    """
    if s.status != "done":
        return None
    for message in reversed(s.messages):
        if message.role == "assistant":
            return redact(_final_text(message.content))
    return None


def schema_instruction(schema: dict[str, Any]) -> str:
    """What the model is told when a run is schema-constrained.

    Validating an answer without ever stating the requirement wastes the first
    turn by construction: a model cannot satisfy a shape it was never shown,
    and a weaker one never converges on it through corrections alone.

    It says it wins: the default system prompt asks the final message for
    prose (what was not tested, what was left running), and prose beside the
    JSON costs a correction round.
    """
    return (
        "Your final answer must be a JSON document satisfying this JSON Schema, "
        "and nothing else. This overrides any other instruction about your "
        "final message:\n"
        f"{json.dumps(schema)}"
    )


def schema_digest(schema: dict[str, Any]) -> str:
    """The schema's identity: key order and whitespace are not a change."""
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def state_schema(session: Session, schema: dict[str, Any]) -> None:
    """Tell the model the schema, once, and again only when it changes.

    Said in the transcript rather than in a system prompt: a resume carries it
    forward, and a reader can see exactly what the model was asked for. A
    resume that validated against a schema the model was never shown would
    fail on an answer the model had no way to get right.
    """
    digest = schema_digest(schema)
    if not session.schema_stated:
        append_user_text(session, schema_instruction(schema))
        session.schema_stated = True
    elif session.schema_sha256 not in (None, digest):
        append_user_text(
            session,
            f"The required JSON Schema has changed. {schema_instruction(schema)}",
        )
        session.events.append(
            Event(
                "progress",
                "schema changed: this run validates against a different schema "
                "from the one the session stated",
                {"schema": {"changed": True}},
            )
        )
        # Retained output is a document that passes the run's schema. One
        # that passed only the old schema is not this run's answer.
        if session.output is not None and validate(session.output, schema):
            session.output = None
    # A file saved before the digest was recorded states nothing new, as it
    # did, and is recorded from here on.
    session.schema_sha256 = digest


def _parses(text: str) -> bool:
    """Whether the answer is a JSON document at all, which is what makes its
    validator messages comparable with an earlier answer's."""
    try:
        extract_json(text)
    except ValueError:
        return False
    return True


def _answer_errors(text: str, schema: dict[str, Any]) -> list[SchemaError]:
    """The validator's complaints about this answer, or none. A reply that is
    not one JSON document, or repeats a key, is a complaint too, coded so a
    caller can tell it from a schema violation."""
    try:
        parsed = extract_json(text)
    except DuplicateKeyError as exc:
        return [
            SchemaError("duplicate_key", location, f"duplicate key: {key}")
            for location, key in exc.found
        ]
    except ValueError as exc:
        return [SchemaError("invalid_json", (), str(exc))]
    return validate(parsed, schema)


# A check takes the parsed answer and returns its errors: SchemaError values, or
# a mapping of code, location, message and guidance, or plain strings. An empty
# result (or None) passes. It runs only on an answer that passes the schema.
Check = Callable[[Any], Iterable[Any] | None]


class CheckRaised(Exception):
    """The caller's check raised. nare did not make that failure; the run ends
    with an error rather than the exception reaching the caller."""


def check_check(check: Check | None, schema: dict[str, Any] | None) -> None:
    """Refuse a check that cannot run, before any turn."""
    if check is None:
        return
    if schema is None:
        raise ValueError("check requires a schema: it checks the parsed answer")
    if not callable(check):
        raise ValueError("check must be a callable")


def _as_error(item: Any) -> SchemaError:
    if isinstance(item, SchemaError):
        return item
    if isinstance(item, str):
        return SchemaError("check", (), item)
    if isinstance(item, Mapping):
        return SchemaError(
            str(item["code"]),
            tuple(item.get("location", ())),
            str(item["message"]),
            item.get("guidance"),
        )
    raise TypeError(
        f"a check returns SchemaError, str or a mapping, not {type(item).__name__}"
    )


def _check_errors(parsed: Any, check: Check) -> list[SchemaError]:
    """The check's errors for a parsed answer. A check that raises, or returns
    something other than a list of errors, raises CheckRaised."""
    try:
        found = check(parsed)
        if isinstance(found, (str, Mapping)):
            raise TypeError("a check returns a list of errors, not one value")
        return [_as_error(item) for item in (found or ())]
    except Exception as exc:
        raise CheckRaised(f"check raised {type(exc).__name__}: {exc}") from exc


def _check_passes(text: str, check: Check | None) -> bool:
    """Whether a tool-turn candidate may be kept as the output. Silent: a check
    that fails here is simply not kept, and the final answer reports it."""
    if check is None:
        return True
    try:
        return not _check_errors(extract_json(text), check)
    except CheckRaised:
        return False


def check_structured_output(
    transport: Transport,
    schema: dict[str, Any] | None,
    structured_output: str | None,
) -> None:
    """Refuse before any turn a constrained run that could not be constrained.

    A run that asked for constrained answers and silently got free text would
    hand its caller the failure it asked to be protected from.
    """
    if structured_output is None:
        return
    if structured_output not in STRUCTURED_OUTPUT_MODES:
        raise ValueError(
            f"unknown structured output mode {structured_output!r}; "
            f"supported: {', '.join(STRUCTURED_OUTPUT_MODES)}"
        )
    if schema is None:
        raise ValueError("structured output requires a schema")
    if getattr(transport, "supports_structured_output", False) is not True:
        raise ValueError(
            f"{type(transport).__name__} does not support structured output"
        )
    # A rail that can refuse a schema at startup does, rather than with a
    # provider failure on the correction turn after the first answer.
    vet = getattr(transport, "check_response_schema", None)
    if callable(vet):
        vet(schema)


def _constrained(
    s: Session,
    schema: dict[str, Any] | None,
    structured_output: str | None,
    tools: list[dict[str, Any]],
) -> bool:
    """Whether this turn is a schema-constrained answer turn.

    A constrained request cannot call a tool (measured on vLLM: the model
    invents an answer instead), so only a turn that needs no tool is
    constrained: the one schema correction, or any turn of a run offering no
    tools. A first answer is never known to be final before it is generated.
    """
    if structured_output is None or schema is None:
        return False
    return s.schema_retried or not tools


def _without_images(messages: list[Message], model: str) -> list[Message]:
    if not any("image" in block for message in messages for block in message.content):
        return messages
    copied = deepcopy(messages)
    for message in copied:
        for block in message.content:
            if (
                block.get("type") == "tool_result"
                and block.pop("image", None) is not None
            ):
                block["content"] = (
                    f"{block.get('content', '')} [image withheld: "
                    f"{model} does not accept image input]"
                )
    return copied


def result_event(name: str, result: dict[str, Any], policy: Policy) -> Event:
    """How one call ended. A built-in result is described, not repeated: the
    transcript holds its content. An MCP result keeps its content, redacted
    and capped like any event detail, as it always has."""
    content = result["content"]
    record: dict[str, Any] = {
        "tool_use_id": result["tool_use_id"],
        "is_error": result["is_error"],
        "chars": len(content),
    }
    if name in policy.external:
        record = {**result, **record}
        source = "caller" if policy.external[name].source == "caller" else "MCP"
        text = f"{source} result: {name}"
    else:
        text = f"{name} result: {'error' if result['is_error'] else 'ok'}"
    return Event(
        "progress",
        text,
        {"tool": name, "tool_result": record},
        tool_use_id=result["tool_use_id"],
    )


async def _with_retries(
    s: Session,
    transport: Transport,
    attempt: Callable[[], Awaitable[Reply]],
    retry: RetryPolicy,
    on_event: Callable[[Event], None] | None,
) -> Reply:
    """One model request, resent on a transient failure within the budget.

    A failed attempt left nothing behind: the turn, usage and transcript are
    only written once a reply arrives, and the messages resent are the same
    ones, so a caching backend still finds its prefix. A failure that is not
    transient, or that outlives the budget, is raised as it came.
    """
    classify = getattr(transport, "transient", None) or transient
    made = 0
    waited = 0.0
    while True:
        try:
            return await attempt()
        except Exception as exc:
            found = classify(exc)
            if found is None or made >= retry.retries:
                raise
            wait = retry.wait(made + 1, found)
            if waited + wait > retry.seconds:
                raise
            made += 1
            waited += wait
            s.retries += 1
            error = f"{type(exc).__name__}: {exc}"
            event = Event(
                "progress",
                f"model request failed ({found.reason}); "
                f"retry {made} of {retry.retries} in {wait:g}s: {error}",
                {
                    "retry": {
                        "attempt": made,
                        "retries": retry.retries,
                        "reason": found.reason,
                        "status": found.status,
                        "wait": wait,
                        "error": error,
                    }
                },
            )
            if on_event is not None:
                on_event(event)
            else:
                s.events.append(event)
            await retry.sleep(wait)


def _hold_for_reads(s: Session, reads: ReadRequirement, max_corrections: int) -> bool:
    """Hold a final answer the run has not read enough to rest on.

    Returns False when nothing is owed, and the answer is accepted. Otherwise a
    read correction is charged and the model told what is missing (returns True,
    the run continues), or, with the budget spent, the run ends `error` with
    `reads_required` (returns True, the run is over).
    """
    gaps = unmet(s, reads)
    if not gaps:
        return False
    # The turn after a read hold is an ordinary turn: an earlier schema
    # correction must not keep it constrained with its tools withheld, since
    # the model now has to read.
    s.schema_retried = False
    granted = charge(s, gaps, max_corrections, comparable=False, kind="reads")
    if granted is None:
        s.status = "error"
        s.stop_reason = "reads_required"
        s.error = "final answer before the required reads: " + "; ".join(gaps)
        s.events.append(Event("error", s.error))
        return True
    append_user_text(
        s,
        "Your final answer came before the reads it must rest on. Before you "
        "answer, " + "; ".join(gaps) + ".",
    )
    s.events.append(
        Event(
            "progress",
            f"read correction {granted.number} (charged): " + "; ".join(gaps),
            {"read_correction": record(granted)},
        )
    )
    return True


async def step(
    s: Session,
    transport: Transport,
    approve: Approve,
    policy: Policy,
    schema: dict[str, Any] | None = None,
    context_window: int = 32000,
    prices: Prices | None = None,
    on_delta: Callable[[Delta], None] | None = None,
    structured_output: str | None = None,
    retry: RetryPolicy = NO_RETRIES,
    on_event: Callable[[Event], None] | None = None,
    tool_calls: ToolCallLimit | None = None,
    max_corrections: int = SCHEMA_CORRECTIONS_DEFAULT,
    cutoffs: CutoffLimit | None = None,
    reads: ReadRequirement = NO_READS,
    check: Check | None = None,
    tools: Sequence[Tool] = (),
) -> Session:
    if tools:
        # The allowlist is the policy's, as for built-ins: a registered tool is
        # offered only when `policy.tools` names it. `pending` is kept, so a name
        # the policy allows but no tool supplies stays allowed.
        policy = replace(
            policy,
            external={**policy.external, **external_tools(tools)},
        )
    report = compact(s, context_window, image_input=policy.image_input)
    if report is not None:
        s.events.append(
            Event(
                "progress",
                f"compacted: elided {report.elided} tool results"
                + (f" and {report.inputs} tool inputs" if report.inputs else "")
                + (
                    f", dropped {report.thinking} thinking blocks"
                    if report.thinking
                    else ""
                )
                + f", ~{report.before:.0f} -> ~{report.after:.0f} tokens",
                {"compaction": asdict(report)},
            )
        )
    size = estimate(s, image_input=policy.image_input)
    if size >= context_window:
        s.status = "error"
        s.stop_reason = "context"
        s.error = f"context estimate {size:.0f} reaches window {context_window}"
        s.events.append(Event("error", s.error))
        return s
    input_messages = len(s.messages)
    input_chars = context_chars(s.messages, image_input=policy.image_input)
    model_messages = (
        s.messages
        if policy.image_input
        else _without_images(s.messages, policy.image_model)
    )
    offered = policy.schemas()
    constrained = _constrained(s, schema, structured_output, offered)
    if constrained:
        s.events.append(
            Event(
                "progress",
                f"schema-constrained answer turn ({structured_output})"
                + ("; tools withheld" if offered else ""),
                {
                    "structured_output": {
                        "mode": structured_output,
                        "tools_withheld": bool(offered),
                    }
                },
            )
        )
    # Off, the transport is called exactly as before: (messages, tools).
    streamed = bool(getattr(transport, "streaming", False)) and on_delta is not None

    async def attempt() -> Reply:
        if streamed:
            reply = None
            async with aclosing(
                cast(StreamingStructuredTransport, transport).stream_turn(
                    model_messages, [], response_schema=schema
                )
                if constrained
                else cast(StreamingTransport, transport).stream_turn(
                    model_messages, offered
                )
            ) as stream:
                async for item in stream:
                    if isinstance(item, Delta):
                        assert on_delta is not None
                        on_delta(item)
                    else:
                        reply = item
            if reply is None:
                raise RuntimeError("stream ended without a completed reply")
            return reply
        return await (
            cast(StructuredTransport, transport).turn(
                model_messages, [], response_schema=schema
            )
            if constrained
            else transport.turn(model_messages, offered)
        )

    reply = await _with_retries(s, transport, attempt, retry, on_event)
    s.last_input_tokens = (
        reply.usage.input + reply.usage.cache_read + reply.usage.cache_write
    )
    s.last_input_messages = input_messages
    s.last_input_chars = input_chars
    s.last_input_image_input = policy.image_input
    usage = turn_usage(reply, prices)
    s.usage += usage
    s.turns += 1
    # A cut-off reply is corrected while the invocation's budget lasts. The
    # decision is made before the reply is kept: an empty one has nothing to
    # keep, and an empty assistant turn is one the vendors reject on resume.
    corrected = False
    if reply.stop_reason in CUTOFF_REASONS:
        s.cutoffs += 1
        if cutoffs is not None:
            corrected = cutoffs.used < cutoffs.limit
            cutoffs.used += 1
            outcome = (
                f"correction {cutoffs.used} of {cutoffs.limit}"
                if corrected
                else f"cut-off budget of {cutoffs.limit} spent"
            )
            s.events.append(
                Event(
                    "progress",
                    f"reply cut off ({reply.stop_reason}) after "
                    f"{reply.usage.output} output tokens: {outcome}",
                    {
                        "cutoff": {
                            "kind": reply.stop_reason,
                            "output_tokens": reply.usage.output,
                            "attempt": cutoffs.used,
                            "limit": cutoffs.limit,
                            "corrected": corrected,
                        }
                    },
                )
            )
    if not (corrected and not reply.content):
        s.messages.append(Message(role="assistant", content=reply.content))

    for block in [] if streamed else reply.content:
        if block.get("type") == "text":
            s.events.append(Event("progress", block.get("text", "")))
        elif block.get("type") == "thinking":
            s.events.append(Event("thinking", block.get("thinking", "")))
    s.events.append(
        Event(
            "cost",
            f"{usage.input} in / {usage.output} out, "
            + (f"${usage.cost:.6f}" if usage.cost is not None else "cost unknown")
            + (" (estimate)" if usage.estimated else ""),
            usage.wire(),
        )
    )

    # A tool turn can carry already valid findings alongside its calls. Keep
    # the latest schema-valid document without claiming the task completed.
    if schema is not None:
        candidate = _final_text(reply.content)
        # On a turn with tool calls only: a plain answer is checked once, by the
        # final-answer branch below, which decides whether it is the output.
        kept = _check_passes(candidate, check) if reply.tool_calls else check is None
        if not _answer_errors(candidate, schema) and kept:
            s.output = extract_json(candidate)

    unfinished = _UNFINISHED.get(reply.stop_reason)
    s.stop_reason = reply.stop_reason

    if not reply.tool_calls:
        if unfinished and corrected:
            append_user_text(s, _CUTOFF_GUIDANCE[reply.stop_reason])
            return s
        if unfinished:
            s.status = "error"
            s.error = unfinished
            s.events.append(Event("error", unfinished))
            return s
        text = _final_text(reply.content)
        if not text and any(block.get("type") == "thinking" for block in reply.content):
            # Reasoning with no answer is what a translation to Anthropic's
            # shape can produce when a proxy drops it: the reasoning and the
            # answer arrive together and only one survives. Reporting done
            # here would read as a successful, silent completion.
            s.error = (
                "model emitted reasoning but no answer; a proxy translation "
                "may have dropped it"
            )
            s.status = "error"
            # The provider said the turn ended normally; left as end_turn, a
            # caller could not tell this from any other failed finished turn.
            s.stop_reason = "no_answer"
            s.events.append(Event("error", s.error))
            return s
        if schema is None:
            if _hold_for_reads(s, reads, max_corrections):
                return s
            s.status = "done"
            s.events.append(Event("output", text))
            return s
        errors = _answer_errors(text, schema)
        if not errors and check is not None:
            try:
                errors = _check_errors(extract_json(text), check)
            except CheckRaised as exc:
                s.status = "error"
                s.stop_reason = None
                s.error = str(exc)
                s.events.append(Event("error", s.error))
                return s
        if not errors:
            if _hold_for_reads(s, reads, max_corrections):
                return s
            s.output = extract_json(text)
            s.status = "done"
            s.events.append(Event("output", text, {"output": s.output}))
            return s
        complaint = "; ".join(errors)
        restated = schema_instruction(schema)
        granted = charge(s, errors, max_corrections, comparable=_parses(text))
        if granted is None:
            # The correction budget is spent. A model that cannot satisfy the
            # schema after its allowed corrections will not satisfy it on the
            # tenth turn either, and a caller waiting on data is better served
            # by a named failure than by a budget spent in a loop.
            s.status = "error"
            s.stop_reason = "schema_violation"
            s.error = f"answer does not satisfy the schema: {complaint}"
            s.events.append(
                Event("error", s.error, {"errors": [e.to_dict() for e in errors]})
            )
            return s
        append_user_text(s, correction_text(granted, errors, restated))
        s.events.append(
            Event(
                "progress",
                f"schema correction {granted.number} "
                f"({'charged' if granted.charged else 'free'}): {complaint}",
                {
                    "schema_correction": record(granted),
                    "errors": [e.to_dict() for e in errors],
                },
            )
        )
        return s

    for call in reply.tool_calls:
        s.events.append(
            Event("tool_use", call.name, dict(call.args), tool_use_id=call.id)
        )

    only_asks = all(call.name == "ask" for call in reply.tool_calls)

    def left_running(pgid: int, command: str) -> None:
        s.process_groups.append({"pgid": pgid, "command": command})

    def evidence(record: dict[str, Any]) -> None:
        # The session keeps the event's copy, so the two never disagree.
        event = Event(
            "progress",
            f"{record['tool']} evidence: {record['path']}",
            {"tool": record["tool"], "evidence": record},
            tool_use_id=record["tool_use_id"],
        )
        s.events.append(event)
        s.evidence.append(event.detail["evidence"])

    results: list[dict[str, Any]] = []
    unanswered = "tool dispatch failed"
    try:
        if unfinished:
            # Don't run a call from a truncated turn: its arguments may be cut
            # off, and a half-written `write` overwrites a real file.
            results = [
                tool_result(call.id, f"not run: {unfinished}", is_error=True)
                for call in reply.tool_calls
            ]
        else:
            # A refused ask (the policy excludes it) still goes to dispatch, so
            # the model learns it cannot ask at all rather than being told to
            # retry it alone.
            # ponytail: tool calls dispatch sequentially. Models do emit
            # parallel tool calls; asyncio.gather is the upgrade once a run is
            # measurably slow because of it, and not before.
            refused = False
            for call in reply.tool_calls:
                if call.name == "ask" and not only_asks and "ask" in policy.tools:
                    results.append(tool_result(call.id, ASK_NOT_ALONE, is_error=True))
                    continue
                if tool_calls is not None and tool_calls.used >= tool_calls.limit:
                    results.append(
                        tool_result(call.id, tool_calls.message(), is_error=True)
                    )
                    refused = True
                    continue
                # Counted as it starts: a call that is cancelled or raises was
                # still run.
                s.tool_calls += 1
                if tool_calls is not None:
                    tool_calls.used += 1
                result = await dispatch(
                    call, policy, approve, left_running=left_running, evidence=evidence
                )
                results.append(result)
                if "image" in result:
                    image = result["image"]
                    s.events.append(
                        Event(
                            "progress",
                            result["content"],
                            {
                                "tool": call.name,
                                "image": {
                                    "media_type": image["source"]["media_type"],
                                    "width": image["width"],
                                    "height": image["height"],
                                },
                            },
                            tool_use_id=call.id,
                        )
                    )
            if refused and tool_calls is not None:
                tool_calls.refused_turns += 1
    except asyncio.CancelledError:
        unanswered = INTERRUPTED
        raise
    finally:
        # Every tool_use gets an answer even if dispatch dies mid-way. A
        # transcript ending in an unanswered tool_use is rejected by the
        # vendor on every future resume, so the session would be dead for
        # good — an unwritable turn is worth more than an unusable file.
        results += [
            tool_result(call.id, unanswered, is_error=True)
            for call in reply.tool_calls[len(results) :]
        ]
        failed = any(r["is_error"] for r in results)
        if only_asks and failed and not unfinished:
            # The turn won't block, so an ask that succeeded was never
            # delivered. Left saying "recorded", the model may stop and its
            # question is lost.
            results = [
                r
                if r["is_error"]
                else tool_result(r["tool_use_id"], ASK_NOT_DELIVERED, is_error=True)
                for r in results
            ]
        s.messages.append(Message(role="user", content=results))
        # One result event per call, from the results as the transcript holds
        # them, so no event reports a value the model was not sent.
        for call, result in zip(reply.tool_calls, results, strict=True):
            s.events.append(result_event(call.name, result, policy))

    if unfinished and corrected:
        # Merged into the tool results just kept, so the turn stays well formed.
        append_user_text(s, _CUTOFF_GUIDANCE[reply.stop_reason] + _CALLS_NOT_RUN)
    elif unfinished:
        s.status = "error"
        s.error = unfinished
        s.events.append(Event("error", unfinished))
    elif only_asks and not failed:
        # Blocked on what happened, not on what the model called: a refused,
        # unapproved or malformed ask came back as an error and asked nothing.
        s.status = "blocked"
        s.questions = questions_from(reply.tool_calls)
    return s


async def _live_step(
    s: Session,
    transport: Transport,
    approve: Approve,
    policy: Policy,
    schema: dict[str, Any] | None,
    window: int,
    prices: Prices | None,
    structured_output: str | None = None,
    retry: RetryPolicy = NO_RETRIES,
    tool_calls: ToolCallLimit | None = None,
    max_corrections: int = SCHEMA_CORRECTIONS_DEFAULT,
    cutoffs: CutoffLimit | None = None,
    reads: ReadRequirement = NO_READS,
    check: Check | None = None,
) -> AsyncGenerator[Event, None]:
    queue: asyncio.Queue[Event] = asyncio.Queue()
    buffers: dict[Literal["progress", "thinking"], LiveText] = {
        "progress": LiveText(),
        "thinking": LiveText(),
    }

    def receive(delta: Delta) -> None:
        text = buffers[delta.kind].feed(delta.text)
        if text:
            queue.put_nowait(Event(delta.kind, text))

    def flush() -> None:
        for kind, buffer in buffers.items():
            text = buffer.finish()
            if text:
                queue.put_nowait(Event(kind, text))

    def retried(event: Event) -> None:
        # The abandoned attempt's last words go out before the retry, so they
        # never run into the next attempt's first ones.
        flush()
        queue.put_nowait(event)

    task = asyncio.create_task(
        step(
            s,
            transport,
            approve,
            policy,
            schema,
            window,
            prices,
            receive,
            structured_output,
            retry,
            retried,
            tool_calls,
            max_corrections=max_corrections,
            cutoffs=cutoffs,
            reads=reads,
            check=check,
        )
    )
    waiter: asyncio.Task[Event] | None = None
    try:
        while not task.done() or not queue.empty():
            if not queue.empty():
                yield queue.get_nowait()
                continue
            waiter = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait(
                {task, waiter}, return_when=asyncio.FIRST_COMPLETED
            )
            if waiter in done:
                yield waiter.result()
            else:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
            waiter = None
        # Flush unfinished words even when the provider failed; these are
        # progress, never a completed assistant message or valid findings.
        flush()
        while not queue.empty():
            yield queue.get_nowait()
        await task
    finally:
        if waiter is not None:
            waiter.cancel()
        if not task.done():
            task.cancel()
        await asyncio.gather(
            task, *([waiter] if waiter is not None else []), return_exceptions=True
        )


async def _run(
    session: Session,
    *,
    transport: Transport,
    approve: Approve = approve_all,
    policy: Policy | None = None,
    schema: dict[str, Any] | None = None,
    max_turns: int = MAX_TURNS_DEFAULT,
    budget_tokens: int | None = None,
    context_window: int | None = None,
    budget_usd: float | None = None,
    prices: Prices | None = None,
    structured_output: str | None = None,
    retry: RetryPolicy | None = None,
    max_tool_calls: int | None = None,
    max_corrections: int = SCHEMA_CORRECTIONS_DEFAULT,
    max_cutoffs: int | None = None,
    reads: ReadRequirement = NO_READS,
    check: Check | None = None,
) -> AsyncGenerator[Event, None]:
    """Advance the session to a terminal status, yielding events as they occur.

    The only place that catches broadly: a harness reports failures as events
    and a status, it does not hand a traceback to its caller.
    """
    configure_budgets(session, budget_tokens, budget_usd)
    if context_window is not None and (
        isinstance(context_window, bool)
        or not isinstance(context_window, int)
        or context_window <= 0
    ):
        raise ValueError("context_window must be a positive integer")
    # A resumed exhausted session must not call the backend, even for metadata.
    if session.status == "working" and check_budget(session):
        while session.events:
            yield session.events.pop(0)
        return
    window = context_window
    source = "flag"
    discover_prices = getattr(transport, "prices", None)
    try:
        if window is None:
            window = await transport.context_window()
            source = "backend"
        if prices is None and discover_prices is not None:
            prices = await discover_prices()
    except Exception as exc:
        # A provider failure like any other, not a failure of the MCP
        # connection that run() wraps this generator in.
        session.status = "error"
        session.stop_reason = None
        session.error = f"{type(exc).__name__}: {exc}"
        session.events.append(Event("error", session.error))
        while session.events:
            yield session.events.pop(0)
        return
    if window is None:
        window = 32000
        source = "default"
    session.events.append(
        Event(
            "progress",
            f"context window {window} ({source})",
            {
                "context_window": {
                    "tokens": window,
                    "source": source,
                }
            },
        )
    )
    policy = Policy() if policy is None else policy
    policy = replace(
        policy,
        image_input=(
            getattr(transport, "supports_image_input", False) is True
            and getattr(transport, "image_input", False) is True
        ),
        image_model=(
            f"{type(transport).__name__}/model="
            f"{getattr(transport, 'model', 'unspecified')}"
        ),
    )
    if schema is not None:
        state_schema(session, schema)
    # Recorded before the first turn, so a run that dies still says what it was
    # allowed to do.
    session.policy = policy.recorded()
    # What this invocation sent, not what the session first ran with: a
    # resume without the flags sends none.
    session.effort = getattr(transport, "effort", None)
    session.thinking_budget = getattr(transport, "thinking_budget", None)
    # Counted from where this invocation started, not from the session's
    # lifetime total. A caller may resume the same session once per feedback
    # round, and a cumulative budget makes every resume past the Nth die
    # instantly with a message that reads like a runaway loop.
    budget_from = session.turns
    # Per invocation too, for the same reason.
    tool_calls = ToolCallLimit(max_tool_calls) if max_tool_calls is not None else None
    cutoffs = CutoffLimit(max_cutoffs) if max_cutoffs is not None else None
    while session.status == "working":
        if check_budget(session):
            pass
        else:
            remaining = max_turns - (session.turns - budget_from)
            summary = remaining <= 0
            turn_policy = policy
            if summary:
                append_user_text(
                    session,
                    "Turn limit reached. Tools are disabled. Summarise what was done, "
                    "what remains, and any processes left running.",
                )
                turn_policy = replace(policy, tools=frozenset(), external={})
            elif remaining <= 5:
                warning = f"{remaining} turns left: wrap up and summarise."
                # Only a run with tools, built-in or MCP, has anything to wrap
                # up. A tool-free run's every turn is an answer: the text
                # would be an instruction its caller never wrote, ahead of
                # that answer, and would make the request depend on the turn
                # count. The event still tells a watching caller the count.
                if policy.schemas():
                    append_user_text(session, warning)
                session.events.append(Event("progress", warning))
            try:
                if getattr(transport, "streaming", False):
                    while session.events:
                        yield session.events.pop(0)
                    async with aclosing(
                        _live_step(
                            session,
                            transport,
                            approve,
                            turn_policy,
                            schema,
                            window,
                            prices,
                            structured_output,
                            retry or RetryPolicy(),
                            tool_calls,
                            max_corrections,
                            cutoffs,
                            reads,
                            check=check,
                        )
                    ) as stream:
                        async for event in stream:
                            yield event
                else:
                    await step(
                        session,
                        transport,
                        approve,
                        turn_policy,
                        schema,
                        window,
                        prices,
                        structured_output=structured_output,
                        retry=retry or RetryPolicy(),
                        tool_calls=tool_calls,
                        max_corrections=max_corrections,
                        cutoffs=cutoffs,
                        reads=reads,
                        check=check,
                    )
                if session.status not in ("done", "blocked"):
                    check_budget(session)
                if (
                    session.status == "working"
                    and tool_calls is not None
                    and tool_calls.refused_turns > 1
                ):
                    # Told once to answer, and it called again. Another turn
                    # would be the same refusal, so the run ends here.
                    session.status = "error"
                    session.stop_reason = "tool_calls"
                    session.error = (
                        f"Tool call limit reached ({tool_calls.limit} per run), "
                        "and the model kept calling tools. Resume for up to "
                        f"{tool_calls.limit} more."
                    )
                    session.events.append(Event("error", session.error))
            except Exception as exc:
                session.status = "error"
                # The last completed turn's reason describes that turn, not
                # this failure: a stale stop_reason on a new error lies.
                session.stop_reason = None
                session.error = f"{type(exc).__name__}: {exc}"
                session.events.append(Event("error", session.error))
            finally:
                if summary:
                    session.status = "error"
                    session.stop_reason = "max_turns"
                    session.error = (
                        f"Turn limit reached ({max_turns} per run). "
                        f"Type `continue` for up to {max_turns} more."
                    )
                    session.events.append(Event("error", session.error))
        while session.events:
            yield session.events.pop(0)


async def run(
    session: Session,
    *,
    transport: Transport,
    approve: Approve = approve_all,
    policy: Policy | None = None,
    schema: dict[str, Any] | None = None,
    max_turns: int = MAX_TURNS_DEFAULT,
    budget_tokens: int | None = None,
    context_window: int | None = None,
    budget_usd: float | None = None,
    prices: Prices | None = None,
    mcp_servers: dict[str, Server] | None = None,
    mcp_allow_all: bool = False,
    structured_output: StructuredOutput | None = None,
    retry: RetryPolicy | None = None,
    max_tool_calls: int | None = None,
    max_corrections: int = SCHEMA_CORRECTIONS_DEFAULT,
    max_cutoffs: int | None = None,
    require_reads: int = 0,
    require_read: Sequence[str] = (),
    check: Check | None = None,
    tools: Sequence[Tool] = (),
) -> AsyncGenerator[Event, None]:
    check_structured_output(transport, schema, structured_output)
    # Refused before any call: a bad registration is the caller's error, not a
    # tool's, so it raises from the generator rather than ending the run.
    caller = external_tools(tools)
    check_max_tool_calls(max_tool_calls)
    check_max_corrections(max_corrections)
    check_max_cutoffs(max_cutoffs)
    reads = build_reads(
        require_reads,
        list(require_read),
        root=policy.root if policy is not None else None,
    )
    if reads.active and "read" not in (policy or Policy()).tools:
        raise ValueError("a read requirement needs the read tool")
    check_check(check, schema)
    configure_budgets(session, budget_tokens, budget_usd)
    if context_window is not None and (
        isinstance(context_window, bool)
        or not isinstance(context_window, int)
        or context_window <= 0
    ):
        raise ValueError("context_window must be a positive integer")
    if session.status == "working" and check_budget(session):
        while session.events:
            yield session.events.pop(0)
        return
    try:
        async with connect(mcp_servers or {}) as mcp:
            external = {**mcp, **caller}
            base = policy or Policy()
            names = (
                base.tools | external.keys()
                if policy is None or mcp_allow_all
                else base.tools
            )
            active = replace(
                base, tools=frozenset(names), external=external, pending=frozenset()
            )
            async with aclosing(
                _run(
                    session,
                    transport=transport,
                    approve=approve,
                    policy=active,
                    schema=schema,
                    max_turns=max_turns,
                    budget_tokens=budget_tokens,
                    context_window=context_window,
                    budget_usd=budget_usd,
                    prices=prices,
                    structured_output=structured_output,
                    retry=retry,
                    max_tool_calls=max_tool_calls,
                    max_corrections=max_corrections,
                    max_cutoffs=max_cutoffs,
                    reads=reads,
                    check=check,
                )
            ) as stream:
                async for event in stream:
                    yield event
    except Exception as exc:
        session.status = "error"
        session.stop_reason = "mcp"
        session.error = f"MCP connection failed: {exc}"
        yield Event("error", session.error)
