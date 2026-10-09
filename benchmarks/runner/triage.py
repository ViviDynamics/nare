"""Why a failed repetition failed.

Two causes are exact and decided here by code, since counting is a documented
Jev weakness. The rest are labelled by Jev and by the reference model, from one
state both of them see.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from benchmarks.runner.config import Config
from benchmarks.runner.jev import estimated_tokens
from benchmarks.runner.judge import (
    JUDGE_ATTEMPTS,
    JUDGE_MAX_TOKENS,
    JudgeAttempt,
    JudgeError,
    _extract_json,
)
from benchmarks.runner.report import RepRecord
from nare.session import Message
from nare.transport import Transport, make_transport

LABELS: dict[str, str] = {
    "asked_when_clear": (
        "Stopped to ask, though the request had one reasonable reading"
    ),
    "did_not_ask": "Went ahead on a request that needed a question first",
    "no_change": (
        "Ended without making the change the task required, and without asking"
    ),
    "wrong_change": (
        "Changed the right place, but the change does not do what was asked"
    ),
    "incomplete_change": (
        "Did part of the task, such as renaming in some files and not others"
    ),
    "off_target_edit": (
        "Edited the wrong file, or one the task said to leave alone, such as the tests"
    ),
    "other": "None of the above",
}
RAN_OUT = frozenset({"max_turns", "budget", "context"})
CLIP = 1_000
# Leaves room under Jev's 32k for the question itself.
STATE_BUDGET = 24_000

TRIAGE_SYSTEM = (
    "You decide why an agent's attempt at a coding task failed.\n\n"
    "You are given, as JSON: the task, the agent's final status, the checks "
    "that failed, the diff it produced, its closing message, and a transcript "
    "of the session.\n\n"
    "Pick the one label that best explains the failure:\n"
    + "\n".join(f"- {label}: {text}" for label, text in LABELS.items())
    + '\n\nReply with JSON only, in exactly this shape:\n{"cause": "<label>"}'
)


def code_cause(rec: RepRecord, min_met: int | None) -> str | None:
    """The causes code can decide exactly, or None for a model to label."""
    if rec.stop_reason in RAN_OUT:
        return "ran_out"
    if (
        all(check.passed for check in rec.checks)
        and min_met is not None
        and rec.judge_met is not None
        and rec.judge_met < min_met
    ):
        return "judge_gate"
    return None


def claimed_done(rec: RepRecord) -> bool:
    """A fact beside the cause, not a cause: it can accompany any of them."""
    return rec.status == "done" and any(not check.passed for check in rec.checks)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            "[image]" if part.get("type") == "image" else str(part.get("text", ""))
            for part in content
            if isinstance(part, dict)
        )
    return ""


def _entry(role: str, block: dict[str, Any]) -> dict[str, str] | None:
    kind = block.get("type")
    if kind == "text":
        return {"role": role, "type": "text", "text": str(block.get("text", ""))[:CLIP]}
    if kind == "tool_result":
        text = _text_of(block.get("content"))[:CLIP]
        if "image" in block:
            text = f"{text}\n[image]".lstrip()
        return {"role": role, "type": "tool_result", "text": text}
    if kind == "tool_use":
        return {
            "role": role,
            "type": "tool_use",
            "tool": str(block.get("name", "")),
            "args": json.dumps(block.get("input", {}))[:CLIP],
        }
    if kind == "image":
        return {"role": role, "type": "image", "text": "[image]"}
    # thinking, redacted_thinking, and anything newer: not evidence.
    return None


def message_entries(message: Any) -> list[dict[str, str]]:
    if not isinstance(message, dict):
        return []
    role = str(message.get("role", ""))
    blocks = message.get("content")
    if isinstance(blocks, str):
        return [{"role": role, "type": "text", "text": blocks[:CLIP]}]
    if not isinstance(blocks, list):
        return []
    entries = (_entry(role, b) for b in blocks if isinstance(b, dict))
    return [e for e in entries if e is not None]


def triage_state(
    prompt: str,
    rec: RepRecord,
    diff: str,
    closing: str,
    messages: Sequence[Any],
) -> dict[str, Any] | None:
    """The state both triage judges see, or None when it cannot fit.

    Over budget, the first user message is kept and the oldest of the rest are
    dropped, whole, until it fits.
    """
    first = next(
        (
            n
            for n, m in enumerate(messages)
            if isinstance(m, dict) and m.get("role") == "user"
        ),
        None,
    )
    head = [] if first is None else message_entries(messages[first])
    rest = [message_entries(m) for n, m in enumerate(messages) if n != first]
    base: dict[str, Any] = {
        "task": prompt,
        "status": rec.status,
        "failed_checks": [
            {"label": c.label, "detail": c.detail} for c in rec.checks if not c.passed
        ],
        "diff": diff,
        "closing_message": closing,
    }
    for dropped in range(len(rest) + 1):
        omitted = (
            [{"type": "omitted", "text": f"[{dropped} messages omitted]"}]
            if dropped
            else []
        )
        kept = [entry for entries in rest[dropped:] for entry in entries]
        state = {**base, "transcript": head + omitted + kept}
        if estimated_tokens(state) <= STATE_BUDGET:
            return state
    return None


def cause_question() -> dict[str, Any]:
    """Jev's one triage question. The criteria are the same descriptions the
    reference reads in TRIAGE_SYSTEM."""
    return {
        "type": "choice",
        "instructions": (
            "Why did the agent's attempt at the task fail? Pick the one label "
            "that best explains it."
        ),
        "criteria": dict(LABELS),
    }


def triage_transport(config: Config, model: str) -> Transport:
    return make_transport(
        config.provider,
        model=model,
        base_url=config.base_url,
        api_key=config.api_key,
        max_tokens=JUDGE_MAX_TOKENS,
        system=TRIAGE_SYSTEM,
    )


def parse_cause(text: str) -> str:
    cause = _extract_json(text).get("cause")
    if not isinstance(cause, str) or cause not in LABELS:
        raise JudgeError(f"triage returned an unknown cause: {cause!r}")
    return cause


async def _ask(prompt: str, transport: Transport) -> tuple[str, JudgeAttempt]:
    """One attempt. Every failure raises JudgeError carrying this attempt."""
    try:
        reply = await transport.turn(
            [Message(role="user", content=[{"type": "text", "text": prompt}])], []
        )
    except Exception as exc:
        why = f"{type(exc).__name__}: {exc}"
        raise JudgeError(why, [JudgeAttempt("transport error", why)]) from exc
    text = "\n".join(
        block.get("text", "") for block in reply.content if block.get("type") == "text"
    )
    attempt = JudgeAttempt(reply.stop_reason, text)
    try:
        return parse_cause(text), attempt
    except JudgeError as exc:
        raise JudgeError(str(exc), [attempt]) from exc


async def triage_reference(
    state: dict[str, Any], *, transport: Transport
) -> tuple[str, tuple[JudgeAttempt, ...]]:
    """The reference's label, with the same two attempts score() makes."""
    prompt = json.dumps(state, indent=2)
    attempts: list[JudgeAttempt] = []
    last: JudgeError | None = None
    for _ in range(JUDGE_ATTEMPTS):
        try:
            cause, attempt = await _ask(prompt, transport)
        except JudgeError as exc:
            attempts += exc.attempts
            last = exc
            continue
        return cause, (*attempts, attempt)
    raise JudgeError(str(last), attempts) from last
