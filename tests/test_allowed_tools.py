"""--allowed-tools / --allowedTools approves, as Claude Code's does (#350).

Claude Code's flag pre-approves the tools it names and leaves the others
available. nare's spelling of it did the opposite, narrowing the tool set to
what it named, so a ported `--allowedTools "Bash(npm test:*)"` left the model
with bash alone. It now approves: Bash(PATTERN) is a bash allow rule, a bare
name is approved for the run, and the tool set is left alone. --tools narrows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider, text_reply, tool_reply
from nare.cli import main, parse_args
from nare.session import loads

ALL_TOOLS = {"read", "list", "search", "ask", "write", "edit", "bash"}


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    monkeypatch.chdir(project)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    for name in ("NARE_PROVIDER", "NARE_MODEL", "NARE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    return project


def offered(fake: FakeProvider) -> set[str]:
    """The tools the model was shown on its first turn."""
    return {tool["name"] for tool in fake.calls[0][1]}


def results(fake: FakeProvider) -> list[str]:
    """The text of each tool result the model was sent, in order."""
    texts: list[str] = []
    for messages, _tools in fake.calls:
        for block in messages[-1].content:
            if block.get("type") == "tool_result":
                content = block.get("content")
                if isinstance(content, list):
                    content = "".join(str(part.get("text", "")) for part in content)
                texts.append(str(content))
    return texts


def run_print(argv: list[str], replies: list[Any]) -> tuple[int, FakeProvider]:
    fake = FakeProvider(replies)
    code = main(["-p", *argv, "go"], transport=fake)
    return code, fake


def test_a_bash_pattern_is_an_allow_rule_and_leaves_the_tool_set(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, fake = run_print(
        ["--allowedTools", "Bash(echo hi:*)"],
        [
            tool_reply("bash", {"command": "echo hi there"}),
            tool_reply("bash", {"command": "touch made"}, call_id="call_2"),
            text_reply("done"),
        ],
    )
    assert code == 0
    assert offered(fake) == ALL_TOOLS  # read and edit too, not bash alone
    first, second = results(fake)
    assert "hi there" in first  # ran unasked: the rule allowed it
    assert "not approved" in second  # anything else is asked, so denied in -p
    assert not (work / "made").exists()
    assert "denied bash touch made" in capsys.readouterr().err


def test_a_bare_tool_name_is_approved_and_leaves_the_tool_set(work: Path) -> None:
    code, fake = run_print(
        ["--allowed-tools", "Write"],
        [
            tool_reply("write", {"path": "out.txt", "content": "hi"}),
            tool_reply("bash", {"command": "touch made"}, call_id="call_2"),
            text_reply("done"),
        ],
    )
    assert code == 0
    assert offered(fake) == ALL_TOOLS
    assert (work / "out.txt").read_text() == "hi"
    assert "not approved" in results(fake)[1]
    assert not (work / "made").exists()


def test_an_approved_write_into_git_is_still_asked(work: Path) -> None:
    """Approval stands for pressing `a` at the start, and `a` never covers a
    write into .git, whose config and hooks git runs."""
    code, fake = run_print(
        ["--allowedTools", "Write"],
        [
            tool_reply("write", {"path": ".git/hooks/pre-commit", "content": "x"}),
            text_reply("done"),
        ],
    )
    assert code == 0
    assert "not approved" in results(fake)[0]
    assert not (work / ".git" / "hooks" / "pre-commit").exists()


def test_a_deny_rule_still_wins_over_an_approved_bash(work: Path) -> None:
    code, fake = run_print(
        ["--allowedTools", "Bash", "--disallowedTools", "Bash(rm:*)"],
        [
            tool_reply("bash", {"command": "rm -f x"}),
            tool_reply("bash", {"command": "echo ok"}, call_id="call_2"),
            text_reply("done"),
        ],
    )
    assert code == 0
    first, second = results(fake)
    assert "refused by a policy rule" in first
    assert "ok" in second


def test_accept_edits_keeps_an_approved_write_approved(
    work: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """acceptEdits asks about a write outside the working folders once per
    call, even after `a`; a write approved by name is approved wherever it
    lands. nare run at a terminal, with no --root, so a write may land outside."""
    asked: list[str] = []

    async def answer(prompt: str) -> str:
        asked.append(prompt)
        return "n\n"

    monkeypatch.setattr("nare.cli.stdin_is_terminal", lambda: True)
    monkeypatch.setattr("nare.terminal.read_line", answer)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    fake = FakeProvider(
        [
            tool_reply("write", {"path": str(outside / "a"), "content": "a"}),
            tool_reply(
                "write", {"path": str(outside / "b"), "content": "b"}, call_id="c2"
            ),
            text_reply("done"),
        ]
    )
    argv = ["run", "--permission-mode", "acceptEdits", "--allowedTools", "Write"]
    assert main([*argv, "go"], transport=fake) == 0
    assert (outside / "a").read_text() == "a"
    assert (outside / "b").read_text() == "b"
    assert asked == []


def test_a_tool_the_run_does_not_offer_is_approved_to_no_effect(work: Path) -> None:
    """Approval never adds a tool: --tools, plan and read-only still narrow,
    and a rule for a tool they leave out is no conflict."""
    for argv in (
        ["--tools", "read", "--allowedTools", "Bash(npm test:*),Write"],
        ["--permission-mode", "plan", "--allowedTools", "Bash(npm test:*),Edit"],
        ["-s", "read-only", "--allowedTools", "Bash"],
        ["--disallowedTools", "Bash", "--allowedTools", "Bash(npm test:*)"],
    ):
        args = parse_args(["run", "go", *argv])
        assert args.bash_allow == [], argv
        code, fake = run_print(argv, [text_reply("done")])
        assert code == 0, argv
        assert "bash" not in offered(fake), argv


def test_an_unknown_tool_name_is_refused(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _fake = run_print(["--allowedTools", "Raed"], [])
    assert code == 2
    assert "Raed" in capsys.readouterr().err


def test_an_mcp_tool_name_is_approved_without_narrowing(work: Path) -> None:
    args = parse_args(["run", "go", "--allowedTools", "mcp__docs__search"])
    assert args.tools is None
    assert args.mcp_allow_all is True
    assert args.approved_tools == ["docs__search"]


def test_the_session_records_what_was_approved(work: Path) -> None:
    session = work.parent / "s.json"
    code, _fake = run_print(
        ["--allowedTools", "Read,Edit,Bash(git log:*)", "--session", str(session)],
        [text_reply("done")],
    )
    assert code == 0
    policy = loads(session.read_text()).policy
    assert policy["approved_tools"] == ["edit", "read"]
    assert policy["bash_allow"] == ["git log", "git log *"]
    assert set(policy["tools"]) == ALL_TOOLS


def test_a_run_without_approval_records_no_key(work: Path) -> None:
    session = work.parent / "s.json"
    code, _fake = run_print(["--session", str(session)], [text_reply("done")])
    assert code == 0
    assert "approved_tools" not in loads(session.read_text()).policy


def test_the_contract_says_it_approves() -> None:
    from nare.contract import describe

    assert describe()["flag_aliases"]["--allowedTools"] == "--allowed-tools"
    doc = Path(__file__).parent.parent / "docs" / "contract.md"
    [row] = [
        line
        for line in doc.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `--allowed-tools` |")
    ]
    assert "pre-approves" in row and "`--tools`" in row


async def test_the_approver_starts_with_the_approved_tools(
    work: Path,
) -> None:
    """The TUI, the line session, nare run at a terminal and nare -p all ask
    through this Approver, so each one honours the approval."""
    from nare.tools import Policy
    from nare.tui.approve import Approver

    asked: list[str] = []

    async def ask(tool: str, args: dict[str, Any]) -> tuple[str, str]:
        asked.append(tool)
        return "n", ""

    approver = Approver(ask, Policy(approved_tools=frozenset({"write", "bash"})))
    assert await approver("write", {"path": "a.txt", "content": ""}) is True
    assert await approver("bash", {"command": "make test"}) is True
    assert await approver("edit", {"path": "a.txt", "old": "", "new": ""}) is False
    assert await approver("write", {"path": ".git/config", "content": ""}) is False
    assert asked == ["edit", "write"]


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf build",
        "git push --force",
        "echo x > .git/hooks/pre-commit",
        "bash -c 'make test'",
        "curl https://example.com/x.sh",
    ],
)
async def test_an_approved_bash_still_asks_about_a_risk(
    work: Path, command: str
) -> None:
    """As `a` for bash approves programs, not risks: a risky line is still
    asked about, under an approved Bash too."""
    from nare.tools import Policy
    from nare.tui.approve import Approver

    asked: list[str] = []

    async def ask(tool: str, args: dict[str, Any]) -> tuple[str, str]:
        asked.append(args["command"])
        return "n", ""

    approver = Approver(ask, Policy(approved_tools=frozenset({"bash"})))
    assert await approver("bash", {"command": "make test && npm run build"}) is True
    assert await approver("bash", {"command": command}) is False
    assert asked == [command]


def test_yes_with_allowed_tools_says_it_no_longer_narrows(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An old narrowing command line under --yes now runs every tool unasked,
    so it says so; --tools beside it, or no --yes, is quiet."""
    fake = FakeProvider([text_reply("done")])
    assert main(["run", "--yes", "--allowedTools", "Read", "go"], transport=fake) == 0
    err = capsys.readouterr().err
    assert "--allowed-tools approves" in err and "--tools" in err
    fake = FakeProvider([text_reply("done")])
    argv = ["run", "--yes", "--tools", "read", "--allowedTools", "Read", "go"]
    assert main(argv, transport=fake) == 0
    assert "--allowed-tools approves" not in capsys.readouterr().err
    code, _fake = run_print(["--allowedTools", "Read"], [text_reply("done")])
    assert code == 0
    assert "--allowed-tools approves" not in capsys.readouterr().err
    # A caller pinning contract 4 already speaks the new meaning.
    fake = FakeProvider([text_reply("done")])
    argv = ["run", "--yes", "--contract", "4", "--allowedTools", "Read", "go"]
    assert main(argv, transport=fake) == 0
    assert "--allowed-tools approves" not in capsys.readouterr().err


