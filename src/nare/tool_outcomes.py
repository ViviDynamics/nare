"""Safe dispatch classification kept outside provider tool-result blocks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ToolOutcome:
    kind: Literal[
        "ok", "invalid_arguments", "caller_error", "unexpected_error", "tool_error"
    ]
    code: str | None = None

    def record(self) -> dict[str, str]:
        result: dict[str, str] = {"kind": self.kind}
        if self.code is not None:
            result["code"] = self.code
        return result


@dataclass(frozen=True)
class CallerResult:
    text: str
    failed: bool
    outcome: ToolOutcome
