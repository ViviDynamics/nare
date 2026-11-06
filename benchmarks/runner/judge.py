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
{"met": [true, false, "unknown", ...], "reasons": ["one short sentence", ...]}

"met" has exactly one entry per claim, in the same order: true when the claim
is true of the diff, false when it is false, and "unknown" when the diff and
the closing message do not settle it. Use "unknown" rather than guessing.
"reasons" has exactly one short sentence per claim, in the same order, saying
what decided that claim.
Judge only what the diff shows. Do not reward effort or intent.
The closing message is context: use it for claims about what the agent said \
or asked, but a claim about a file's contents is true only if the diff shows it.\
"""


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
    if config.judge_model is None:
        # require_judge refuses this before any rep; reaching it is a bug.
        raise JudgeError("no judge model is configured")
    return make_transport(
        config.provider,
        model=config.judge_model,
        base_url=config.base_url,
        api_key=config.api_key,
        max_tokens=config.judge_max_tokens,
        system=JUDGE_SYSTEM,
        streaming=config.stream,
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


def parse_reply(text: str, expected: int) -> tuple[list[bool | None], list[str]]:
    """The verdicts (None where the judge said "unknown") and one reason each.

    A reply missing a reason for a claim is refused like one missing a
    verdict: the reasons are what a human reads when a verdict is wrong.
    """
    payload = _extract_json(text)
    met = payload.get("met")
    if not isinstance(met, list) or len(met) != expected:
        raise JudgeError(
            f"judge returned {len(met) if isinstance(met, list) else 'no'} "
            f"verdicts, expected {expected}"
        )
    verdicts: list[bool | None] = []
    for value in met:
        if isinstance(value, bool):
            verdicts.append(value)
        elif isinstance(value, str) and value.strip().lower() == "unknown":
            verdicts.append(None)
        else:
            raise JudgeError(
                "judge returned a verdict that is neither boolean nor 'unknown': "
                f"{value!r}"
            )
    reasons = payload.get("reasons")
    if (
        not isinstance(reasons, list)
        or len(reasons) != expected
        or not all(isinstance(r, str) for r in reasons)
    ):
        raise JudgeError(
            f"judge returned {len(reasons) if isinstance(reasons, list) else 'no'} "
            f"reasons, expected one string per claim ({expected})"
        )
    return verdicts, [r.strip() for r in reasons]


async def _ask(
    prompt: str, expected: int, transport: Transport
) -> tuple[list[bool | None], list[str], JudgeAttempt]:
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
        met, reasons = parse_reply(text, expected)
    except JudgeError as exc:
        raise JudgeError(str(exc), [attempt]) from exc
    return met, reasons, attempt


async def score(
    case: Case, diff: str, final_text: str, *, transport: Transport
) -> tuple[list[bool | None], list[str], tuple[JudgeAttempt, ...]]:
    assert case.judge is not None, "score is only called for judged cases"
    prompt = build_prompt(case, diff, final_text)
    attempts: list[JudgeAttempt] = []
    last: JudgeError | None = None
    for _ in range(JUDGE_ATTEMPTS):
        try:
            met, reasons, attempt = await _ask(
                prompt, len(case.judge.assertions), transport
            )
        except JudgeError as exc:
            attempts += exc.attempts
            last = exc
            continue
        return met, reasons, (*attempts, attempt)
    raise JudgeError(str(last), attempts) from last
