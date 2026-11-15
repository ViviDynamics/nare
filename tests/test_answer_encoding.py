"""Selected answer encodings preserve validation, usage and permitted recovery tools."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest

from fake_provider import StructuredProvider, text_reply, tool_reply
from nare import Policy, Reply, Tool, new_session, run
from nare.loop import StructuredOutput
from nare.transport.openai import OpenAITransport

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def document(
    mode: StructuredOutput,
    *,
    arguments: str = '{"ok":true}',
    calls: list[Any] | None = None,
    finish: str | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": arguments}
    if mode == "tool_call":
        message["content"] = None
        message["tool_calls"] = (
            calls
            if calls is not None
            else [
                {
                    "id": "answer-1",
                    "type": "function",
                    "function": {"name": "nare_final_answer", "arguments": arguments},
                }
            ]
        )
    return {
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish
                or ("tool_calls" if mode == "tool_call" else "stop"),
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }


def streaming_response(data: dict[str, Any]) -> httpx2.Response:
    choice = data["choices"][0]
    delta = dict(choice["message"])
    if isinstance(delta.get("tool_calls"), list):
        delta["tool_calls"] = [
            dict(call, index=index) if isinstance(call, dict) else call
            for index, call in enumerate(delta["tool_calls"])
        ]
    frame = {
        "choices": [
            {"index": 0, "delta": delta, "finish_reason": choice["finish_reason"]}
        ],
        "usage": data["usage"],
    }
    return httpx2.Response(
        200,
        text="data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n",
        headers={"content-type": "text/event-stream"},
    )


async def execute(
    mode: StructuredOutput,
    streaming: bool,
    replies: list[dict[str, Any]],
    *,
    max_corrections: int = 1,
) -> tuple[Any, list[Any], list[Any]]:
    payloads = []

    def provider(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            return httpx2.Response(
                200,
                json={
                    "data": [
                        {
                            "model_name": "fixture",
                            "model_info": {"max_input_tokens": 262144},
                        }
                    ]
                },
            )
        payloads.append(json.loads(request.content))
        data = replies.pop(0)
        return (
            streaming_response(data) if streaming else httpx2.Response(200, json=data)
        )

    transport = OpenAITransport(
        model="fixture",
        api_key="fixture-key",
        base_url="https://provider.example/v1",
        streaming=streaming,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(provider)),
    )
    session = new_session("Return a JSON judgment.")
    try:
        events = [
            event
            async for event in run(
                session,
                transport=transport,
                schema=SCHEMA,
                structured_output=mode,
                context_window=262144,
                policy=Policy(tools=frozenset()),
                max_corrections=max_corrections,
            )
        ]
    finally:
        await transport._client.aclose()
    return session, payloads, events


@pytest.mark.parametrize("mode", ["json_schema", "json_object", "tool_call"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_answer_mode_reaches_wire_and_canonical_validation(
    mode: StructuredOutput, streaming: bool
) -> None:
    session, payloads, events = await execute(mode, streaming, [document(mode)])
    assert session.status == "done" and session.output == {"ok": True}
    assert session.usage.input == 11 and session.usage.output == 7
    payload = payloads[0]
    if mode == "tool_call":
        assert payload["tool_choice"] == {
            "type": "function",
            "function": {"name": "nare_final_answer"},
        }
        assert payload["tools"][0]["function"]["parameters"] == SCHEMA
        assert payload["parallel_tool_calls"] is False
        assert "response_format" not in payload
        assert not any(event.type in {"tool_use", "tool_result"} for event in events)
    else:
        assert payload["response_format"]["type"] == mode
        assert "tools" not in payload
    assert [
        event.detail["structured_output"]["mode"]
        for event in events
        if "structured_output" in event.detail
    ] == [mode]


@pytest.mark.parametrize("mode", ["json_object", "tool_call"])
async def test_encoding_does_not_replace_local_schema(mode: StructuredOutput) -> None:
    session, payloads, _ = await execute(
        mode, False, [document(mode, arguments='{"ok":"wrong"}'), document(mode)]
    )
    assert session.status == "done" and session.output == {"ok": True}
    assert len(payloads) == 2 and session.schema_corrections == 1
    assert session.usage.input == 22


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "calls",
    [
        [],
        [
            {
                "id": "bad",
                "type": "function",
                "function": {"name": "run_shell", "arguments": '{"ok":true}'},
            }
        ],
        [
            {
                "id": "one",
                "type": "function",
                "function": {"name": "nare_final_answer", "arguments": '{"ok":true}'},
            },
            {
                "id": "two",
                "type": "function",
                "function": {"name": "run_shell", "arguments": "{}"},
            },
        ],
        [
            {
                "id": "dup",
                "type": "function",
                "function": {
                    "name": "nare_final_answer",
                    "arguments": '{"ok":true,"ok":false}',
                },
            }
        ],
    ],
)
async def test_invalid_function_answer_cannot_be_dispatched_or_admitted(
    streaming: bool, calls: list[Any]
) -> None:
    session, payloads, events = await execute(
        "tool_call",
        streaming,
        [document("tool_call", calls=calls), document("tool_call")],
    )
    assert session.output == {"ok": True} and session.status == "done"
    assert session.schema_corrections == 1 and len(payloads) == 2
    assert not any(event.type in {"tool_use", "tool_result"} for event in events)


@pytest.mark.parametrize("streaming", [False, True])
async def test_function_answer_cutoff_is_not_a_completed_judgment(
    streaming: bool,
) -> None:
    session, _, events = await execute(
        "tool_call", streaming, [document("tool_call", finish="length")]
    )
    assert session.status == "error"
    assert not any(event.type == "output" for event in events)
    assert session.stop_reason == "max_tokens"
    assert session.usage.input == 11


@pytest.mark.parametrize("callback", ["check", "diagnose"])
async def test_domain_correction_restores_tools_after_structural_correction(
    callback: str,
) -> None:
    seen = False

    async def read(args: Any) -> str:
        nonlocal seen
        seen = True
        return "verified evidence"

    async def validate(answer: Any) -> list[Any]:
        if not isinstance(answer, dict) or "ok" not in answer:
            return []
        return (
            []
            if seen
            else [
                {
                    "code": "evidence_required",
                    "message": "Read the evidence before judging.",
                }
            ]
        )

    provider = StructuredProvider(
        [
            text_reply("{}"),
            text_reply('{"ok":true}'),
            tool_reply("read_evidence", {}),
            text_reply('{"ok":true}'),
        ]
    )
    session = new_session("Review")
    [
        event
        async for event in run(
            session,
            transport=provider,
            schema=SCHEMA,
            structured_output="json_schema",
            check=validate if callback == "check" else None,
            diagnose=validate if callback == "diagnose" else None,
            max_corrections=2,
            tools=[
                Tool(
                    name="read_evidence",
                    description="Read evidence",
                    input_schema={"type": "object"},
                    call=read,
                )
            ],
            policy=Policy(
                tools=frozenset({"read_evidence"}), pending=frozenset({"read_evidence"})
            ),
        )
    ]
    assert session.status == "done" and session.output == {"ok": True}
    assert provider.constraints == [None, SCHEMA, None, None]
    assert seen and session.schema_corrections == 2


@pytest.mark.parametrize("mode", ["json_object", "tool_call"])
async def test_legacy_schema_transport_refuses_unsupported_mode_before_call(
    mode: StructuredOutput,
) -> None:
    provider = StructuredProvider([])
    with pytest.raises(ValueError, match="support"):
        [
            event
            async for event in run(
                new_session("Review"),
                transport=provider,
                schema=SCHEMA,
                structured_output=mode,
            )
        ]
    assert provider.calls == []


@pytest.mark.parametrize(
    "calls",
    [[None], [{"function": []}], [{"function": "invalid"}], {"unexpected": True}],
)
@pytest.mark.parametrize("streaming", [False, True])
async def test_malformed_function_envelope_spends_correction_not_transport_retry(
    calls: Any,
    streaming: bool,
) -> None:
    malformed = document("tool_call")
    malformed["choices"][0]["message"]["tool_calls"] = calls
    session, payloads, events = await execute(
        "tool_call", streaming, [malformed, document("tool_call")]
    )
    assert session.status == "done" and session.output == {"ok": True}
    assert len(payloads) == 2 and session.schema_corrections == 1
    assert session.usage.input == 22 and session.retries == 0
    assert not any(event.type in {"tool_use", "tool_result"} for event in events)


@pytest.mark.parametrize("mode", ["json_object", "tool_call"])
async def test_streamed_answer_repetition_is_cut_off_for_each_encoding(
    mode: StructuredOutput,
) -> None:
    frames: list[dict[str, Any]] = []
    for index, fragment in enumerate(['{"note":"', "repeat " * 400, '"}']):
        delta: dict[str, Any] = {"content": fragment}
        if mode == "tool_call":
            delta = {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "answer" if index == 0 else "",
                        "function": {
                            "name": "nare_final_answer" if index == 0 else "",
                            "arguments": fragment,
                        },
                    }
                ]
            }
        frames.append(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": delta,
                        "finish_reason": "stop" if index == 2 else None,
                    }
                ]
            }
        )
    frames.append(
        {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 800}}
    )
    body = (
        "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
        + "data: [DONE]\n\n"
    )
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, text=body))
    )
    transport = OpenAITransport(
        model="fixture", api_key="fixture-key", http_client=client
    )
    try:
        items = [
            item
            async for item in transport.stream_turn(
                [], [], response_schema={"type": "object"}, response_mode=mode
            )
        ]
    finally:
        await client.aclose()
    reply = items[-1]
    assert isinstance(reply, Reply)
    assert reply.stop_reason == "repetition"
    assert reply.tool_calls == []


async def test_optional_null_function_delta_can_be_followed_by_answer_arguments() -> (
    None
):
    frames = [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [{"index": 0, "id": "answer-1", "function": None}]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {
                                    "name": "nare_final_answer",
                                    "arguments": '{"ok":true}',
                                },
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        },
    ]
    body = (
        "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
        + "data: [DONE]\n\n"
    )
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, text=body))
    )
    transport = OpenAITransport(
        model="fixture", api_key="fixture-key", http_client=client
    )
    try:
        items = [
            item
            async for item in transport.stream_turn(
                [], [], response_schema=SCHEMA, response_mode="tool_call"
            )
        ]
    finally:
        await client.aclose()
    reply = items[-1]
    assert isinstance(reply, Reply)
    assert reply.content == [{"type": "text", "text": '{"ok":true}'}]
    assert reply.tool_calls == [] and reply.stop_reason == "end_turn"
    assert reply.usage.input == 11
