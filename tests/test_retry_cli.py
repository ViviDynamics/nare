"""Retries and the request timeout across the real CLI boundary (issue 134)."""

from __future__ import annotations

import asyncio
import json
import math
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx2
import pytest

import nare.loop
from nare.cli import build_parser, main, transport_from_args
from nare.contract import describe
from nare.loop import RETRIES_DEFAULT, RETRY_SECONDS_DEFAULT
from nare.session import Message, loads
from nare.transport import make_transport
from nare.transport.anthropic import AnthropicTransport
from nare.transport.openai import DEFAULT_TIMEOUT, OpenAITransport
from test_retry import Flaky, answer, timeout
from test_streaming import anthropic_chunks, openai_chunks

ENV = (
    "NARE_REQUEST_TIMEOUT",
    "NARE_REQUEST_RETRIES",
    "NARE_REQUEST_RETRY_SECONDS",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nare.loop, "RETRY_FIRST_WAIT", 0.0)


def rows(out: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.splitlines()]


def test_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    args = build_parser().parse_args(["run", "go"])
    assert args.request_retries == RETRIES_DEFAULT == 3
    assert args.request_retry_seconds == RETRY_SECONDS_DEFAULT == 60
    assert args.request_timeout is None


def test_the_environment_supplies_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARE_REQUEST_RETRIES", "0")
    monkeypatch.setenv("NARE_REQUEST_RETRY_SECONDS", "5")
    monkeypatch.setenv("NARE_REQUEST_TIMEOUT", "30")
    args = build_parser().parse_args(["run", "go"])
    assert (args.request_retries, args.request_retry_seconds) == (0, 5)
    assert args.request_timeout == 30


@pytest.mark.usefixtures("no_wait")
def test_the_result_and_session_count_retries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    saved = tmp_path / "s.json"
    fake = Flaky([timeout(), answer()])
    argv = ["run", "--yes", "--jsonl", "--session", str(saved), "go"]
    assert main(argv, transport=fake) == 0
    emitted = rows(capsys.readouterr().out)
    assert emitted[-1]["retries"] == 1
    assert emitted[-1]["status"] == "done"
    assert [
        r["detail"]["retry"]["reason"]
        for r in emitted
        if "retry" in r.get("detail", {})
    ]
    assert loads(saved.read_text()).retries == 1


@pytest.mark.usefixtures("no_wait")
def test_zero_retries_from_the_flag(capsys: pytest.CaptureFixture[str]) -> None:
    fake = Flaky([timeout(), answer()])
    argv = ["run", "--yes", "--jsonl", "--request-retries", "0", "go"]
    assert main(argv, transport=fake) == 1
    emitted = rows(capsys.readouterr().out)
    assert emitted[-1]["retries"] == 0
    assert emitted[-1]["stop_reason"] is None
    assert emitted[-1]["error"] == "ReadTimeout: read timed out"


def test_an_older_session_file_loads_with_no_retries() -> None:
    raw = {
        "id": "s",
        "messages": [],
        "usage": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        "version": 1,
    }
    assert loads(json.dumps(raw)).retries == 0


