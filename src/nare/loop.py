"""The agent loop. A plain Session advanced by an explicit step(), surfaced as
an async generator: resumable by construction, deterministic under test.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import random
import re
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
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal, cast

from nare import mcp_defer as deferral
from nare.accounting import Prices, finite_number, turn_usage
from nare.caller_tools import Tool, external_tools
from nare.capacity import Change, Grant, RouteLimiter
from nare.compact import (
    COMPACTION_DEFAULT,
    COMPACTION_MODES,
    TARGET,
    TRIGGER,
    CompactionMode,
    CompactionPoint,
    CompactionReport,
    SummaryPlan,
    apply_summary,
    check_compaction_point,
    compact,
    context_chars,
    estimate,
    plan_summary,
)
from nare.corrections import (
    CORRECTION_LEAD,
    SCHEMA_CORRECTIONS_DEFAULT,
    charge,
    check_max_corrections,
    correction_text,
    record,
)
from nare.events import Event, LiveText, redact
from nare.mcp import Server, connect
from nare.reads import NO_READS, ReadRequirement, cited_unmet, read_record, unmet
from nare.reads import build as build_reads
from nare.schema import DuplicateKeyError, SchemaError, extract_json, validate
from nare.session import (
    Message,
    Session,
    Usage,
    append_user_text,
    drop_signed_thinking,
)
from nare.tools import (
    READ_ONLY_TOOLS,
    Approve,
    Policy,
    approve_all,
    dispatch,
    questions_from,
    route_deferred,
    tool_result,
)
from nare.transport import (
    CAPACITY_STATUS,
    Delta,
    Reply,
    StopReason,
    StreamingStructuredTransport,
    StreamingTransport,
    StructuredTransport,
    ToolChoiceTransport,
    Transient,
    Transport,
    capacity_refusal,
    transient,
)

MAX_TURNS_DEFAULT = 50

# What the model is told as the turn limit nears, and when it is reached. With
# WRAP_UP_TURNS or fewer turns left, a run with tools is told the count.
WRAP_UP_TURNS = 5
WRAP_UP = "{} turns left: wrap up and summarise."
TURN_LIMIT = (
    "Turn limit reached. Tools are disabled. Summarise what was done, "
    "what remains, and any processes left running."
)

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

# Admission queueing defaults: the most seconds one request may spend queued,
# and the most refusals one run queues. Backoff is 2 seconds doubling to 60,
# each scaled by a jitter in [0.5, 1); a Retry-After replaces it.
CAPACITY_WAIT_DEFAULT = 1800.0
CAPACITY_REFUSALS_DEFAULT = 50
CAPACITY_FIRST_WAIT = 2.0
CAPACITY_MAX_WAIT = 60.0


@dataclass
class CapacityQueue:
    """Admission queueing for one run: a capacity refusal is waited out, and the
    byte-identical request is sent again.

    Not a retry. A refusal is raised before dispatch, so nothing was generated
    or charged, and it spends none of `RetryPolicy`'s budget. Past either limit
    the refusal is surfaced exactly as it would be without this, which means it
    is then judged by the retry policy. `refused` counts this run's queued
    refusals, so a run gets a fresh queue: the CLI and `run()` callers make one
    per run. The session's own totals (`capacity_refusals`, `capacity_wait`)
    are cumulative, like `retries`.
    """

    max_wait: float = CAPACITY_WAIT_DEFAULT
    max_refusals: int = CAPACITY_REFUSALS_DEFAULT
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    rng: random.Random = field(default_factory=random.Random, repr=False)
    refused: int = 0
    # The route's adaptive limit (nare.capacity), shared by every run that
    # holds this queue. None: no limit, only the queue. A refusal cuts it
    # whether or not the queue admits the refusal.
    limiter: RouteLimiter | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_wait, bool)
            or not isinstance(self.max_wait, (int, float))
            or not math.isfinite(self.max_wait)
            or self.max_wait < 0
        ):
            raise ValueError(
                "capacity wait seconds must be a nonnegative finite number"
            )
        if (
            isinstance(self.max_refusals, bool)
            or not isinstance(self.max_refusals, int)
            or self.max_refusals < 0
        ):
            raise ValueError("capacity refusals must be a nonnegative integer")

    def wait(self, queued: int, retry_after: float | None) -> float:
        """The wait before the request's `queued`-th resend (1-based)."""
        if retry_after is not None:
            return float(retry_after)
        base = min(CAPACITY_FIRST_WAIT * 2 ** (queued - 1), CAPACITY_MAX_WAIT)
        return float(base * self.rng.uniform(0.5, 1.0))


