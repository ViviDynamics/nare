"""Claude on Amazon Bedrock, checked offline.

No AWS account, credential or network is used. The request is signed by the
real botocore signer with fake keys, and responses come from a MockTransport,
either as JSON or as an AWS event stream framed the way Bedrock frames it. The
live check against Bedrock is a separate step before release.
"""

import base64
import json
import struct
import sys
import zlib
from pathlib import Path
from typing import Any

import httpx2
import pytest
from anthropic import APIError, AsyncAnthropicBedrock

from nare.cli import build_parser, main, transport_from_args
from nare.loop import check_structured_output
from nare.session import Message
from nare.tools import _shell_env
from nare.transport import (
    CREDENTIAL_ENV,
    Delta,
    Reply,
    StreamDropped,
    Transient,
    make_transport,
)
from nare.transport.anthropic import NONSTREAMING_MAX_TOKENS
from nare.transport.bedrock import BedrockTransport, bedrock_base_model

MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
OPUS = "us.anthropic.claude-opus-4-20250514-v1:0"
AWS_ENV = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "NARE_AWS_REGION",
    "AWS_BEARER_TOKEN_BEDROCK",
    "AWS_ROLE_ARN",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "NARE_PROVIDER",
)


@pytest.fixture(autouse=True)
def no_aws(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in AWS_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")


@pytest.fixture
def aws_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDOFFLINE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "offline-secret")


def without_botocore(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("boto3", "botocore", "botocore.session"):
        monkeypatch.setitem(sys.modules, name, None)


CANNED_BODY: dict[str, Any] = {
    "id": "msg_01",
    "type": "message",
    "role": "assistant",
    "model": MODEL,
    "content": [
        {"type": "text", "text": "reading it now"},
        {"type": "tool_use", "id": "call_1", "name": "read", "input": {"path": "a"}},
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


def event_frame(payload: dict[str, Any]) -> bytes:
    """One AWS event-stream message carrying an Anthropic stream event: the
    framing Bedrock's invoke-with-response-stream uses in place of SSE."""
    body = json.dumps(
        {"bytes": base64.b64encode(json.dumps(payload).encode()).decode()}
    ).encode()
    headers = b""
    for name, value in (
        (":event-type", "chunk"),
        (":content-type", "application/json"),
        (":message-type", "event"),
    ):
        headers += bytes([len(name)]) + name.encode() + b"\x07"
        headers += struct.pack(">H", len(value)) + value.encode()
    total = 12 + len(headers) + len(body) + 4
    prelude = struct.pack(">II", total, len(headers))
    prelude += struct.pack(">I", zlib.crc32(prelude))
    message = prelude + headers + body
    return message + struct.pack(">I", zlib.crc32(message))


STREAM_EVENTS: list[dict[str, Any]] = [
    {
        "type": "message_start",
        "message": {
            "id": "m",
            "type": "message",
            "role": "assistant",
            "model": MODEL,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 7,
                "output_tokens": 0,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 2,
            },
        },
    },
    {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "thinking", "thinking": "", "signature": ""},
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "thinking_delta", "thinking": "plan"},
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "signature_delta", "signature": "sig"},
    },
    {"type": "content_block_stop", "index": 0},
    {
        "type": "content_block_start",
        "index": 1,
        "content_block": {"type": "text", "text": ""},
    },
    {
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "text_delta", "text": "hello"},
    },
    {"type": "content_block_stop", "index": 1},
    {
        "type": "content_block_start",
        "index": 2,
        "content_block": {"type": "tool_use", "id": "c1", "name": "read", "input": {}},
    },
    {
        "type": "content_block_delta",
        "index": 2,
        "delta": {"type": "input_json_delta", "partial_json": '{"path": "a"}'},
    },
    {"type": "content_block_stop", "index": 2},
    {
        "type": "message_delta",
        "delta": {"stop_reason": "tool_use", "stop_sequence": None},
        "usage": {"output_tokens": 5},
    },
    {"type": "message_stop"},
]


def with_arguments(events: list[dict[str, Any]], partial: str) -> list[dict[str, Any]]:
    """The stream with its tool call's argument fragment replaced."""
    edited = []
    for event in events:
        if event.get("type") == "content_block_delta" and event.get("index") == 2:
            event = {
                **event,
                "delta": {"type": "input_json_delta", "partial_json": partial},
            }
        edited.append(event)
    return edited


async def collect(stream: Any) -> list[Any]:
    return [item async for item in stream]


