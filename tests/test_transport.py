import dataclasses
import json
import logging
import os
from typing import Any, cast, get_args

import httpx2
import pytest

from fake_provider import FakeProvider, text_reply
from nare.session import Message, Usage
from nare.transport import Reply, StopReason, ToolCall, make_transport
from nare.transport.anthropic import (
    _STOP_REASONS,
    DEFAULT_MAX_TOKENS,
    EFFORT_BUDGETS,
    NONSTREAMING_MAX_TOKENS,
    AnthropicTransport,
    stop_reason_from,
    usage_from,
)


def test_stop_reason_vocabulary_is_exactly_the_five() -> None:
    assert set(get_args(StopReason)) == {
        "end_turn",
        "tool_use",
        "max_tokens",
        "stop_sequence",
        "refusal",
    }


async def test_fake_provider_replays_in_order() -> None:
    fake = FakeProvider([text_reply("one"), text_reply("two")])
    first = await fake.turn([Message("user", [])], [])
    second = await fake.turn([Message("user", [])], [])
    assert first.content[0]["text"] == "one"
    assert second.content[0]["text"] == "two"
    assert len(fake.calls) == 2


async def test_fake_provider_raises_when_exhausted() -> None:
    with pytest.raises(AssertionError, match="ran out"):
        await FakeProvider([]).turn([], [])


def test_reply_is_frozen() -> None:
    reply = Reply(content=[], tool_calls=[], usage=Usage(), stop_reason="end_turn")
    with pytest.raises(dataclasses.FrozenInstanceError):
        reply.__setattr__("stop_reason", "tool_use")


def test_tool_call_carries_id_name_and_args() -> None:
    call = ToolCall(id="c1", name="read", args={"path": "x.py"})
    assert (call.id, call.name, call.args) == ("c1", "read", {"path": "x.py"})


async def test_recorded_calls_are_snapshots_not_live_references() -> None:
    messages = [Message("user", [{"type": "text", "text": "one"}])]
    tools = [{"name": "read"}]
    fake = FakeProvider([text_reply("ok")])
    await fake.turn(messages, tools)
    # Mutating the caller's objects after the call must not rewrite history:
    # later tasks assert on fake.calls to prove what a transport really saw.
    messages[0].content.append({"type": "text", "text": "two"})
    tools.append({"name": "write"})
    recorded_messages, recorded_tools = fake.calls[0]
    assert recorded_messages[0].content == [{"type": "text", "text": "one"}]
    assert recorded_tools == [{"name": "read"}]


def build(**kwargs: Any) -> AnthropicTransport:
    params: dict[str, Any] = {"model": "claude-sonnet-5", "api_key": "test-key"}
    params.update(kwargs)
    return AnthropicTransport(**params)


def test_max_tokens_defaults_to_8192() -> None:
    assert build().max_tokens == DEFAULT_MAX_TOKENS


def test_explicit_max_tokens_is_bound() -> None:
    assert build(max_tokens=512).max_tokens == 512


def test_no_effort_means_no_thinking_block() -> None:
    assert build().thinking is None


@pytest.mark.parametrize("effort,budget", sorted(EFFORT_BUDGETS.items()))
def test_effort_renders_a_thinking_budget(effort: str, budget: int) -> None:
    t = build(effort=effort)
    assert t.thinking == {"type": "enabled", "budget_tokens": budget}
    assert t.max_tokens == min(budget + DEFAULT_MAX_TOKENS, NONSTREAMING_MAX_TOKENS)


def test_effort_high_clamps_to_the_nonstreaming_ceiling() -> None:
    # budget + 8192 would be 24576, which the SDK refuses without streaming.
    t = build(effort="high")
    assert t.thinking == {"type": "enabled", "budget_tokens": 16384}
    assert t.max_tokens == NONSTREAMING_MAX_TOKENS == 21333


def test_temperature_is_refused_outright() -> None:
    # anthropic 1.6.0 removed temperature from messages.create. Refusing beats
    # silently dropping a sampling parameter the caller explicitly asked for.
    with pytest.raises(ValueError, match="temperature"):
        build(temperature=0.2)


def test_temperature_is_refused_with_effort_too() -> None:
    with pytest.raises(ValueError, match="temperature"):
        build(effort="high", temperature=0.2)


def test_effort_with_max_tokens_at_or_below_the_budget_raises() -> None:
    with pytest.raises(ValueError, match="16384"):
        build(effort="high", max_tokens=16384)


def test_effort_with_max_tokens_above_the_budget_is_accepted() -> None:
    assert build(effort="high", max_tokens=20000).max_tokens == 20000


