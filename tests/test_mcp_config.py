"""nare mcp add|list|get|remove and .mcp.json (#329).

User servers live in NARE_HOME/mcp.json and project servers in the project's
.mcp.json, both in Claude Code's {"mcpServers": {...}} format. The
person-facing forms load them (project servers only once the project is
trusted); nare run loads only what --mcp-config names, as before.
"""

from __future__ import annotations

import argparse
import io
import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider
from nare import mcp_config, person
from nare.cli import main, parse_args, validate_run_args
from nare.mcp import Server

# A Claude Code .mcp.json as projects carry it, ${VAR} expansion included.
CLAUDE_MCP_JSON = {
    "mcpServers": {
        "filesystem": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "${HOME}"],
            "env": {"LOG_LEVEL": "${NARE_TEST_LEVEL:-info}"},
        },
        "docs": {
            "type": "http",
            "url": "https://mcp.example.com/mcp",
            "headers": {"Authorization": "Bearer ${NARE_TEST_TOKEN}"},
        },
    }
}


@pytest.fixture
def here(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NARE_TEST_TOKEN", "t0ken")
    monkeypatch.delenv("NARE_TEST_LEVEL", raising=False)
    return work


@pytest.fixture
def tui_calls(monkeypatch: pytest.MonkeyPatch) -> list[argparse.Namespace]:
    calls: list[argparse.Namespace] = []

    def fake_tui(args: argparse.Namespace, transport: Any = None) -> int:
        validate_run_args(args)
        calls.append(args)
        return 0

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(person, "tui_available", lambda: True)
    monkeypatch.setattr(person, "interactive_terminal", lambda: True)
    monkeypatch.setattr("nare.tui.app.main", fake_tui)
    return calls


def user_file() -> Path:
    path = mcp_config.user_path()
    assert path is not None
    return path


def test_a_claude_code_mcp_json_reads_as_is(here: Path) -> None:
    servers = mcp_config.servers_from(CLAUDE_MCP_JSON)
    assert servers["filesystem"] == Server(
        command="npx",
        args=[
            "-y",
            "@modelcontextprotocol/server-filesystem",
            str(here.parent / "home"),
        ],
        env={"LOG_LEVEL": "info"},
    )
    assert servers["docs"] == Server(
        url="https://mcp.example.com/mcp", headers={"Authorization": "Bearer t0ken"}
    )


def test_todays_format_is_unchanged(here: Path) -> None:
    """No expansion in nare's own format: what it meant before, it means now."""
    raw = {"local": {"command": "python", "args": ["${HOME}"]}}
    assert mcp_config.servers_from(raw) == {
        "local": Server(command="python", args=["${HOME}"])
    }


def test_mcp_config_accepts_the_mcp_servers_format(here: Path) -> None:
    (here / "servers.json").write_text(json.dumps(CLAUDE_MCP_JSON))
    args = parse_args(["run", "--yes", "--mcp-config", "servers.json", "x"])
    validate_run_args(args)
    assert set(args.mcp_servers) == {"filesystem", "docs"}


def test_sse_is_refused_by_name(here: Path) -> None:
    raw = {"mcpServers": {"old": {"type": "sse", "url": "https://x.example/sse"}}}
    with pytest.raises(ValueError, match="sse"):
        mcp_config.servers_from(raw)


def test_add_a_stdio_server_to_the_user_scope(
    here: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["mcp", "add", "--env", "K=V", "local", "--", "python", "-m", "srv", "-x"]
    assert main(argv) == 0
    saved = json.loads(user_file().read_text())
    assert saved == {
        "mcpServers": {
            "local": {
                "command": "python",
                "args": ["-m", "srv", "-x"],
                "env": {"K": "V"},
            }
        }
    }
    assert stat.S_IMODE(user_file().stat().st_mode) == 0o600


def test_add_without_dashes_takes_the_rest_as_the_command(here: Path) -> None:
    assert main(["mcp", "add", "local", "python", "srv.py"]) == 0
    entry = json.loads(user_file().read_text())["mcpServers"]["local"]
    assert entry == {"command": "python", "args": ["srv.py"]}


def test_add_a_remote_server_to_the_project(here: Path) -> None:
    (here / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"kept": {"command": "a"}}})
    )
    argv = [
        "mcp",
        "add",
        "--scope",
        "project",
        "--transport",
        "http",
        "--header",
        "X-Team=core",
        "docs",
        "--url",
        "https://mcp.example.com/mcp",
    ]
    assert main(argv) == 0
    saved = json.loads((here / ".mcp.json").read_text())
    assert saved["mcpServers"] == {
        "kept": {"command": "a"},
        "docs": {
            "type": "http",
            "url": "https://mcp.example.com/mcp",
            "headers": {"X-Team": "core"},
        },
    }


