"""The agent loop. A plain Session advanced by an explicit step(), surfaced as
an async generator: resumable by construction, deterministic under test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Any

from nare.events import Event
from nare.session import Message, Session
from nare.tools import TOOL_SCHEMAS, Approve, approve_all, dispatch, questions_from
from nare.transport import Transport

MAX_TURNS_DEFAULT = 50


def _final_text(content: list[dict[str, Any]]) -> str:
    return "\n".join(b.get("text", "") for b in content if b.get("type") == "text")


async def step(s: Session, transport: Transport, approve: Approve) -> Session:
    reply = await transport.turn(s.messages, TOOL_SCHEMAS)
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

    if not reply.tool_calls:
        s.status = "done"
        s.stop_reason = reply.stop_reason
        s.events.append(Event("output", _final_text(reply.content)))
        return s

    for call in reply.tool_calls:
        s.events.append(Event("tool_use", call.name, dict(call.args)))

    # ponytail: tool calls dispatch sequentially. Models do emit parallel tool
    # calls; asyncio.gather is the upgrade once a run is measurably slow
    # because of it, and not before.
    results = [await dispatch(call, approve) for call in reply.tool_calls]
    s.messages.append(Message(role="user", content=results))

    if any(call.name == "ask" for call in reply.tool_calls):
        s.status = "blocked"
        s.questions = questions_from(reply.tool_calls)
        s.stop_reason = reply.stop_reason
    return s


async def run(
    session: Session,
    *,
    transport: Transport,
    approve: Approve = approve_all,
    max_turns: int = MAX_TURNS_DEFAULT,
) -> AsyncIterator[Event]:
    """Advance the session to a terminal status, yielding events as they occur.

    The only place that catches broadly: a harness reports failures as events
    and a status, it does not hand a traceback to its caller.
    """
    while session.status == "working":
        if session.turns >= max_turns:
            session.status = "error"
            session.stop_reason = "max_turns"
            session.error = f"stopped after {max_turns} turns"
            session.events.append(Event("error", session.error))
        else:
            try:
                await step(session, transport, approve)
            except Exception as exc:
                session.status = "error"
                session.error = f"{type(exc).__name__}: {exc}"
                session.events.append(Event("error", session.error))
        while session.events:
            yield session.events.pop(0)