def test_max_tokens_above_the_nonstreaming_ceiling_raises() -> None:
    with pytest.raises(ValueError, match="streaming"):
        build(max_tokens=NONSTREAMING_MAX_TOKENS + 1)


def test_missing_api_key_raises_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        AnthropicTransport(model="claude-sonnet-5")


CANNED_BODY: dict[str, Any] = {
    "id": "msg_01",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-5",
    "content": [
        {"type": "text", "text": "reading it now"},
        {"type": "tool_use", "id": "call_1", "name": "read", "input": {"path": "a.py"}},
    ],
    "stop_reason": "tool_use",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 200,
        "output_tokens": 50,
        "cache_read_input_tokens": 800,
        "cache_creation_input_tokens": 12,
    },
}


def recording_client(captured: list[httpx2.Request]) -> httpx2.AsyncClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        return httpx2.Response(200, json=CANNED_BODY)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


async def outbound(**kwargs: Any) -> dict[str, Any]:
    captured: list[httpx2.Request] = []
    transport = build(http_client=recording_client(captured), **kwargs)
    await transport.turn(
        [Message("user", [{"type": "text", "text": "read a.py"}])],
        [{"name": "read", "description": "read a file", "input_schema": {}}],
    )
    body: dict[str, Any] = json.loads(captured[0].content)
    return body


async def test_model_and_max_tokens_reach_the_wire() -> None:
    body = await outbound(max_tokens=512)
    assert body["model"] == "claude-sonnet-5"
    assert body["max_tokens"] == 512


async def test_temperature_never_reaches_the_wire() -> None:
    # The constructor refuses --temperature outright (task 5), so there is no
    # path by which this vendor's request body can carry one.
    assert "temperature" not in await outbound()
    with pytest.raises(ValueError, match="temperature"):
        await outbound(temperature=0.2)


async def test_unset_sampling_params_are_omitted_entirely() -> None:
    body = await outbound()
    assert "thinking" not in body
    assert "system" not in body


async def test_system_reaches_the_wire() -> None:
    assert (await outbound(system="You are the architect."))["system"] == (
        "You are the architect."
    )


async def test_effort_renders_thinking_clamped_to_the_ceiling() -> None:
    body = await outbound(effort="high")
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 16384}
    assert "temperature" not in body
    assert body["max_tokens"] == 21333


async def test_tool_schemas_pass_through_anthropic_shaped() -> None:
    assert (await outbound())["tools"] == [
        {"name": "read", "description": "read a file", "input_schema": {}}
    ]


async def test_messages_serialize_as_role_plus_blocks() -> None:
    assert (await outbound())["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "read a.py"}]}
    ]


async def test_turn_normalizes_the_response() -> None:
    transport = build(http_client=recording_client([]))
    reply = await transport.turn([Message("user", [])], [])
    assert reply.stop_reason == "tool_use"
    assert reply.tool_calls == [
        ToolCall(id="call_1", name="read", args={"path": "a.py"})
    ]
    assert reply.content[0]["text"] == "reading it now"
    assert reply.usage == Usage(input=200, output=50, cache_read=800, cache_write=12)


# --- Normalization tables, section 4 of the transport spec -------------------
# The OpenAI rows are asserted now, before the OpenAI transport exists. If its
# spec later contradicts these rows, that contradiction is worth catching.

ANTHROPIC_STOP_REASONS = [
    ("end_turn", "end_turn"),
    ("tool_use", "tool_use"),
    ("max_tokens", "max_tokens"),
    ("stop_sequence", "stop_sequence"),
    ("refusal", "refusal"),
]

OPENAI_FINISH_REASONS = [
    ("stop", "end_turn"),
    ("tool_calls", "tool_use"),
    ("length", "max_tokens"),
    ("content_filter", "refusal"),
]


@pytest.mark.parametrize("raw,expected", ANTHROPIC_STOP_REASONS)
def test_anthropic_stop_reasons_normalize(raw: str, expected: str) -> None:
    assert stop_reason_from(raw) == expected


@pytest.mark.parametrize("raw,expected", OPENAI_FINISH_REASONS)
def test_openai_finish_reasons_have_a_target_in_the_vocabulary(
    raw: str, expected: str
) -> None:
    assert expected in get_args(StopReason)


def test_every_vendor_stop_reason_is_mapped() -> None:
    # The map must be total against the SDK we ship with. Without this, a
    # new vendor value degrades silently to end_turn, and a truncated run
    # reports itself as done. Verified once by hand that this catches
    # model_context_window_exceeded; the point is that it keeps catching
    # whatever the next SDK release adds.
    from anthropic.types.stop_reason import StopReason as VendorStopReason

    for value in get_args(VendorStopReason):
        assert value in _STOP_REASONS, f"unmapped vendor stop_reason: {value}"


