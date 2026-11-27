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


def test_the_trust_question_shows_each_command_line_and_url(
    here: Path,
    tui_calls: list[argparse.Namespace],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (here / ".mcp.json").write_text(json.dumps(CLAUDE_MCP_JSON))
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    assert main(["do it"]) == 0
    err = capsys.readouterr().err
    assert "filesystem: npx -y @modelcontextprotocol/server-filesystem" in err
    assert "'${HOME}'" in err  # as the file has it, before any expansion
    assert "(env LOG_LEVEL=${NARE_TEST_LEVEL:-info}; reads ${HOME}" in err
    assert (
        "docs: https://mcp.example.com/mcp "
        "(headers Authorization; reads ${NARE_TEST_TOKEN})"
    ) in err
    assert "t0ken" not in err


def test_the_trust_question_survives_a_malformed_entry(
    here: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    raw: dict[str, Any] = {"x": {"command": "srv", "args": 5}, "y": {"args": None}}
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    assert not mcp_config.terminal_ask(raw)
    err = capsys.readouterr().err
    assert "x: srv 5" in err and "y: " in err


def test_a_withheld_credential_is_left_out_of_the_trust_question(
    here: Path,
    tui_calls: list[argparse.Namespace],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    servers = {
        "leaky": {"command": "srv", "args": ["${OPENAI_API_KEY}"]},
        "fine": {"command": "ok"},
    }
    (here / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))
    assert main(["do it"]) == 0
    assert set(tui_calls[-1].mcp_servers) == {"fine"}
    err = capsys.readouterr().err
    assert "skipped MCP server leaky" in err and "${OPENAI_API_KEY}" in err
    assert "Trust it" in err and "  leaky:" not in err and "  fine: ok" in err


def test_a_file_of_only_withheld_credentials_asks_nothing(
    here: Path,
    tui_calls: list[argparse.Namespace],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    leaky = {"mcpServers": {"leaky": {"command": "srv", "args": ["${OPENAI_API_KEY}"]}}}
    (here / ".mcp.json").write_text(json.dumps(leaky))
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))
    assert main(["do it"]) == 0
    assert tui_calls[-1].mcp_servers == {}
    err = capsys.readouterr().err
    assert "${OPENAI_API_KEY}" in err and "Trust it" not in err
    assert not mcp_config.trusted(str(here))


def test_a_malformed_project_file_is_refused_before_the_trust_question(
    here: Path,
    tui_calls: list[argparse.Namespace],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A yes is never recorded for a file that could never load."""
    bad = {"mcpServers": {"old": {"type": "sse", "url": "https://h.example/sse"}}}
    (here / ".mcp.json").write_text(json.dumps(bad))
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))
    assert main(["do it"]) == 2
    err = capsys.readouterr().err
    assert "sse" in err and "Trust it" not in err
    assert not mcp_config.trusted(str(here))


def test_a_credential_name_in_another_case_is_refused_too(
    here: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Windows reads the environment without regard to case."""
    seen: list[dict[str, Server]] = []
    monkeypatch.setattr("nare.cli._execute", _capture_servers(seen))
    leaky = {
        "mcpServers": {"leaky": {"command": "srv", "args": ["${anthropic_api_key}"]}}
    }
    (here / ".mcp.json").write_text(json.dumps(leaky))
    monkeypatch.setenv("anthropic_api_key", "s3cret-value")
    mcp_config.trust(str(here))
    assert main(["-p", "x"], transport=FakeProvider([])) == 0
    err = capsys.readouterr().err
    assert "${anthropic_api_key}" in err and "provider credential" in err
    assert "s3cret-value" not in err
    assert seen[-1] == {}


def test_the_skipped_line_escapes_control_characters() -> None:
    line = mcp_config.skipped_line(["a\x1b[2Kb"])
    assert "\x1b" not in line and "a\\x1b[2Kb" in line


def test_the_trust_question_escapes_control_characters(
    here: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = {"mcpServers": {"x": {"command": "run\x1b[2K", "args": ["a\rb"]}}}
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    assert not mcp_config.terminal_ask(raw["mcpServers"])
    err = capsys.readouterr().err
    assert "\x1b" not in err and "\r" not in err
    assert "\\x1b[2K" in err and "a\\rb" in err


CREDENTIAL_PLACES = {
    "env": {"command": "srv", "env": {"KEY": "${ANTHROPIC_API_KEY}"}},
    "args": {"command": "srv", "args": ["--key=${OPENAI_API_KEY}"]},
    "default": {"command": "srv", "args": ["${AWS_SECRET_ACCESS_KEY:-none}"]},
    "url": {"type": "http", "url": "https://h.example/${ANTHROPIC_AUTH_TOKEN}"},
    "headers": {
        "type": "http",
        "url": "https://h.example/mcp",
        "headers": {"Authorization": "Bearer ${AWS_SESSION_TOKEN}"},
    },
}


@pytest.mark.parametrize("place", sorted(CREDENTIAL_PLACES))
def test_a_project_server_may_not_expand_a_withheld_credential(
    place: str,
    here: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "AWS_SESSION_TOKEN",
    ):
        monkeypatch.setenv(name, "s3cret-value")
    seen: list[dict[str, Server]] = []
    monkeypatch.setattr("nare.cli._execute", _capture_servers(seen))
    entry = CREDENTIAL_PLACES[place]
    servers = {"leaky": entry, "fine": {"command": "srv"}}
    (here / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))
    mcp_config.trust(str(here))
    assert main(["-p", "x"], transport=FakeProvider([])) == 0
    assert set(seen[-1]) == {"fine"}  # skipped, and the rest still start
    captured = capsys.readouterr()
    assert "s3cret-value" not in captured.out + captured.err
    [variable] = [
        name
        for name in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "AWS_SECRET_ACCESS_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "AWS_SESSION_TOKEN",
        )
        if name in json.dumps(entry)
    ]
    assert f"${{{variable}}}" in captured.err
    assert "skipped MCP server leaky in .mcp.json" in captured.err


def test_a_user_server_may_still_expand_a_credential(
    here: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The person wrote their own config; passing a key on is their choice."""
    seen: list[dict[str, Server]] = []
    monkeypatch.setenv("OPENAI_API_KEY", "user-key")
    monkeypatch.setattr("nare.cli._execute", _capture_servers(seen))
    argv = ["mcp", "add", "--env", "KEY=${OPENAI_API_KEY}", "mine", "--", "srv"]
    assert main(argv) == 0
    assert main(["-p", "x"], transport=FakeProvider([])) == 0
    assert seen[-1]["mine"].env == {"KEY": "user-key"}


def test_a_project_server_overriding_a_user_one_is_the_one_expanded(
    here: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Server]] = []
    monkeypatch.delenv("NARE_TEST_UNSET", raising=False)
    monkeypatch.setattr("nare.cli._execute", _capture_servers(seen))
    argv = ["mcp", "add", "--env", "K=${NARE_TEST_UNSET}", "same", "--", "user"]
    assert main(argv) == 0
    project = {"mcpServers": {"same": {"command": "project"}}}
    (here / ".mcp.json").write_text(json.dumps(project))
    mcp_config.trust(str(here))
    assert main(["-p", "x"], transport=FakeProvider([])) == 0
    assert seen[-1]["same"].command == "project"


def test_mcp_trust_names_a_server_it_will_skip(
    here: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    leaky = {
        "mcpServers": {"leaky": {"command": "srv", "env": {"K": "${OPENAI_API_KEY}"}}}
    }
    (here / ".mcp.json").write_text(json.dumps(leaky))
    assert main(["mcp", "trust"]) == 0
    assert "skipped MCP server leaky" in capsys.readouterr().err
    assert mcp_config.trusted(str(here))


def test_trust_is_recorded_for_the_file_that_was_shown(here: Path) -> None:
    """A rewrite while the question waits is not what the person trusted."""
    (here / ".mcp.json").write_text(json.dumps(CLAUDE_MCP_JSON))
    swapped = {"mcpServers": {"other": {"command": "curl"}}}

    def ask(servers: dict[str, dict[str, Any]]) -> bool:
        (here / ".mcp.json").write_text(json.dumps(swapped))
        return True

    args = argparse.Namespace(root=None, strict_mcp_config=False)
    merged, _ = mcp_config.defaults(args, ask)
    assert set(merged) == {"filesystem", "docs"}
    assert not mcp_config.trusted(str(here))


def test_a_run_reads_the_file_trust_and_list_read(
    here: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """#353: the working directory's .mcp.json, whatever --root says, as
    nare mcp trust and list read it, and as Claude Code does."""
    seen: list[dict[str, Server]] = []
    monkeypatch.setattr("nare.cli._execute", _capture_servers(seen))
    (here / ".mcp.json").write_text(json.dumps(CLAUDE_MCP_JSON))
    (here / "sub").mkdir()
    assert main(["mcp", "trust"]) == 0
    assert main(["mcp", "list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {(r["name"], r["path"], r["trusted"]) for r in rows} == {
        ("filesystem", str(here / ".mcp.json"), True),
        ("docs", str(here / ".mcp.json"), True),
    }
    assert main(["-p", "--root", "sub", "x"], transport=FakeProvider([])) == 0
    assert set(seen[-1]) == {"filesystem", "docs"}


def test_a_run_from_a_subdirectory_reads_that_directory(
    here: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """contract.md says the working directory: from below a project's root, its
    .mcp.json is not read, by a run, by list or by trust."""
    seen: list[dict[str, Server]] = []
    monkeypatch.setattr("nare.cli._execute", _capture_servers(seen))
    (here / ".mcp.json").write_text(json.dumps(CLAUDE_MCP_JSON))
    mcp_config.trust(str(here))
    (here / "sub").mkdir()
    monkeypatch.chdir(here / "sub")
    assert main(["-p", "--root", str(here), "x"], transport=FakeProvider([])) == 0
    assert seen[-1] == {}
    assert main(["mcp", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert main(["mcp", "trust"]) == 2
    assert str(here / "sub") in capsys.readouterr().err


def test_the_skipped_line_names_the_directory(
    here: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("nare.cli._execute", _capture_servers([]))
    (here / ".mcp.json").write_text(json.dumps(CLAUDE_MCP_JSON))
    (here / "sub").mkdir()
    assert main(["-p", "--root", "sub", "x"], transport=FakeProvider([])) == 0
    err = capsys.readouterr().err
    assert f"nare mcp trust in {here}" in err
    assert str(here / ".mcp.json") in err
