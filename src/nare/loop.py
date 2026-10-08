"""The agent loop. A plain Session advanced by an explicit step(), surfaced as
an async generator: resumable by construction, deterministic under test.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from copy import deepcopy
from dataclasses import asdict, replace
from typing import Any, Literal, cast

from nare.accounting import Prices, finite_number, turn_usage
from nare.compact import compact, context_chars, estimate
from nare.events import Event, LiveText
from nare.mcp import Server, connect
from nare.schema import extract_json, validate
from nare.session import Message, Session, append_user_text
from nare.tools import (
    Approve,
    Policy,
    approve_all,
    dispatch,
    questions_from,
    tool_result,
)
from nare.transport import Delta, StopReason, StreamingTransport, Transport

MAX_TURNS_DEFAULT = 50

# What an unanswered call says when a person stopped the turn, so the model is
# told what happened instead of that a tool broke.
INTERRUPTED = "interrupted by the user"


def budget_record(s: Session) -> dict[str, Any]:
    return {**s.budget, "used_tokens": s.usage.total_tokens, "used_usd": s.usage.cost}


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
        Event("error", s.error, {"budget": budget_record(s), "usage": asdict(s.usage)})
    )
    return True


# A turn the vendor cut off, or that the model refused, is not a finished one.
# The conductor adapter maps done -> completed, so reporting either as done
# would tell it the task succeeded. The transport already takes care to map
# model_context_window_exceeded to max_tokens for exactly this reason; the
# loop is where that distinction was being thrown away.
_UNFINISHED: dict[StopReason, str] = {
    "max_tokens": "the model's turn was cut off at max_tokens",
    "refusal": "the model refused to continue",
}

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


def schema_instruction(schema: dict[str, Any]) -> str:
    """What the model is told when a run is schema-constrained.

    Validating an answer without ever stating the requirement wastes the first
    turn by construction: a model cannot satisfy a shape it was never shown,
    and a weaker one never converges on it through corrections alone.
    """
    return (
        "Your final answer must be a JSON document satisfying this JSON Schema, "
        "and nothing else:\n"
        f"{json.dumps(schema)}"
    )


def _answer_errors(text: str, schema: dict[str, Any]) -> list[str]:
    """The validator's complaints about this answer, or none."""
    try:
        parsed = extract_json(text)
    except ValueError as exc:
        return [str(exc)]
    errors = validate(parsed, schema)
    return errors


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


