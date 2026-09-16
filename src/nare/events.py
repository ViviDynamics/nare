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

_SECRETS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|auth|token|secret|password)\b\s*[=:]\s*\S+"),
]


def redact(text: str) -> str:
    for pattern in _SECRETS:
        text = pattern.sub("[redacted]", text)
    return text


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
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
        object.__setattr__(self, "detail", _redact_value(self.detail))