@pytest.mark.parametrize(
    "argv",
    [
        ["--request-retries", "-1"],
        ["--request-retries", "two"],
        ["--request-retry-seconds", "-1"],
        ["--request-retry-seconds", "nan"],
        ["--request-retry-seconds", "inf"],
        ["--request-timeout", "0"],
        ["--request-timeout", "-5"],
        ["--request-timeout", "nan"],
        ["--request-timeout", "inf"],
        ["--request-timeout", "soon"],
    ],
)
def test_an_invalid_value_exits_two(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    try:
        code = main(["run", "--yes", "--jsonl", *argv, "go"], transport=Flaky([]))
    except SystemExit as exc:
        code = int(str(exc.code))
    assert code == 2
    assert capsys.readouterr().out == ""


def test_an_invalid_environment_value_exits_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("NARE_REQUEST_TIMEOUT", "0")
    assert main(["run", "--yes", "--jsonl", "go"], transport=Flaky([])) == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_the_timeout_reaches_both_rails(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-key")
    base = ["run", "go", "--provider", provider, "--model", "m"]
    default = transport_from_args(build_parser().parse_args(base))
    chosen = transport_from_args(
        build_parser().parse_args([*base, "--request-timeout", "12.5"])
    )
    if provider == "openai":
        assert isinstance(default, OpenAITransport)
        assert isinstance(chosen, OpenAITransport)
        assert default.timeout == DEFAULT_TIMEOUT == 600
        assert chosen.timeout == 12.5
    else:
        assert isinstance(default, AnthropicTransport)
        assert isinstance(chosen, AnthropicTransport)
        assert default._client.timeout == httpx2.Timeout(600, connect=5.0)
        assert chosen._client.timeout == 12.5


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("value", [0, -1, math.nan, math.inf, True])
def test_a_transport_refuses_an_invalid_timeout(provider: str, value: Any) -> None:
    with pytest.raises(ValueError, match="request_timeout"):
        make_transport(provider, model="m", api_key="k", request_timeout=value)


def test_the_contract_advertises_retries_and_the_timeout() -> None:
    contract = describe()
    assert contract["request_retries"] == {
        "flags": {
            "retries": "--request-retries",
            "seconds": "--request-retry-seconds",
        },
        "env": {
            "retries": "NARE_REQUEST_RETRIES",
            "seconds": "NARE_REQUEST_RETRY_SECONDS",
        },
        "default": {"retries": 3, "seconds": 60.0},
        "per": "model_request",
        "providers": ["anthropic", "bedrock", "openai"],
        "event": "progress",
        "event_detail": "retry",
        "result_field": "retries",
        "session_field": "retries",
    }
    assert contract["request_timeout"] == {
        "flag": "--request-timeout",
        "env": "NARE_REQUEST_TIMEOUT",
        "default": 600.0,
        "providers": ["anthropic", "bedrock", "openai"],
    }


# A loopback provider whose first answer comes later than the request timeout.

ANSWERS = {
    "openai": {
        "choices": [{"message": {"content": "hello world"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 5},
    },
    "anthropic": {
        "id": "m",
        "type": "message",
        "role": "assistant",
        "model": "m",
        "content": [{"type": "text", "text": "hello world"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 7, "output_tokens": 5},
    },
}


@pytest.fixture
def slow_once() -> Iterator[tuple[str, dict[str, Any]]]:
    state: dict[str, Any] = {"bodies": [], "status": None}

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["content-length"]))
            state["bodies"].append(body)
            anthropic = self.path.endswith("/messages")
            try:
                if state["status"] is not None:
                    self.send_response(state["status"])
                    self.send_header("content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error": {"message": "key not allowed"}}')
                    return
                if len(state["bodies"]) == 1:
                    # Longer than the caller's request timeout.
                    time.sleep(1.0)
                if json.loads(body).get("stream"):
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.end_headers()
                    for chunk in anthropic_chunks() if anthropic else openai_chunks():
                        self.wfile.write(chunk)
                    return
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(ANSWERS["anthropic" if anthropic else "openai"]).encode()
                )
            except OSError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def cli(provider: str, url: str, *extra: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ENV}
    env["OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"] = (
        "offline-fixture-key"
    )
    env["NO_PROXY"] = "127.0.0.1,localhost"
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from nare.cli import main; raise SystemExit(main())",
            "run",
            "hi",
            "--yes",
            "--jsonl",
            "--provider",
            provider,
            "--model",
            "m",
            "--base-url",
            url,
            "--context-window",
            "32000",
            "--tools",
            "none",
            *extra,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_a_slow_request_times_out_and_is_resent(
    slow_once: tuple[str, dict[str, Any]], provider: str, stream: bool
) -> None:
    url, state = slow_once
    extra = ["--request-timeout", "0.3", "--request-retries", "1"]
    proc = cli(provider, url, *extra, *(["--stream"] if stream else []))
    assert proc.returncode == 0, proc.stderr
    emitted = rows(proc.stdout)
    result = emitted[-1]
    assert result["status"] == "done"
    assert result["retries"] == 1
    assert result["usage"]["input"] == 7 and result["usage"]["output"] == 5
    retries = [r["detail"]["retry"] for r in emitted if "retry" in r.get("detail", {})]
    assert [(r["attempt"], r["reason"]) for r in retries] == [(1, "timeout")]
    # The resent request is the same bytes, so a cached prefix still hits.
    assert len(state["bodies"]) == 2
    assert state["bodies"][0] == state["bodies"][1]


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_a_refused_key_is_not_resent(
    slow_once: tuple[str, dict[str, Any]], provider: str
) -> None:
    url, state = slow_once
    state["status"] = 401
    proc = cli(provider, url)
    assert proc.returncode == 1, proc.stderr
    result = rows(proc.stdout)[-1]
    assert result["status"] == "error"
    assert result["stop_reason"] is None
    assert result["retries"] == 0
    assert len(state["bodies"]) == 1


def test_a_library_turn_sends_the_timeout() -> None:
    seen: list[Any] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.extensions["timeout"])
        return httpx2.Response(200, json=ANSWERS["openai"])

    rail = OpenAITransport(
        model="m",
        api_key="k",
        request_timeout=7,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    asyncio.run(rail.turn([Message("user", [{"type": "text", "text": "hi"}])], []))
    assert seen[0]["read"] == 7