async def step(
    s: Session,
    transport: Transport,
    approve: Approve,
    policy: Policy,
    schema: dict[str, Any] | None = None,
    context_window: int = 32000,
    prices: Prices | None = None,
    on_delta: Callable[[Delta], None] | None = None,
) -> Session:
    report = compact(s, context_window, image_input=policy.image_input)
    if report is not None:
        s.events.append(
            Event(
                "progress",
                f"compacted: elided {report.elided} tool results, "
                f"~{report.before:.0f} -> ~{report.after:.0f} tokens",
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
    streamed = bool(getattr(transport, "streaming", False)) and on_delta is not None
    if streamed:
        reply = None
        async with aclosing(
            cast(StreamingTransport, transport).stream_turn(
                model_messages, policy.schemas()
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
    else:
        reply = await transport.turn(model_messages, policy.schemas())
    s.last_input_tokens = (
        reply.usage.input + reply.usage.cache_read + reply.usage.cache_write
    )
    s.last_input_messages = input_messages
    s.last_input_chars = input_chars
    s.last_input_image_input = policy.image_input
    usage = turn_usage(reply, prices)
    s.usage += usage
    s.turns += 1
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
            + (f"${usage.cost:.6f}" if usage.cost is not None else "cost unknown"),
            asdict(usage),
        )
    )

    # A tool turn can carry already valid findings alongside its calls. Keep
    # the latest schema-valid document without claiming the task completed.
    if schema is not None:
        candidate = _final_text(reply.content)
        if not _answer_errors(candidate, schema):
            s.output = extract_json(candidate)

    unfinished = _UNFINISHED.get(reply.stop_reason)
    s.stop_reason = reply.stop_reason

    if not reply.tool_calls:
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
            s.events.append(Event("error", s.error))
            return s
        if schema is None:
            s.status = "done"
            s.events.append(Event("output", text))
            return s
        errors = _answer_errors(text, schema)
        if not errors:
            s.output = extract_json(text)
            s.status = "done"
            s.events.append(Event("output", text, {"output": s.output}))
            return s
        complaint = "; ".join(errors)
        restated = schema_instruction(schema)
        if s.schema_retried:
            # One correction round, then stop. A model that cannot satisfy the
            # schema twice will not satisfy it on the tenth turn either, and a
            # caller waiting on data is better served by a named failure than
            # by a budget spent in a loop.
            s.status = "error"
            s.stop_reason = "schema_violation"
            s.error = f"answer does not satisfy the schema: {complaint}"
            s.events.append(Event("error", s.error))
            return s
        s.schema_retried = True
        append_user_text(
            s,
            f"Your answer did not satisfy the required JSON Schema: {complaint}.\n"
            f"{restated}",
        )
        return s

    for call in reply.tool_calls:
        s.events.append(Event("tool_use", call.name, dict(call.args)))

    only_asks = all(call.name == "ask" for call in reply.tool_calls)
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
            for call in reply.tool_calls:
                if call.name == "ask" and not only_asks and "ask" in policy.tools:
                    results.append(tool_result(call.id, ASK_NOT_ALONE, is_error=True))
                    continue
                result = await dispatch(call, policy, approve)
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
                        )
                    )
                if call.name in policy.external:
                    s.events.append(
                        Event(
                            "progress",
                            f"MCP result: {call.name}",
                            {"tool": call.name, "tool_result": result},
                        )
                    )
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
        s.messages.append(Message(role="user", content=results))

    failed = any(r["is_error"] for r in results)
    if only_asks and failed and not unfinished:
        # The turn won't block, so an ask that succeeded was never delivered.
        # Left saying "recorded", the model may stop and its question is lost.
        # Rewritten in place: `results` is the content just appended.
        results[:] = [
            r
            if r["is_error"]
            else tool_result(r["tool_use_id"], ASK_NOT_DELIVERED, is_error=True)
            for r in results
        ]
    if unfinished:
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

    task = asyncio.create_task(
        step(s, transport, approve, policy, schema, window, prices, receive)
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
        for kind, buffer in buffers.items():
            text = buffer.finish()
            if text:
                queue.put_nowait(Event(kind, text))
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
    if window is None:
        window = await transport.context_window()
        source = "backend"
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
    if schema is not None and not session.schema_stated:
        # Said once, in the transcript rather than in a system prompt: a resume
        # carries it forward, and a reader can see exactly what the model was
        # asked for.
        append_user_text(session, schema_instruction(schema))
        session.schema_stated = True
    # Recorded before the first turn, so a run that dies still says what it was
    # allowed to do.
    session.policy = policy.recorded()
    # Counted from where this invocation started, not from the session's
    # lifetime total. Conductor resumes the same session once per feedback
    # round, and a cumulative budget makes every resume past the Nth die
    # instantly with a message that reads like a runaway loop.
    budget_from = session.turns
    while session.status == "working":
        if check_budget(session):
            pass
        elif session.turns - budget_from >= max_turns:
            session.status = "error"
            session.stop_reason = "max_turns"
            session.error = f"stopped after {max_turns} turns"
            session.events.append(Event("error", session.error))
        else:
            try:
                if getattr(transport, "streaming", False):
                    while session.events:
                        yield session.events.pop(0)
                    async with aclosing(
                        _live_step(
                            session, transport, approve, policy, schema, window, prices
                        )
                    ) as stream:
                        async for event in stream:
                            yield event
                else:
                    await step(
                        session, transport, approve, policy, schema, window, prices
                    )
                if session.status not in ("done", "blocked"):
                    check_budget(session)
            except Exception as exc:
                session.status = "error"
                # The last completed turn's reason describes that turn, not
                # this failure: a stale stop_reason on a new error lies.
                session.stop_reason = None
                session.error = f"{type(exc).__name__}: {exc}"
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
) -> AsyncGenerator[Event, None]:
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
        async with connect(mcp_servers or {}) as external:
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
                )
            ) as stream:
                async for event in stream:
                    yield event
    except Exception as exc:
        session.status = "error"
        session.stop_reason = "mcp"
        session.error = f"MCP connection failed: {exc}"
        yield Event("error", session.error)