def limit_event(change: Change) -> Event:
    """A change of the route's limit, with its reason. The detail carries no
    route name: it is a host and model, and events go to logs."""
    if change.reason == "refusal":
        text = (
            f"capacity limit cut from {change.limit_before} to "
            f"{change.limit_after} after a refusal; requests in flight drain"
        )
    else:
        text = (
            f"capacity limit raised from {change.limit_before} to "
            f"{change.limit_after} after a quiet period"
        )
    return Event(
        "progress",
        text,
        {
            "capacity_limit": {
                "limit": change.limit_after,
                "previous": change.limit_before,
                "reason": change.reason,
            }
        },
    )


def fresh_queue(queue: CapacityQueue | None) -> CapacityQueue | None:
    """A queue with the same limits and no refusals yet, for one run."""
    if queue is None:
        return None
    return replace(queue, refused=0)


async def _admitted(
    s: Session,
    limiter: RouteLimiter,
    attempt: Callable[[], Awaitable[Reply]],
    on_event: Callable[[Event], None] | None,
) -> Reply:
    """One attempt under the route's limit. The slot is held only while the
    attempt is in flight, so a refused request that waits holds nothing."""
    grant: Grant = await limiter.acquire()
    s.capacity_limit = grant.limit
    s.capacity_slot_wait += grant.waited
    events: list[Event] = []
    for note in grant.notes:
        events.append(
            Event(
                "progress",
                note,
                {"capacity_limit": {"limit": grant.limit, "reason": "declared"}},
            )
        )
    for change in grant.changes:
        events.append(limit_event(change))
    if grant.waited > 0:
        events.append(
            Event(
                "progress",
                f"waited {grant.waited:g}s for a request slot under limit "
                f"{grant.limit}",
                {
                    "capacity_limit": {
                        "limit": grant.limit,
                        "reason": "slot",
                        "waited": grant.waited,
                    }
                },
            )
        )
    for event in events:
        if on_event is not None:
            on_event(event)
        else:
            s.events.append(event)
    try:
        return await attempt()
    finally:
        grant.release()


@dataclass
class _Emitted:
    """Whether an attempt has handed anything to the caller. A stream item that
    reached on_delta means a response has started, and a refusal after that is
    not queued. Sticky across the attempts of one request."""

    any: bool = False


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


@dataclass
class SummaryTier:
    """--compaction summary for one invocation: on until a summary is not
    applied, then off, so a model that cannot summarize is asked once. Not
    saved: a resume tries again."""

    failed: bool = False


def check_compaction(value: object) -> None:
    if value not in COMPACTION_MODES:
        raise ValueError(
            f"compaction must be one of {', '.join(COMPACTION_MODES)}, not {value!r}"
        )


def compaction_point(trigger: float, target: float) -> CompactionPoint:
    """The validated point for one invocation (issue #43). Refused before any
    turn, as an unknown --compaction is."""
    check_compaction_point(trigger, target)
    return CompactionPoint(trigger, target)


# An ask beside other calls is not a question about the request: the turn has
# already acted on one reading of it. Honouring it told the caller that work
# was waiting on them when it was finished.
ASK_NOT_ALONE = (
    "ask was not recorded: call ask on its own, before making any change. "
    "The other calls in this turn ran."
)
SUMMARY_REFUSED = (
    "Tools are disabled for the turn-limit summary: this call was not run."
)
QUESTION_NUDGE = (
    "Your reply asks a question, but a text reply ends the session as finished, "
    "so the question may never be answered. If the request can be read more than "
    "one way and the readings lead to different changes, call ask on its own with "
    "your question. Otherwise, reply again without the question."
)
# Code a reply may quote, which a question inside does not make a question: a
# fenced block (closed, or running to the end of the reply) or a backtick span
# on one line. A URL's trailing '?' is not a question either.
_CODE = re.compile(r"(`{3,}|~{3,})[\s\S]*?(?:\1|\Z)|(`+)[^\n]*?\2")
_URL = re.compile(r"https?://\S+")
# A '?' that ends a sentence: followed by space, the end, or closing markup.
_SENTENCE_QUESTION = re.compile(r"\?(?=\s|$|[)\"'*_\]])")


def asks_in_prose(text: str) -> bool:
    """Whether a text reply asks a question outside code and URLs: a sentence-
    ending '?' that is not in a fenced block, a backtick span or a link.
    """
    prose = _URL.sub(" ", _CODE.sub(" ", text))
    return _SENTENCE_QUESTION.search(prose) is not None


def _history(s: Session) -> Iterable[tuple[Any, list[Any]]]:
    """Every message the session has held, as (role, blocks): the turns a
    summary replaced, from its archive, then the transcript. A record a file
    holds in another shape adds nothing."""
    for archived in s.summaries:
        replaced = archived.get("replaced") if isinstance(archived, dict) else None
        for message in replaced if isinstance(replaced, list) else ():
            if isinstance(message, dict) and isinstance(message.get("content"), list):
                yield message.get("role"), message["content"]
    for message in s.messages:
        yield message.role, message.content


