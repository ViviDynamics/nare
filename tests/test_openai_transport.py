"""The OpenAI-compatible transport: the second rail, and the one that carries
reasoning models whose answer the Anthropic translation drops.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest

from nare.session import Message
from nare.transport import Transport, make_transport
from nare.transport.openai import (
    OpenAITransport,
    messages_from,
    stop_reason_from,
    tools_from,
    usage_from,
)

assert callable(tools_from)

_check: Transport = OpenAITransport(model="m", api_key="k")

TOOL = {
    "name": "read",
    "description": "Read a file.",
    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
}


def responder(
    payload: dict[str, Any], seen: list[httpx2.Request]
) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=payload)

    return httpx2.MockTransport(handler)


def completion(**overrides: Any) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": "hello"}
    message.update(overrides.pop("message", {}))
    return {
        "id": "chatcmpl-1",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        **overrides,
    }


async def turn_with(
    payload: dict[str, Any],
    messages: list[Message] | None = None,
    tools: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> tuple[Any, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []
    transport = OpenAITransport(
        model="ada/qwen3-14b",
        api_key="k",
        base_url="https://proxy.example/v1",
        http_client=httpx2.AsyncClient(transport=responder(payload, seen)),
        **kwargs,
    )
    reply = await transport.turn(
        messages
        if messages is not None
        else [Message("user", [{"type": "text", "text": "hi"}])],
        tools if tools is not None else [],
    )
    return reply, seen


def body(request: httpx2.Request) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(request.content)
    return parsed


async def test_the_request_is_a_chat_completion_at_the_configured_base_url() -> None:
    _, seen = await turn_with(completion())

    assert str(seen[0].url) == "https://proxy.example/v1/chat/completions"
    assert body(seen[0])["model"] == "ada/qwen3-14b"
    assert body(seen[0])["messages"] == [{"role": "user", "content": "hi"}]


async def test_the_api_key_travels_as_a_bearer_token() -> None:
    _, seen = await turn_with(completion())

    assert seen[0].headers["authorization"] == "Bearer k"


async def test_a_system_prompt_leads_the_messages() -> None:
    _, seen = await turn_with(completion(), system="you are a verifier")

    assert body(seen[0])["messages"][0] == {
        "role": "system",
        "content": "you are a verifier",
    }


async def test_sampling_parameters_are_sent_when_set() -> None:
    _, seen = await turn_with(
        completion(), temperature=0.2, max_tokens=256, effort="high"
    )

    sent = body(seen[0])
    assert sent["temperature"] == 0.2
    assert sent["max_tokens"] == 256
    assert sent["reasoning_effort"] == "high"


async def test_unset_sampling_parameters_are_omitted_rather_than_guessed() -> None:
    _, seen = await turn_with(completion())

    sent = body(seen[0])
    assert "temperature" not in sent
    assert "reasoning_effort" not in sent


async def test_tools_are_translated_into_function_declarations() -> None:
    _, seen = await turn_with(completion(), tools=[TOOL])

    assert body(seen[0])["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "read",
                "description": "Read a file.",
                "parameters": TOOL["input_schema"],
            },
        }
    ]


async def test_no_tools_means_no_tools_field() -> None:
    _, seen = await turn_with(completion())

    assert "tools" not in body(seen[0])


def test_an_assistant_tool_call_becomes_a_tool_calls_entry() -> None:
    translated = messages_from(
        [
            Message(
                "assistant",
                [
                    {
                        "type": "tool_use",
                        "id": "c1",
                        "name": "read",
                        "input": {"path": "a"},
                    }
                ],
            ),
        ]
    )

    assert translated == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "read",
                        "arguments": json.dumps({"path": "a"}),
                    },
                }
            ],
        }
    ]


def test_a_tool_result_becomes_its_own_tool_message() -> None:
    translated = messages_from(
        [
            Message(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": "c1",
                        "content": "file body",
                        "is_error": False,
                    },
                    {"type": "text", "text": "and now this"},
                ],
            )
        ]
    )

    assert translated[0] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "file body",
    }
    assert translated[1] == {"role": "user", "content": "and now this"}


def test_thinking_is_not_sent_back_to_the_model() -> None:
    translated = messages_from(
        [
            Message(
                "assistant",
                [
                    {"type": "thinking", "thinking": "hmm"},
                    {"type": "text", "text": "answer"},
                ],
            )
        ]
    )

    assert translated == [{"role": "assistant", "content": "answer"}]


async def test_a_text_answer_comes_back_as_a_text_block() -> None:
    reply, _ = await turn_with(completion())

    assert reply.content == [{"type": "text", "text": "hello"}]
    assert reply.tool_calls == []
    assert reply.stop_reason == "end_turn"


async def test_reasoning_content_arrives_as_thinking_and_not_as_the_answer() -> None:
    # The whole reason this transport exists: the Anthropic translation drops
    # this model's answer, and folding reasoning into the answer would be worse.
    reply, _ = await turn_with(
        completion(
            message={
                "content": '{"verdict": "pass"}',
                "reasoning_content": "let me think",
            }
        )
    )

    assert reply.content == [
        {"type": "thinking", "thinking": "let me think"},
        {"type": "text", "text": '{"verdict": "pass"}'},
    ]


async def test_a_tool_call_comes_back_parsed() -> None:
    reply, _ = await turn_with(
        {
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_9",
                                "type": "function",
                                "function": {
                                    "name": "read",
                                    "arguments": '{"path": "a.txt"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    )

    assert reply.stop_reason == "tool_use"
    assert [(call.id, call.name, call.args) for call in reply.tool_calls] == [
        ("call_9", "read", {"path": "a.txt"})
    ]
    assert reply.content == [
        {"type": "tool_use", "id": "call_9", "name": "read", "input": {"path": "a.txt"}}
    ]


async def test_tool_arguments_that_are_not_json_do_not_kill_the_run() -> None:
    reply, _ = await turn_with(
        {
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "read", "arguments": "{oops"},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
    )

    # An empty argument dict reaches dispatch, which answers with an is_error
    # tool_result the model can correct. A raised exception would end the run.
    assert reply.tool_calls[0].args == {}


@pytest.mark.parametrize(
    ("finish", "expected"),
    [
        ("stop", "end_turn"),
        ("tool_calls", "tool_use"),
        ("function_call", "tool_use"),
        ("length", "max_tokens"),
        ("content_filter", "refusal"),
        (None, "end_turn"),
        ("something_new", "end_turn"),
    ],
)
def test_finish_reasons_map_into_nares_vocabulary(
    finish: str | None, expected: str
) -> None:
    assert stop_reason_from(finish) == expected


def test_cached_prompt_tokens_are_excluded_from_input() -> None:
    # nare's convention, taken from Anthropic: input counts what was actually
    # read. Leaving cached tokens in would inflate every cost a caller records.
    usage = usage_from(
        {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 60},
        }
    )

    assert (usage.input, usage.cache_read, usage.output) == (40, 60, 20)


def test_usage_survives_a_response_that_omits_it() -> None:
    usage = usage_from(None)

    assert (usage.input, usage.output) == (0, 0)


async def test_an_http_error_is_raised_with_the_body_the_proxy_sent() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            403, json={"error": {"message": "key not allowed to access model"}}
        )

    transport = OpenAITransport(
        model="m",
        api_key="k",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )

    with pytest.raises(RuntimeError, match="key not allowed"):
        await transport.turn([Message("user", [{"type": "text", "text": "hi"}])], [])


def test_a_missing_key_is_refused_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAITransport(model="m")


def test_the_factory_builds_it() -> None:
    built = make_transport("openai", model="m", api_key="k")

    assert isinstance(built, OpenAITransport)
