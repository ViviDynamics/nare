from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest

from benchmarks.runner.jev import (
    JEV_PRICE_IN,
    Choice,
    Jev,
    JevClient,
    JevError,
    Noul,
    estimated_tokens,
    retry_after,
)

QUESTIONS: dict[str, dict[str, Any]] = {
    "bug": {"type": "noul", "instructions": "Does this function have a bug?"},
    "team": {
        "type": "choice",
        "instructions": "Which team?",
        "criteria": {"billing": "Payments", "technical": "Bugs"},
    },
}
ANSWERS = {
    "bug": {"type": "noul", "noul": 0.93},
    "team": {
        "type": "choice",
        "choice": "technical",
        "probabilities": {"billing": 0.1, "technical": 0.9},
        "confidence": 0.82,
    },
}


def ok(answers: Any = None, **extra: Any) -> httpx2.Response:
    body = {
        "model": "jev-1.13.0",
        "answers": ANSWERS if answers is None else answers,
        "usage": {"input_tokens": 312, "output_tokens": 48},
    }
    return httpx2.Response(200, json=body, **extra)


class Script:
    """Answers each POST with the next scripted response, keeping the requests."""

    def __init__(self, *responses: httpx2.Response | Exception) -> None:
        self.responses: Iterator[httpx2.Response | Exception] = iter(responses)
        self.requests: list[httpx2.Request] = []
        self.slept: list[float] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        step = next(self.responses)
        if isinstance(step, Exception):
            raise step
        return step

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def client(self, base_url: str = "https://proxy.example/v1") -> JevClient:
        ticks = iter(float(n) for n in range(100))
        return JevClient(
            base_url=base_url,
            api_key="sk-proxy",
            model="jev-latest",
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(self.handler)
            ),
            sleep=self.sleep,
            clock=lambda: next(ticks),
        )


_check: Jev = Script().client()


async def test_posts_to_the_pass_through_with_the_proxy_key() -> None:
    script = Script(ok())
    await script.client().ask("def add(a, b): ...", QUESTIONS)
    (request,) = script.requests
    assert str(request.url) == "https://proxy.example/typesafe/v1/systemone"
    assert request.headers["authorization"] == "Bearer sk-proxy"
    assert json.loads(request.content) == {
        "model": "jev-latest",
        "state": "def add(a, b): ...",
        "questions": QUESTIONS,
    }


async def test_a_base_url_without_v1_is_used_as_it_is() -> None:
    script = Script(ok())
    await script.client("https://proxy.example/").ask("s", QUESTIONS)
    assert str(script.requests[0].url) == "https://proxy.example/typesafe/v1/systemone"


async def test_parses_noul_and_choice_answers() -> None:
    reply = await Script(ok()).client().ask("s", QUESTIONS)
    assert reply.model == "jev-1.13.0"
    assert reply.answers == {
        "bug": Noul(0.93),
        "team": Choice("technical", {"billing": 0.1, "technical": 0.9}, 0.82),
    }
    assert reply.input_tokens == 312
    assert reply.attempts == 1


async def test_a_429_is_retried_once_after_retry_after() -> None:
    script = Script(httpx2.Response(429, headers={"retry-after": "7"}), ok())
    reply = await script.client().ask("s", QUESTIONS)
    assert script.slept == [7.0]
    assert reply.attempts == 2
    assert reply.latency_s > 0


async def test_a_second_529_is_an_error() -> None:
    script = Script(httpx2.Response(529), httpx2.Response(529, text="overloaded"))
    with pytest.raises(JevError) as info:
        await script.client().ask("s", QUESTIONS)
    assert script.slept == [2.0]
    assert info.value.attempts == 2
    assert info.value.body == "overloaded"


@pytest.mark.parametrize(
    ("header", "seconds"),
    [(None, 2.0), ("7", 7.0), ("600", 30.0), ("-3", 0.0), ("soon", 2.0), ("nan", 2.0)],
)
def test_retry_after_is_bounded(header: str | None, seconds: float) -> None:
    headers = {} if header is None else {"retry-after": header}
    assert retry_after(httpx2.Headers(headers)) == seconds


async def test_a_422_raises_with_its_body_and_is_not_retried() -> None:
    script = Script(httpx2.Response(422, text='{"detail": "questions: required"}'))
    with pytest.raises(JevError, match="422") as info:
        await script.client().ask("s", QUESTIONS)
    assert "questions: required" in info.value.body
    assert len(script.requests) == 1


async def test_a_401_raises_with_its_body() -> None:
    script = Script(httpx2.Response(401, text="unknown key"))
    with pytest.raises(JevError) as info:
        await script.client().ask("s", QUESTIONS)
    assert info.value.body == "unknown key"


async def test_a_missing_answer_raises() -> None:
    script = Script(ok({"bug": ANSWERS["bug"]}))
    with pytest.raises(JevError, match="team"):
        await script.client().ask("s", QUESTIONS)


async def test_a_wrongly_typed_answer_raises() -> None:
    script = Script(ok({**ANSWERS, "bug": {"type": "score", "score": 3}}))
    with pytest.raises(JevError, match="bug"):
        await script.client().ask("s", QUESTIONS)


@pytest.mark.parametrize("p_yes", [1.5, "0.9", True, None])
async def test_a_noul_outside_zero_to_one_raises(p_yes: Any) -> None:
    script = Script(ok({**ANSWERS, "bug": {"type": "noul", "noul": p_yes}}))
    with pytest.raises(JevError, match="bug"):
        await script.client().ask("s", QUESTIONS)


async def test_a_choice_outside_the_labels_raises() -> None:
    team = {**ANSWERS["team"], "choice": "sales"}
    with pytest.raises(JevError, match="sales"):
        await Script(ok({**ANSWERS, "team": team})).client().ask("s", QUESTIONS)


async def test_a_body_that_is_not_json_raises() -> None:
    script = Script(httpx2.Response(200, text="<html>bad gateway</html>"))
    with pytest.raises(JevError) as info:
        await script.client().ask("s", QUESTIONS)
    assert "bad gateway" in info.value.body


async def test_a_dropped_connection_raises() -> None:
    script = Script(httpx2.ConnectError("refused"))
    with pytest.raises(JevError, match="refused"):
        await script.client().ask("s", QUESTIONS)


async def test_cost_comes_from_the_proxy_header() -> None:
    script = Script(ok(headers={"x-litellm-response-cost": "0.0000131"}))
    reply = await script.client().ask("s", QUESTIONS)
    assert reply.cost == pytest.approx(0.0000131)
    assert not reply.cost_estimated


async def test_cost_is_estimated_from_tokens_without_the_header() -> None:
    reply = await Script(ok()).client().ask("s", QUESTIONS)
    assert reply.cost == pytest.approx(312 * JEV_PRICE_IN / 1e6)
    assert reply.cost_estimated


async def test_cost_is_unknown_without_the_header_or_a_count() -> None:
    body = {"model": "jev-1.13.0", "answers": ANSWERS}
    reply = await Script(httpx2.Response(200, json=body)).client().ask("s", QUESTIONS)
    assert reply.cost is None
    assert not reply.cost_estimated


def test_size_is_four_characters_per_token() -> None:
    state = {"diff": "x" * 390}
    assert estimated_tokens(state, "q" * 2) == (len(json.dumps(state)) + 2) / 4
