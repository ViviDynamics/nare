"""TypeSafe's Jev, reached through the LiteLLM proxy, for compare-judges only.

A second client, which judge.py exists to avoid: it runs through nare's own
transport so judging reaches every provider nare reaches, with no second
credential path. Jev is not a nare provider and has no Chat Completions form,
so there is nothing to reuse. This client stays in benchmarks/, is reached only
through the proxy's /jev/decisions route (LiteLLM forwards it to the TypeSafe
provider), and sends the run's own proxy key; the TypeSafe key never leaves
the proxy. Like the openai rail, it uses httpx2, which already arrives with
anthropic, so it adds no dependency.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx2

from nare.transport import reported_cost

# Dollars per million input tokens. Output tokens are free.
JEV_PRICE_IN = 0.042
# Jev takes at most this many tokens of state plus its longest question.
JEV_STATE_LIMIT = 32_000
RETRY_STATUSES = frozenset({429, 529})
RETRY_AFTER_DEFAULT = 2.0
RETRY_AFTER_CAP = 30.0
TIMEOUT = 120.0


def estimated_tokens(state: Any, longest_question: str = "") -> float:
    """Four characters per token, as compact.py estimates a transcript."""
    return (len(json.dumps(state)) + len(longest_question)) / 4


@dataclass(frozen=True)
class Noul:
    p_yes: float


@dataclass(frozen=True)
class Choice:
    choice: str
    probabilities: dict[str, float]
    confidence: float


Answer = Noul | Choice


@dataclass(frozen=True)
class JevReply:
    model: str  # the pinned version that answered
    answers: dict[str, Answer]
    input_tokens: int
    cost: float | None
    cost_estimated: bool
    latency_s: float  # wall clock, retries included
    attempts: int


class JevError(Exception):
    """Jev could not answer. `body` is the response body, when there was one."""

    def __init__(
        self,
        message: str,
        body: str = "",
        *,
        attempts: int = 1,
        latency_s: float = 0.0,
    ) -> None:
        super().__init__(message)
        self.body = body
        self.attempts = attempts
        self.latency_s = latency_s


class Jev(Protocol):
    """What compare-judges needs from Jev. Structural, like Transport."""

    model: str

    async def ask(
        self, state: Any, questions: Mapping[str, Mapping[str, Any]]
    ) -> JevReply: ...


def endpoint(base_url: str) -> str:
    """The Jev route hangs off the proxy root, not off its /v1."""
    root = base_url.rstrip("/").removesuffix("/v1")
    return f"{root}/jev/decisions"


def retry_after(headers: Mapping[str, str]) -> float:
    raw = headers.get("retry-after")
    try:
        seconds = float(raw) if raw is not None else RETRY_AFTER_DEFAULT
    except ValueError:
        # An HTTP date, or garbage. Neither is worth parsing for one retry.
        seconds = RETRY_AFTER_DEFAULT
    if not math.isfinite(seconds):
        seconds = RETRY_AFTER_DEFAULT
    return min(max(seconds, 0.0), RETRY_AFTER_CAP)


def _probability(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if 0.0 <= number <= 1.0 else None


def _input_tokens(usage: Any) -> int | None:
    raw = usage.get("input_tokens") if isinstance(usage, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def _body_cost(usage: Any) -> float | None:
    raw = usage.get("cost") if isinstance(usage, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    return float(raw) if math.isfinite(raw) and raw >= 0 else None


def parse_answer(qid: str, question: Mapping[str, Any], raw: Any) -> Answer:
    """One answer, checked against the question it answers."""
    wanted = question.get("type")
    if not isinstance(raw, dict) or raw.get("type") != wanted:
        got = raw.get("type") if isinstance(raw, dict) else raw
        raise JevError(f"answer {qid!r} is {got!r}, expected a {wanted} answer")
    if wanted == "noul":
        p_yes = _probability(raw.get("noul"))
        if p_yes is None:
            raise JevError(f"answer {qid!r} has no probability: {raw!r}")
        return Noul(p_yes)
    if wanted == "choice":
        labels = question.get("criteria", {})
        choice = raw.get("choice")
        probabilities = raw.get("probabilities")
        confidence = _probability(raw.get("confidence"))
        if not isinstance(choice, str) or choice not in labels:
            raise JevError(f"answer {qid!r} chose {choice!r}, not one of its labels")
        if not isinstance(probabilities, dict) or confidence is None:
            raise JevError(f"answer {qid!r} is missing probabilities or confidence")
        parsed = {str(k): _probability(v) for k, v in probabilities.items()}
        clean = {k: v for k, v in parsed.items() if v is not None}
        if len(clean) != len(parsed):
            raise JevError(f"answer {qid!r} has a probability outside 0 to 1")
        return Choice(choice, clean, confidence)
    raise JevError(f"question {qid!r} has type {wanted!r}, which is not parsed here")


class JevClient:
    """One POST per ask. Retries once on 429 or 529, and never on anything else."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        http_client: httpx2.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.model = model
        self._url = endpoint(base_url)
        self._key = api_key
        self._client = http_client or httpx2.AsyncClient(timeout=TIMEOUT)
        self._sleep = sleep
        self._clock = clock

    async def ask(
        self, state: Any, questions: Mapping[str, Mapping[str, Any]]
    ) -> JevReply:
        started = self._clock()
        attempts = 0
        while True:
            attempts += 1
            try:
                response = await self._client.post(
                    self._url,
                    headers={"authorization": f"Bearer {self._key}"},
                    json={"model": self.model, "state": state, "questions": questions},
                    timeout=TIMEOUT,
                )
            except httpx2.HTTPError as exc:
                raise JevError(
                    f"{type(exc).__name__}: {exc}",
                    attempts=attempts,
                    latency_s=self._clock() - started,
                ) from exc
            if response.status_code in RETRY_STATUSES and attempts == 1:
                await self._sleep(retry_after(response.headers))
                continue
            break
        latency = self._clock() - started
        try:
            return self._reply(response, questions, attempts, latency)
        except JevError as exc:
            exc.attempts, exc.latency_s = attempts, latency
            raise

    def _reply(
        self,
        response: httpx2.Response,
        questions: Mapping[str, Mapping[str, Any]],
        attempts: int,
        latency: float,
    ) -> JevReply:
        if response.status_code != 200:
            # A 422 is our request malformed: a bug to fix, so never retried.
            raise JevError(f"jev returned HTTP {response.status_code}", response.text)
        try:
            payload = response.json()
        except ValueError as exc:
            raise JevError(
                "jev returned a body that is not JSON", response.text
            ) from exc
        if not isinstance(payload, dict) or not isinstance(
            payload.get("answers"), dict
        ):
            raise JevError("jev returned no answers object", response.text)
        try:
            answers = {
                qid: parse_answer(qid, question, payload["answers"].get(qid))
                for qid, question in questions.items()
            }
        except JevError as exc:
            exc.body = response.text
            raise
        usage = payload.get("usage")
        counted = _input_tokens(usage)
        # The proxy's header, else the cost in Jev's own body; neither is an
        # estimate. Unknown, not zero, when there is no cost and no count.
        cost = reported_cost(response.headers)
        if cost is None:
            cost = _body_cost(usage)
        estimated = cost is None and counted is not None
        if cost is None and counted is not None:
            cost = counted * JEV_PRICE_IN / 1e6
        return JevReply(
            model=str(payload.get("model") or self.model),
            answers=answers,
            input_tokens=counted or 0,
            cost=cost,
            cost_estimated=estimated,
            latency_s=latency,
            attempts=attempts,
        )