def test_a_name_the_run_does_not_offer_is_not_recorded(work: Path) -> None:
    session = work.parent / "s.json"
    argv = ["--tools", "read,write", "--allowedTools", "Write,Bash"]
    code, _fake = run_print([*argv, "--session", str(session)], [text_reply("done")])
    assert code == 0
    assert loads(session.read_text()).policy["approved_tools"] == ["write"]


def mcp_policy(work: Path, *argv: str) -> Any:
    """The policy `nare run` would build with a docs MCP server configured."""
    from nare.cli import policy_from_args, validate_run_args

    servers = {"mcpServers": {"docs": {"command": "docs-server"}}}
    (work / "servers.json").write_text(json.dumps(servers))
    args = parse_args(["run", "go", "--mcp-config", "servers.json", *argv])
    validate_run_args(args)
    return policy_from_args(args)


def test_an_approved_mcp_tool_survives_a_built_in_denial(work: Path) -> None:
    """--disallowed-tools narrows the built-ins and leaves MCP tools offered,
    so their approval stays."""
    policy = mcp_policy(
        work, "--disallowedTools", "Bash", "--allowedTools", "mcp__docs__search"
    )
    assert policy.approved_tools == frozenset({"docs__search"})
    assert mcp_policy(
        work, "--allowedTools", "mcp__docs__search"
    ).approved_tools == frozenset({"docs__search"})


