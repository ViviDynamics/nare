"""Offline subprocess entry point: real CLI, only the transport is scripted.

Usage: uv run python tests/budget_probe.py SCRIPT.json [nare CLI arguments]
The script records each model request in SCRIPT.calls.json, including max_tokens.
No environment credentials or network are used.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import nare.cli
from nare.session import Message, Usage
from nare.transport import Reply, ToolCall


class ScriptedTransport:
    def __init__(self, path: Path, max_tokens: int | None) -> None:
        self.path = path
        self.script = json.loads(path.read_text())
        self.max_tokens = max_tokens
        self.calls: list[dict[str, Any]] = []

    async def context_window(self) -> int | None:
        value = self.script.get("window")
        return int(value) if value is not None else None

    async def turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> Reply:
        input_chars = len(json.dumps([asdict(m) for m in messages]))
        self.calls.append(
            {
                "max_tokens": self.max_tokens,
                "messages": len(messages),
                "input_chars": input_chars,
            }
        )
        self.path.with_suffix(".calls.json").write_text(json.dumps(self.calls))
        raw = self.script["replies"][len(self.calls) - 1]
        if "error" in raw:
            raise RuntimeError(raw["error"])
        usage = raw.get("usage", {"input": 10, "output": 5})
        if self.script.get("measure_input"):
            usage = {**usage, "input": input_chars // 4}
        return Reply(
            content=raw["content"],
            tool_calls=[ToolCall(**call) for call in raw.get("tool_calls", [])],
            usage=Usage(**usage),
            stop_reason=raw.get("stop_reason", "end_turn"),
            cost=raw.get("cost"),
        )


def main() -> int:
    path = Path(sys.argv[1])
    # Production parser, policy, loop, persistence, event/result rendering and
    # exit code run unchanged. Only vendor construction is replaced.
    nare.cli.transport_from_args = lambda args: ScriptedTransport(path, args.max_tokens)
    return nare.cli.main(sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
