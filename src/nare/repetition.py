"""Degenerate output: a reply whose tail is one short span repeated.

A model that loops writes the same span until it reaches its output cap. This
rule recognises that while the reply still streams, so a transport can stop
reading, and on a reply that has finished, so a transport can report it. The
loop then corrects the turn instead of waiting for the cap.

The rule is about text only. A span that repeats with only its digits changing
is caught too, because a loop that counts upward is the same failure.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

# A text is degenerate when its last REPEAT_SPAN characters repeat with a
# period of at most MAX_PERIOD characters. That is at least eight exact copies
# of the longest period, which valid output does not repeat verbatim at that
# length.
REPEAT_SPAN = 2048
MAX_PERIOD = 256
# The same test over the text with each run of digits folded to one digit, and
# over twice the span, catches a loop that only counts.
COUNTING_SPAN = 2 * REPEAT_SPAN
_DIGITS = re.compile(r"[0-9]+")
# A stream is checked again only after this many new characters, not per delta.
CHECK_CHARS = 256
# The test reads at most this much of the tail, so a watch keeps no more.
_KEEP = 2 * COUNTING_SPAN


def degenerate(text: str) -> bool:
    """True when the tail of text is one short span repeated, exactly or with
    only its digits changing."""
    counting = _DIGITS.sub("0", text[-_KEEP:])
    return _periodic(text, REPEAT_SPAN) or _periodic(counting, COUNTING_SPAN)


def _periodic(text: str, span: int) -> bool:
    if len(text) < span + 1:
        return False
    tail = text[-span:]
    return any(
        tail == text[-span - period : -period]
        for period in range(1, min(MAX_PERIOD, len(text) - span) + 1)
    )


class RepetitionWatch:
    """Checks one stream of text as its fragments arrive.

    feed returns True at a check where the tail has degenerated. The watch
    keeps only the tail the rule reads, so a long reply costs no more memory
    than a short one. A transport keeps one watch per stream it reads (reply
    text and reasoning are separate streams).
    """

    def __init__(self) -> None:
        self._tail = ""
        self._seen = 0
        self._checked = 0

    def feed(self, fragment: str) -> bool:
        if not fragment:
            return False
        self._tail += fragment
        if len(self._tail) > 2 * _KEEP:
            self._tail = self._tail[-_KEEP:]
        self._seen += len(fragment)
        if self._seen - self._checked < CHECK_CHARS:
            return False
        self._checked = self._seen
        return degenerate(self._tail)


def looping(content: Iterable[dict[str, Any]]) -> bool:
    """True when a finished reply's text or reasoning blocks are degenerate."""
    for block in content:
        kind = block.get("type")
        value = block.get("text") if kind == "text" else None
        if kind == "thinking":
            value = block.get("thinking")
        if isinstance(value, str) and degenerate(value):
            return True
    return False