def server(
    captured: list[httpx2.Request],
    *,
    stream: bool = False,
    events: list[dict[str, Any]] | None = None,
    raw: bytes | None = None,
    status: int = 200,
    error: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    raise_for_request: BaseException | None = None,
) -> httpx2.AsyncClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        if raise_for_request is not None:
            raise raise_for_request
        if error is not None:
            return httpx2.Response(
                status,
                json=error,
                headers={"content-type": "application/json", **(headers or {})},
            )
        if stream:
            frames = STREAM_EVENTS if events is None else events
            return httpx2.Response(
                200,
                headers={"content-type": "application/vnd.amazon.eventstream"},
                content=b"".join(event_frame(e) for e in frames),
            )
        body = raw if raw is not None else json.dumps(CANNED_BODY).encode()
        return httpx2.Response(
            200, content=body, headers={"content-type": "application/json"}
        )

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def build(**kwargs: Any) -> BedrockTransport:
    params: dict[str, Any] = {"model": MODEL, "region": "us-east-1"}
    params.update(kwargs)
    return BedrockTransport(**params)


TOOLS = [{"name": "read", "description": "read a file", "input_schema": {}}]
PROMPT = [Message("user", [{"type": "text", "text": "read a"}])]


# Construction, region and credentials.


@pytest.mark.usefixtures("aws_keys")
def test_bedrock_kind_builds_a_bedrock_client_without_an_anthropic_key() -> None:
    t = make_transport("bedrock", model=MODEL, region="eu-west-1")
    assert isinstance(t, BedrockTransport)
    assert isinstance(t._client, AsyncAnthropicBedrock)
    assert t._client.aws_region == "eu-west-1"
    assert str(t._client.base_url).startswith(
        "https://bedrock-runtime.eu-west-1.amazonaws.com"
    )


@pytest.mark.usefixtures("aws_keys")
@pytest.mark.parametrize(
    ("env", "flag", "expected"),
    [
        ({"NARE_AWS_REGION": "a", "AWS_REGION": "b"}, "flag", "flag"),
        ({"NARE_AWS_REGION": "a", "AWS_REGION": "b"}, None, "a"),
        ({"AWS_REGION": "b", "AWS_DEFAULT_REGION": "c"}, None, "b"),
        ({"AWS_DEFAULT_REGION": "c"}, None, "c"),
    ],
)
def test_region_resolution_order(
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
    flag: str | None,
    expected: str,
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert BedrockTransport(model=MODEL, region=flag).region == expected


@pytest.mark.usefixtures("aws_keys")
def test_missing_region_names_region_and_credentials() -> None:
    with pytest.raises(ValueError) as caught:
        BedrockTransport(model=MODEL)
    message = str(caught.value)
    assert "region" in message
    assert "credentials" in message
    assert "--region" in message


def test_missing_credentials_names_region_and_credentials() -> None:
    with pytest.raises(ValueError) as caught:
        BedrockTransport(model=MODEL, region="us-east-1")
    message = str(caught.value)
    assert "credentials" in message
    assert "region" in message


def test_a_bearer_token_needs_no_aws_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "offline-bearer")
    t = build()
    assert t._client.api_key == "offline-bearer"


@pytest.mark.usefixtures("aws_keys")
def test_without_the_extra_bedrock_is_refused_naming_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    without_botocore(monkeypatch)
    with pytest.raises(ValueError, match=r"nare\[bedrock\]"):
        build()


def test_a_bearer_token_still_needs_the_extra_for_stream_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "offline-bearer")
    without_botocore(monkeypatch)
    with pytest.raises(ValueError, match=r"nare\[bedrock\]"):
        build()


@pytest.mark.usefixtures("aws_keys")
def test_base_url_overrides_the_runtime_endpoint() -> None:
    t = build(base_url="https://vpce.example.test")
    assert str(t._client.base_url).startswith("https://vpce.example.test")


@pytest.mark.usefixtures("aws_keys")
def test_temperature_is_refused_as_on_anthropic() -> None:
    with pytest.raises(ValueError, match="temperature"):
        build(temperature=0.2)


@pytest.mark.usefixtures("aws_keys")
def test_an_api_key_is_refused_because_bedrock_takes_aws_credentials() -> None:
    with pytest.raises(ValueError, match="AWS credentials"):
        make_transport("bedrock", model=MODEL, region="us-east-1", api_key="k")


@pytest.mark.usefixtures("aws_keys")
def test_proxy_num_retries_is_refused_on_bedrock() -> None:
    with pytest.raises(ValueError, match="openai provider"):
        make_transport("bedrock", model=MODEL, region="us-east-1", proxy_num_retries=0)


def test_region_is_refused_for_the_anthropic_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with pytest.raises(ValueError, match="--region applies only to --provider bedrock"):
        make_transport("anthropic", model="claude-sonnet-5", region="us-east-1")


