"""The TUI's approver: y, n, or always for this tool. No Textual here; the app
passes in how to ask.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

# These change nothing, so a person is never asked about them.
EXEMPT = frozenset({"read", "ask"})

Ask = Callable[[str, dict[str, Any]], Awaitable[str]]


class Approver:
    """The "always" set lives for this process only and is never written to
    the session: a saved file must not grant a later run anything.
    """

    def __init__(self, ask: Ask) -> None:
        self.ask = ask
        self.always: set[str] = set()

    async def __call__(self, tool: str, args: dict[str, Any]) -> bool:
        if tool in EXEMPT or tool in self.always:
            return True
        key = await self.ask(tool, args)
        if key == "a":
            self.always.add(tool)
        return key in ("y", "a")
