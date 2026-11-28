"""Claude Code's and Codex's flag spellings, accepted as aliases (#326).

Each alias is its own parser option, so it is advertised under `flags` and in
the flag table, and no existing flag's key changes. After parsing, an alias
sets its canonical option, so the run is the canonical flag's run.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider, text_reply, tool_reply
from nare.cli import build_parser, main, parse_args
from nare.contract import describe

SCHEMA = '{"type": "object", "properties": {"a": {"type": "string"}}}'


def resolved(*argv: str) -> dict[str, Any]:
    """The run's options after aliases are resolved, without the alias slots."""
    args = parse_args(list(argv))
    return {k: v for k, v in vars(args).items() if not k.startswith("alias_")}


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("NARE_PROVIDER", "NARE_MODEL", "NARE_BASE_URL", "NARE_BUDGET_USD"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sys.txt").write_text("be brief")
    (tmp_path / "schema.json").write_text(SCHEMA)
    (tmp_path / "extra").mkdir()
    return tmp_path


ALIASES = [
    (["--append-system-prompt", "x"], ["--append-system", "x"]),
    (["--append-system-prompt-file", "sys.txt"], ["--append-system-file", "sys.txt"]),
    (["--system-prompt", "x"], ["--system", "x"]),
    (["--system-prompt-file", "sys.txt"], ["--system-file", "sys.txt"]),
    (["--dangerously-skip-permissions"], ["--yes"]),
    (["--dangerously-bypass-approvals-and-sandbox"], ["--yes"]),
    (["--yolo"], ["--yes"]),
    (["--permission-mode", "bypassPermissions"], ["--yes"]),
    (["-a", "never"], ["--yes"]),
    (["--ask-for-approval", "never"], ["--yes"]),
    (["--disallowedTools", "Bash(rm:*)"], ["--bash-deny", "rm", "--bash-deny", "rm *"]),
    (["--permission-mode", "plan"], ["--tools", "ask,list,read,search"]),
    (["-s", "read-only"], ["--tools", "ask,list,read,search"]),
    (
        ["--sandbox", "workspace-write", "--root", "."],
        ["--bash-sandbox", "--root", "."],
    ),
    (["--sandbox", "danger-full-access"], []),
    (["--permission-mode", "default"], []),
    (["-a", "on-request"], []),
    (["--max-budget-usd", "2.5"], ["--budget-usd", "2.5"]),
    (["--json-schema", "schema.json"], ["--schema", "schema.json"]),
    (["--output-schema", "schema.json"], ["--schema", "schema.json"]),
    (["-m", "claude-opus-5"], ["--model", "claude-opus-5"]),
    (["--model", "openai/gpt-5"], ["--provider", "openai", "--model", "gpt-5"]),
    (["-m", "bedrock/claude-x"], ["--provider", "bedrock", "--model", "claude-x"]),
    (["--add-dir", "extra", "--root", "."], ["--add-root", "extra", "--root", "."]),
]


@pytest.mark.parametrize(("alias", "canonical"), ALIASES, ids=lambda a: " ".join(a))
def test_an_alias_is_its_canonical_flag(
    alias: list[str], canonical: list[str], files: Path
) -> None:
    run = ["run", "go"]
    assert resolved(*run, *alias) == resolved(*run, *canonical)


@pytest.mark.parametrize(
    ("argv", "approved", "allow"),
    [
        (["--allowedTools", "Read,Edit"], ["edit", "read"], []),
        (["--allowed-tools", "Read Edit"], ["edit", "read"], []),
        (["--allowed-tools", "read", "--allowed-tools", "bash"], ["bash", "read"], []),
        (
            ["--allowedTools", "Bash(git log:*),Read"],
            ["read"],
            ["git log", "git log *"],
        ),
        (["--allowedTools", "Bash(*)"], ["bash"], []),
    ],
    ids=lambda a: " ".join(a) if isinstance(a, list) else None,
)
def test_allowed_tools_approves_and_leaves_the_tool_set(
    argv: list[str], approved: list[str], allow: list[str], files: Path
) -> None:
    """Claude Code's flag approves (#350); --tools is what narrows."""
    args = resolved("run", "go", *argv)
    assert args["tools"] is None
    assert args["approved_tools"] == approved
    assert args["bash_allow"] == allow


def test_accept_edits_is_its_own_setting(files: Path) -> None:
    assert resolved("run", "go", "--permission-mode", "acceptEdits")["accept_edits"]
    assert not resolved("run", "go")["accept_edits"]


def test_a_model_with_an_explicit_provider_is_passed_whole(files: Path) -> None:
    """A proxy's model names carry a prefix; an explicit provider keeps it."""
    args = resolved("run", "go", "--provider", "openai", "--model", "openai/gpt-5")
    assert (args["provider"], args["model"]) == ("openai", "openai/gpt-5")
    args = resolved("run", "go", "--base-url", "http://p", "-m", "anthropic/claude")
    assert args["model"] == "anthropic/claude"


def test_a_model_with_a_provider_from_the_environment_is_passed_whole(
    files: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NARE_PROVIDER", "openai")
    args = resolved("run", "go", "--model", "anthropic/claude-x")
    assert (args["provider"], args["model"]) == ("openai", "anthropic/claude-x")


def test_a_model_prefix_that_is_not_a_provider_is_passed_whole(files: Path) -> None:
    assert resolved("run", "go", "--model", "org/model")["model"] == "org/model"


def test_inline_json_schema(files: Path) -> None:
    args = parse_args(["run", "go", "--json-schema", SCHEMA])
    from nare.cli import schema_from_args

    assert schema_from_args(args) == json.loads(SCHEMA)


def test_add_dir_and_add_root_combine(files: Path) -> None:
    (files / "more").mkdir()
    args = resolved(
        "run", "go", "--root", ".", "--add-root", "extra", "--add-dir", "more"
    )
    assert args["add_root"] == ["extra", "more"]


CONFLICTS = [
    (["--append-system", "a", "--append-system-prompt", "b"], "--append-system"),
    (["--system", "a", "--system-prompt", "b"], "--system"),
    (["--system", "a", "--system-prompt-file", "sys.txt"], "--system"),
    (["--budget-usd", "1", "--max-budget-usd", "2"], "--budget-usd"),
    (["--schema", "schema.json", "--json-schema", SCHEMA], "--schema"),
    (["--json-schema", "schema.json", "--output-schema", SCHEMA], "--json-schema"),
    (["--tools", "bash", "--permission-mode", "plan"], "--tools"),
    (["--bash-sandbox", "--root", ".", "-s", "danger-full-access"], "--bash-sandbox"),
]


@pytest.mark.parametrize(("argv", "canonical"), CONFLICTS, ids=lambda a: str(a))
def test_conflicting_values_exit_2_naming_both(
    argv: list[str],
    canonical: str,
    files: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["run", "--yes", *argv, "go"], transport=FakeProvider([])) == 2
    err = capsys.readouterr().err
    assert canonical in err
    named = [a for a in argv if a.startswith("-") and a != canonical]
    assert any(flag in err for flag in named), err


def test_the_same_value_twice_is_not_a_conflict(files: Path) -> None:
    args = resolved("run", "go", "--system", "a", "--system-prompt", "a")
    assert args["system"] == "a"


def test_disallowed_tools_removes_a_built_in_tool(files: Path) -> None:
    args = resolved("run", "go", "--disallowedTools", "Bash,Write")
    assert set(args["tools"].split(",")) == {"read", "list", "search", "ask", "edit"}
    # MCP tools stay allowed, as with no --tools at all.
    assert args["mcp_allow_all"] is True


def test_yolo_approves_a_write_in_a_real_run(
    files: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = files / "out.txt"
    fake = FakeProvider(
        [
            tool_reply("write", {"path": str(target), "content": "hi"}),
            text_reply("done"),
        ]
    )
    assert main(["run", "--yolo", "--jsonl", "write it"], transport=fake) == 0
    assert target.read_text() == "hi"


def test_an_alias_is_still_refused_without_a_terminal(
    files: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the --yes spellings stand in for --yes; the rest are refused alike."""
    monkeypatch.setattr("nare.cli.stdin_is_terminal", lambda: False)
    for argv in (["--permission-mode", "acceptEdits"], ["-a", "on-request"]):
        assert main(["run", *argv, "go"], transport=FakeProvider([])) == 2
        assert capsys.readouterr().err == (
            "nare run refuses to start unattended without --yes\n"
        )


def test_accept_edits_in_print_mode_writes_but_denies_bash(
    files: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = files / "out.txt"
    fake = FakeProvider(
        [
            tool_reply("write", {"path": "out.txt", "content": "hi"}),
            tool_reply("bash", {"command": "touch made"}, call_id="call_2"),
            text_reply("done"),
        ]
    )
    code = main(["-p", "--permission-mode", "acceptEdits", "go"], transport=fake)
    assert code == 0
    assert target.read_text() == "hi"
    assert not (files / "made").exists()


def _run_parser() -> argparse.ArgumentParser:
    [sub] = [
        a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)
    ]
    run: argparse.ArgumentParser = sub.choices["run"]
    return run


def test_help_shows_each_alias_beside_its_canonical_flag() -> None:
    text = _run_parser().format_help()
    for canonical, aliases in (
        ("--yes", ("--dangerously-skip-permissions", "--yolo")),
        ("--append-system TEXT", ("--append-system-prompt",)),
        ("--schema SCHEMA", ("--json-schema", "--output-schema")),
        ("--model", ("-m",)),
        ("--add-root PATH", ("--add-dir",)),
        ("--budget-usd BUDGET_USD", ("--max-budget-usd",)),
    ):
        [line] = [
            line for line in text.splitlines() if line.startswith(f"  {canonical}")
        ]
        for alias in aliases:
            assert alias in line, (canonical, line)


def test_every_alias_is_advertised() -> None:
    flags = describe()["flags"]
    for name in (
        "--append-system-prompt",
        "--system-prompt-file",
        "--dangerously-skip-permissions",
        "--yolo",
        "--allowed-tools",
        "--disallowed-tools",
        "--permission-mode",
        "--ask-for-approval",
        "--sandbox",
        "--max-budget-usd",
        "--json-schema",
        "--output-schema",
        "--add-dir",
    ):
        assert name in flags, name
    aliases = describe()["flag_aliases"]
    assert aliases["--yolo"] == "--yes"
    assert aliases["--allowedTools"] == "--allowed-tools"  # approval (#350)
    assert aliases["--disallowedTools"] == "--disallowed-tools"
    assert aliases["-m"] == "--model"
    assert set(aliases) >= {"--append-system-prompt", "--add-dir", "--max-budget-usd"}
    # No existing flag's key moved.
    assert "--yes" in flags and "--model" in flags and "--tools" in flags


def test_the_flag_table_names_the_camel_case_spellings() -> None:
    doc = Path(__file__).parent.parent / "docs" / "contract.md"
    section = doc.read_text(encoding="utf-8").split("## Run flags", 1)[1]
    for spelling in ("--allowedTools", "--disallowedTools", "`-m`", "`-a`", "`-s`"):
        assert spelling in section, spelling
    assert re.search(r"`flag_aliases`", section)


@pytest.mark.parametrize(
    ("prefix", "dest", "value"),
    [
        (["--y"], "yes", True),
        (["--js"], "jsonl", True),
        (["--add", "extra", "--root", "."], "add_root", ["extra"]),
        (["--system-f", "sys.txt"], "system_file", "sys.txt"),
        (["--append-system-f", "sys.txt"], "append_system_file", "sys.txt"),
        (["--max-tu", "3"], "max_turns", 3),
    ],
)
def test_a_prefix_that_worked_before_the_aliases_still_works(
    prefix: list[str], dest: str, value: object, files: Path
) -> None:
    """argparse takes a unique prefix of a long option. The aliases match only
    in full, so none of them makes an old prefix ambiguous."""
    assert resolved("run", "go", *prefix)[dest] == value


def test_json_is_jsonl_in_run(files: Path) -> None:
    assert resolved("run", "go", "--json")["jsonl"] is True
