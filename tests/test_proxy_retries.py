"""Proxy retry control crosses the real CLI boundary, without live inference."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from nare.cli import build_parser, transport_from_args
from nare.contract import describe
from nare.session import Message
from nare.transport import make_transport
from nare.transport.openai import OpenAITransport
from test_streaming import openai_chunks


@pytest.mark.parametrize("setting", [None, 0, 2])
def test_cli_binds_proxy_retries(
    monkeypatch: pytest.MonkeyPatch, setting: int | None
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")
    argv = ["run", "hello", "--provider", "openai", "--model", "m", "--tools", "none"]
    if setting is not None:
        argv += ["--proxy-num-retries", str(setting)]
    transport = transport_from_args(build_parser().parse_args(argv))
    assert isinstance(transport, OpenAITransport)
    payload = transport._payload(
        [Message("user", [{"type": "text", "text": "hi"}])], []
    )
    if setting is None:
        assert json.dumps(payload) == json.dumps(
            {
                "model": "m",
                "messages": [{"role": "user", "content": "hi"}],
            }
        )
    else:
        assert payload["num_retries"] == setting


@pytest.mark.parametrize("value", [-1, True, 0.5, "0"])
def test_invalid_proxy_retries_refused(value: Any) -> None:
    with pytest.raises(ValueError, match="proxy_num_retries"):
        OpenAITransport(model="m", api_key="k", proxy_num_retries=value)


def test_other_provider_refuses_proxy_retries() -> None:
    with pytest.raises(ValueError, match="openai"):
        make_transport("anthropic", model="m", api_key="k", proxy_num_retries=0)


def test_contract_advertises_proxy_retries() -> None:
    assert describe()["proxy_num_retries"] == {
        "flag": "--proxy-num-retries",
        "providers": ["openai"],
        "request_field": "num_retries",
        "default": None,
    }


@pytest.mark.parametrize("streaming", [False, True])
def test_process_sends_zero_on_every_schema_request(
    tmp_path: Path, streaming: bool
) -> None:
    seen: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            seen.append(
                json.loads(self.rfile.read(int(self.headers["content-length"])))
            )
            self.send_response(200)
            self.end_headers()
            if streaming:
                for chunk in openai_chunks():
                    self.wfile.write(
                        chunk.replace(b"hello ", b'\\"hello ').replace(
                            b'world"', b'world\\""'
                        )
                    )
            else:
                self.wfile.write(
                    json.dumps(
                        {
                            "choices": [
                                {
                                    "message": {"content": '"hello"'},
                                    "finish_reason": "stop",
                                }
                            ],
                            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                        }
                    ).encode()
                )

    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"string"}')
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    argv = [
        sys.executable,
        "-c",
        "from nare.cli import main; raise SystemExit(main())",
        "run",
        "hello",
        "--yes",
        "--jsonl",
        "--provider",
        "openai",
        "--model",
        "m",
        "--base-url",
        f"http://127.0.0.1:{server.server_port}/v1",
        "--context-window",
        "32000",
        "--tools",
        "none",
        "--max-turns",
        "1",
        "--proxy-num-retries",
        "0",
        "--schema",
        str(schema),
        "--structured-output",
        "json_schema",
    ]
    if streaming:
        argv.append("--stream")
    try:
        result = subprocess.run(
            argv,
            env={
                **os.environ,
                "OPENAI_API_KEY": "offline-key",
                "NO_PROXY": "127.0.0.1,localhost",
            },
            capture_output=True,
            timeout=20,
        )
        assert seen, result.stderr.decode()
        assert result.returncode == 0, result.stderr.decode() + result.stdout.decode()
        assert all(r["num_retries"] == 0 for r in seen)
        assert all(r["response_format"]["type"] == "json_schema" for r in seen)
        assert len(seen) <= 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
