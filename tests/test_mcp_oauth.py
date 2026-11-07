"""Remote MCP servers with OAuth, offline.

The fake authorization server and MCP server run as a subprocess
(tests/mcp_oauth_server.py), and the browser is a function that performs the
redirects over real HTTP. No third-party service is contacted.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import secrets
import socket
import stat
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

import httpx2
import pytest

from nare import mcp_auth
from nare.cli import main
from nare.mcp import Server, parse_servers
from test_mcp import MCPModel

SERVER = Path(__file__).with_name("mcp_oauth_server.py")
REPLY_SENTINEL = "SENTINEL-REPLY-7731"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


class Fake:
    def __init__(self, url: str, log: Path, proc: subprocess.Popen[bytes]) -> None:
        self.url = url
        self.log = log
        self.proc = proc

    def events(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]


@pytest.fixture
def start(tmp_path: Path) -> Iterator[Callable[..., Fake]]:
    procs: list[subprocess.Popen[bytes]] = []

    def launch(
        *,
        access_ttl: int = 3600,
        refresh_mode: str = "ok",
        code_mode: str = "ok",
        preregistered: str | None = None,
        redirect: str | None = None,
        issuer_base: str | None = None,
        token_endpoint: str | None = None,
        echo_bearer: bool = False,
        prm_url: str | None = None,
    ) -> Fake:
        port = free_port()

        def fill(text: str | None) -> str | None:
            return None if text is None else text.replace("{port}", str(port))

        log = tmp_path / f"fake-{port}.jsonl"
        argv = [
            sys.executable,
            str(SERVER),
            str(port),
            "--log",
            str(log),
            "--access-ttl",
            str(access_ttl),
            "--refresh-mode",
            refresh_mode,
            "--code-mode",
            code_mode,
        ]
        if preregistered is not None:
            argv += [
                "--preregistered",
                preregistered,
                "--redirect",
                fill(redirect) or "",
            ]
        if issuer_base is not None:
            argv += ["--issuer-base", fill(issuer_base) or ""]
        if token_endpoint is not None:
            argv += ["--token-endpoint", fill(token_endpoint) or ""]
        if echo_bearer:
            argv += ["--echo-bearer"]
        if prm_url is not None:
            argv += ["--prm-url", fill(prm_url) or ""]
        proc = subprocess.Popen(
            argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        procs.append(proc)
        deadline = time.monotonic() + 15
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    raise AssertionError("fake OAuth server did not start") from None
                time.sleep(0.05)
        return Fake(f"http://127.0.0.1:{port}", log, proc)

    yield launch
    for proc in procs:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "nare-home"
    monkeypatch.setenv("NARE_HOME", str(path))
    return path


def write_config(tmp_path: Path, servers: dict[str, Any]) -> Path:
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps(servers))
    return path


def browser(tamper_state: bool = False) -> Callable[[str], Any]:
    """Stands in for a person at a browser: follows the authorization redirect
    and then the loopback callback, over real HTTP."""

    async def present(url: str) -> None:
        if tamper_state:
            parts = urlsplit(url)
            query = [
                (k, "forged" if k == "state" else v) for k, v in parse_qsl(parts.query)
            ]
            url = urlunsplit(parts._replace(query=urlencode(query)))
        async with httpx2.AsyncClient(
            follow_redirects=False, trust_env=False
        ) as client:
            first = await client.get(url)
            assert first.status_code in (302, 303, 307), first.status_code
            callback = await client.get(first.headers["location"])
            # A redirect with a state this login did not start is refused, and
            # the listener keeps waiting for the real one.
            expected = 400 if tamper_state else 200
            assert callback.status_code == expected, callback.status_code

    return present


def login(alias: str, server: Server, **kwargs: Any) -> list[str]:
    return asyncio.run(
        mcp_auth.login(
            alias, server, timeout=20, present=kwargs.pop("present", browser())
        )
    )


def token_file(home: Path, alias: str = "remote") -> Path:
    return home / "mcp-auth" / f"{alias}.json"


def stored(home: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(token_file(home).read_text())
    return data


def run_with(
    config: Path, model: MCPModel, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str, str]:
    code = main(
        ["run", "echo", "--yes", "--jsonl", "--mcp-config", str(config)],
        transport=model,
    )
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_login_stores_tokens_and_run_refreshes_them(
    tmp_path: Path,
    home: Path,
    start: Callable[..., Fake],
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = start(access_ttl=2)
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]

    assert login("remote", server) == ["remote__echo"]

    path = token_file(home)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    grants = [e for e in fake.events() if e["event"] == "grant"]
    assert [g["grant"] for g in grants] == ["authorization_code"]
    assert any(e["event"] == "register" for e in fake.events())

    record = stored(home)
    access = record["tokens"]["access_token"]
    refresh = record["tokens"]["refresh_token"]

    time.sleep(2.2)  # the access token is now past its lifetime
    model = MCPModel("remote__echo")
    code, out, err = run_with(config, model, capsys)
    assert code == 0, err
    assert "hello" in model.messages[-1].content[0]["content"]
    assert [e["grant"] for e in fake.events() if e["event"] == "grant"] == [
        "authorization_code",
        "refresh_token",
    ]

    # No token in the event stream, stderr, logs, or any file outside the token file.
    for secret in (access, refresh):
        assert secret not in out and secret not in err
        assert secret not in caplog.text
        for other in home.rglob("*"):
            if other.is_file() and other != path:
                assert secret not in other.read_text(errors="ignore")


def test_login_process_command_line_carries_no_secret(
    tmp_path: Path,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    start: Callable[..., Fake],
) -> None:
    """The client secret reaches the process through its environment only.
    The check reads the live process's command line while it waits for the
    browser, which is when a secret passed on argv would be visible."""
    secret = "argv-secret-" + secrets.token_hex(12)
    port = free_port()
    monkeypatch.setenv("FAKE_SERVER_CLIENT_SECRET", secret)
    monkeypatch.setenv("NARE_TEST_CLIENT_SECRET", secret)
    fake = start(preregistered="cli-2", redirect=f"http://127.0.0.1:{port}/callback")
    config = write_config(
        tmp_path,
        {
            "remote": {
                "url": f"{fake.url}/mcp",
                "timeout": 5,
                "auth": {
                    "client_id": "cli-2",
                    "client_secret_env": "NARE_TEST_CLIENT_SECRET",
                    "callback_port": port,
                },
            }
        },
    )
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; from nare.cli import main; sys.exit(main(sys.argv[1:]))",
            "mcp",
            "login",
            "remote",
            "--mcp-config",
            str(config),
            "--timeout",
            "20",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stderr is not None
        url = ""
        for line in child.stderr:
            if line.startswith("http://"):
                url = line.strip()
                break
        assert url, "login did not present an authorization URL"
        listing = subprocess.run(
            ["ps", "-o", "command=", "-p", str(child.pid)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert "remote" in listing  # the process is the one we are inspecting
        assert secret not in listing
        assert secret not in str(child.args)
        asyncio.run(browser()(url))
        assert child.wait(timeout=30) == 0
    finally:
        if child.poll() is None:
            child.kill()
    assert "authorized remote" in (child.stderr.read() if child.stderr else "")
    assert secret not in token_file(home).read_text()


def test_run_without_login_stops_before_any_model_call(
    tmp_path: Path,
    home: Path,
    start: Callable[..., Fake],
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = start()
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    model = MCPModel("remote__echo")
    code, out, _ = run_with(config, model, capsys)
    assert code == 1
    assert model.calls == 0
    result = json.loads(out.splitlines()[-1])
    assert result["stop_reason"] == "mcp"
    assert "nare mcp login remote" in result["error"]
    assert fake.events() == []


def test_preregistered_client_secret_comes_from_the_environment(
    tmp_path: Path,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    start: Callable[..., Fake],
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "client-secret-" + secrets.token_hex(12)
    port = free_port()
    monkeypatch.setenv("FAKE_SERVER_CLIENT_SECRET", secret)
    monkeypatch.setenv("NARE_TEST_CLIENT_SECRET", secret)
    fake = start(
        preregistered="cli-1",
        redirect=f"http://127.0.0.1:{port}/callback",
    )
    config = write_config(
        tmp_path,
        {
            "remote": {
                "url": f"{fake.url}/mcp",
                "timeout": 5,
                "auth": {
                    "client_id": "cli-1",
                    "client_secret_env": "NARE_TEST_CLIENT_SECRET",
                    "callback_port": port,
                },
            }
        },
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]

    assert login("remote", server) == ["remote__echo"]
    assert not any(e["event"] == "register" for e in fake.events())
    assert secret not in token_file(home).read_text()
    assert not (stored(home).get("client") or {}).get("client_secret")

    code, out, err = run_with(config, MCPModel("remote__echo"), capsys)
    assert code == 0, err
    assert secret not in out + err


def test_refresh_failure_names_the_login_and_hides_the_reply(
    tmp_path: Path,
    home: Path,
    start: Callable[..., Fake],
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = start(access_ttl=2, refresh_mode="echo")
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    login("remote", server)
    time.sleep(2.2)

    model = MCPModel("remote__echo")
    code, out, err = run_with(config, model, capsys)
    assert code == 1
    assert model.calls == 0
    assert "nare mcp login remote" in out + err
    assert REPLY_SENTINEL not in out + err
    assert stored(home)["tokens"]["refresh_token"] not in out + err


def test_token_reply_on_code_exchange_is_not_surfaced(
    tmp_path: Path, home: Path, start: Callable[..., Fake]
) -> None:
    fake = start(code_mode="reject")
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    with pytest.raises(mcp_auth.McpAuthError) as caught:
        login("remote", server)
    assert "authorization failed" in str(caught.value)
    assert REPLY_SENTINEL not in str(caught.value)
    assert not token_file(home).exists() or stored(home)["tokens"] is None


def test_forged_state_is_rejected_and_nothing_is_stored(
    tmp_path: Path, home: Path, start: Callable[..., Fake]
) -> None:
    fake = start()
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    with pytest.raises(mcp_auth.McpAuthError, match="timed out"):
        asyncio.run(
            mcp_auth.login(
                "remote", server, timeout=2, present=browser(tamper_state=True)
            )
        )
    assert not any(e["event"] == "grant" for e in fake.events())


def test_token_file_readable_by_others_is_refused(
    tmp_path: Path,
    home: Path,
    start: Callable[..., Fake],
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = start()
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    login("remote", server)
    token_file(home).chmod(0o644)

    code, out, err = run_with(config, MCPModel("remote__echo"), capsys)
    assert code == 1
    assert "readable by other users" in out + err
    assert stored(home)["tokens"]["access_token"] not in out + err


def test_login_refuses_a_server_without_auth(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_config(
        tmp_path, {"plain": {"url": "http://127.0.0.1:9/mcp", "timeout": 1}}
    )
    assert main(["mcp", "login", "plain", "--mcp-config", str(config)]) == 2
    assert main(["mcp", "login", "missing", "--mcp-config", str(config)]) == 2


def test_login_cli_reports_a_failure_on_stderr_only(
    tmp_path: Path,
    home: Path,
    start: Callable[..., Fake],
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = start()
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    # A redirect that never comes back times out; nothing reaches stdout.
    code = main(
        ["mcp", "login", "remote", "--mcp-config", str(config), "--timeout", "0.5"]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "nare:" in captured.err


@pytest.mark.parametrize(
    "entry",
    [
        {"command": "x", "auth": {}},
        {"url": "http://127.0.0.1:9/mcp", "auth": {"client_secret_env": "X"}},
        {"url": "http://127.0.0.1:9/mcp", "auth": {"client_id": "a", "unknown": 1}},
        {
            "url": "http://127.0.0.1:9/mcp",
            "auth": {"client_id": "a", "callback_port": 0},
        },
        {
            "url": "http://127.0.0.1:9/mcp",
            "auth": {"client_id": "a", "callback_port": True},
        },
        {
            "url": "http://127.0.0.1:9/mcp",
            "auth": {"client_id": "a", "client_secret_env": "1BAD"},
        },
        {"url": "http://127.0.0.1:9/mcp", "auth": "yes"},
        {
            "url": "http://127.0.0.1:9/mcp",
            "auth": {"client_id": "a", "authorization_server": "http://example.com"},
        },
        {
            "url": "http://127.0.0.1:9/mcp",
            "auth": {"authorization_server": "https://u:p@example.com"},
        },
        {
            "url": "http://127.0.0.1:9/mcp",
            "headers": {"Authorization": "Bearer x"},
            "auth": {},
        },
    ],
)
def test_invalid_auth_configuration_is_rejected(entry: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        parse_servers({"remote": entry})


def test_auth_configuration_parses() -> None:
    servers = parse_servers(
        {
            "remote": {
                "url": "http://127.0.0.1:9/mcp",
                "auth": {
                    "client_id": "a",
                    "client_secret_env": "A_SECRET",
                    "callback_port": 8123,
                    "scope": "x",
                },
            }
        }
    )
    assert servers["remote"].auth == mcp_auth.OAuthSettings(
        client_id="a", client_secret_env="A_SECRET", callback_port=8123, scope="x"
    )


def test_missing_secret_variable_is_named_without_its_value(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NARE_ABSENT_SECRET", raising=False)
    server = Server(
        url="http://127.0.0.1:9/mcp",
        auth=mcp_auth.OAuthSettings(
            client_id="a", client_secret_env="NARE_ABSENT_SECRET"
        ),
    )
    with pytest.raises(mcp_auth.McpAuthError, match="NARE_ABSENT_SECRET is not set"):
        login("remote", server)


def test_auth_over_plain_http_is_refused_off_loopback() -> None:
    with pytest.raises(ValueError, match="https"):
        Server(url="http://example.com/mcp", auth=mcp_auth.OAuthSettings())
    Server(url="http://127.0.0.1:9/mcp", auth=mcp_auth.OAuthSettings())
    Server(url="https://example.com/mcp", auth=mcp_auth.OAuthSettings())


# -- regression tests for the review of this change --------------------------
# Each test drives the SDK's real flow against the fake server. Each one fails
# when the fix it covers is removed.


def events_of(fake: Fake, name: str) -> list[dict[str, Any]]:
    return [e for e in fake.events() if e["event"] == name]


def test_downgraded_token_endpoint_gets_no_credentials(
    tmp_path: Path, home: Path, start: Callable[..., Fake]
) -> None:
    """The server publishes a plain-http token endpoint on another host. The
    code, the verifier and the client credentials must not be posted there."""
    fake = start(token_endpoint="http://0.0.0.0:{port}/token")
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    with pytest.raises(mcp_auth.McpAuthError, match="must use https"):
        login("remote", server)
    assert events_of(fake, "grant") == []


def test_hostile_authorization_server_gets_no_client_secret(
    tmp_path: Path,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    start: Callable[..., Fake],
) -> None:
    """The MCP server names an https authorization server on another origin.
    Without a pinned authorization_server the login stops before any request
    reaches it, so the pre-registered secret is never sent."""
    secret = "hostile-secret-" + secrets.token_hex(8)
    monkeypatch.setenv("NARE_TEST_CLIENT_SECRET", secret)
    monkeypatch.setenv("FAKE_SERVER_CLIENT_SECRET", secret)
    port = free_port()
    fake = start(
        preregistered="cli-h",
        redirect=f"http://127.0.0.1:{port}/callback",
        issuer_base="http://localhost:{port}",
    )
    config = write_config(
        tmp_path,
        {
            "remote": {
                "url": f"{fake.url}/mcp",
                "timeout": 5,
                "auth": {
                    "client_id": "cli-h",
                    "client_secret_env": "NARE_TEST_CLIENT_SECRET",
                    "callback_port": port,
                },
            }
        },
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    with pytest.raises(mcp_auth.McpAuthError, match="another origin"):
        login("remote", server)
    assert events_of(fake, "authorize") == []
    assert events_of(fake, "grant") == []
    assert (
        secret not in token_file(home).read_text()
        if token_file(home).exists()
        else True
    )


def test_pinned_authorization_server_receives_the_secret(
    tmp_path: Path,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    start: Callable[..., Fake],
) -> None:
    """The same layout works when the configuration names the authorization
    server it trusts, and the secret goes there."""
    secret = "pinned-secret-" + secrets.token_hex(8)
    monkeypatch.setenv("NARE_TEST_CLIENT_SECRET", secret)
    monkeypatch.setenv("FAKE_SERVER_CLIENT_SECRET", secret)
    port = free_port()
    fake = start(
        preregistered="cli-p",
        redirect=f"http://127.0.0.1:{port}/callback",
        issuer_base="http://localhost:{port}",
    )
    config = write_config(
        tmp_path,
        {
            "remote": {
                "url": f"{fake.url}/mcp",
                "timeout": 5,
                "auth": {
                    "client_id": "cli-p",
                    "client_secret_env": "NARE_TEST_CLIENT_SECRET",
                    "callback_port": port,
                    "authorization_server": fake.url.replace("127.0.0.1", "localhost"),
                },
            }
        },
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    assert login("remote", server) == ["remote__echo"]
    assert len(events_of(fake, "grant")) == 1
    assert secret not in token_file(home).read_text()


def test_refresh_in_a_fresh_process_uses_the_stored_authorization_server(
    tmp_path: Path,
    home: Path,
    start: Callable[..., Fake],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A refresh by a new process goes to the authorization server the login
    used, not to the MCP server's origin, which would receive the refresh token."""
    fake = start(access_ttl=2, issuer_base="http://localhost:{port}")
    port = fake.url.rsplit(":", 1)[1]
    config = write_config(
        tmp_path,
        {
            "remote": {
                "url": f"{fake.url}/mcp",
                "timeout": 5,
                "auth": {"authorization_server": f"http://localhost:{port}"},
            }
        },
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    login("remote", server)
    time.sleep(2.2)
    model = MCPModel("remote__echo")
    code, out, err = run_with(config, model, capsys)
    assert code == 0, err
    assert "hello" in model.messages[-1].content[0]["content"]
    assert [g["grant"] for g in events_of(fake, "grant")] == [
        "authorization_code",
        "refresh_token",
    ]


def test_error_text_from_the_mcp_server_is_not_shown(
    tmp_path: Path,
    home: Path,
    start: Callable[..., Fake],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An MCP server whose error quotes the bearer token must not leak it into
    the tool result, the events or stderr."""
    fake = start(echo_bearer=True)
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    login("remote", server)
    bearer = stored(home)["tokens"]["access_token"]
    model = MCPModel("remote__echo")
    code, out, err = run_with(config, model, capsys)
    result = model.messages[-1].content[0]
    assert result["is_error"], "the echoing server should make the call fail"
    assert bearer not in out + err
    assert bearer not in json.dumps(result)


def test_error_chain_hides_the_authorization_server_reply(
    tmp_path: Path, home: Path, start: Callable[..., Fake]
) -> None:
    """The raised error must not keep the authorization server's reply
    reachable through its exception chain."""
    fake = start(code_mode="reject")
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    with pytest.raises(mcp_auth.McpAuthError) as caught:
        login("remote", server)
    seen: list[BaseException] = []
    pending: list[BaseException] = [caught.value]
    while pending:
        link = pending.pop()
        if link in seen:
            continue
        seen.append(link)
        for linked in (link.__cause__, link.__context__):
            if linked is not None:
                pending.append(linked)
        if isinstance(link, BaseExceptionGroup):
            pending.extend(link.exceptions)
    text = "".join(traceback.format_exception(caught.value))
    assert all(
        REPLY_SENTINEL not in str(e) and REPLY_SENTINEL not in repr(e) for e in seen
    )
    assert REPLY_SENTINEL not in text


def test_log_scrub_keeps_the_reply_out_of_logs(
    tmp_path: Path,
    home: Path,
    start: Callable[..., Fake],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The SDK logs the failed exchange with its exception. The reply it quotes
    must not reach the log; removing the scrub filter makes this fail."""
    fake = start(code_mode="reject")
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    caplog.set_level(logging.DEBUG)
    with pytest.raises(mcp_auth.McpAuthError):
        login("remote", server)
    assert "OAuth flow error" in caplog.text
    assert REPLY_SENTINEL not in caplog.text


def test_fresh_login_repairs_a_loose_token_file(
    tmp_path: Path, home: Path, start: Callable[..., Fake]
) -> None:
    fake = start()
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    login("remote", server)
    token_file(home).chmod(0o644)
    assert login("remote", server) == ["remote__echo"]
    assert stat.S_IMODE(token_file(home).stat().st_mode) == 0o600


def test_oversized_loopback_request_is_refused_quietly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> bytes:
        async with mcp_auth._Loopback(None, 5) as loopback:
            reader, writer = await asyncio.open_connection("127.0.0.1", loopback.port)
            writer.write(b"GET /callback?" + b"a" * 70000)
            await writer.drain()
            reply = await reader.read()
            writer.close()
            return reply

    caplog.set_level(logging.DEBUG)
    reply = asyncio.run(scenario())
    assert reply.startswith(b"HTTP/1.1 400")
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# -- round-2 regression tests -------------------------------------------------


@contextlib.contextmanager
def tcp_sink(reply: bytes = b"") -> Iterator[tuple[int, list[bytes]]]:
    """A listener on 127.0.0.1 that records what connects to it. Anything that
    reaches it appears in the list; a test asserts the list is empty."""
    received: list[bytes] = []
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    listener.settimeout(0.2)
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                continue
            conn.settimeout(1.0)
            try:
                received.append(conn.recv(65536))
                if reply:
                    conn.sendall(reply)
            except OSError:
                pass
            finally:
                conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1], received
    finally:
        stop.set()
        thread.join(timeout=5)
        listener.close()


def test_hostile_token_endpoint_gets_no_request(
    tmp_path: Path, home: Path, start: Callable[..., Fake]
) -> None:
    """The metadata names an https token endpoint on another origin. The POST
    rule refuses it before any byte is sent there."""
    with tcp_sink() as (sink, received):
        fake = start(token_endpoint=f"https://127.0.0.1:{sink}/token")
        config = write_config(
            tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
        )
        server = parse_servers(json.loads(config.read_text()))["remote"]
        with pytest.raises(mcp_auth.McpAuthError, match="outside its authorization"):
            login("remote", server)
    assert received == []
    assert events_of(fake, "grant") == []


def test_hostile_resource_metadata_gets_no_request(
    tmp_path: Path, home: Path, start: Callable[..., Fake]
) -> None:
    """The challenge names a metadata URL on a third origin. The GET rule
    refuses it before a request is made."""
    with tcp_sink() as (sink, received):
        fake = start(prm_url=f"http://127.0.0.1:{sink}/prm")
        config = write_config(
            tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
        )
        server = parse_servers(json.loads(config.read_text()))["remote"]
        with pytest.raises(mcp_auth.McpAuthError):
            login("remote", server)
    assert received == []


def test_startup_failure_is_mapped_to_fixed_text(tmp_path: Path, home: Path) -> None:
    """A connection failure during startup reaches the caller as the kind of
    failure, not the transport's own exception."""
    config = write_config(
        tmp_path,
        {
            "remote": {
                "url": f"http://127.0.0.1:{free_port()}/mcp",
                "timeout": 2,
                "auth": {},
            }
        },
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    with pytest.raises(mcp_auth.McpAuthError) as caught:
        login("remote", server)
    assert str(caught.value).startswith("MCP server remote: ")
    assert "connection" not in str(caught.value).lower()


def test_startup_exception_is_mapped_before_it_leaves_connect(
    tmp_path: Path,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    start: Callable[..., Fake],
) -> None:
    """A non-OAuth exception raised while the server is being set up must not
    reach the caller as itself, with its text. This drives the setup step of
    connect(), not the teardown that a connection failure happens to reach."""
    import nare.mcp

    async def discover(*_: Any, **__: Any) -> None:
        raise RuntimeError("upstream replied: DO-NOT-SHOW")

    monkeypatch.setattr(nare.mcp, "_discover", discover)
    fake = start()
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    with pytest.raises(mcp_auth.McpAuthError) as caught:
        login("remote", server)
    assert str(caught.value) == "MCP server remote: RuntimeError"


def test_authorized_connection_ignores_environment_proxy(
    tmp_path: Path,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    start: Callable[..., Fake],
) -> None:
    """A proxy named in the environment must never see the token exchange: an
    authorized connection does not honour the proxy variables."""
    reply = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n"
    with tcp_sink(reply=reply) as (proxy, seen):
        for name in ("HTTP_PROXY", "http_proxy"):
            monkeypatch.setenv(name, f"http://127.0.0.1:{proxy}")
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)
        fake = start()
        config = write_config(
            tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
        )
        server = parse_servers(json.loads(config.read_text()))["remote"]
        assert login("remote", server) == ["remote__echo"]
    assert seen == []


def test_forged_loopback_delivery_does_not_abort_the_login(
    tmp_path: Path, home: Path, start: Callable[..., Fake]
) -> None:
    """A local process delivers a code with the wrong state before the real
    redirect. The listener refuses it and the real login still completes."""
    fake = start()
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    real = browser()

    async def present(url: str) -> None:
        redirect = parse_qs(urlsplit(url).query)["redirect_uri"][0]
        async with httpx2.AsyncClient(trust_env=False) as client:
            forged = await client.get(
                redirect, params={"code": "ATTACKER-CODE", "state": "guess"}
            )
            assert forged.status_code == 400
        await real(url)

    assert login("remote", server, present=present) == ["remote__echo"]
    assert stored(home)["tokens"]["access_token"]


# -- logout, revocation, and the token lock ------------------------------------


class Recorder:
    """A loopback HTTP server that records every POST and answers with a fixed
    status. It stands in for an authorization server's revocation endpoint."""

    def __init__(self, status: int, body: bytes = b"") -> None:
        self.posts: list[tuple[str, dict[str, list[str]]]] = []
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                form = parse_qs(self.rfile.read(length).decode())
                recorder.posts.append((self.path, form))
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_: object) -> None:
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def recorder() -> Iterator[Callable[..., Recorder]]:
    made: list[Recorder] = []

    def make(status: int = 200, body: bytes = b"") -> Recorder:
        rec = Recorder(status, body)
        made.append(rec)
        return rec

    yield make
    for rec in made:
        rec.close()


