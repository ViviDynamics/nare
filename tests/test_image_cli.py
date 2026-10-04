"""Production CLI and native HTTP requests, without real providers."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from test_images import picture


@pytest.fixture
def backend() -> Iterator[tuple[str, list[dict[str, Any]]]]:
    requests: list[dict[str, Any]] = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            payload = json.loads(self.rfile.read(int(self.headers["content-length"])))
            requests.append(payload)
            anthropic = self.path.endswith("/messages")
            first = len(requests) == 1
            content: Any
            body: dict[str, Any]
            if first:
                if anthropic:
                    content = [
                        {
                            "type": "tool_use",
                            "id": "c1",
                            "name": "read",
                            "input": {"path": "chart.png"},
                        }
                    ]
                else:
                    content = {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {
                                    "name": "read",
                                    "arguments": '{"path":"chart.png"}',
                                },
                            }
                        ],
                    }
            else:
                if anthropic:
                    last_block = payload["messages"][-1]["content"][0]
                    result = last_block.get("content", last_block.get("text", ""))
                    data = (
                        result[-1]["source"]["data"]
                        if isinstance(result, list)
                        else None
                    )
                    explanation = str(result)
                else:
                    last = payload["messages"][-1]
                    data = (
                        last["content"][-1]["image_url"]["url"].split(",", 1)[1]
                        if last["role"] == "user" and isinstance(last["content"], list)
                        else None
                    )
                    explanation = str(last)
                if data is not None:
                    with Image.open(
                        BytesIO(base64.b64decode(data, validate=True))
                    ) as image:
                        answer = (
                            "blue square"
                            if image.getpixel((0, 0)) == (0, 0, 255)
                            else "wrong image"
                        )
                else:
                    answer = "image unavailable: " + explanation
                content = (
                    [{"type": "text", "text": answer}]
                    if anthropic
                    else {"content": answer}
                )
            if anthropic:
                body = {
                    "id": "msg1",
                    "type": "message",
                    "role": "assistant",
                    "model": "test-model",
                    "content": content,
                    "stop_reason": "tool_use" if first else "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }
            else:
                body = {
                    "choices": [
                        {
                            "message": content,
                            "finish_reason": "tool_calls" if first else "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def invoke_image(
    provider: str,
    url: str,
    root: Path,
    enabled: bool,
    resume: bool = False,
    budget: str | None = None,
    window: str = "32000",
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"] = (
        "offline-fixture-key"
    )
    env["NO_PROXY"] = "127.0.0.1,localhost"
    for name in list(env):
        if name.startswith(("NARE_BUDGET_", "NARE_PRICE_", "NARE_CONTEXT_")):
            del env[name]
    args = [
        sys.executable,
        "-c",
        "from nare.cli import main; raise SystemExit(main())",
        "run",
        "look again" if resume else "describe chart.png",
        "--yes",
        "--jsonl",
        "--provider",
        provider,
        "--model",
        "test-model",
        "--base-url",
        url,
        "--tools",
        "read",
        "--root",
        str(root),
        "--context-window",
        window,
        "--session",
        str(root / "session.json"),
    ]
    if budget is not None:
        args += ["--budget-tokens", budget]
    if enabled:
        args.append("--image-input")
    if resume:
        args += ["--resume", str(root / "session.json")]
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=10)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("enabled", [False, True])
def test_actual_cli_read_only_image_roundtrip(
    tmp_path: Path,
    backend: tuple[str, list[dict[str, Any]]],
    provider: str,
    enabled: bool,
) -> None:
    url, requests = backend
    original = picture(tmp_path / "chart.png")
    proc = invoke_image(provider, url, tmp_path, enabled)
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(line) for line in proc.stdout.splitlines()]
    assert rows[-1]["status"] == "done" and rows[-1]["turns"] == 2
    assert rows[-1]["usage"]["input"] == 20 and rows[-1]["usage"]["output"] == 10
    saved = json.loads((tmp_path / "session.json").read_text())
    result = saved["messages"][2]["content"][0]
    if enabled:
        assert not result["is_error"]
        assert (
            base64.b64decode(result["image"]["source"]["data"], validate=True)
            == original
        )
        assert saved["messages"][-1]["content"][0]["text"] == "blue square"
        metadata = [r for r in rows if r.get("detail", {}).get("image")]
        assert metadata and "data" not in str(metadata)
    else:
        assert result["is_error"] and "image" not in result
        assert "test-model" in result["content"]
        assert "does not accept image input" in result["content"]
    assert len(requests) == 2
    tools = requests[0]["tools"]
    assert [t.get("name", t.get("function", {}).get("name")) for t in tools] == ["read"]
    (tmp_path / "stdout.jsonl").write_text(proc.stdout)
    (tmp_path / "requests.json").write_text(json.dumps(requests))
    assert (tmp_path / "chart.png").read_bytes() == original
    if enabled:
        resumed = invoke_image(provider, url, tmp_path, False, resume=True)
        assert resumed.returncode == 0, resumed.stderr
        assert len(requests) == 3 and "base64" not in str(requests[-1])
        assert "image withheld" in str(requests[-1]) and "test-model" in str(
            requests[-1]
        )
        resumed_saved = json.loads((tmp_path / "session.json").read_text())
        assert resumed_saved["messages"][2]["content"][0]["image"] == result["image"]
        (tmp_path / "resume-stdout.jsonl").write_text(resumed.stdout)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_cli_budget_resume_ignores_withheld_large_image_context(
    tmp_path: Path, backend: tuple[str, list[dict[str, Any]]], provider: str
) -> None:
    url, requests = backend
    picture(tmp_path / "chart.png", size=(2048, 2048))
    stopped = invoke_image(provider, url, tmp_path, True, budget="15")
    assert stopped.returncode == 1, stopped.stderr
    assert json.loads(stopped.stdout.splitlines()[-1])["stop_reason"] == "budget"
    assert len(requests) == 1
    resumed = invoke_image(
        provider, url, tmp_path, False, resume=True, budget="30", window="8192"
    )
    assert resumed.returncode == 0, resumed.stderr + resumed.stdout
    result = json.loads(resumed.stdout.splitlines()[-1])
    assert result["status"] == "done" and result["budget"]["used_tokens"] == 30
    assert len(requests) == 2 and "base64" not in str(requests[-1])
    saved = json.loads((tmp_path / "session.json").read_text())
    assert "image" in saved["messages"][2]["content"][0]
    (tmp_path / "budget-stop.jsonl").write_text(stopped.stdout)
    (tmp_path / "budget-resume.jsonl").write_text(resumed.stdout)