def _wrote_a_file(s: Session) -> bool:
    """Whether a write or edit has succeeded in this session, including in the
    turns a summary has since replaced."""
    blocks = [
        (role, block)
        for role, content in _history(s)
        for block in content
        if isinstance(block, dict)
    ]
    writes = {
        block.get("id")
        for role, block in blocks
        if role == "assistant"
        and block.get("type") == "tool_use"
        and block.get("name") in ("write", "edit")
    }
    return any(
        role == "user"
        and block.get("type") == "tool_result"
        and block.get("tool_use_id") in writes
        and not block.get("is_error")
        for role, block in blocks
    )


def _nudge_question(
    s: Session, text: str, policy: Policy, *, enabled: bool, summary: bool
) -> bool:
    """Take one more turn when a headless text reply asked a question in prose.

    A text reply ends the run as finished, so a question in one is never
    answered. Once per session, and only before any write or edit: a model
    that has done the work and offers follow-up is not nudged. Returns True
    when the nudge was sent and the run continues.
    """
    # A summary is the last turn of a run that has reached its cap: a nudge
    # would only add a turn the cap has already ruled out.
    if not enabled or summary or s.ask_nudged or "ask" not in policy.tools:
        return False
    if _wrote_a_file(s) or not asks_in_prose(text):
        return False
    s.ask_nudged = True
    append_user_text(s, QUESTION_NUDGE)
    s.events.append(
        Event(
            "progress",
            "reply asked a question in prose: nudged toward ask",
            {"question_nudge": {"asked": True}},
        )
    )
    return True


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


def prefix_changes(recorded: dict[str, Any], now: dict[str, Any]) -> list[str]:
    """What about the tool set differs from what the session recorded.

    Empty for a session with no record (a file from before the record existed),
    which keeps today's behaviour. A digest is compared only when the record has
    one, so a file saved before MCP digests were kept is not read as changed.
    """
    if "tools" not in recorded:
        return []
    changed: list[str] = []
    before, after = set(recorded["tools"]), set(now["tools"])
    if before != after:
        parts = []
        if after - before:
            parts.append("added " + ", ".join(sorted(after - before)))
        if before - after:
            parts.append("removed " + ", ".join(sorted(before - after)))
        changed.append("tools " + " and ".join(parts))
    if recorded.get("mcp_defer") != now.get("mcp_defer"):
        # The request's external part changes shape, so the digest below would
        # only restate it.
        state = "on" if now.get("mcp_defer") else "off"
        changed.append(f"MCP tool deferral turned {state}")
    elif "external_sha256" in recorded and recorded["external_sha256"] != now.get(
        "external_sha256"
    ):
        changed.append("the definitions of MCP tools")
    return changed


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


# A check takes the parsed answer, and optionally the run's reads as a second
# argument, and returns its errors: SchemaError values, or
# a mapping of code, location, message and guidance, or plain strings. An empty
# result (or None) passes. It runs only on an answer that passes the schema.
# A check may be async: an awaitable result is awaited, so a check that waits
# on a process or the network does not hold the event loop (#296).
Check = Callable[..., Iterable[Any] | Awaitable[Iterable[Any] | None] | None]


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


