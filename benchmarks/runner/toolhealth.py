"""Tool-call health per model, read from what nare wrote for each rep.

Two files, both kept per rep (`keep_evidence`): `session.json`, the saved
transcript (the calls, the result nare gave each one, and the reply text), and
`stdout.jsonl`, the event stream. A turn in the stream is its events up to a
`cost` event, which nare writes once per model reply. Nothing here calls a
model; the counts say how often the serving stack or the model made a call
nare could not use, so a model's failures can be told from a harness's.

Each count is what nare itself reported, not a guess at what it meant:

- `argument_errors`: a call nare refused before running it, because its
  arguments were not a JSON object, or failed the tool's argument checks.
- `unknown_tools`: a name no tool in nare has.
- `empty_arguments`: `{}` (or nothing) to a tool whose required parameters
  were then reported missing. A subset of `argument_errors`.
- `reasoning_no_answer`: a turn with reasoning, no answer text and no call.
- `markup_in_text`: a reply whose text carries call markup or JSON shaped like
  a call, instead of a call nare could run.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from benchmarks.runner.report import RepRecord

COUNTED = (
    "turns",
    "tool_calls",
    "argument_errors",
    "unknown_tools",
    "empty_arguments",
    "reasoning_no_answer",
    "markup_in_text",
)

# The starts of the messages nare gives a call whose arguments never parsed
# (the transports say these, streamed or not).
_MALFORMED = (
    "tool arguments are invalid",
    "tool arguments are not a complete JSON object",
    "incomplete or nonobject JSON arguments",
)
_UNKNOWN = "unknown tool "
_MISSING = "missing required parameter"

_MARKUP = re.compile(
    r"<tool_call>|</tool_call>|<function_call>|<\|tool_call"
    r"|\[TOOL_CALLS\]|<function=|"
    r"\{\s*\"name\"\s*:\s*\"[^\"]+\"\s*,\s*\"(?:arguments|parameters|input)\"\s*:"
)


def score_session(session: Mapping[str, Any]) -> dict[str, int]:
    """The counts a saved transcript carries: calls, their results, the text."""
    counts = dict.fromkeys(COUNTED, 0)
    calls: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for message in session.get("messages") or []:
        if not isinstance(message, dict) or not isinstance(
            message.get("content"), list
        ):
            continue
        assistant = message.get("role") == "assistant"
        for block in message["content"]:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if assistant and kind == "tool_use":
                calls[str(block.get("id"))] = {
                    "name": str(block.get("name") or ""),
                    "input": block.get("input") or {},
                }
                counts["tool_calls"] += 1
            elif (
                assistant and kind == "text" and _MARKUP.search(block.get("text") or "")
            ):
                counts["markup_in_text"] += 1
            elif kind == "tool_result" and block.get("is_error"):
                results.append(block)
    for block in results:
        text = str(block.get("content") or "")
        call = calls.get(str(block.get("tool_use_id")), {"name": "", "input": {}})
        if text.startswith(_MALFORMED):
            counts["argument_errors"] += 1
        elif text.startswith(_UNKNOWN):
            counts["unknown_tools"] += 1
        elif call["name"] and text.startswith(f"{call['name']}: "):
            # nare's argument checks are the only errors that open with the
            # tool's own name (tools.py dispatch).
            counts["argument_errors"] += 1
            if call["input"] == {} and _MISSING in text:
                counts["empty_arguments"] += 1
    return counts


def score_events(events: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """The turns, and the turns that reasoned without answering or calling."""
    counts = {"turns": 0, "reasoning_no_answer": 0}
    thinking = answered = called = False
    for event in events:
        kind = event.get("type")
        if kind == "thinking":
            thinking = True
        elif kind == "progress" and not event.get("tool_use_id"):
            answered = answered or bool(str(event.get("text") or "").strip())
        elif kind == "tool_use":
            called = True
        elif kind == "cost":
            counts["turns"] += 1
            if thinking and not answered and not called:
                counts["reasoning_no_answer"] += 1
            thinking = answered = called = False
    return counts


def _json_lines(text: str) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue  # a line cut off by a kill is not an event
        if isinstance(payload, dict):
            lines.append(payload)
    return lines


def rep_health(evidence: Path) -> dict[str, int] | None:
    """One rep's counts from its kept evidence, or None when neither file is there.

    `session` and `events` say which files were read: a rep whose session never
    saved still has its turns counted, and says so rather than reading zeros.
    """
    session: dict[str, Any] | None = None
    session_file = evidence / "session.json"
    if session_file.is_file():
        try:
            loaded = json.loads(session_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            loaded = None
        session = loaded if isinstance(loaded, dict) else None
    events: list[dict[str, Any]] | None = None
    stream = evidence / "stdout.jsonl"
    if stream.is_file():
        events = _json_lines(stream.read_text(encoding="utf-8"))
    if session is None and events is None:
        return None
    counts = dict.fromkeys(COUNTED, 0)
    if session is not None:
        counts.update(score_session(session))
    if events is not None:
        counts.update(score_events(events))
    counts["session"] = int(session is not None)
    counts["events"] = int(events is not None)
    return counts


@dataclass(frozen=True)
class ModelHealth:
    model: str
    reps: int
    read: int  # reps with any evidence read
    with_session: int  # reps whose saved transcript was read
    counts: dict[str, int]


def model_health(records: Sequence[RepRecord]) -> list[ModelHealth]:
    """The counts summed per model, reps that were never read counted as such."""
    by_model: dict[str, list[RepRecord]] = {}
    for rec in records:
        by_model.setdefault(rec.model, []).append(rec)
    rows: list[ModelHealth] = []
    for model in sorted(by_model):
        reps = by_model[model]
        counts = dict.fromkeys(COUNTED, 0)
        read = with_session = 0
        for rec in reps:
            health = rec.tool_health
            if not health:
                continue
            read += 1
            with_session += health.get("session", 0)
            for key in COUNTED:
                counts[key] += health.get(key, 0)
        rows.append(ModelHealth(model, len(reps), read, with_session, counts))
    return rows


def render_health(rows: Sequence[ModelHealth]) -> str:
    lines: list[str] = []
    for row in rows:
        c = row.counts
        lines.append(
            f"tool-call health: {row.model}, {row.read} of {row.reps} reps read "
            f"({row.with_session} with a saved transcript)"
        )
        lines.append(f"  turns {c['turns']}, tool calls {c['tool_calls']}")
        lines.append(
            f"  argument errors {c['argument_errors']} (refused before they ran)"
        )
        lines.append(f"  unknown tool names {c['unknown_tools']}")
        lines.append(
            f"  empty arguments to a tool with required parameters "
            f"{c['empty_arguments']}"
        )
        lines.append(
            f"  reasoning with no answer and no call {c['reasoning_no_answer']}"
        )
        lines.append(f"  tool-call markup in reply text {c['markup_in_text']}")
    return "\n".join(lines)


def read_evidence(results_dir: Path, records: Sequence[RepRecord]) -> list[RepRecord]:
    """Each record with its health read from its kept evidence, for results
    written before the counts were recorded. `artifacts` is relative to the
    results directory."""
    out: list[RepRecord] = []
    for rec in records:
        if rec.tool_health is None and rec.artifacts:
            rec = replace(rec, tool_health=rep_health(results_dir / rec.artifacts))
        out.append(rec)
    return out