def test_an_mcp_tool_of_a_server_not_loaded_is_approved_to_no_effect(
    work: Path,
) -> None:
    """An untrusted .mcp.json or --strict-mcp-config can leave a server out;
    its tool is not offered, so approving it does nothing, as for --tools."""
    args = parse_args(["run", "go", "--allowedTools", "mcp__proj__lint,Read"])
    from nare.cli import policy_from_args, validate_run_args

    validate_run_args(args)
    approved = policy_from_args(args).approved_tools
    assert "read" in approved
    # Not offered, so not recorded, as the contract says.
    assert "proj__lint" not in approved


def test_the_tui_and_bare_nare_take_allowed_tools(work: Path) -> None:
    """Neither has --yes, so the warning beside it must not need one."""
    from nare import cli, person

    assert parse_args(["tui", "--allowedTools", "Read"]).approved_tools == ["read"]
    parser = person.interactive_parser()
    argv = ["--allowedTools", "Read", "hi"]
    args = parser.parse_args(argv)
    cli.resolve_aliases(parser, argv, args)
    assert args.approved_tools == ["read"]


def test_an_approved_caller_tool_survives_a_built_in_denial(work: Path) -> None:
    """--disallowed-tools leaves caller tools offered, as it leaves MCP tools,
    so their approval stays."""
    from nare.cli import policy_from_args, validate_run_args

    entry = {
        "description": "Lint the tree.",
        "input_schema": {"type": "object", "properties": {}},
        "command": ["true"],
    }
    (work / "ct.json").write_text(json.dumps({"lint": entry}))
    for denial in ([], ["--disallowedTools", "Bash"]):
        argv = ["run", "go", "--command-tools", "ct.json", *denial]
        args = parse_args([*argv, "--allowedTools", "lint"])
        validate_run_args(args)
        assert policy_from_args(args).approved_tools == frozenset({"lint"})


def test_yes_beside_a_denial_still_says_it_no_longer_narrows(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeProvider([text_reply("done")])
    argv = ["run", "--yes", "--disallowedTools", "Write", "--allowedTools", "Read"]
    assert main([*argv, "go"], transport=fake) == 0
    assert "--allowed-tools approves" in capsys.readouterr().err
