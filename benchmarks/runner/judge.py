"""Scoring a case's assertions.

The judge answers a checklist of binary, objectively checkable claims about
the diff, not a holistic rating. Every model available to score is a small
one, and a small model is far better at "is this true, yes or no" than at
rating quality on a scale -- which also makes the score stable run to run.

It runs through nare's own transport, so the benchmark adds no second client,
no second credential path, and reaches every provider nare reaches.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from benchmarks.runner.case import Case
from benchmarks.runner.config import Config
from nare.session import Message
from nare.transport import Transport, make_transport

JUDGE_SYSTEM = """\
You check whether specific claims about a code change are true.

You are given a task, the diff that an agent produced for it, and a numbered
list of claims. For each claim, decide whether it is true of the diff.

Reply with JSON only, in exactly this shape:
{"met": [true, false, ...], "why": "one short sentence"}

The "met" array must have exactly one boolean per claim, in the same order.
Judge only what the diff shows. Do not reward effort or intent.
The closing message is context: use it for claims about what the agent said \
or asked, but a claim about a file's contents is true only if the diff shows it.\
"""

# Judging is a short answer; this bounds a runaway reply rather than the task.
JUDGE_MAX_TOKENS = 1024


@dataclass(frozen=True)
class JudgeAttempt:
    """One call to the judge, kept whole so a bad reply can be read later."""

    stop_reason: str | None
    text: str


class JudgeError(Exception):
    """The judge could not answer. Never a zero -- see grade.rep_outcome."""

    def __init__(self, message: str, attempts: Sequence[JudgeAttempt] = ()) -> None:
        super().__init__(message)
        self.attempts = tuple(attempts)


# One retry: enough to survive a single empty or garbled reply, few enough
# that a judge which cannot answer costs two calls rather than a loop.
JUDGE_ATTEMPTS = 2


def render_attempts(attempts: Sequence[JudgeAttempt]) -> str:
    """The body of judge.txt: each attempt's stop reason, then its raw text."""
    body = "\n\n".join(
        f"attempt {n}: stop_reason={a.stop_reason}\n{a.text}"
        for n, a in enumerate(attempts, start=1)
    )
    return body + "\n"


def judge_transport(config: Config) -> Transport:
    return make_transport(
        config.provider,
        model=config.judge_model,
        base_url=config.base_url,
        api_key=config.api_key,
        max_tokens=JUDGE_MAX_TOKENS,
        system=JUDGE_SYSTEM,
    )


def build_prompt(case: Case, diff: str, final_text: str) -> str:
    assert case.judge is not None, "build_prompt is only called for judged cases"
    claims = "\n".join(
        f"{i}. {text}" for i, text in enumerate(case.judge.assertions, start=1)
    )
    return (
        f"## Task given to the agent\n\n{case.prompt}\n\n"
        f"## Diff the agent produced\n\n```diff\n{diff}\n```\n\n"
        f"## The agent's closing message\n\n{final_text}\n\n"
        f"## Claims to check\n\n{claims}\n"
    )


def _extract_json(text: str) -> dict[str, Any]:
    """Small models pad their answers with prose and fences. Dig the object out."""
    candidates = [text]
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    candidates = fenced + candidates
    braced = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if braced:
        candidates.append(braced.group(0))
    for candidate in candidates:
        try:
            payload = json.loads(candidate.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise JudgeError(f"no JSON object in the judge's reply: {text[:200]!r}")


def parse_reply(text: str, expected: int) -> list[bool]:
    payload = _extract_json(text)
    met = payload.get("met")
    if not isinstance(met, list) or len(met) != expected:
        raise JudgeError(
            f"judge returned {len(met) if isinstance(met, list) else 'no'} "
            f"verdicts, expected {expected}"
        )
    if not all(isinstance(value, bool) for value in met):
        raise JudgeError(f"judge returned non-boolean verdicts: {met!r}")
    return [bool(value) for value in met]


async def _ask(
    prompt: str, expected: int, transport: Transport
) -> tuple[list[bool], str, JudgeAttempt]:
    """One attempt. Every failure raises JudgeError carrying this attempt."""
    try:
        reply = await transport.turn(
            [Message(role="user", content=[{"type": "text", "text": prompt}])],
            [],
        )
    except Exception as exc:
        # A 429 or a dropped connection is the judge failing to answer. Raised
        # as JudgeError so the rep records an error instead of crashing the
        # run and discarding every rep already paid for.
        why = f"{type(exc).__name__}: {exc}"
        raise JudgeError(why, [JudgeAttempt("transport error", why)]) from exc
    text = "\n".join(
        block.get("text", "") for block in reply.content if block.get("type") == "text"
    )
    attempt = JudgeAttempt(reply.stop_reason, text)
    try:
        met = parse_reply(text, expected)
    except JudgeError as exc:
        raise JudgeError(str(exc), [attempt]) from exc
    return met, str(_extract_json(text).get("why", "")), attempt


async def score(
    case: Case, diff: str, final_text: str, *, transport: Transport
) -> tuple[list[bool], str, tuple[JudgeAttempt, ...]]:
    assert case.judge is not None, "score is only called for judged cases"
    prompt = build_prompt(case, diff, final_text)
    attempts: list[JudgeAttempt] = []
    while True:
        try:
            met, why, attempt = await _ask(
                prompt, len(case.judge.assertions), transport
            )
        except JudgeError as exc:
            attempts += exc.attempts
            if len(attempts) >= JUDGE_ATTEMPTS:
                raise JudgeError(str(exc), attempts) from exc
            continue
        return met, why, (*attempts, attempt)