def _takes_reads(check: Check) -> bool:
    """Whether a check takes the run's reads as a second positional argument.

    Only a callable with two positional parameters is given them, so a check
    written for the answer alone (including one that takes `*args`) is called
    exactly as before.
    """
    try:
        params = inspect.signature(check).parameters.values()
    except (TypeError, ValueError):
        return False
    positional = [
        p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    return len(positional) >= 2


async def _check_errors(
    parsed: Any, check: Check, reads: list[dict[str, Any]]
) -> list[SchemaError]:
    """The check's errors for a parsed answer. A check that raises, or returns
    something other than a list of errors, raises CheckRaised. `reads` are the
    run's read records, passed on to a check that takes them. An awaitable
    result is awaited; a cancellation passes through, as for any other await."""
    try:
        found = check(parsed, reads) if _takes_reads(check) else check(parsed)
        if inspect.isawaitable(found):
            found = await found
        if isinstance(found, (str, Mapping)):
            raise TypeError("a check returns a list of errors, not one value")
        return [_as_error(item) for item in (found or ())]
    except Exception as exc:
        raise CheckRaised(f"check raised {type(exc).__name__}: {exc}") from exc


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
    *,
    capacity: CapacityQueue | None = None,
    emitted: _Emitted | None = None,
) -> Reply:
    """One model request, resent on a transient failure within the budget.

    A failed attempt left nothing behind: the turn, usage and transcript are
    only written once a reply arrives, and the messages resent are the same
    ones, so a caching backend still finds its prefix. A failure that is not
    transient, or that outlives the budget, is raised as it came.

    A capacity refusal that `capacity` admits is queued before any of that: it
    is waited out and resent identically, and counted in `capacity_refusals` and
    `capacity_wait`, never in `retries`. One the queue does not admit falls
    through to the retry policy, as it did before the queue existed.
    """
    classify = getattr(transport, "transient", None) or transient
    markers: tuple[str, ...] = getattr(transport, "capacity_markers", ())
    limiter = capacity.limiter if capacity is not None else None

    async def admitted() -> Reply:
        if limiter is None:
            return await attempt()
        return await _admitted(s, limiter, attempt, on_event)

    made = 0
    waited = 0.0
    queued = 0
    queued_wait = 0.0
    while True:
        try:
            return await admitted()
        except Exception as exc:
            if capacity is not None:
                refusal = capacity_refusal(
                    exc, markers, emitted=emitted is not None and emitted.any
                )
                if refusal is not None:
                    if limiter is not None:
                        cut_to = limiter.refused()
                        if cut_to is not None:
                            s.capacity_limit = cut_to.limit_after
                            event = limit_event(cut_to)
                            if on_event is not None:
                                on_event(event)
                            else:
                                s.events.append(event)
                    wait = capacity.wait(queued + 1, refusal.retry_after)
                    if (
                        queued_wait + wait <= capacity.max_wait
                        and capacity.refused < capacity.max_refusals
                    ):
                        queued += 1
                        queued_wait += wait
                        capacity.refused += 1
                        s.capacity_refusals += 1
                        s.capacity_wait += wait
                        event = Event(
                            "progress",
                            f"capacity refusal (HTTP {CAPACITY_STATUS}, "
                            f"{refusal.marker}); queued {queued} for {wait:g}s, "
                            "then resent unchanged",
                            {
                                "capacity": {
                                    "attempt": queued,
                                    "wait": wait,
                                    "waited": queued_wait,
                                    "refusals": capacity.refused,
                                    "refusals_limit": capacity.max_refusals,
                                    "status": CAPACITY_STATUS,
                                    "reason": refusal.marker,
                                }
                            },
                        )
                        if on_event is not None:
                            on_event(event)
                        else:
                            s.events.append(event)
                        await capacity.sleep(wait)
                        continue
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


# How a read correction begins; the reads still owed follow it.
READS_HELD = (
    "Your final answer came before the reads it must rest on. Before you answer, "
)


def _hold_for_reads(
    s: Session, reads: ReadRequirement, max_corrections: int, answer: Any = None
) -> bool:
    """Hold a final answer the run has not read enough to rest on.

    Returns False when nothing is owed, and the answer is accepted. Otherwise a
    read correction is charged and the model told what is missing (returns True,
    the run continues), or, with the budget spent, the run ends `error` with
    `reads_required` (returns True, the run is over). `answer` is the parsed
    answer, which the cited-path requirement reads; it is set only for a
    schema answer, the only kind a cited requirement can have.
    """
    gaps = unmet(s, reads)
    if reads.cited:
        gaps += cited_unmet(s, reads, answer)
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
    append_user_text(s, READS_HELD + "; ".join(gaps) + ".")
    s.events.append(
        Event(
            "progress",
            f"read correction {granted.number} (charged): " + "; ".join(gaps),
            {"read_correction": record(granted)},
        )
    )
    return True


# The `cost` event's `detail.call` for a request that is not a turn.
SUMMARY_CALL = "compaction_summary"

# The user text the loop writes about the run itself, from the texts that write
# it. A summary reads these and does not carry them into the first message:
# carried, every countdown and correction would stay beside the task for good.
# The schema statements are not among them: they state the caller's schema.
_NOTICES = frozenset(
    {
        QUESTION_NUDGE,
        TURN_LIMIT,
        *(WRAP_UP.format(n) for n in range(1, WRAP_UP_TURNS + 1)),
        *_CUTOFF_GUIDANCE.values(),
        *(guidance + _CALLS_NOT_RUN for guidance in _CUTOFF_GUIDANCE.values()),
    }
)
# The notices whose end is filled in: the reads owed, the schema's complaints.
_NOTICE_LEADS = (READS_HELD, CORRECTION_LEAD)


def loop_notice(text: str) -> bool:
    """Whether a user text block is one of the loop's own notices, not text a
    caller or a person supplied (a prompt, feedback, an answer to `ask`, a
    queued line, a schema statement)."""
    return text in _NOTICES or text.startswith(_NOTICE_LEADS)


