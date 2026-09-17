"""The agent loop. A plain Session advanced by an explicit step(), surfaced as
an async generator: resumable by construction, deterministic under test.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from nare.events import Event
from nare.session import Message, Session
from nare.tools import TOOL_SCHEMAS, Approve, dispatch, questions_from
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
