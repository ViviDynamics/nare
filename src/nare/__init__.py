"""nare — a standalone agent harness.

import asyncio
from nare import make_transport, new_session, run

async def main() -> None:
    transport = make_transport("anthropic", model="claude-sonnet-5")
    session = new_session("add a docstring to foo() in bar.py")
    async for event in run(session, transport=transport):
        print(event.type, event.text)

asyncio.run(main())
"""

from nare.events import Event
from nare.loop import run, step
from nare.session import Message, Session, Usage, dumps, loads, new_session
from nare.tools import Policy, approve_all
from nare.transport import Reply, ToolCall, Transport, make_transport

__version__ = "0.1.0"

__all__ = [
    "Event",
    "Message",
    "Policy",
    "Reply",
    "Session",
    "ToolCall",
    "Transport",
    "Usage",
    "approve_all",
    "dumps",
    "loads",
    "make_transport",
    "new_session",
    "run",
    "step",
]