# How a summary request ended. All but `applied` leave the session as `elide`
# would.
SummaryStatus = Literal[
    "applied", "failed", "empty", "tool_call", "refused", "cut_off", "too_long"
]
SUMMARY_STATUSES: tuple[SummaryStatus, ...] = (
    "applied",
    "failed",
    "empty",
    "tool_call",
    "refused",
    "cut_off",
    "too_long",
)


def _cost_event(usage: Usage, call: str | None = None) -> Event:
    """One request's cost. `call` names a request that is not a turn."""
    detail = usage.wire()
    if call is not None:
        detail["call"] = call
    return Event(
        "cost",
        f"{usage.input} in / {usage.output} out, "
        + (f"${usage.cost:.6f}" if usage.cost is not None else "cost unknown")
        + (" (estimate)" if usage.estimated else ""),
        detail,
    )


def _turns(n: int) -> str:
    return f"{n} turn" if n == 1 else f"{n} turns"


def _compaction_text(report: CompactionReport) -> str:
    """The compaction event's text. A summary that was not applied leaves an
    elision, whose text is the one `elide` mode writes."""
    if report.summarized:
        done = f"summarized {_turns(report.summarized)}"
    else:
        done = f"elided {report.elided} tool results" + (
            f" and {report.inputs} tool inputs" if report.inputs else ""
        )
    return (
        f"compacted: {done}"
        + (f", dropped {report.thinking} thinking blocks" if report.thinking else "")
        + f", ~{report.before:.0f} -> ~{report.after:.0f} tokens"
    )


