"""A provider failure's kind and HTTP status on the terminal result (issue 314).

A caller branches on contract fields, never on `error` text, so a run that ended
because the provider failed says how: `failure` on the terminal `error` event's
detail, on the `result` line and in the session file.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx2
import pytest

import nare.loop
from fake_provider import tool_reply
from nare.cli import main
from nare.contract import describe
from nare.events import REDACTED
from nare.loop import RetryPolicy, run
from nare.session import dumps, loads, new_session, reopen
from nare.tools import Policy
from nare.transport import ProviderStatusError, StreamDropped, provider_failure
from nare.transport.anthropic import AnthropicTransport
from nare.transport.bedrock import BedrockTransport
from test_retry import Flaky, answer, timeout
from test_streaming import Bytes, anthropic_chunks, openai_chunks, sse

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}
REFUSAL = {
    "error": {
        "message": "response_format json_schema is not supported",
        "type": "invalid_request_error",
    }
}


SECRET = "Authorization: Bearer offline-fixture-credential-0123456789"


def rows(out: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.splitlines()]


def failure(
    reason: str | None,
    status: int | None,
    *,
    transient: bool,
    constrained: bool = False,
    attempts: int = 1,
) -> dict[str, Any]:
    return {
        "reason": reason,
        "status": status,
        "transient": transient,
        "constrained": constrained,
        "attempts": attempts,
    }


@pytest.fixture
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nare.loop, "RETRY_FIRST_WAIT", 0.0)


@pytest.fixture
def route() -> Iterator[tuple[str, dict[str, Any]]]:
    """A loopback provider. `mode` is an HTTP status to answer every model
    request with, or "drop" for a stream that ends before its final marker."""
    state: dict[str, Any] = {"mode": 400, "posts": 0}

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            self._send(200, {"data": [{"id": "m"}]})

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            state["posts"] += 1
            anthropic = self.path.endswith("/messages")
            try:
                if state["mode"] == "drop":
                    chunks = anthropic_chunks()[:-1] if anthropic else openai_chunks()
                    if not anthropic:
                        chunks = chunks[:1]
                    assert body.get("stream")
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.end_headers()
                    for chunk in chunks:
                        self.wfile.write(chunk)
                    return
                error: dict[str, Any] = (
                    {"type": "error", "error": {"type": "x_error", "message": "no"}}
                    if anthropic
                    else dict(REFUSAL)
                )
                # A credential in the body must never reach the record.
                error["echo"] = SECRET
                self._send(state["mode"], error)
            except OSError:
                pass

        def _send(self, code: int, body: Any) -> None:
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

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


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


def run_main(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    provider: str,
    url: str,
    *extra: str,
) -> list[dict[str, Any]]:
    monkeypatch.setenv(
        "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY",
        "offline-fixture-key",
    )
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    argv = [
        "run",
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
        "go",
    ]
    assert main(argv) == 1
    return rows(capsys.readouterr().out)


def terminal(emitted: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    errors = [r for r in emitted if r.get("type") == "error"]
    assert len(errors) == 1
    result = emitted[-1]
    assert result["type"] == "result"
    assert result["status"] == "error"
    assert result["stop_reason"] is None
    return errors[0]["detail"], result


# The issue's reproduction: a constrained turn the backend refuses with a 400.


def test_the_reproduction_reports_a_refused_constrained_request(
    route: tuple[str, dict[str, Any]], tmp_path: Path
) -> None:
    url, state = route
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(SCHEMA))
    saved = tmp_path / "s.json"
    env = dict(os.environ)
    env["OPENAI_API_KEY"] = "placeholder"
    env["NO_PROXY"] = "127.0.0.1,localhost"
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from nare.cli import main; raise SystemExit(main())",
            "run",
            "--jsonl",
            "--yes",
            "--contract",
            "3",
            "--provider",
            "openai",
            "--model",
            "m",
            "--base-url",
            url,
            "--tools",
            "none",
            "--schema",
            str(schema),
            "--structured-output",
            "json_schema",
            "--request-retries",
            "0",
            "--max-turns",
            "2",
            "--session",
            str(saved),
            'Answer {"ok": true}.',
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 1, proc.stderr
    detail, result = terminal(rows(proc.stdout))
    expected = failure("status", 400, transient=False, constrained=True)
    assert detail["failure"] == expected
    assert result["failure"] == expected
    # The error text is unchanged: still the reason a person reads.
    assert result["error"].startswith(
        "ProviderStatusError: chat completion failed with HTTP 400"
    )
    assert loads(saved.read_text()).failure == expected
    assert state["posts"] == 1


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_a_refusal_reports_its_status(
    route: tuple[str, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    provider: str,
    stream: bool,
    status: int,
) -> None:
    url, state = route
    state["mode"] = status
    extra = ["--stream"] if stream else []
    detail, result = terminal(run_main(monkeypatch, capsys, provider, url, *extra))
    expected = failure("status", status, transient=False)
    assert detail["failure"] == expected
    assert result["failure"] == expected
    assert state["posts"] == 1
    assert "offline-fixture-credential" not in json.dumps(
        [detail["failure"], result["failure"]]
    )


@pytest.mark.usefixtures("no_wait")
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_gateway_failure_after_retries(
    route: tuple[str, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    provider: str,
    stream: bool,
    status: int,
) -> None:
    url, state = route
    state["mode"] = status
    extra = ["--request-retries", "2", *(["--stream"] if stream else [])]
    emitted = run_main(monkeypatch, capsys, provider, url, *extra)
    detail, result = terminal(emitted)
    expected = failure("status", status, transient=True, attempts=3)
    assert detail["failure"] == expected
    assert result["failure"] == expected
    assert result["retries"] == 2
    assert state["posts"] == 3


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_a_connection_that_never_opened(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    provider: str,
    stream: bool,
) -> None:
    url = f"http://127.0.0.1:{closed_port()}/v1"
    extra = ["--request-retries", "0", *(["--stream"] if stream else [])]
    detail, result = terminal(run_main(monkeypatch, capsys, provider, url, *extra))
    expected = failure("connection", None, transient=True)
    assert detail["failure"] == expected
    assert result["failure"] == expected


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_a_dropped_stream(
    route: tuple[str, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    provider: str,
) -> None:
    url, state = route
    state["mode"] = "drop"
    extra = ["--stream", "--request-retries", "0"]
    detail, result = terminal(run_main(monkeypatch, capsys, provider, url, *extra))
    expected = failure("stream", None, transient=True)
    assert detail["failure"] == expected
    assert result["failure"] == expected


@pytest.mark.parametrize("stream", [False, True])
async def test_bedrock_reports_through_the_anthropic_rail(
    monkeypatch: pytest.MonkeyPatch, stream: bool
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDOFFLINE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "offline-secret")

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            403,
            json={"message": "not authorized to invoke this model"},
        )

    rail = BedrockTransport(
        model="anthropic.claude-sonnet-4-20250514-v1:0",
        region="us-east-1",
        streaming=stream,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    session = new_session("go")
    events = [
        e
        async for e in run(
            session,
            transport=rail,
            policy=Policy(),
            retry=RetryPolicy(retries=0),
            context_window=100000,
        )
    ]
    expected = failure("status", 403, transient=False)
    assert session.failure == expected
    assert [e.detail.get("failure") for e in events if e.type == "error"] == [expected]


# The record in the library, and what it must never hold.


async def test_a_successful_run_and_a_nare_failure_have_none(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    session = new_session("go")
    [_ async for _ in run(session, transport=Flaky([answer()]), policy=Policy())]
    assert session.status == "done"
    assert session.failure is None

    # A failure that is nare's own, not a provider request's, has no record.
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise KeyError("nare's own bug")

    monkeypatch.setattr(nare.loop, "turn_usage", broken)
    session = new_session("go")
    events = [
        e async for e in run(session, transport=Flaky([answer()]), policy=Policy())
    ]
    assert session.status == "error"
    assert session.failure is None
    assert [e.detail for e in events if e.type == "error"] == [{}]


async def test_an_unclassified_provider_failure_has_a_null_reason() -> None:
    session = new_session("go")
    exc = RuntimeError("incomplete model stream: final usage missing")
    events = [
        e
        async for e in run(
            session,
            transport=Flaky([exc]),
            policy=Policy(),
            retry=RetryPolicy(retries=0),
        )
    ]
    expected = failure(None, None, transient=False)
    assert session.failure == expected
    assert [e.detail for e in events if e.type == "error"] == [{"failure": expected}]


async def test_a_failure_after_a_completed_turn_is_this_requests() -> None:
    refused = ProviderStatusError(
        "chat completion failed with HTTP 401: no", status_code=401
    )
    session = new_session("go")
    [
        _
        async for _ in run(
            session,
            transport=Flaky([tool_reply("ls", {}), timeout(), refused]),
            policy=Policy(),
            retry=RetryPolicy(retries=1, sleep=_no_sleep),
        )
    ]
    assert session.failure == failure("status", 401, transient=False, attempts=2)


async def _no_sleep(seconds: float) -> None:
    return None


def test_a_resume_clears_the_record_and_an_older_file_loads_without_it() -> None:
    session = new_session("go")
    session.failure = failure("status", 400, transient=False)
    reloaded = loads(dumps(session))
    assert reloaded.failure == session.failure
    reopen(reloaded, "again")
    assert reloaded.failure is None

    raw = json.loads(dumps(new_session("go")))
    del raw["failure"]
    assert loads(json.dumps(raw)).failure is None
    raw["failure"] = "status 400"
    with pytest.raises(ValueError, match="failure"):
        loads(json.dumps(raw))


def test_the_record_holds_no_body_header_or_credential() -> None:
    exc = ProviderStatusError(
        f"chat completion failed with HTTP 401: {SECRET}", status_code=401
    )
    record = provider_failure(exc, None, constrained=False, attempts=1)
    assert record == failure("status", 401, transient=False)
    assert "offline-fixture-credential" not in json.dumps(record)
    assert REDACTED not in json.dumps(record)


@pytest.mark.parametrize(
    ("exc", "reason", "status"),
    [
        (StreamDropped("cut"), "stream", None),
        (timeout(), "timeout", None),
        (ProviderStatusError("x", status_code=599), "status", 599),
        # Not an HTTP error status, so not reported as one.
        (ProviderStatusError("x", status_code=200), None, None),
        (ValueError("bad frame"), None, None),
    ],
)
def test_the_classification(
    exc: BaseException, reason: str | None, status: int | None
) -> None:
    record = provider_failure(exc, None, constrained=True, attempts=1)
    assert (record["reason"], record["status"]) == (reason, status)


def test_the_contract_advertises_the_failure_record() -> None:
    assert describe()["provider_failure"] == {
        "reasons": ["timeout", "connection", "stream", "status", "overloaded", None],
        "fields": ["reason", "status", "transient", "constrained", "attempts"],
        "providers": ["anthropic", "bedrock", "openai"],
        "event": "error",
        "event_detail": "failure",
        "result_field": "failure",
        "session_field": "failure",
    }
    assert describe()["contract"] == 3


# Review follow-ups: discovery, the last-turn summary, a failed session write
# and an overloaded stream that outlasts its retries.


class Discovery(Flaky):
    def __init__(self, exc: BaseException) -> None:
        super().__init__([answer()])
        self.exc = exc

    async def context_window(self) -> int | None:
        raise self.exc


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (
            ProviderStatusError("model info failed with HTTP 401: no", status_code=401),
            failure("status", 401, transient=False),
        ),
        (timeout(), failure("timeout", None, transient=True)),
        # Nothing names it, and discovery parses too: possibly nare's own.
        (KeyError("max_input_tokens"), None),
    ],
)
async def test_a_discovery_failure(
    exc: BaseException, expected: dict[str, Any] | None
) -> None:
    session = new_session("go")
    events = [e async for e in run(session, transport=Discovery(exc), policy=Policy())]
    assert session.status == "error"
    assert session.failure == expected
    assert [e.detail for e in events if e.type == "error"] == [
        {} if expected is None else {"failure": expected}
    ]


async def test_a_failed_last_turn_summary_has_no_record() -> None:
    refused = ProviderStatusError(
        "chat completion failed with HTTP 400: no", status_code=400
    )
    session = new_session("go")
    events = [
        e
        async for e in run(
            session,
            transport=Flaky([tool_reply("ls", {}), refused]),
            policy=Policy(),
            max_turns=1,
            retry=RetryPolicy(retries=0),
            context_window=100000,
        )
    ]
    assert session.stop_reason == "max_turns"
    assert session.failure is None
    assert all("failure" not in e.detail for e in events)


def test_a_failed_session_write_has_no_record(
    route: tuple[str, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    url, state = route
    state["mode"] = 401
    unwritable = tmp_path / "missing" / "dir" / "s.json"
    (tmp_path / "missing").write_text("a file, not a directory")
    emitted = run_main(monkeypatch, capsys, "openai", url, "--session", str(unwritable))
    result = emitted[-1]
    assert result["stop_reason"] == "session_write"
    assert result["failure"] is None


@pytest.mark.usefixtures("no_wait")
async def test_an_overloaded_stream_after_retries() -> None:
    chunks = [
        *anthropic_chunks()[:3],
        sse(
            {"type": "error", "error": {"type": "overloaded_error", "message": "x"}},
            "error",
        ),
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Bytes(list(chunks)),
        )

    rail = AnthropicTransport(
        model="m",
        api_key="test",
        streaming=True,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    session = new_session("go")
    events = [
        e
        async for e in run(
            session,
            transport=rail,
            policy=Policy(),
            retry=RetryPolicy(retries=1, sleep=_no_sleep),
            context_window=100000,
        )
    ]
    assert session.failure == failure("overloaded", None, transient=True, attempts=2)
    assert [e.detail.get("failure") for e in events if e.type == "error"] == [
        session.failure
    ]
