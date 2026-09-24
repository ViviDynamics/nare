"""Typed events. Field names and type values match conductor's BackendEvent
verbatim, so its adapter is `BackendEvent(**json.loads(line))` and not a
mapping table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

EventType = Literal["progress", "tool_use", "thinking", "cost", "error", "output"]

# One version number for the rule set, and one stable name per rule. Both are
# published through `nare contract`, so a caller can tell when the redaction it
# is paying for has changed without diffing patterns.
REDACTION_VERSION = 1
REDACTION_RULES: tuple[str, ...] = (
    "anthropic-key",
    "openai-key",
    "github-token",
    "credential-assignment",
)

_REDACTED = "[redacted]"

# Keys and tokens are anchored at their own prefixes, so a failed match attempt
# costs bounded work no matter how long the text is.
_PREFIX_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai-key", re.compile(r"sk-[A-Za-z0-9_\-]{20,}")),
    ("github-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
)

# No \b around the keyword: `_` is a word character, so \btoken\b never
# matches inside AUTH_TOKEN and \bauth\b never matches inside
# Authorization. Affixes are allowed instead, which is what the real
# names look like.
_CREDENTIAL_RUN = re.compile(r"[\w.\-]+")
_CREDENTIAL_KEYWORD = re.compile(
    r"(?i)(?:api[_-]?key|auth|token|secret|password|passwd|credential)"
)
# The optional scheme word covers `Bearer <token>`, where the secret is the
# SECOND word after the colon.
_CREDENTIAL_TAIL = re.compile(
    r"""(?i)["']?\s*[:=]\s*["']?(?:bearer|basic|token)?\s*\S+"""
)


def _redact_credentials(text: str) -> str:
    """The key/value rule, in linear time.

    Written out as three passes rather than one regex because the single
    regex cannot be linear: matching `[\\w.-]*(keyword)[\\w.-]*` at every
    start position re-scans the run between them, so a megabyte of word
    characters took minutes before the first value was even ruled out. The
    equivalent linear shape, proven on this pattern, is:

    A match spans from the start of the maximal `\\w.-` run holding a keyword
    to the end of the value tail after that run. The tail is matched at the
    run's end, so which keyword occurrence the legacy pattern would have
    picked cannot change the span, and a run whose tail does not match can
    only fail everywhere: the tail's quote, separator and scheme characters
    are never run characters, so the greedy run before it is maximal, and
    `[=:]` cannot occur inside a run. The differential tests against the
    legacy rule above hold this to its word.
    """
    pieces: list[str] = []
    replaced = 0
    for run in _CREDENTIAL_RUN.finditer(text):
        if run.start() < replaced:
            continue
        if not _CREDENTIAL_KEYWORD.search(text, run.start(), run.end()):
            continue
        tail = _CREDENTIAL_TAIL.match(text, run.end())
        if tail is None:
            continue
        pieces.append(text[replaced : run.start()])
        pieces.append(_REDACTED)
        replaced = tail.end()
    pieces.append(text[replaced:])
    return "".join(pieces)


def redact(text: str) -> str:
    for _, pattern in _PREFIX_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return _redact_credentials(text)


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        # Normalized to a list on purpose: all of these serialize as a JSON
        # array anyway, and `detail` has to stay JSON-serializable for the
        # JSONL wire. json.dumps actually RAISES on a set, so this closes a
        # latent emission crash as well as the redaction hole.
        return [redact_value(v) for v in value]
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Event:
    type: EventType
    text: str
    detail: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        # Redaction at construction, not at emission: an unredacted Event must
        # never exist, because every later surface trusts it.
        object.__setattr__(self, "text", redact(self.text))
        object.__setattr__(self, "detail", redact_value(self.detail))