@pytest.mark.usefixtures("aws_keys")
def test_the_client_keeps_nares_timeout_and_no_sdk_retries() -> None:
    t = build(request_timeout=5.0)
    assert t._client.timeout == 5.0
    assert t._client.max_retries == 0


@pytest.mark.usefixtures("aws_keys")
def test_structured_output_is_refused_before_any_turn() -> None:
    t = build()
    assert t.supports_structured_output is False
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    with pytest.raises(ValueError, match="does not support structured output"):
        check_structured_output(t, schema, "json_schema")


# Model caps and discovery.


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("us.anthropic.claude-opus-4-20250514-v1:0", "claude-opus-4-20250514"),
        ("eu.anthropic.claude-haiku-4-5-20251001-v1:0", "claude-haiku-4-5-20251001"),
        ("anthropic.claude-opus-4-1-20250805-v1:0", "claude-opus-4-1-20250805"),
        ("claude-sonnet-5", "claude-sonnet-5"),
        (
            "arn:aws:bedrock:us-east-1:1:application-inference-profile/abc",
            "arn:aws:bedrock:us-east-1:1:application-inference-profile/abc",
        ),
    ],
)  # fmt: skip
def test_bedrock_ids_map_to_their_base_model(model: str, expected: str) -> None:
    assert bedrock_base_model(model) == expected


@pytest.mark.usefixtures("aws_keys")
def test_a_profile_id_gets_its_models_nonstreaming_cap() -> None:
    with pytest.raises(ValueError, match="non-streaming cap"):
        build(model=OPUS, max_tokens=12288)
    assert build(model=MODEL, max_tokens=12288).max_tokens == 12288
    with pytest.raises(ValueError, match=str(NONSTREAMING_MAX_TOKENS)):
        build(max_tokens=NONSTREAMING_MAX_TOKENS + 1)


@pytest.mark.usefixtures("aws_keys")
async def test_no_context_window_discovery_request() -> None:
    captured: list[httpx2.Request] = []
    t = build(
        base_url="https://vpce.example.test",
        http_client=server(captured),
    )
    assert await t.context_window() is None
    assert await t.prices() is None
    assert captured == []


# The wire, signed. Non-streaming.


@pytest.mark.usefixtures("aws_keys")
async def test_a_turn_is_sigv4_signed_and_normalized_like_anthropic() -> None:
    captured: list[httpx2.Request] = []
    t = build(http_client=server(captured))
    reply = await t.turn(PROMPT, TOOLS)
    request = captured[0]
    assert request.url.host == "bedrock-runtime.us-east-1.amazonaws.com"
    assert request.url.path == f"/model/{MODEL}/invoke"
    assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256")
    assert "x-api-key" not in request.headers
    body = json.loads(request.content)
    assert body["anthropic_version"] == "bedrock-2023-05-31"
    assert "model" not in body
    assert body["max_tokens"] == t.max_tokens
    assert reply.tool_calls[0].args == {"path": "a"}
    assert reply.stop_reason == "tool_use"
    assert (
        reply.usage.input,
        reply.usage.output,
        reply.usage.cache_read,
        reply.usage.cache_write,
    ) == (200, 50, 800, 12)
    assert reply.cost is None


@pytest.mark.usefixtures("aws_keys")
async def test_effort_reaches_the_wire() -> None:
    captured: list[httpx2.Request] = []
    t = build(effort="high", http_client=server(captured))
    await t.turn(PROMPT, TOOLS)
    body = json.loads(captured[0].content)
    assert body["output_config"] == {"effort": "high"}
    assert body["thinking"] == {"type": "adaptive"}


@pytest.mark.usefixtures("aws_keys")
async def test_a_thinking_budget_reaches_the_wire() -> None:
    captured: list[httpx2.Request] = []
    t = build(thinking_budget=1024, http_client=server(captured))
    await t.turn(PROMPT, TOOLS)
    body = json.loads(captured[0].content)
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert "output_config" not in body


@pytest.mark.usefixtures("aws_keys")
async def test_tool_choice_none_reaches_the_wire_on_the_summary_turn() -> None:
    captured: list[httpx2.Request] = []
    t = build(http_client=server(captured))
    await t.turn(PROMPT, TOOLS, tool_choice="none")
    body = json.loads(captured[0].content)
    assert body["tool_choice"] == {"type": "none"}
    assert body["tools"][0]["name"] == "read"


