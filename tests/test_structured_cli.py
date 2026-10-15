"""`nare run --structured-output`: startup refusals and the real CLI over HTTP."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from nare.cli import main

PERSON: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
    "additionalProperties": False,
}


@pytest.fixture
def schema_file(tmp_path: Path) -> Path:
    path = tmp_path / "person.schema.json"
    path.write_text(json.dumps(PERSON), encoding="utf-8")
    return path


def test_structured_output_without_a_schema_never_starts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    code = main(
        ["run", "--yes", "--jsonl", "--provider", "openai"]
        + ["--structured-output", "json_schema", "go"]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "--structured-output requires --schema" in captured.err


def test_the_anthropic_provider_cannot_be_constrained(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    schema_file: Path,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    code = main(
        ["run", "--yes", "--jsonl", "--provider", "anthropic", "--schema"]
        + [str(schema_file), "--structured-output", "json_schema", "go"]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "does not support structured output" in captured.err


def test_only_known_modes_parse(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["run", "--yes", "--structured-output", "grammar", "go"])
    assert exit_info.value.code == 2


def frames(message: dict[str, Any], finish: str) -> bytes:
    delta: dict[str, Any] = {}
    if message.get("content"):
        delta["content"] = message["content"]
    if message.get("tool_calls"):
        delta["tool_calls"] = [
            {"index": i, **call} for i, call in enumerate(message["tool_calls"])
        ]
    chunks = [
        {"choices": [{"index": 0, "delta": delta}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
    ]
    return b"".join(("data: " + json.dumps(c) + "\n\n").encode() for c in chunks) + (
        b"data: [DONE]\n\n"
    )


@pytest.fixture
def backend() -> Iterator[tuple[str, list[dict[str, Any]]]]:
    """Read, then a malformed answer, then (only if constrained) a valid one."""
    seen: list[dict[str, Any]] = []
    scripted: list[tuple[dict[str, Any], str]] = [
        (
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read",
                            "arguments": json.dumps({"path": "NOTES.txt"}),
                        },
                    }
                ],
            },
            "tool_calls",
        ),
        ({"content": '{"name": "ada"} and some stray tokens'}, "stop"),
        ({"content": '{"name": "ada"}'}, "stop"),
    ]

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            payload = json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen.append(payload)
            message, finish = scripted[min(len(seen), len(scripted)) - 1]
            if len(seen) == 3 and "response_format" not in payload:
                message = {"content": "unconstrained again"}
            self.send_response(200)
            if payload.get("stream"):
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(frames(message, finish))
            else:
                body = {
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", **message},
                            "finish_reason": finish,
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", seen
    finally:
        server.shutdown()
        server.server_close()


def cli(url: str, root: Path, schema: Path, extra: list[str]) -> Any:
    env = {k: v for k, v in os.environ.items() if not k.startswith("NARE_")} | {
        "OPENAI_API_KEY": "offline-fixture-key",
        "NO_PROXY": "127.0.0.1,localhost",
    }
    args = [
        sys.executable,
        "-c",
        "from nare.cli import main; raise SystemExit(main())",
        "run",
        "who is in NOTES.txt?",
        "--yes",
        "--jsonl",
        "--provider",
        "openai",
        "--model",
        "test-model",
        "--base-url",
        url,
        "--tools",
        "read",
        "--root",
        str(root),
        "--context-window",
        "32000",
        "--schema",
        str(schema),
        "--session",
        str(root / "session.json"),
        *extra,
    ]
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=20)


@pytest.mark.parametrize("stream", [False, True])
def test_the_cli_constrains_only_the_correction_over_http(
    backend: tuple[str, list[dict[str, Any]]],
    tmp_path: Path,
    schema_file: Path,
    stream: bool,
) -> None:
    url, seen = backend
    root = tmp_path / "root"
    root.mkdir()
    (root / "NOTES.txt").write_text("ada\n", encoding="utf-8")

    done = cli(
        url,
        root,
        schema_file,
        ["--structured-output", "json_schema", *(["--stream"] if stream else [])],
    )

    assert done.returncode == 0, done.stderr
    lines = [json.loads(line) for line in done.stdout.splitlines()]
    result = lines[-1]
    assert result["status"] == "done"
    assert result["output"] == {"name": "ada"}
    assert len(seen) == 3
    assert ["response_format" in body for body in seen] == [False, False, True]
    assert ["tools" in body for body in seen] == [True, True, False]
    assert seen[2]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "answer", "schema": PERSON, "strict": True},
    }
    markers = [e for e in lines if "structured_output" in (e.get("detail") or {})]
    assert [m["detail"]["structured_output"] for m in markers] == [
        {"mode": "json_schema", "tools_withheld": True}
    ]


def test_without_the_flag_the_cli_sends_no_constraint(
    backend: tuple[str, list[dict[str, Any]]], tmp_path: Path, schema_file: Path
) -> None:
    url, seen = backend
    root = tmp_path / "root"
    root.mkdir()
    (root / "NOTES.txt").write_text("ada\n", encoding="utf-8")

    failed = cli(url, root, schema_file, [])

    assert failed.returncode == 1
    result = json.loads(failed.stdout.splitlines()[-1])
    assert result["stop_reason"] == "schema_violation"
    assert all("response_format" not in body for body in seen)
    assert all("tools" in body for body in seen)
