"""The Claude Code arm of the comparison (#114). Pure: it reads what a
`claude -p` run wrote and maps it onto the shape nare's grading already reads.

Nothing here runs a container or calls a model; `claude_arm` does that. The
mapping is deliberately small. Claude Code's headless result is a single
object, and nare's is a result line, so the two meet at `ResultLine`: the
status and stop reason, the four usage fields, the turn count and the final
text. Everything else (checks, judge, outcome) is the nare arm's grading,
unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from benchmarks.runner.grade import USAGE_FIELDS, ResultLine

# Cases both arms run on the same fixture, with the same prompt and the same
# checks. A case is here when nothing in it reads a nare-only channel.
COMPARABLE_CASES: tuple[str, ...] = (
    "bash-timeout-recovery",
    "bootstrap-without-database",
    "edit-docstring",
    "expense-tracker-cli",
    "fix-failing-test",
    "long-test-output",
    "multi-file-rename",
    "read-only-question",
    "stale-backup-cleanup",
)

# Every other case, with why it is left out. A case added to benchmarks/cases
# must land in one of the two sets; the tests hold that.
EXCLUDED_CASES: dict[str, str] = {
    "ambiguous-after-reading": (
        "a correct run asks, and the ask is recorded in nare's session file; "
        "headless Claude Code has no ask tool to record it with"
    ),
    "ambiguous-request": (
        "a correct run ends blocked with a question, nare's stop status and "
        "session file, which headless Claude Code does not have"
    ),
    "impossible-test": (
        "its honest outcomes are nare's blocked status or a closing message, "
        "read from nare's session file"
    ),
    "context-overflow": (
        "measures nare's context elision, read from the session file's message "
        "history; Claude Code compacts by a different rule and exposes no such record"
    ),
    "context-overflow-small-window": (
        "passes --context-window, a nare flag with no Claude Code counterpart"
    ),
    "mcp-fanout": (
        "MCP is wired per harness (nare's mcp.json and server__tool names); "
        "not wired for Claude Code in this round"
    ),
    "mcp-large-result": (
        "MCP is wired per harness (nare's mcp.json and server__tool names); "
        "not wired for Claude Code in this round"
    ),
    "typed-classify": (
        "uses nare's --schema and typed output; Claude Code's --json-schema "
        "reports its result differently and is not wired here"
    ),
}

# Subagents and web tools are off, because nare has none of them. A subagent's
# spend would also sit outside the usage the result reports for the agent.
DISALLOWED_TOOLS = "Task,WebFetch,WebSearch"


# Per-token prices, read from the gateway's model info at run time and saved
# with the results, so a price change between runs is visible.
@dataclass(frozen=True)
class ModelPrice:
    input: float
    output: float
    cache_read: float
    cache_write: float


_PRICE_FIELDS = {
    "input": "input_cost_per_token",
    "output": "output_cost_per_token",
    "cache_read": "cache_read_input_token_cost",
    "cache_write": "cache_creation_input_token_cost",
}


def price_from_model_info(info: Mapping[str, Any]) -> ModelPrice:
    """The four per-token prices from one model's gateway entry.

    A missing price is refused rather than costed as zero: a run whose cache
    tokens cost nothing would make the cache look free.
    """
    values: dict[str, float] = {}
    for name, key in _PRICE_FIELDS.items():
        raw = info.get(key)
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise ValueError(f"model info has no {name} price ({key})")
        values[name] = float(raw)
    return ModelPrice(**values)


def dollars(usage: Mapping[str, int], price: ModelPrice) -> float:
    return float(
        sum(usage.get(name, 0) * getattr(price, name) for name in USAGE_FIELDS)
    )


@dataclass(frozen=True)
class ClaudeRun:
    """What one `claude -p --output-format stream-json` run wrote."""

    result: dict[str, Any] | None
    tool_calls: dict[str, int] = field(default_factory=dict)

    @property
    def closing(self) -> str:
        text = None if self.result is None else self.result.get("result")
        return text if isinstance(text, str) else ""

    @property
    def turns(self) -> int:
        raw = None if self.result is None else self.result.get("num_turns")
        return int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else 0

    @property
    def reported_cost(self) -> float | None:
        raw = None if self.result is None else self.result.get("total_cost_usd")
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            return None
        return float(raw)


def parse_stream(text: str) -> ClaudeRun:
    """Read the stream: the final result object, and each tool call once.

    A streamed message can repeat its content blocks, so a tool call is keyed
    by its block id. A line that is not JSON is skipped, not fatal: Claude Code
    writes warnings to the same stream on some failures.
    """
    result: dict[str, Any] | None = None
    seen: set[str] = set()
    tools: dict[str, int] = {}
    for raw in text.splitlines():
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "result":
            result = obj
        elif obj.get("type") == "assistant":
            message = obj.get("message")
            blocks = message.get("content") if isinstance(message, dict) else None
            for block in blocks or []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                block_id = str(block.get("id", ""))
                if block_id in seen:
                    continue
                seen.add(block_id)
                name = str(block.get("name", "unknown"))
                tools[name] = tools.get(name, 0) + 1
    return ClaudeRun(result=result, tool_calls=tools)


def _usage(result: Mapping[str, Any]) -> dict[str, int]:
    raw = result.get("usage")
    source = raw if isinstance(raw, dict) else {}
    return {
        "input": int(source.get("input_tokens", 0) or 0),
        "output": int(source.get("output_tokens", 0) or 0),
        "cache_read": int(source.get("cache_read_input_tokens", 0) or 0),
        "cache_write": int(source.get("cache_creation_input_tokens", 0) or 0),
    }


def to_result_line(run: ClaudeRun, price: ModelPrice) -> ResultLine | None:
    """The nare-shaped result line for a run, or None when it wrote no result.

    `success` is nare's `done` (the model ended on a turn with no tool call).
    A turn limit is nare's `max_turns`, which is an error status, as in nare.
    Anything else is an error with no stop reason: the same infrastructure
    error nare's grading already leaves out of the pass rate.
    """
    if run.result is None:
        return None
    result = run.result
    usage = _usage(result)
    cost = dollars(usage, price)
    subtype = result.get("subtype")
    if result.get("stop_reason") == "max_tokens":
        # A truncated final turn is not a finish, whatever the subtype says.
        status, stop_reason, error = "error", "max_tokens", None
    elif subtype == "success" and not result.get("is_error"):
        status, stop_reason, error = "done", "end_turn", None
    elif subtype == "error_max_turns":
        status, stop_reason, error = "error", "max_turns", None
    else:
        status, stop_reason = "error", None
        error = run.closing or str(subtype or "run errored")
    return ResultLine(
        status=status,
        turns=run.turns,
        usage=usage,
        stop_reason=stop_reason,
        error=error,
        questions=[],
        cost=cost,
        cost_estimated=True,
    )


# The agent's script, as nare's is: values arrive as environment variables, so
# a prompt is never spliced into shell text. The stream goes to the artifacts
# directory, read after the container exits. The leftovers snapshot is the
# same one nare's arm writes, for checks that read what the agent left running.
_AGENT_SCRIPT = """\
set -e
git init -q -b main
git add -A
git -c user.email=bench@nare.invalid -c user.name=nare-bench commit -qm fixture
status=0
claude -p "$BENCH_PROMPT" \\
     --model "$BENCH_MODEL" \\
     --output-format stream-json --verbose \\
     --permission-mode bypassPermissions \\
     --max-turns "$BENCH_MAX_TURNS" \\
     --disallowedTools="$BENCH_DISALLOWED" \\
     > /out/stream.jsonl 2> /out/stderr.txt || status=$?
python3 -I -c "$BENCH_LEFTOVERS" /out/after.json || true
exit "$status"
"""


def agent_script() -> str:
    return _AGENT_SCRIPT
