import base64
import json
import struct
import sys
import zlib
from pathlib import Path
from typing import Any

import httpx2
import pytest
from anthropic import AsyncAnthropicBedrock

from nare.cli import main
from nare.session import Message
from nare.transport import Delta, Reply, make_transport
from nare.transport.anthropic import NONSTREAMING_MAX_TOKENS
from nare.transport.bedrock import BedrockTransport, bedrock_base_model

MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
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
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BEDROCK_BASE_URL",
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
    """One AWS event-stream message carrying an Anthropic stream event, the
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


def recording(
    captured: list[httpx2.Request], *, stream: bool = False
) -> httpx2.AsyncClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        if stream:
            return httpx2.Response(
                200,
                headers={"content-type": "application/vnd.amazon.eventstream"},
                content=b"".join(event_frame(e) for e in STREAM_EVENTS),
            )
        return httpx2.Response(200, json=CANNED_BODY)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


TOOLS = [{"name": "read", "description": "read a file", "input_schema": {}}]
PROMPT = [Message("user", [{"type": "text", "text": "read a"}])]


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


def test_a_bedrock_bearer_token_needs_no_aws_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "offline-bearer")
    t = BedrockTransport(model=MODEL, region="us-east-1")
    assert t._client.api_key == "offline-bearer"


@pytest.mark.usefixtures("aws_keys")
def test_without_the_extra_bedrock_is_refused_naming_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("boto3", "botocore", "botocore.session"):
        monkeypatch.setitem(sys.modules, name, None)
    with pytest.raises(ValueError, match=r"nare\[bedrock\]"):
        BedrockTransport(model=MODEL, region="us-east-1")


@pytest.mark.usefixtures("aws_keys")
def test_base_url_overrides_the_runtime_endpoint() -> None:
    t = BedrockTransport(
        model=MODEL, region="us-east-1", base_url="https://vpce.example.test"
    )
    assert str(t._client.base_url).startswith("https://vpce.example.test")


@pytest.mark.usefixtures("aws_keys")
def test_temperature_is_refused_as_on_anthropic() -> None:
    with pytest.raises(ValueError, match="temperature"):
        BedrockTransport(model=MODEL, region="us-east-1", temperature=0.2)


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
def test_a_profile_id_gets_its_base_models_nonstreaming_cap() -> None:
    opus = "us.anthropic.claude-opus-4-20250514-v1:0"
    with pytest.raises(ValueError, match="non-streaming cap"):
        BedrockTransport(model=opus, region="us-east-1", max_tokens=12288)
    t = BedrockTransport(model=MODEL, region="us-east-1", max_tokens=12288)
    assert t.max_tokens == 12288
    with pytest.raises(ValueError, match=str(NONSTREAMING_MAX_TOKENS)):
        BedrockTransport(
            model=MODEL, region="us-east-1", max_tokens=NONSTREAMING_MAX_TOKENS + 1
        )


@pytest.mark.usefixtures("aws_keys")
async def test_no_context_window_discovery_request() -> None:
    captured: list[httpx2.Request] = []
    t = BedrockTransport(
        model=MODEL,
        region="us-east-1",
        base_url="https://vpce.example.test",
        http_client=recording(captured),
    )
    assert await t.context_window() is None
    assert captured == []


@pytest.mark.usefixtures("aws_keys")
async def test_a_turn_is_sigv4_signed_and_normalized_like_anthropic() -> None:
    captured: list[httpx2.Request] = []
    t = BedrockTransport(
        model=MODEL,
        region="us-east-1",
        effort="low",
        http_client=recording(captured),
    )
    reply = await t.turn(PROMPT, TOOLS)
    request = captured[0]
    assert request.url.host == "bedrock-runtime.us-east-1.amazonaws.com"
    assert request.url.path.endswith("/invoke")
    assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256")
    assert "x-api-key" not in request.headers
    body = json.loads(request.content)
    assert body["anthropic_version"].startswith("bedrock-")
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert body["tools"] == TOOLS
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
async def test_streaming_decodes_the_aws_event_stream() -> None:
    captured: list[httpx2.Request] = []
    t = BedrockTransport(
        model=MODEL,
        region="us-east-1",
        streaming=True,
        effort="low",
        http_client=recording(captured, stream=True),
    )
    items = [item async for item in t.stream_turn(PROMPT, TOOLS)]
    assert captured[0].url.path.endswith("/invoke-with-response-stream")
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
    for name in ("boto3", "botocore", "botocore.session"):
        monkeypatch.setitem(sys.modules, name, None)
    argv = ["run", "--yes", "--provider", "bedrock", "--region", "us-east-1", "go"]
    assert main([*argv[:-1], "--model", MODEL, "go"]) == 2
    assert "nare[bedrock]" in capsys.readouterr().err


@pytest.mark.usefixtures("aws_keys")
def test_cli_region_flag_is_refused_for_other_providers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert main(["run", "--yes", "--region", "us-east-1", "go"]) == 2
    assert "--region" in capsys.readouterr().err


def test_a_bearer_token_still_needs_the_extra_for_stream_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "offline-bearer")
    for name in ("boto3", "botocore", "botocore.session"):
        monkeypatch.setitem(sys.modules, name, None)
    with pytest.raises(ValueError, match=r"nare\[bedrock\]"):
        BedrockTransport(model=MODEL, region="us-east-1")
