"""Actual CLI and HTTP transports through an idle-enforcing loopback proxy."""

from __future__ import annotations

import json
import os
import select
import socket
import socketserver
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from test_streaming import anthropic_chunks, openai_chunks


@pytest.fixture
def proxy() -> Iterator[tuple[str, threading.Event, dict[str, Any]]]:
    release = threading.Event()
    state: dict[str, Any] = {"requests": [], "idle_timeouts": 0, "finished": False}
    idle = 0.3

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            payload = json.loads(self.rfile.read(int(self.headers["content-length"])))
            state["requests"].append(payload)
            if not payload.get("stream"):
                release.wait(2)
                try:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"{}")
                except OSError:
                    pass
                return
            chunks = (
                anthropic_chunks()
                if self.path.endswith("/messages")
                else openai_chunks()
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            # Anthropic's first text delta follows start + block-start.
            prefix = 3 if self.path.endswith("/messages") else 1
            try:
                for chunk in chunks[:prefix]:
                    self.wfile.write(chunk)
                self.wfile.flush()
                while not release.wait(0.03):
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                for chunk in chunks[prefix:]:
                    self.wfile.write(chunk)
                self.wfile.flush()
                state["finished"] = True
            except OSError:
                pass

    backend = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    backend.daemon_threads = True

    class Relay(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            with socket.create_connection(
                ("127.0.0.1", backend.server_port), timeout=2
            ) as upstream:
                sockets = [self.request, upstream]
                last_upstream = time.monotonic()
                sent_headers = False
                while True:
                    ready, _, _ = select.select(sockets, [], [], 0.03)
                    if time.monotonic() - last_upstream > idle:
                        state["idle_timeouts"] += 1
                        if not sent_headers:
                            self.request.sendall(
                                b"HTTP/1.1 524 Timeout\r\nContent-Length: 0\r\n"
                                b"Connection: close\r\n\r\n"
                            )
                        return
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        destination = (
                            upstream if source is self.request else self.request
                        )
                        if source is upstream:
                            last_upstream = time.monotonic()
                            sent_headers = True
                        try:
                            destination.sendall(data)
                        except OSError:
                            return

    class Proxy(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    edge = Proxy(("127.0.0.1", 0), Relay)
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in (backend, edge)
    ]
    for thread in threads:
        thread.start()
    try:
        yield f"http://127.0.0.1:{edge.server_address[1]}/v1", release, state
    finally:
        release.set()
        edge.shutdown()
        backend.shutdown()
        edge.server_close()
        backend.server_close()
        for thread in threads:
            thread.join(timeout=3)


def cli(provider: str, url: str, path: Path, stream: bool) -> subprocess.Popen[bytes]:
    env = dict(os.environ)
    env["OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"] = (
        "offline-fixture-key"
    )
    env["NO_PROXY"] = "127.0.0.1,localhost"
    args = [
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
        "--session",
        str(path),
    ]
    if stream:
        args.append("--stream")
    return subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, bufsize=0
    )


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_cli_stream_survives_idle_proxy_and_emits_before_done(
    tmp_path: Path, proxy: tuple[str, threading.Event, dict[str, Any]], provider: str
) -> None:
    url, release, state = proxy
    saved = tmp_path / "session.json"
    proc = cli(provider, url, saved, True)
    assert proc.stdout is not None
    prefix: list[bytes] = []
    try:
        deadline = time.monotonic() + 10
        while True:
            ready, _, _ = select.select(
                [proc.stdout], [], [], max(0, deadline - time.monotonic())
            )
            assert ready, "no live progress before timeout"
            line = proc.stdout.readline()
            assert line, "CLI ended before live progress"
            prefix.append(line)
            row = json.loads(line)
            if row.get("type") == "progress" and row.get("text") == "hello ":
                break
        assert not state["finished"]
        assert not release.is_set()
        # More than twice the proxy's idle window, with real wire heartbeats.
        time.sleep(0.75)
        assert proc.poll() is None
        release.set()
        remaining, err = proc.communicate(timeout=10)
        (tmp_path / "stdout.jsonl").write_bytes(b"".join(prefix) + remaining)
        rows = [
            json.loads(line)
            for line in b"".join(prefix).splitlines() + remaining.splitlines()
        ]
        assert proc.returncode == 0, err.decode()
        assert rows[-1]["status"] == "done"
        assert rows[-1]["usage"] == {
            "input": 7,
            "output": 5,
            "cache_read": 3,
            "cache_write": 0,
            "cost": None,
        }
        assert state["idle_timeouts"] == 0
        assert len(state["requests"]) == 1
        session = json.loads(saved.read_text())
        assert session["messages"][-1]["content"][-1]["text"] == "hello world"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)


def test_nonstreaming_call_hits_same_proxy_idle_window(
    tmp_path: Path, proxy: tuple[str, threading.Event, dict[str, Any]]
) -> None:
    url, _, state = proxy
    proc = cli("openai", url, tmp_path / "session.json", False)
    out, err = proc.communicate(timeout=10)
    assert proc.returncode == 1, err.decode()
    rows = [json.loads(line) for line in out.splitlines()]
    assert rows[-1]["status"] == "error"
    assert "524" in rows[-1]["error"]
    assert state["idle_timeouts"] == 1
