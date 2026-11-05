"""Effort per rail (issue 135): request shapes with no key, CLI, session."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest

from fake_provider import FakeProvider, text_reply
from nare.cli import build_parser, main, transport_from_args
from nare.contract import describe
from nare.loop import run
from nare.session import Message, loads, new_session
from nare.transport import EFFORTS
from nare.transport.anthropic import AnthropicTransport
from nare.transport.openai import OpenAITransport
from test_streaming import Bytes, anthropic_chunks, openai_chunks

HELLO = [Message("user", [{"type": "text", "text": "hi"}])]


def recorder(seen: list[dict[str, Any]], chunks: list[bytes]) -> httpx2.AsyncClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Bytes(chunks)
        )

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


@pytest.mark.parametrize("effort", EFFORTS)
async def test_a_streamed_anthropic_turn_sends_output_config(effort: Any) -> None:
    seen: list[dict[str, Any]] = []
    transport = AnthropicTransport(
        model="m",
        api_key="test",
        effort=effort,
        streaming=True,
        http_client=recorder(seen, anthropic_chunks()),
    )
    await transport.turn(HELLO, [])
    assert seen[0]["output_config"] == {"effort": effort}
    assert seen[0]["thinking"] == {"type": "adaptive"}
    assert "budget_tokens" not in json.dumps(seen[0])
    assert seen[0]["max_tokens"] == 64000


@pytest.mark.parametrize("effort", EFFORTS)
@pytest.mark.parametrize("streaming", [False, True])
async def test_the_openai_rail_passes_every_level_through(
    effort: Any, streaming: bool
) -> None:
    seen: list[dict[str, Any]] = []
    if streaming:
        client = recorder(seen, openai_chunks())
    else:

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "ok"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )

        client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    transport = OpenAITransport(
        model="m",
        api_key="k",
        effort=effort,
        streaming=streaming,
        http_client=client,
    )
    await transport.turn(HELLO, [])
    # Passed through as it came: what a level means is the server's.
    assert seen[0]["reasoning_effort"] == effort
    assert "output_config" not in seen[0] and "thinking" not in seen[0]


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize("effort", ["xhigh", "max"])
def test_the_new_levels_are_accepted_on_both_rails(
    monkeypatch: pytest.MonkeyPatch, provider: str, effort: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline")
    monkeypatch.setenv("OPENAI_API_KEY", "offline")
    args = build_parser().parse_args(
        ["run", "go", "--provider", provider, "--model", "m", "--effort", effort]
    )
    transport = transport_from_args(args)
    assert isinstance(transport, (AnthropicTransport, OpenAITransport))
    assert transport.effort == effort


def test_an_unknown_level_is_refused() -> None:
    with pytest.raises(SystemExit) as raised:
        build_parser().parse_args(["run", "go", "--effort", "adaptive"])
    assert raised.value.code == 2


def test_the_thinking_budget_flag_reaches_the_anthropic_rail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline")
    args = build_parser().parse_args(
        ["run", "go", "--model", "claude-haiku-4-5", "--thinking-budget", "2048"]
    )
    transport = transport_from_args(args)
    assert isinstance(transport, AnthropicTransport)
    assert transport.thinking == {"type": "enabled", "budget_tokens": 2048}


@pytest.mark.parametrize(
    "argv",
    [
        ["--provider", "openai", "--thinking-budget", "2048"],
        ["--thinking-budget", "0"],
        ["--thinking-budget", "-5"],
        ["--thinking-budget", "4096", "--max-tokens", "4096"],
    ],
)
def test_an_unusable_thinking_budget_exits_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline")
    monkeypatch.setenv("OPENAI_API_KEY", "offline")
    assert main(["run", "--yes", "--jsonl", "--model", "m", *argv, "go"]) == 2
    assert capsys.readouterr().out == ""


class Effortful(FakeProvider):
    effort = "xhigh"
    thinking_budget = 2048


async def test_the_session_records_what_was_sent() -> None:
    session = new_session("go")
    async for _ in run(session, transport=Effortful([text_reply("done")])):
        pass
    assert (session.effort, session.thinking_budget) == ("xhigh", 2048)
    # A resume without the flags records that none was sent.
    session.status = "working"
    async for _ in run(session, transport=FakeProvider([text_reply("again")])):
        pass
    assert (session.effort, session.thinking_budget) == (None, None)


def test_an_older_session_file_loads_with_no_effort() -> None:
    raw = {
        "id": "s",
        "messages": [],
        "usage": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        "version": 1,
    }
    loaded = loads(json.dumps(raw))
    assert (loaded.effort, loaded.thinking_budget) == (None, None)


def test_the_contract_lists_the_levels_per_rail() -> None:
    assert describe()["effort"] == {
        "flag": "--effort",
        "levels": ["low", "medium", "high", "xhigh", "max"],
        "providers": {
            "anthropic": {
                "request": {"output_config": {"effort": "LEVEL"}},
                "thinking": {"type": "adaptive"},
            },
            "bedrock": {
                "request": {"output_config": {"effort": "LEVEL"}},
                "thinking": {"type": "adaptive"},
            },
            "openai": {"request": {"reasoning_effort": "LEVEL"}, "meaning": "server"},
        },
        "thinking_budget": {
            "flag": "--thinking-budget",
            "providers": ["anthropic", "bedrock"],
            "request": {"thinking": {"type": "enabled", "budget_tokens": "N"}},
        },
        "session_fields": ["effort", "thinking_budget"],
    }
