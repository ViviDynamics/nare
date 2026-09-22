"""The agent loop. A plain Session advanced by an explicit step(), surfaced as
an async generator: resumable by construction, deterministic under test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Any

from nare.events import Event
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
from nare.transport import StopReason, Transport

MAX_TURNS_DEFAULT = 50

# A turn the vendor cut off, or that the model refused, is not a finished one.
# The conductor adapter maps done -> completed, so reporting either as done
# would tell it the task succeeded. The transport already takes care to map
# model_context_window_exceeded to max_tokens for exactly this reason; the
# loop is where that distinction was being thrown away.
_UNFINISHED: dict[StopReason, str] = {
    "max_tokens": "the model's turn was cut off at max_tokens",
    "refusal": "the model refused to continue",
}


def _final_text(content: list[dict[str, Any]]) -> str:
    return "\n".join(b.get("text", "") for b in content if b.get("type") == "text")


def _answer_errors(text: str, schema: dict[str, Any]) -> list[str]:
    """The validator's complaints about this answer, or none."""
    try:
        parsed = extract_json(text)
    except ValueError as exc:
        return [str(exc)]
    errors = validate(parsed, schema)
    return errors


async def step(
    s: Session,
    transport: Transport,
    approve: Approve,
    policy: Policy,
    schema: dict[str, Any] | None = None,
) -> Session:
    reply = await transport.turn(s.messages, policy.schemas())
    s.usage += reply.usage
    s.turns += 1
    s.messages.append(Message(role="assistant", content=reply.content))

    for block in reply.content:
        if block.get("type") == "text":
            s.events.append(Event("progress", block.get("text", "")))
        elif block.get("type") == "thinking":
            s.events.append(Event("thinking", block.get("thinking", "")))
    s.events.append(
        Event(
            "cost",
            f"{reply.usage.input} in / {reply.usage.output} out",
            asdict(reply.usage),
        )
    )

    unfinished = _UNFINISHED.get(reply.stop_reason)
    s.stop_reason = reply.stop_reason

    if not reply.tool_calls:
        if unfinished:
            s.status = "error"
            s.error = unfinished
            s.events.append(Event("error", unfinished))
            return s
        text = _final_text(reply.content)
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
            "Your answer did not satisfy the required JSON Schema: "
            f"{complaint}. Reply with JSON that satisfies it, and nothing else.",
        )
        return s

    for call in reply.tool_calls:
        s.events.append(Event("tool_use", call.name, dict(call.args)))

    results: list[dict[str, Any]] = []
    try:
        if unfinished:
            # Don't run a call from a truncated turn: its arguments may be cut
            # off, and a half-written `write` overwrites a real file.
            results = [
                tool_result(call.id, f"not run: {unfinished}", is_error=True)
                for call in reply.tool_calls
            ]
        else:
            # ponytail: tool calls dispatch sequentially. Models do emit
            # parallel tool calls; asyncio.gather is the upgrade once a run is
            # measurably slow because of it, and not before.
            for call in reply.tool_calls:
                results.append(await dispatch(call, policy, approve))
    finally:
        # Every tool_use gets an answer even if dispatch dies mid-way. A
        # transcript ending in an unanswered tool_use is rejected by the
        # vendor on every future resume, so the session would be dead for
        # good — an unwritable turn is worth more than an unusable file.
        results += [
            tool_result(call.id, "tool dispatch failed", is_error=True)
            for call in reply.tool_calls[len(results) :]
        ]
        s.messages.append(Message(role="user", content=results))

    if unfinished:
        s.status = "error"
        s.error = unfinished
        s.events.append(Event("error", unfinished))
    elif any(call.name == "ask" for call in reply.tool_calls):
        s.status = "blocked"
        s.questions = questions_from(reply.tool_calls)
    return s


async def run(
    session: Session,
    *,
    transport: Transport,
    approve: Approve = approve_all,
    policy: Policy | None = None,
    schema: dict[str, Any] | None = None,
    max_turns: int = MAX_TURNS_DEFAULT,
) -> AsyncIterator[Event]:
    """Advance the session to a terminal status, yielding events as they occur.

    The only place that catches broadly: a harness reports failures as events
    and a status, it does not hand a traceback to its caller.
    """
    policy = Policy() if policy is None else policy
    # Recorded before the first turn, so a run that dies still says what it was
    # allowed to do.
    session.policy = policy.recorded()
    # Counted from where this invocation started, not from the session's
    # lifetime total. Conductor resumes the same session once per feedback
    # round, and a cumulative budget makes every resume past the Nth die
    # instantly with a message that reads like a runaway loop.
    budget_from = session.turns
    while session.status == "working":
        if session.turns - budget_from >= max_turns:
            session.status = "error"
            session.stop_reason = "max_turns"
            session.error = f"stopped after {max_turns} turns"
            session.events.append(Event("error", session.error))
        else:
            try:
                await step(session, transport, approve, policy, schema)
            except Exception as exc:
                session.status = "error"
                # The last completed turn's reason describes that turn, not
                # this failure: a stale stop_reason on a new error lies.
                session.stop_reason = None
                session.error = f"{type(exc).__name__}: {exc}"
                session.events.append(Event("error", session.error))
        while session.events:
            yield session.events.pop(0)