@pytest.mark.usefixtures("aws_keys")
async def test_tool_choice_is_omitted_without_tools() -> None:
    captured: list[httpx2.Request] = []
    t = build(http_client=server(captured))
    await t.turn(PROMPT, [], tool_choice="none")
    assert "tool_choice" not in json.loads(captured[0].content)


@pytest.mark.usefixtures("aws_keys")
async def test_prompt_cache_breakpoints_reach_the_wire() -> None:
    captured: list[httpx2.Request] = []
    t = build(http_client=server(captured))
    await t.turn(PROMPT, TOOLS)
    body = json.loads(captured[0].content)
    marker = {"type": "ephemeral"}
    assert body["tools"][-1]["cache_control"] == marker
    assert body["messages"][-1]["content"][-1]["cache_control"] == marker


@pytest.mark.usefixtures("aws_keys")
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("max_tokens", "max_tokens"),
        ("model_context_window_exceeded", "max_tokens"),
        ("end_turn", "end_turn"),
    ],
)
async def test_a_cut_off_reply_is_reported_as_a_cut_off(
    raw: str, expected: str
) -> None:
    body = {**CANNED_BODY, "stop_reason": raw, "content": [CANNED_BODY["content"][0]]}
    t = build(http_client=server([], raw=json.dumps(body).encode()))
    reply = await t.turn(PROMPT, [])
    assert reply.stop_reason == expected


@pytest.mark.usefixtures("aws_keys")
async def test_a_repeated_key_in_tool_arguments_is_refused_not_run() -> None:
    raw = json.dumps(CANNED_BODY).replace('{"path": "a"}', '{"path": "a", "path": "b"}')
    t = build(http_client=server([], raw=raw.encode()))
    reply = await t.turn(PROMPT, TOOLS)
    call = reply.tool_calls[0]
    assert call.args == {}
    assert call.error is not None and "tool not run" in call.error


@pytest.mark.usefixtures("aws_keys")
async def test_a_throttled_request_is_transient_with_its_retry_after() -> None:
    captured: list[httpx2.Request] = []
    t = build(
        http_client=server(
            captured,
            status=429,
            error={"message": "Too many requests"},
            headers={"retry-after": "3"},
        )
    )
    with pytest.raises(APIError) as caught:
        await t.turn(PROMPT, TOOLS)
    assert t.transient(caught.value) == Transient("status", status=429, retry_after=3.0)


@pytest.mark.usefixtures("aws_keys")
async def test_a_bedrock_500_is_transient_as_it_is_on_anthropic() -> None:
    t = build(
        http_client=server([], status=500, error={"message": "internal"}),
    )
    with pytest.raises(APIError) as caught:
        await t.turn(PROMPT, TOOLS)
    assert t.transient(caught.value) == Transient("status", status=500)


@pytest.mark.usefixtures("aws_keys")
async def test_a_validation_error_is_permanent_and_not_resent() -> None:
    t = build(
        http_client=server([], status=400, error={"message": "bad request"}),
    )
    with pytest.raises(APIError) as caught:
        await t.turn(PROMPT, TOOLS)
    assert t.transient(caught.value) is None


@pytest.mark.usefixtures("aws_keys")
async def test_a_read_timeout_is_transient() -> None:
    t = build(http_client=server([], raise_for_request=httpx2.ReadTimeout("slow")))
    with pytest.raises(APIError) as caught:
        await t.turn(PROMPT, TOOLS)
    assert t.transient(caught.value) == Transient("timeout")


# The wire, streamed. The AWS event stream, decoded by the SDK.


@pytest.mark.usefixtures("aws_keys")
async def test_streaming_decodes_the_aws_event_stream() -> None:
    captured: list[httpx2.Request] = []
    t = build(streaming=True, effort="low", http_client=server(captured, stream=True))
    items = await collect(t.stream_turn(PROMPT, TOOLS))
    assert captured[0].url.path == f"/model/{MODEL}/invoke-with-response-stream"
    assert captured[0].headers["authorization"].startswith("AWS4-HMAC-SHA256")
    deltas = [item for item in items if isinstance(item, Delta)]
    assert Delta("thinking", "plan") in deltas
    assert Delta("progress", "hello") in deltas
    reply = items[-1]
    assert isinstance(reply, Reply)
    assert reply.tool_calls[0].args == {"path": "a"}
    assert reply.stop_reason == "tool_use"
    assert (
        reply.usage.input,
        reply.usage.output,
        reply.usage.cache_read,
        reply.usage.cache_write,
    ) == (7, 5, 3, 2)