async def _summarize(
    s: Session,
    transport: Transport,
    policy: Policy,
    plan: SummaryPlan,
    tier: SummaryTier,
    *,
    window: int,
    prices: Prices | None,
    retry: RetryPolicy,
    on_event: Callable[[Event], None] | None,
    capacity: CapacityQueue | None = None,
) -> tuple[CompactionReport | None, bool]:
    """Ask the model for the summary `plan` describes, and apply it.

    Returns the report when the summary was applied, and whether a reply was
    accounted. The request is not a turn: it counts in `usage` and `retries`,
    and the transcript changes only through `apply_summary`. It is never
    streamed, so no delta of it reaches the caller. A summary that is not
    applied turns `tier` off and leaves the session as `elide` would. A request
    that raises is one of those, but cancellation is not an `Exception`, so a
    deadline or a stop signal still ends the run.
    """
    request = (
        plan.request
        if policy.image_input
        else _without_images(plan.request, policy.image_model)
    )
    offered = policy.schemas()

    async def attempt() -> Reply:
        # As the turn-limit summary does: the turn's tools stay on the wire, so
        # the prefix the signed thinking binds to is the one it was made
        # against, and tool_choice none forbids calls. A run that offers no
        # tools sends its ordinary request.
        if offered:
            return await cast(ToolChoiceTransport, transport).turn(
                request, offered, tool_choice="none"
            )
        return await transport.turn(request, offered)

    status: SummaryStatus
    error: str | None = None
    usage: Usage | None = None
    text = ""
    report = None
    try:
        reply = await _with_retries(
            s, transport, attempt, retry, on_event, capacity=capacity
        )
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
    else:
        usage = turn_usage(reply, prices)
        s.usage += usage
        s.events.append(_cost_event(usage, SUMMARY_CALL))
        text = _final_text(reply.content).strip()
        if reply.tool_calls:
            status = "tool_call"
        elif reply.stop_reason == "refusal":
            # Its text says no, and would stand in for the turns it replaces.
            status = "refused"
        elif reply.stop_reason in CUTOFF_REASONS:
            status = "cut_off"
        elif not text:
            status = "empty"
        else:
            report = apply_summary(
                s,
                plan,
                text,
                window,
                image_input=policy.image_input,
                notice=loop_notice,
            )
            status = "applied" if report is not None else "too_long"
    if status != "applied":
        tier.failed = True
    if status == "applied":
        notice = (
            f"compaction summary: {_turns(plan.turns)} into ~{len(text) / 4:.0f} tokens"
        )
    else:
        notice = f"compaction summary discarded ({status}); eliding instead"
    s.events.append(
        Event(
            "progress",
            notice,
            {
                "compaction_summary": {
                    "status": status,
                    "error": error,
                    "turns": plan.turns,
                    "messages": plan.messages,
                    "replaced_chars": plan.replaced_chars,
                    "chars": len(text),
                    "usage": usage.wire() if usage is not None else None,
                    "text": text or None,
                }
            },
        )
    )
    return report, status != "failed"


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
    summary: bool = False,
    question_nudge: bool = False,
    compaction: CompactionMode = COMPACTION_DEFAULT,
    summary_tier: SummaryTier | None = None,
    compaction_trigger: float = TRIGGER,
    compaction_target: float = TARGET,
    capacity: CapacityQueue | None = None,
) -> Session:
    # As run() does: a caller that drives step() itself with a typo is told,
    # rather than getting elide.
    check_compaction(compaction)
    point = compaction_point(compaction_trigger, compaction_target)
    if tools:
        # The allowlist is the policy's, as for built-ins: a registered tool is
        # offered only when `policy.tools` names it. `pending` is kept, so a name
        # the policy allows but no tool supplies stays allowed.
        policy = replace(
            policy,
            external={**policy.external, **external_tools(tools)},
        )
    report: CompactionReport | None = None
    tier = summary_tier if summary_tier is not None else SummaryTier()
    # Not on the turn-limit summary: the run ends after that call, and a
    # resume compacts on its first step anyway. Not once a summary has not
    # been applied in this invocation.
    spent = False
    if compaction == "summary" and not summary and not tier.failed:
        cap = getattr(transport, "max_tokens", None)
        plan = plan_summary(
            s,
            context_window,
            image_input=policy.image_input,
            point=point,
            output_cap=(
                cap
                if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0
                else None
            ),
        )
        if plan is not None:
            report, spent = await _summarize(
                s,
                transport,
                policy,
                plan,
                tier,
                window=context_window,
                prices=prices,
                retry=retry,
                on_event=on_event,
                capacity=capacity,
            )
    if report is None:
        report = compact(s, context_window, image_input=policy.image_input, point=point)
    if report is not None:
        report = replace(report, mode=compaction)
        s.events.append(
            Event("progress", _compaction_text(report), {"compaction": asdict(report)})
        )
    # A summary request that crossed a limit has still been kept when it passed
    # its checks, and the turn is not sent. Checked before the context size, so
    # the run ends `budget` even when the estimate still reaches the window.
    if spent and check_budget(s):
        return s
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
    # A summary keeps this turn's tools on the wire, so the request's prefix
    # matches the turn before it: signed thinking binds to the tools array, and
    # a tool set rebuilt mid-run is rejected by the vendor. tool_choice none
    # keeps the model from calling them, and any call it makes is refused. A
    # constrained turn withholds tools, so a summary of a run that offers tools
    # is never constrained.
    keep_tools = summary and bool(offered)
    constrained = not keep_tools and _constrained(s, schema, structured_output, offered)
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

    emitted = _Emitted()

    async def attempt() -> Reply:
        if streamed:
            reply = None
            async with aclosing(
                cast(StreamingStructuredTransport, transport).stream_turn(
                    model_messages, [], response_schema=schema
                )
                if constrained
                else cast(ToolChoiceTransport, transport).stream_turn(
                    model_messages, offered, tool_choice="none"
                )
                if keep_tools
                else cast(StreamingTransport, transport).stream_turn(
                    model_messages, offered
                )
            ) as stream:
                async for item in stream:
                    if isinstance(item, Delta):
                        assert on_delta is not None
                        emitted.any = True
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
            else cast(ToolChoiceTransport, transport).turn(
                model_messages, offered, tool_choice="none"
            )
            if keep_tools
            else transport.turn(model_messages, offered)
        )

    reply = await _with_retries(
        s, transport, attempt, retry, on_event, capacity=capacity, emitted=emitted
    )
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
    s.events.append(_cost_event(usage))

    # A tool turn can carry already valid findings alongside its calls. Keep
    # the latest schema-valid document without claiming the task completed.
    # With a check, nothing is kept here: the check runs only on a candidate
    # final answer, and the final-answer branch below decides whether it is
    # the output. Turn text that is not JSON is never parsed for a check.
    if schema is not None and check is None:
        candidate = _final_text(reply.content)
        if not _answer_errors(candidate, schema):
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
            if _nudge_question(
                s, text, policy, enabled=question_nudge, summary=summary
            ):
                return s
            if _hold_for_reads(s, reads, max_corrections):
                return s
            s.status = "done"
            s.events.append(Event("output", text))
            return s
        if summary:
            # The summary is not an answer: the run ends `max_turns` at the cap
            # either way, so a free-text summary is not checked against the
            # schema and does not spend a correction.
            return s
        errors = _answer_errors(text, schema)
        if not errors and check is not None:
            try:
                errors = await _check_errors(
                    extract_json(text), check, read_record(s)
                )
            except CheckRaised as exc:
                s.status = "error"
                s.stop_reason = None
                s.error = str(exc)
                s.events.append(Event("error", s.error))
                return s
        if not errors:
            answer = extract_json(text)
            if _hold_for_reads(s, reads, max_corrections, answer):
                return s
            s.output = answer
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

    def bash_rule(record: dict[str, Any]) -> None:
        # The session keeps the event's copy, so the two never disagree. The
        # command text is never recorded: only the rule that decided the call.
        event = Event(
            "progress",
            f"bash {record['effect']} by rule: {', '.join(record['patterns'])}",
            {"tool": "bash", "bash_rule": record},
            tool_use_id=record["tool_use_id"],
        )
        s.events.append(event)
        s.bash_rules.append(event.detail["bash_rule"])

    results: list[dict[str, Any]] = []
    # The name each call's result event carries, by call id: the real tool's
    # name for a call that mcp_call ran, the call's own name otherwise.
    named: dict[str, str] = {}
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
                if summary:
                    results.append(tool_result(call.id, SUMMARY_REFUSED, is_error=True))
                    continue
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
                routed = route_deferred(call, policy, s.mcp_loaded)
                if routed is not None and routed.loaded:
                    s.events.append(
                        Event(
                            "progress",
                            "MCP tools loaded: " + ", ".join(routed.loaded),
                            {"mcp_defer": {"loaded": routed.loaded}},
                        )
                    )
                if routed is not None and routed.result is not None:
                    named[call.id] = routed.name
                    results.append(routed.result)
                    continue
                to_run = call if routed is None else routed.call
                assert to_run is not None
                named[call.id] = to_run.name
                result = await dispatch(
                    to_run,
                    policy,
                    approve,
                    left_running=left_running,
                    evidence=evidence,
                    rule_match=bash_rule,
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
            s.events.append(result_event(named.get(call.id, call.name), result, policy))

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
    summary: bool = False,
    question_nudge: bool = False,
    compaction: CompactionMode = COMPACTION_DEFAULT,
    summary_tier: SummaryTier | None = None,
    compaction_trigger: float = TRIGGER,
    compaction_target: float = TARGET,
    capacity: CapacityQueue | None = None,
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
            summary=summary,
            question_nudge=question_nudge,
            compaction=compaction,
            compaction_trigger=compaction_trigger,
            compaction_target=compaction_target,
            summary_tier=summary_tier,
            capacity=capacity,
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
    inbox: Callable[[], list[str]] | None = None,
    question_nudge: bool = False,
    compaction: CompactionMode = COMPACTION_DEFAULT,
    compaction_trigger: float = TRIGGER,
    compaction_target: float = TARGET,
    capacity: CapacityQueue | None = None,
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
    recorded = policy.recorded()
    changed = prefix_changes(session.policy, recorded)
    if changed:
        # Signed thinking is bound to the tools it was made with, and a request
        # that rebuilds the tool set is rejected. Dropped here, and said.
        dropped = drop_signed_thinking(session)
        if dropped:
            outcome = (
                f"Signed thinking dropped from {dropped} blocks, since it is "
                "bound to the tools it was made with"
            )
        else:
            outcome = "No signed thinking to drop"
        session.events.append(
            Event(
                "progress",
                "tool set changed on resume: " + "; ".join(changed) + ". " + outcome,
                {"tools": {"changed": changed, "thinking_dropped": dropped}},
            )
        )
    # A loaded definition is kept only while it is the one the model saw. The
    # tools array does not move for this, so signed thinking is not dropped.
    session.mcp_loaded, stale = deferral.reconcile_loaded(
        session.mcp_loaded, policy.deferred()
    )
    if stale:
        session.events.append(
            Event(
                "progress",
                "MCP tools to load again on this resume (their definition changed, "
                "or deferral is off): " + ", ".join(stale),
                {"mcp_defer": {"dropped": stale}},
            )
        )
    session.policy = recorded
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
    # Per run, as the limits above: a caller's queue is only its configuration.
    capacity = fresh_queue(capacity)
    summary_tier = SummaryTier() if compaction == "summary" else None
    while session.status == "working":
        if inbox is not None:
            # What a person typed while the last step ran. It follows that
            # step's tool results, so the model reads it on this turn.
            for text in inbox():
                append_user_text(session, text)
                session.events.append(Event("progress", text, {"queued": True}))
            while session.events:
                yield session.events.pop(0)
        if check_budget(session):
            pass
        else:
            remaining = max_turns - (session.turns - budget_from)
            summary = remaining <= 0
            if summary:
                append_user_text(session, TURN_LIMIT)
            elif remaining <= WRAP_UP_TURNS:
                warning = WRAP_UP.format(remaining)
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
                            policy,
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
                            summary=summary,
                            question_nudge=question_nudge,
                            compaction=compaction,
                            compaction_trigger=compaction_trigger,
                            compaction_target=compaction_target,
                            summary_tier=summary_tier,
                            capacity=capacity,
                        )
                    ) as stream:
                        async for event in stream:
                            yield event
                else:
                    await step(
                        session,
                        transport,
                        approve,
                        policy,
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
                        summary=summary,
                        question_nudge=question_nudge,
                        compaction=compaction,
                        compaction_trigger=compaction_trigger,
                        compaction_target=compaction_target,
                        summary_tier=summary_tier,
                        capacity=capacity,
                    )
                # A summary request that crossed a limit already ended the
                # step with `budget`; checking again would report it twice.
                if (
                    session.status not in ("done", "blocked")
                    and session.stop_reason != "budget"
                ):
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
    approve: Approve | None = None,
    policy: Policy | None = None,
    schema: dict[str, Any] | None = None,
    max_turns: int = MAX_TURNS_DEFAULT,
    budget_tokens: int | None = None,
    context_window: int | None = None,
    budget_usd: float | None = None,
    prices: Prices | None = None,
    mcp_servers: dict[str, Server] | None = None,
    mcp_allow_all: bool = False,
    mcp_defer: bool = False,
    structured_output: StructuredOutput | None = None,
    retry: RetryPolicy | None = None,
    max_tool_calls: int | None = None,
    max_corrections: int = SCHEMA_CORRECTIONS_DEFAULT,
    max_cutoffs: int | None = None,
    require_reads: int = 0,
    require_read: Sequence[str] = (),
    require_cited_reads: Sequence[str] = (),
    check: Check | None = None,
    inbox: Callable[[], list[str]] | None = None,
    tools: Sequence[Tool] = (),
    question_nudge: bool = False,
    compaction: CompactionMode = COMPACTION_DEFAULT,
    compaction_trigger: float = TRIGGER,
    compaction_target: float = TARGET,
    capacity: CapacityQueue | None = None,
) -> AsyncGenerator[Event, None]:
    check_structured_output(transport, schema, structured_output)
    # Refused before any call: a bad registration is the caller's error, not a
    # tool's, so it raises from the generator rather than ending the run.
    caller = external_tools(tools)
    if mcp_defer or (policy is not None and policy.defer_mcp):
        # The search step owns these names while deferral is on. A caller tool
        # that took one would be routed to the step, or sent beside it under
        # the same name, so the caller's error is named here, before any call.
        taken = sorted(set(caller) & set(deferral.META_TOOLS))
        if taken:
            raise ValueError(
                f"tool name(s) {', '.join(taken)} are reserved while MCP tool "
                "deferral is on; rename the caller tool"
            )
    check_max_tool_calls(max_tool_calls)
    check_max_corrections(max_corrections)
    check_max_cutoffs(max_cutoffs)
    check_compaction(compaction)
    check_compaction_point(compaction_trigger, compaction_target)
    # No policy: read-only tools, whatever approver is passed. Only an explicit
    # policy enables write, edit or bash; the approver gates what it allows.
    base = Policy(tools=READ_ONLY_TOOLS) if policy is None else policy
    approver = approve_all if approve is None else approve
    reads = build_reads(
        require_reads,
        list(require_read),
        root=policy.root if policy is not None else None,
        cited=list(require_cited_reads),
    )
    if reads.active and "read" not in base.tools:
        raise ValueError("a read requirement needs the read tool")
    if reads.cited and schema is None:
        raise ValueError(
            "require_cited_reads needs a schema: it reads the fields of the answer"
        )
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
            names = (
                base.tools | external.keys()
                if policy is None or mcp_allow_all
                else base.tools
            )
            active = replace(
                base,
                tools=frozenset(names),
                external=external,
                pending=frozenset(),
                defer_mcp=base.defer_mcp or mcp_defer,
            )
            async with aclosing(
                _run(
                    session,
                    transport=transport,
                    approve=approver,
                    policy=active,
                    schema=schema,
                    max_turns=max_turns,
                    budget_tokens=budget_tokens,
                    context_window=context_window,
                    budget_usd=budget_usd,
                    prices=prices,
                    structured_output=structured_output,
                    retry=retry,
                    capacity=capacity,
                    max_tool_calls=max_tool_calls,
                    max_corrections=max_corrections,
                    max_cutoffs=max_cutoffs,
                    reads=reads,
                    check=check,
                    inbox=inbox,
                    question_nudge=question_nudge,
                    compaction=compaction,
                    compaction_trigger=compaction_trigger,
                    compaction_target=compaction_target,
                )
            ) as stream:
                async for event in stream:
                    yield event
    except Exception as exc:
        session.status = "error"
        session.stop_reason = "mcp"
        session.error = f"MCP connection failed: {exc}"
        yield Event("error", session.error)
