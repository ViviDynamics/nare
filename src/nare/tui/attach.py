"""Watching a session file another process writes. No Textual here.

`nare run` rewrites the file by atomic rename after every turn, so every read
sees a whole file and a turn-level view costs no new contract surface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from nare.contract import CONTRACT_VERSION
from nare.session import SESSION_VERSION, Session, loads


@dataclass(frozen=True)
class Update:
    session: Session
    redraw: bool  # clear the view first, instead of appending


def _mismatched(text: str) -> bool:
    """A file from another nare is permanent; a broken one may be fixed."""
    try:
        raw = json.loads(text)
    except ValueError:
        return False
    return isinstance(raw, dict) and (
        raw.get("version", 0) != SESSION_VERSION
        or raw.get("contract", CONTRACT_VERSION) != CONTRACT_VERSION
    )


class Watcher:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.session: Session | None = None
        self.problem: str | None = None
        self.fatal = False
        self.mtime: float | None = None
        self._stamp: tuple[int, int] | None = None

    def poll(self) -> Update | None:
        """Look at the file once. None means there is nothing new to draw."""
        if self.fatal:
            return None
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            # A run writes nothing before its first turn.
            self._stamp = None
            self.problem = f"waiting for {self.path}"
            return None
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp == self._stamp:
            return None
        self._stamp = stamp
        self.mtime = stat.st_mtime
        try:
            text = self.path.read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            self.problem = f"{self.path}: {exc}"
            return None
        try:
            session = loads(text)
        except ValueError as exc:
            # The last good session stays drawn; only a mismatch is permanent.
            self.problem = f"{self.path}: {exc}"
            self.fatal = _mismatched(text)
            return None
        old, self.session, self.problem = self.session, session, None
        # Append only when the old transcript is an untouched prefix of the
        # new one. A resume merges text into the last message, so that one is
        # compared; compaction edits older ones in place, and the view keeps
        # the text it already drew.
        grew = (
            old is not None
            and bool(old.messages)
            and session.id == old.id
            and len(session.messages) > len(old.messages)
            and session.messages[len(old.messages) - 1] == old.messages[-1]
        )
        return Update(session=session, redraw=not grew)
