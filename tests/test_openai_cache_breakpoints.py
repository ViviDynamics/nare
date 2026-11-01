"""Opt-in prompt-cache breakpoints on the openai rail.

A proxy that forwards `cache_control` (LiteLLM does, to Anthropic and Bedrock)
can only cache what the request marks. These tests hold the openai rail's
markers to the anthropic rail's positions, and hold an unmarked request to
exactly what it was before the option existed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from nare.cli import build_parser, transport_from_args, validate_run_args
from nare.contract import describe
from nare.session import Message
from nare.transport import make_transport
from nare.transport.anthropic import AnthropicTransport, _with_cache_breakpoints
from nare.transport.anthropic import messages_from as anthropic_messages_from
from nare.transport.openai import OpenAITransport, messages_from, tools_from

MARK = {"type": "ephemeral"}

TOOLS = [
    {"name": "read", "description": "Read.", "input_schema": {"type": "object"}},
    {"name": "bash", "description": "Run.", "input_schema": {"type": "object"}},
]


def user(text: str) -> Message:
    return Message("user", [{"type": "text", "text": text}])


def call(call_id: str) -> Message:
    return Message(
        "assistant",
        [
            {"type": "text", "text": "reading"},
            {"type": "tool_use", "id": call_id, "name": "read", "input": {}},
        ],
    )


def results(*call_ids: str) -> Message:
    return Message(
        "user",
        [
            {"type": "tool_result", "tool_use_id": i, "content": f"body {i}"}
            for i in call_ids
        ],
    )


TRANSCRIPTS: dict[str, list[Message]] = {
    "ends on a prompt": [user("go")],
    "ends on a tool result": [user("go"), call("c1"), results("c1")],
    "ends on several tool results": [user("go"), call("c1"), results("c1", "c2")],
    "ends on a tool call": [user("go"), call("c1")],
    "ends on an assistant answer": [
        user("go"),
        Message("assistant", [{"type": "text", "text": "done"}]),
    ],
    "ends on thinking only": [
        user("go"),
        call("c1"),
        results("c1"),
        Message("assistant", [{"type": "thinking", "thinking": "hmm"}]),
    ],
}


def anthropic_marked(messages: list[Message]) -> list[tuple[str, str]]:
    wire, _ = _with_cache_breakpoints(anthropic_messages_from(messages), [])
    return [
        identity(block)
        for message in wire
        for block in message["content"]
        if block.get("cache_control") == MARK
    ]


def identity(block: dict[str, Any]) -> tuple[str, str]:
    kind = block["type"]
    if kind == "tool_result":
        return kind, block["tool_use_id"]
    if kind == "tool_use":
        return kind, block["id"]
    return kind, block["text"]


def openai_marked(payload: dict[str, Any]) -> list[tuple[str, str]]:
    marked: list[tuple[str, str]] = []
    for message in payload["messages"]:
        for tool_call in message.get("tool_calls") or []:
            if tool_call.get("cache_control") == MARK:
                marked.append(("tool_use", tool_call["id"]))
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if part.get("cache_control") != MARK:
                continue
            if message["role"] == "tool":
                marked.append(("tool_result", message["tool_call_id"]))
            else:
                marked.append(("text", part["text"]))
    return marked


def transport(**kwargs: Any) -> OpenAITransport:
    return OpenAITransport(model="m", api_key="k", **kwargs)


@pytest.mark.parametrize("name", list(TRANSCRIPTS))
def test_marks_the_same_block_as_the_anthropic_rail(name: str) -> None:
    messages = TRANSCRIPTS[name]

    payload = transport(cache_breakpoints=True)._payload(messages, TOOLS)

    assert openai_marked(payload) == anthropic_marked(messages)
    assert len(openai_marked(payload)) == 1


def test_marks_the_last_tool_as_the_anthropic_rail_does() -> None:
    payload = transport(cache_breakpoints=True)._payload([user("go")], TOOLS)
    _, anthropic_tools = _with_cache_breakpoints([], TOOLS)

    marked = [
        tool["function"]["name"]
        for tool in payload["tools"]
        if tool["function"].get("cache_control") == MARK
    ]
    assert marked == [
        tool["name"] for tool in anthropic_tools if tool.get("cache_control") == MARK
    ]
    assert marked == ["bash"]


def test_marking_leaves_the_text_and_the_callers_tools_unchanged() -> None:
    payload = transport(cache_breakpoints=True)._payload(
        TRANSCRIPTS["ends on a tool result"], TOOLS
    )

    assert payload["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": [{"type": "text", "text": "body c1", "cache_control": MARK}],
    }
    # Only the marked message changes shape; the rest stay strings.
    assert payload["messages"][0] == {"role": "user", "content": "go"}
    assert all("cache_control" not in tool for tool in TOOLS)


def test_a_system_prompt_is_not_marked() -> None:
    payload = transport(cache_breakpoints=True, system="be brief")._payload(
        [user("go")], []
    )

    assert payload["messages"][0] == {"role": "system", "content": "be brief"}
    assert openai_marked(payload) == [("text", "go")]


def test_an_image_result_is_marked_at_its_last_part() -> None:
    messages = [
        user("look"),
        call("c1"),
        Message(
            "user",
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "c1",
                    "content": "a.png",
                    "image": {
                        "source": {"media_type": "image/png", "data": "AAAA"},
                    },
                }
            ],
        ),
    ]

    payload = transport(cache_breakpoints=True, image_input=True)._payload(messages, [])

    # The image travels after the tool message on this rail, so the end of the
    # newest block is the image part, not the tool message.
    last = payload["messages"][-1]
    assert last["content"][-1]["type"] == "image_url"
    assert last["content"][-1]["cache_control"] == MARK
    assert json.dumps(payload).count('"cache_control"') == 1


@pytest.mark.parametrize("name", list(TRANSCRIPTS))
def test_off_by_default_the_request_is_unchanged(name: str) -> None:
    messages = TRANSCRIPTS[name]

    default = transport()._payload(messages, TOOLS)
    off = transport(cache_breakpoints=False)._payload(messages, TOOLS)

    expected = {
        "model": "m",
        "messages": messages_from(messages, image_model="OpenAITransport/model=m"),
        "tools": tools_from(TOOLS),
    }
    assert json.dumps(default) == json.dumps(expected)
    assert json.dumps(off) == json.dumps(expected)
    assert "cache_control" not in json.dumps(default)


def completion() -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 100},
        },
    }


async def test_a_recorded_request_carries_the_breakpoints() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=completion())

    rail = OpenAITransport(
        model="m",
        api_key="k",
        cache_breakpoints=True,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    messages = TRANSCRIPTS["ends on a tool result"]

    reply = await rail.turn(messages, TOOLS)

    sent = json.loads(seen[0].content)
    assert openai_marked(sent) == anthropic_marked(messages)
    assert sent["tools"][-1]["function"]["cache_control"] == MARK
    assert reply.usage.cache_read == 100


async def test_a_streamed_request_carries_the_breakpoints() -> None:
    from test_streaming import openai_chunks

    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, content=b"".join(openai_chunks()))

    rail = OpenAITransport(
        model="m",
        api_key="k",
        cache_breakpoints=True,
        streaming=True,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )

    await rail.turn([user("go")], TOOLS)

    sent = json.loads(seen[0].content)
    assert openai_marked(sent) == [("text", "go")]


@pytest.mark.parametrize("streaming", [False, True])
async def test_a_rejection_names_the_option_to_turn_off(streaming: bool) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            400, json={"error": {"message": "Extra inputs are not permitted"}}
        )

    rail = OpenAITransport(
        model="m",
        api_key="k",
        cache_breakpoints=True,
        streaming=streaming,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )

    with pytest.raises(RuntimeError) as raised:
        await rail.turn([user("go")], [])

    assert "Extra inputs are not permitted" in str(raised.value)
    assert "--cache-breakpoints" in str(raised.value)
    assert "NARE_CACHE_BREAKPOINTS" in str(raised.value)


async def test_a_rejection_without_breakpoints_does_not_blame_them() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, json={"error": {"message": "bad"}})

    rail = OpenAITransport(
        model="m",
        api_key="k",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )

    with pytest.raises(RuntimeError) as raised:
        await rail.turn([user("go")], [])

    assert "cache" not in str(raised.value)


def parse(argv: list[str]) -> Any:
    args = build_parser().parse_args(
        ["run", "hi", "--model", "m", "--tools", "none", *argv]
    )
    validate_run_args(args)
    return args


@pytest.mark.parametrize(
    ("argv", "env", "expected"),
    [
        ([], None, False),
        (["--cache-breakpoints"], None, True),
        ([], "1", True),
        ([], "true", True),
        ([], "0", False),
        ([], "", False),
    ],
)
def test_the_cli_binds_the_flag_and_the_environment(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    env: str | None,
    expected: bool,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")
    if env is None:
        monkeypatch.delenv("NARE_CACHE_BREAKPOINTS", raising=False)
    else:
        monkeypatch.setenv("NARE_CACHE_BREAKPOINTS", env)

    rail = transport_from_args(parse(["--provider", "openai", *argv]))

    assert isinstance(rail, OpenAITransport)
    assert rail.cache_breakpoints is expected


def test_the_tui_shares_the_flag() -> None:
    args = build_parser().parse_args(["tui", "--cache-breakpoints"])

    assert args.cache_breakpoints is True


def test_the_anthropic_rail_accepts_it_and_already_marks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-key")

    rail = transport_from_args(
        parse(["--provider", "anthropic", "--cache-breakpoints"])
    )

    assert isinstance(rail, AnthropicTransport)


def test_the_factory_passes_it_to_the_openai_rail() -> None:
    rail = make_transport("openai", model="m", api_key="k", cache_breakpoints=True)

    assert isinstance(rail, OpenAITransport)
    assert rail.cache_breakpoints is True


def test_the_contract_advertises_it() -> None:
    assert describe()["cache_breakpoints"] == {
        "flag": "--cache-breakpoints",
        "env": "NARE_CACHE_BREAKPOINTS",
        "providers": ["openai"],
        "default": False,
        "marker": {"type": "ephemeral"},
    }


def test_the_documented_contract_describes_it() -> None:
    root = Path(__file__).parent.parent
    contract = (root / "docs" / "contract.md").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")

    assert "## Prompt-cache breakpoints" in contract
    for text in (contract, readme):
        assert "--cache-breakpoints" in text
        assert "NARE_CACHE_BREAKPOINTS" in text
    # Automatic prefix caches on other servers hit only on a stable prefix.
    assert "stable prefix" in contract
