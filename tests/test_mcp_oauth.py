"""Remote MCP servers with OAuth, offline.

The fake authorization server and MCP server run as a subprocess
(tests/mcp_oauth_server.py), and the browser is a function that performs the
redirects over real HTTP. No third-party service is contacted.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import socket
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
    ) -> Fake:
        port = free_port()
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
            argv += ["--preregistered", preregistered, "--redirect", redirect or ""]
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
        async with httpx2.AsyncClient(follow_redirects=False) as client:
            first = await client.get(url)
            assert first.status_code in (302, 303, 307), first.status_code
            callback = await client.get(first.headers["location"])
            assert callback.status_code == 200, callback.status_code

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
    with pytest.raises(mcp_auth.McpAuthError, match="authorization failed"):
        login("remote", server, present=browser(tamper_state=True))
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


def test_discovered_authorization_server_must_be_https(home: Path) -> None:
    async def present(_: str) -> None:
        raise AssertionError("no browser in this test")

    async def receive() -> Any:
        raise AssertionError("no callback in this test")

    provider = mcp_auth.build_provider(
        "remote",
        "https://example.com/mcp",
        mcp_auth.OAuthSettings(),
        mcp_auth.Authorizer(
            redirect_uri="http://127.0.0.1:1/callback", present=present, receive=receive
        ),
    )
    with pytest.raises(mcp_auth.McpAuthError, match="https"):
        provider._select_authorization_server(["http://attacker.example"])
    assert provider._select_authorization_server(["https://auth.example"]) == (
        "https://auth.example"
    )