def write_record(
    home: Path, alias: str, server_url: str, revocation_endpoint: str | None
) -> Path:
    """A token file as a login would leave it, written by hand so the test
    controls the advertised revocation endpoint."""
    path = token_file(home, alias)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata: dict[str, Any] = {
        "issuer": server_url,
        "authorization_endpoint": f"{server_url}/authorize",
        "token_endpoint": f"{server_url}/token",
    }
    if revocation_endpoint is not None:
        metadata["revocation_endpoint"] = revocation_endpoint
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "server": server_url,
                "tokens": {
                    "access_token": "ACCESS-VALUE",
                    "token_type": "Bearer",
                    "refresh_token": "REFRESH-VALUE",
                },
                "expires_at": None,
                "metadata": metadata,
                "client": {"client_id": "registered-client"},
            }
        )
    )
    path.chmod(0o600)
    return path


def test_logout_revokes_then_the_next_run_asks_for_login(
    tmp_path: Path,
    home: Path,
    start: Callable[..., Fake],
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = start()
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    assert login("remote", server) == ["remote__echo"]
    refresh = stored(home)["tokens"]["refresh_token"]

    code = main(["mcp", "logout", "remote", "--mcp-config", str(config)])
    err = capsys.readouterr().err
    assert code == 0, err
    assert not token_file(home).exists()
    assert "revoked at the server" in err
    revoked = [e for e in fake.events() if e["event"] == "revoked"]
    assert sorted(e["token_type"] for e in revoked) == [
        "access_token",
        "refresh_token",
    ]
    assert refresh not in err

    model = MCPModel("remote__echo")
    code, out, _ = run_with(config, model, capsys)
    assert code == 1
    assert model.calls == 0
    result = json.loads(out.splitlines()[-1])
    assert result["stop_reason"] == "mcp"
    assert "nare mcp login remote" in result["error"]


def test_logout_without_config_deletes_the_file_and_revokes_nothing(
    tmp_path: Path,
    home: Path,
    start: Callable[..., Fake],
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = start()
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    login("remote", server)

    assert main(["mcp", "logout", "remote"]) == 0
    assert not token_file(home).exists()
    assert "not revoked" in capsys.readouterr().err
    assert not [e for e in fake.events() if e["event"] == "revoked"]


def test_logout_with_nothing_stored_is_a_clean_no_op(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["mcp", "logout", "remote"]) == 0
    assert "no stored authorization for remote" in capsys.readouterr().err


def test_revocation_goes_to_the_pinned_authorization_server(
    tmp_path: Path, home: Path, recorder: Callable[..., Recorder]
) -> None:
    pinned = recorder(200)
    mcp_host = recorder(200)
    write_record(
        home,
        "remote",
        f"{mcp_host.url}/mcp",
        revocation_endpoint=f"{pinned.url}/revoke",
    )
    config = write_config(
        tmp_path,
        {
            "remote": {
                "url": f"{mcp_host.url}/mcp",
                "timeout": 5,
                "auth": {"authorization_server": pinned.url, "client_id": "pinned"},
            }
        },
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]

    message = asyncio.run(mcp_auth.logout("remote", server))

    assert "revoked at the server" in message
    assert [path for path, _ in pinned.posts] == ["/revoke", "/revoke"]
    assert mcp_host.posts == []
    refresh_form = pinned.posts[0][1]
    assert refresh_form["token"] == ["REFRESH-VALUE"]
    assert refresh_form["token_type_hint"] == ["refresh_token"]
    assert refresh_form["client_id"] == ["pinned"]
    assert not token_file(home).exists()


def test_revocation_outside_the_authorization_server_is_never_sent(
    tmp_path: Path, home: Path, recorder: Callable[..., Recorder]
) -> None:
    """The advertised endpoint names a third origin. Nothing is sent to it,
    the tokens are still removed, and the failure is said plainly."""
    mcp_host = recorder(200)
    elsewhere = recorder(200)
    write_record(
        home,
        "remote",
        f"{mcp_host.url}/mcp",
        revocation_endpoint=f"{elsewhere.url}/revoke",
    )
    config = write_config(
        tmp_path, {"remote": {"url": f"{mcp_host.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]

    with pytest.raises(mcp_auth.McpAuthError) as caught:
        asyncio.run(mcp_auth.logout("remote", server))

    assert elsewhere.posts == [] and mcp_host.posts == []
    assert "revocation was not sent" in str(caught.value)
    assert "stored tokens were removed" in str(caught.value)
    assert not token_file(home).exists()


def test_revocation_plain_http_off_loopback_is_never_sent(
    tmp_path: Path, home: Path
) -> None:
    write_record(
        home,
        "remote",
        "https://mcp.example.test/mcp",
        revocation_endpoint="http://revoke.example.test/revoke",
    )
    config = write_config(
        tmp_path,
        {
            "remote": {
                "url": "https://mcp.example.test/mcp",
                "timeout": 5,
                "auth": {},
            }
        },
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]

    with pytest.raises(mcp_auth.McpAuthError, match="https"):
        asyncio.run(mcp_auth.logout("remote", server))
    assert not token_file(home).exists()


def test_revocation_failure_text_is_fixed_and_hides_the_reply(
    tmp_path: Path, home: Path, recorder: Callable[..., Recorder]
) -> None:
    host = recorder(500, b"SENTINEL-REPLY-7731 token REFRESH-VALUE is retired")
    write_record(
        home, "remote", f"{host.url}/mcp", revocation_endpoint=f"{host.url}/revoke"
    )
    config = write_config(
        tmp_path, {"remote": {"url": f"{host.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]

    with pytest.raises(mcp_auth.McpAuthError) as caught:
        asyncio.run(mcp_auth.logout("remote", server))
    text = str(caught.value)
    assert "HTTP 500" in text
    assert "SENTINEL" not in text and "REFRESH-VALUE" not in text
    assert not token_file(home).exists()


def test_server_without_a_revocation_endpoint_is_said_so(
    tmp_path: Path, home: Path, recorder: Callable[..., Recorder]
) -> None:
    host = recorder(200)
    write_record(home, "remote", f"{host.url}/mcp", revocation_endpoint=None)
    config = write_config(
        tmp_path, {"remote": {"url": f"{host.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]

    message = asyncio.run(mcp_auth.logout("remote", server))
    assert "advertises no revocation endpoint" in message
    assert host.posts == []


def test_logout_waits_for_the_token_lock_and_keeps_the_file_on_timeout(
    tmp_path: Path,
    home: Path,
    recorder: Callable[..., Recorder],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = recorder(200)
    path = write_record(
        home, "remote", f"{host.url}/mcp", revocation_endpoint=f"{host.url}/revoke"
    )
    config = write_config(
        tmp_path, {"remote": {"url": f"{host.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    monkeypatch.setattr(mcp_auth, "_LOCK_WAIT", 0.2)

    holder = os.open(path.parent / "remote.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        with pytest.raises(mcp_auth.McpAuthError, match="another nare process"):
            asyncio.run(mcp_auth.logout("remote", server))
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        os.close(holder)
    assert path.exists()
    assert host.posts == []


def test_leftover_tmp_file_is_removed_on_the_next_save(home: Path) -> None:
    folder = home / "mcp-auth"
    folder.mkdir(mode=0o700, parents=True)
    old = folder / ".mcp-crashed.tmp"
    old.write_text("{}")
    two_hours = time.time() - 7200
    os.utime(old, (two_hours, two_hours))
    recent = folder / ".mcp-inflight.tmp"
    recent.write_text("{}")

    tokens = mcp_auth.TokenFile(
        token_file(home),
        "https://mcp.example.test/mcp",
        mcp_auth.OAuthSettings(),
        None,
        "http://127.0.0.1/callback",
        fresh=True,
    )
    tokens.reset()

    assert not old.exists()
    assert recent.exists(), "a recent tmp file may be another process's save"


def test_refresh_uses_the_rotated_token_another_process_stored(
    tmp_path: Path,
    home: Path,
    start: Callable[..., Fake],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Process B loads its tokens, then process A refreshes and the server
    rotates the refresh token. B must not spend the retired token: it reads the
    file under the lock and carries on with what A stored."""
    fake = start(refresh_mode="rotate", access_ttl=3600)
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    login("remote", server)
    original = stored(home)["tokens"]["refresh_token"]

    async def scenario() -> tuple[str, str]:
        request = httpx2.Request("POST", f"{fake.url}/mcp", json={})
        b = mcp_auth.build_provider(
            "remote", f"{fake.url}/mcp", mcp_auth.OAuthSettings(), None
        )
        await b._initialize()
        b.context.token_expiry_time = time.time() - 1
        a = mcp_auth.build_provider(
            "remote", f"{fake.url}/mcp", mcp_auth.OAuthSettings(), None
        )
        await a._initialize()
        a.context.token_expiry_time = time.time() - 1
        flow = a._auth_flow(request)
        outgoing = await flow.__anext__()
        async with httpx2.AsyncClient(trust_env=False) as client:
            while mcp_auth._refresh_token_sent(outgoing) is not None:
                outgoing = await flow.asend(await client.send(outgoing))
        await flow.aclose()
        assert stored(home)["tokens"]["refresh_token"] != original

        # B now refreshes from its stale state.
        flow_b = b._auth_flow(request)
        first = await flow_b.__anext__()
        assert mcp_auth._refresh_token_sent(first) is None, "B spent a retired token"
        await flow_b.aclose()
        return first.headers["authorization"], stored(home)["tokens"]["access_token"]

    header, current = asyncio.run(scenario())
    assert header == f"Bearer {current}"
    grants = [e for e in fake.events() if e["event"] == "grant"]
    assert [g["grant"] for g in grants] == ["authorization_code", "refresh_token"]


def test_refresh_after_logout_does_not_resurrect_the_session(
    tmp_path: Path,
    home: Path,
    start: Callable[..., Fake],
) -> None:
    fake = start(refresh_mode="rotate", access_ttl=3600)
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    login("remote", server)

    async def scenario() -> None:
        request = httpx2.Request("POST", f"{fake.url}/mcp", json={})
        b = mcp_auth.build_provider(
            "remote", f"{fake.url}/mcp", mcp_auth.OAuthSettings(), None
        )
        await b._initialize()
        b.context.token_expiry_time = time.time() - 1
        await mcp_auth.logout("remote", server)
        flow = b._auth_flow(request)
        with pytest.raises(mcp_auth.McpAuthError, match="needs authorization again"):
            await flow.__anext__()

    asyncio.run(scenario())
    assert not token_file(home).exists()
    assert [e["grant"] for e in fake.events() if e["event"] == "grant"] == [
        "authorization_code"
    ]


def test_two_processes_refreshing_at_once_both_end_with_a_valid_token(
    tmp_path: Path,
    home: Path,
    start: Callable[..., Fake],
) -> None:
    """Two refreshes race against a server that rotates refresh tokens. The
    lock serialises them: one spends the token, the other reads the rotated
    one. Neither fails, and the server sees one refresh."""
    fake = start(refresh_mode="rotate", access_ttl=3600)
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    login("remote", server)
    url = f"{fake.url}/mcp"
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []
    headers: list[str] = []

    async def one() -> None:
        request = httpx2.Request("POST", url, json={})
        provider = mcp_auth.build_provider(
            "remote", url, mcp_auth.OAuthSettings(), None
        )
        await provider._initialize()
        provider.context.token_expiry_time = time.time() - 1
        flow = provider._auth_flow(request)
        outgoing = await flow.__anext__()
        async with httpx2.AsyncClient(trust_env=False) as client:
            while mcp_auth._refresh_token_sent(outgoing) is not None:
                outgoing = await flow.asend(await client.send(outgoing))
        headers.append(outgoing.headers["authorization"])
        await flow.aclose()

    def worker() -> None:
        try:
            barrier.wait(10)
            asyncio.run(one())
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)

    assert errors == []
    current = stored(home)["tokens"]["access_token"]
    assert headers == [f"Bearer {current}"] * 2
    refreshes = [
        e
        for e in fake.events()
        if e["event"] == "grant" and e["grant"] == "refresh_token"
    ]
    assert len(refreshes) == 1


def test_revocation_sends_both_stored_tokens(
    tmp_path: Path, home: Path, recorder: Callable[..., Recorder]
) -> None:
    """A server need not revoke the access token when the refresh token is
    revoked, so both are sent."""
    host = recorder(200)
    write_record(
        home, "remote", f"{host.url}/mcp", revocation_endpoint=f"{host.url}/revoke"
    )
    config = write_config(
        tmp_path, {"remote": {"url": f"{host.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]

    asyncio.run(mcp_auth.logout("remote", server))

    sent = sorted(form["token_type_hint"][0] for _, form in host.posts)
    assert sent == ["access_token", "refresh_token"]
    values = {form["token"][0] for _, form in host.posts}
    assert values == {"ACCESS-VALUE", "REFRESH-VALUE"}


def test_malformed_revocation_endpoint_is_a_fixed_failure(
    tmp_path: Path, home: Path, recorder: Callable[..., Recorder]
) -> None:
    host = recorder(200)
    write_record(
        home,
        "remote",
        f"{host.url}/mcp",
        revocation_endpoint="https://revoke.example.test:99999/revoke",
    )
    config = write_config(
        tmp_path, {"remote": {"url": f"{host.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]

    with pytest.raises(mcp_auth.McpAuthError) as caught:
        asyncio.run(mcp_auth.logout("remote", server))
    assert "not a valid URL" in str(caught.value)
    assert not token_file(home).exists()


def test_logout_refuses_an_alias_that_is_not_a_server_name(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The alias names a file under NARE_HOME. A traversal name must not reach
    a file outside the token folder."""
    victim = home / "victim.json"
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_text("{}")

    assert main(["mcp", "logout", "../victim"]) == 1
    assert victim.exists()
    assert "not a valid server name" in capsys.readouterr().err


def test_metadata_discovered_later_does_not_restore_retired_tokens(
    tmp_path: Path,
    home: Path,
    start: Callable[..., Fake],
) -> None:
    """Process B holds tokens from before process A refreshed. When B later
    learns new authorization server metadata, saving it must not write B's
    retired refresh token back over A's."""
    fake = start(refresh_mode="rotate", access_ttl=3600)
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    login("remote", server)
    url = f"{fake.url}/mcp"

    async def scenario() -> None:
        request = httpx2.Request("POST", url, json={})
        b = mcp_auth.build_provider("remote", url, mcp_auth.OAuthSettings(), None)
        await b._initialize()
        a = mcp_auth.build_provider("remote", url, mcp_auth.OAuthSettings(), None)
        await a._initialize()
        a.context.token_expiry_time = time.time() - 1
        flow = a._auth_flow(request)
        outgoing = await flow.__anext__()
        async with httpx2.AsyncClient(trust_env=False) as client:
            while mcp_auth._refresh_token_sent(outgoing) is not None:
                outgoing = await flow.asend(await client.send(outgoing))
        await flow.aclose()
        rotated = stored(home)["tokens"]["refresh_token"]

        metadata = b.context.storage.metadata
        assert metadata is not None
        changed = metadata.model_copy(update={"scopes_supported": ["changed"]})
        b.context.storage.set_metadata(changed)
        assert stored(home)["tokens"]["refresh_token"] == rotated

    asyncio.run(scenario())
    assert stored(home)["metadata"]["scopes_supported"] == ["changed"]


def test_metadata_discovered_after_logout_does_not_recreate_the_file(
    tmp_path: Path,
    home: Path,
    start: Callable[..., Fake],
) -> None:
    fake = start(access_ttl=3600)
    config = write_config(
        tmp_path, {"remote": {"url": f"{fake.url}/mcp", "timeout": 5, "auth": {}}}
    )
    server = parse_servers(json.loads(config.read_text()))["remote"]
    login("remote", server)
    url = f"{fake.url}/mcp"

    async def scenario() -> None:
        b = mcp_auth.build_provider("remote", url, mcp_auth.OAuthSettings(), None)
        await b._initialize()
        await mcp_auth.logout("remote", server)
        metadata = b.context.storage.metadata
        assert metadata is not None
        b.context.storage.set_metadata(
            metadata.model_copy(update={"scopes_supported": ["changed"]})
        )

    asyncio.run(scenario())
    assert not token_file(home).exists()