def test_a_genuinely_unknown_stop_reason_degrades_to_end_turn() -> None:
    assert stop_reason_from("something_the_vendor_added_later") == "end_turn"


class _RawUsage:
    input_tokens = 200
    output_tokens = 50
    cache_read_input_tokens = 800
    cache_creation_input_tokens = 12


def test_anthropic_usage_excludes_cache_reads_from_input() -> None:
    assert usage_from(_RawUsage()) == Usage(
        input=200, output=50, cache_read=800, cache_write=12
    )


def test_openai_usage_would_subtract_cached_tokens_to_reach_the_same_numbers() -> None:
    # OpenAI's prompt_tokens INCLUDES cache reads; Anthropic's input_tokens does
    # not. The same logical request must produce the same Usage on both edges.
    prompt_tokens, cached_tokens, completion_tokens = 1000, 800, 50
    openai_side = Usage(
        input=prompt_tokens - cached_tokens,
        output=completion_tokens,
        cache_read=cached_tokens,
        cache_write=0,  # OpenAI has no cache-write concept
    )
    assert openai_side.input == usage_from(_RawUsage()).input
    assert openai_side.cache_read == usage_from(_RawUsage()).cache_read


def test_a_missing_cache_field_warns_instead_of_silently_reporting_zero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class MovedSchema:
        input_tokens = 200
        output_tokens = 50
        # cache_read_input_tokens / cache_creation_input_tokens are gone

    with caplog.at_level(logging.WARNING):
        usage = usage_from(MovedSchema())
    assert usage == Usage(input=200, output=50, cache_read=0, cache_write=0)
    assert "cache_read_input_tokens" in caplog.text
    assert "cache_creation_input_tokens" in caplog.text


def test_a_null_cache_field_is_a_quiet_zero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class NullCache:
        input_tokens = 200
        output_tokens = 50
        cache_read_input_tokens = None
        cache_creation_input_tokens = None

    with caplog.at_level(logging.WARNING):
        usage = usage_from(NullCache())
    assert usage == Usage(input=200, output=50, cache_read=0, cache_write=0)
    assert caplog.text == ""


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"), reason="needs a real API key"
)
async def test_live_smoke() -> None:
    transport = AnthropicTransport(
        model=os.environ.get("NARE_MODEL", "claude-sonnet-5"), max_tokens=64
    )
    reply = await transport.turn(
        [Message("user", [{"type": "text", "text": "Reply with the word OK."}])], []
    )
    assert reply.stop_reason in get_args(StopReason)
    assert reply.usage.output > 0


def test_anthropic_kind_builds_an_anthropic_transport() -> None:
    t = make_transport("anthropic", model="claude-sonnet-5", api_key="test-key")
    assert isinstance(t, AnthropicTransport)


def test_parameters_reach_the_transport() -> None:
    t = cast(
        AnthropicTransport,
        make_transport(
            "anthropic", model="m", api_key="k", max_tokens=99, system="persona"
        ),
    )
    assert (t.model, t.max_tokens, t.system) == ("m", 99, "persona")


def test_an_unknown_kind_names_the_supported_kinds() -> None:
    with pytest.raises(ValueError, match="anthropic"):
        make_transport("openai", model="gpt-4o", api_key="k")


def test_temperature_refusal_surfaces_from_the_factory() -> None:
    with pytest.raises(ValueError, match="temperature"):
        make_transport("anthropic", model="m", api_key="k", temperature=0.2)


# claude-opus-4-0 caps non-streaming output at 8192 in the SDK's own table.
# Checking only the global ceiling let it construct and then fail on every
# single turn, with an error blaming streaming rather than the model.
OPUS = "claude-opus-4-0"


def test_a_models_own_nonstreaming_cap_is_enforced_at_construction() -> None:
    with pytest.raises(ValueError, match="non-streaming cap"):
        build(model=OPUS, max_tokens=12288)


def test_effort_resolves_under_the_models_cap_rather_than_the_global_one() -> None:
    assert build(model=OPUS, effort="medium").max_tokens == 8192
    # An uncapped model still gets the global headroom.
    assert build(effort="medium").max_tokens > 8192


def test_an_effort_budget_the_model_cannot_afford_fails_loudly() -> None:
    # high wants 16384 of thinking, which this model cannot fit under 8192.
    with pytest.raises(ValueError, match="must exceed"):
        build(model=OPUS, effort="high")