@pytest.mark.usefixtures("aws_keys")
async def test_a_streamed_repeated_key_in_tool_arguments_is_refused() -> None:
    events = with_arguments(STREAM_EVENTS, '{"path": "a", "path": "b"}')
    t = build(streaming=True, http_client=server([], stream=True, events=events))
    items = await collect(t.stream_turn(PROMPT, TOOLS))
    reply = items[-1]
    assert isinstance(reply, Reply)
    assert reply.tool_calls[0].args == {}
    assert reply.tool_calls[0].error is not None


@pytest.mark.usefixtures("aws_keys")
async def test_a_stream_that_ends_without_message_stop_is_transient() -> None:
    events = [e for e in STREAM_EVENTS if e["type"] != "message_stop"]
    t = build(streaming=True, http_client=server([], stream=True, events=events))
    with pytest.raises(StreamDropped) as caught:
        await collect(t.stream_turn(PROMPT, TOOLS))
    assert t.transient(caught.value) == Transient("stream")


@pytest.mark.usefixtures("aws_keys")
async def test_a_streamed_cut_off_is_reported_as_max_tokens() -> None:
    events = [
        {**e, "delta": {"stop_reason": "max_tokens", "stop_sequence": None}}
        if e["type"] == "message_delta"
        else e
        for e in STREAM_EVENTS
    ]
    t = build(streaming=True, http_client=server([], stream=True, events=events))
    items = await collect(t.stream_turn(PROMPT, TOOLS))
    reply = items[-1]
    assert isinstance(reply, Reply)
    assert reply.stop_reason == "max_tokens"


# Credentials and the bash environment.


def test_the_bedrock_credentials_are_withheld_from_bash() -> None:
    assert {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_BEARER_TOKEN_BEDROCK",
    } <= CREDENTIAL_ENV


def test_bash_does_not_see_the_aws_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDOFFLINE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "offline-secret")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    env = _shell_env(credentials=False)
    assert env is not None
    assert "AWS_ACCESS_KEY_ID" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert env.get("AWS_REGION") == "us-east-1"


# The command line.


def test_cli_builds_the_bedrock_transport_from_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {
        "AWS_ACCESS_KEY_ID": "AKIDOFFLINE",
        "AWS_SECRET_ACCESS_KEY": "offline-secret",
    }.items():
        monkeypatch.setenv(name, value)
    args = build_parser().parse_args(
        ["run", "--yes", "--provider", "bedrock", "--region", "eu-west-1",
         "--model", MODEL, "--effort", "low", "go"],
    )  # fmt: skip
    t = transport_from_args(args)
    assert isinstance(t, BedrockTransport)
    assert t.region == "eu-west-1"


def test_cli_missing_region_exits_2_naming_both(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["run", "--yes", "--provider", "bedrock", "--model", MODEL, "go"])
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "region" in captured.err
    assert "credentials" in captured.err


@pytest.mark.usefixtures("aws_keys")
def test_cli_without_the_extra_exits_2_naming_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    without_botocore(monkeypatch)
    code = main(
        ["run", "--yes", "--provider", "bedrock", "--region", "us-east-1",
         "--model", MODEL, "go"],
    )  # fmt: skip
    assert code == 2
    assert "nare[bedrock]" in capsys.readouterr().err


def test_cli_region_flag_is_refused_for_other_providers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert main(["run", "--yes", "--region", "us-east-1", "go"]) == 2
    assert "--region" in capsys.readouterr().err


def test_cli_thinking_budget_is_accepted_on_bedrock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {
        "AWS_ACCESS_KEY_ID": "AKIDOFFLINE",
        "AWS_SECRET_ACCESS_KEY": "offline-secret",
    }.items():
        monkeypatch.setenv(name, value)
    args = build_parser().parse_args(
        ["run", "--yes", "--provider", "bedrock", "--region", "us-east-1",
         "--model", MODEL, "--thinking-budget", "2048", "go"],
    )  # fmt: skip
    t = transport_from_args(args)
    assert isinstance(t, BedrockTransport)
    assert t.thinking == {"type": "enabled", "budget_tokens": 2048}


def test_cli_structured_output_is_refused_on_bedrock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, value in {
        "AWS_ACCESS_KEY_ID": "AKIDOFFLINE",
        "AWS_SECRET_ACCESS_KEY": "offline-secret",
    }.items():
        monkeypatch.setenv(name, value)
    schema = tmp_path / "schema.json"
    schema.write_text('{"type": "object", "properties": {"a": {"type": "string"}}}')
    code = main(
        ["run", "--yes", "--provider", "bedrock", "--region", "us-east-1",
         "--model", MODEL, "--structured-output", "json_schema",
         "--schema", str(schema), "go"],
    )  # fmt: skip
    assert code == 2
    assert "does not support structured output" in capsys.readouterr().err