def test_add_refuses_a_name_already_there(
    here: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["mcp", "add", "local", "--", "a"]) == 0
    assert main(["mcp", "add", "local", "--", "b"]) == 2
    assert "local" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["mcp", "add", "bad__name", "--", "a"],
        ["mcp", "add", "x"],
        ["mcp", "add", "x", "--url", "https://a.example", "--", "cmd"],
        ["mcp", "add", "x", "--env", "NOEQUALS", "--", "a"],
        ["mcp", "add", "x", "--transport", "sse", "--url", "https://a.example"],
    ],
)
def test_add_refuses_a_bad_server(
    argv: list[str], here: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == 2
    assert not user_file().exists()


def test_list_get_remove(here: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["mcp", "add", "--env", "TOKEN=s3cret", "local", "--", "python"]) == 0
    (here / ".mcp.json").write_text(json.dumps(CLAUDE_MCP_JSON))
    capsys.readouterr()
    assert main(["mcp", "list"]) == 0
    out = capsys.readouterr().out
    assert "local" in out and "filesystem" in out and "docs" in out
    assert "not trusted" in out
    assert main(["mcp", "list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {row["name"]: row["scope"] for row in rows} == {
        "local": "user",
        "filesystem": "project",
        "docs": "project",
    }
    assert main(["mcp", "get", "local"]) == 0
    out = capsys.readouterr().out
    assert "python" in out and "TOKEN" in out and "s3cret" not in out
    assert main(["mcp", "remove", "local"]) == 0
    assert json.loads(user_file().read_text()) == {"mcpServers": {}}
    assert main(["mcp", "get", "local"]) == 2


def test_run_with_no_mcp_config_loads_no_servers(here: Path) -> None:
    assert main(["mcp", "add", "local", "--", "python"]) == 0
    (here / ".mcp.json").write_text(json.dumps(CLAUDE_MCP_JSON))
    mcp_config.trust(str(here))
    args = parse_args(["run", "--yes", "x"])
    validate_run_args(args)
    assert args.mcp_servers == {}


def test_print_mode_loads_user_and_trusted_project_servers(
    here: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Server]] = []
    monkeypatch.setattr("nare.cli._execute", _capture_servers(seen), raising=True)
    assert main(["mcp", "add", "local", "--", "python"]) == 0
    (here / ".mcp.json").write_text(json.dumps(CLAUDE_MCP_JSON))
    assert main(["-p", "x"], transport=FakeProvider([])) == 0
    assert set(seen[-1]) == {"local"}  # the project is not trusted yet
    mcp_config.trust(str(here))
    assert main(["-p", "x"], transport=FakeProvider([])) == 0
    assert set(seen[-1]) == {"local", "filesystem", "docs"}
    assert main(["-p", "--strict-mcp-config", "x"], transport=FakeProvider([])) == 0
    assert seen[-1] == {}


def test_a_changed_mcp_json_needs_trust_again(here: Path) -> None:
    (here / ".mcp.json").write_text(json.dumps(CLAUDE_MCP_JSON))
    mcp_config.trust(str(here))
    assert mcp_config.trusted(str(here))
    changed = {"mcpServers": {"evil": {"command": "curl"}}}
    (here / ".mcp.json").write_text(json.dumps(changed))
    assert not mcp_config.trusted(str(here))


def test_untrusted_project_servers_are_named_on_stderr(
    here: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("nare.cli._execute", _capture_servers([]))
    (here / ".mcp.json").write_text(json.dumps(CLAUDE_MCP_JSON))
    assert main(["-p", "x"], transport=FakeProvider([])) == 0
    err = capsys.readouterr().err
    assert "filesystem" in err and "nare mcp trust" in err


def test_the_tui_asks_once_to_trust_the_project(
    here: Path,
    tui_calls: list[argparse.Namespace],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (here / ".mcp.json").write_text(json.dumps(CLAUDE_MCP_JSON))
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))
    assert main(["do it"]) == 0
    assert set(tui_calls[-1].mcp_servers) == {"filesystem", "docs"}
    assert "Trust it" in capsys.readouterr().err
    # Asked once: the next start does not ask, and nothing is typed.
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert main(["do it"]) == 0
    assert set(tui_calls[-1].mcp_servers) == {"filesystem", "docs"}
    assert "Trust it" not in capsys.readouterr().err


def test_saying_no_starts_no_project_server(
    here: Path, tui_calls: list[argparse.Namespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    (here / ".mcp.json").write_text(json.dumps(CLAUDE_MCP_JSON))
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    assert main(["do it"]) == 0
    assert tui_calls[-1].mcp_servers == {}
    assert not mcp_config.trusted(str(here))


def test_mcp_trust_command(here: Path) -> None:
    (here / ".mcp.json").write_text(json.dumps(CLAUDE_MCP_JSON))
    assert main(["mcp", "trust"]) == 0
    assert mcp_config.trusted(str(here))
    assert main(["mcp", "trust", "--revoke"]) == 0
    assert not mcp_config.trusted(str(here))


def test_mcp_config_wins_over_the_stores(
    here: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Server]] = []
    monkeypatch.setattr("nare.cli._execute", _capture_servers(seen))
    assert main(["mcp", "add", "local", "--", "python"]) == 0
    (here / "mine.json").write_text(json.dumps({"local": {"command": "node"}}))
    assert (
        main(["-p", "--mcp-config", "mine.json", "x"], transport=FakeProvider([])) == 0
    )
    assert seen[-1]["local"].command == "node"


def test_strict_mcp_config_is_a_run_flag(here: Path) -> None:
    from nare.contract import describe

    assert describe()["flags"]["--strict-mcp-config"] == {
        "takes_value": False,
        "choices": None,
    }


def _capture_servers(seen: list[dict[str, Server]]) -> Any:
    async def execute(
        session: Any, transport: Any, args: argparse.Namespace, *rest: Any
    ) -> int:
        seen.append(dict(args.mcp_servers))
        return 0

    return execute
