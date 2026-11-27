"""The roots are pinned when the policy is built, and a replaced root stops the run.

`--root` and each `--add-root` are resolved once, when the Policy is made, and
every later use (the file tools' path check, bash's working directory, the
sandbox profile) reads those real paths. Before each tool call the pins are
checked: a root that no longer resolves to its pinned path, or is no longer the
same directory, was replaced under the run. That call is refused, so is every
later one, and the run ends `error` with `stop_reason` `root_replaced`.

These tests swap the roots from the test itself, standing in for any process
that could, so they run on every platform. tests/test_sandbox_live.py drives
the same swaps through the real OS sandbox.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider, calls_reply, text_reply, tool_reply
from nare import sandbox
from nare.cli import main
from nare.contract import describe
from nare.loop import run
from nare.sandbox import Availability
from nare.session import Message, new_session, reopen
from nare.tools import Policy, dispatch
from nare.transport import Reply, ToolCall

FILE_TOOLS = frozenset({"read", "write", "edit", "list", "search"})
SECRET = "OUTSIDE-SECRET-VALUE"


def call(name: str, **args: object) -> ToolCall:
    return ToolCall(id="call_1", name=name, args=dict(args))


def swap_for_link(path: Path, target: Path) -> Path:
    """Move `path` aside and leave a link to `target` in its place."""
    aside = path.with_name(path.name + ".aside")
    path.rename(aside)
    path.symlink_to(target, target_is_directory=True)
    return aside


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A folder no root reaches, holding a file the run must never read."""
    out = tmp_path / "outside"
    out.mkdir()
    (out / "secret").write_text(SECRET, encoding="utf-8")
    return out


def refused(result: dict[str, Any]) -> bool:
    return result["is_error"] is True and "replaced" in result["content"]


# The pins themselves.


def test_the_roots_are_resolved_when_the_policy_is_built(
    tmp_path: Path, outside: Path
) -> None:
    root, added = tmp_path / "R", tmp_path / "B"
    root.mkdir()
    added.mkdir()
    policy = Policy(tools=FILE_TOOLS, root=root, extra_roots=(added,))
    pinned = (root.resolve(), added.resolve())

    swap_for_link(root, outside)

    assert policy.real_root == pinned[0]
    assert policy.real_extra_roots == (pinned[1],)
    with pytest.raises(ValueError, match="outside the root"):
        policy.resolve("secret")


def test_the_pins_survive_a_copy_of_the_policy(tmp_path: Path, outside: Path) -> None:
    from dataclasses import replace

    root = tmp_path / "R"
    root.mkdir()
    policy = Policy(tools=FILE_TOOLS, root=root)
    swap_for_link(root, outside)

    # The loop copies the policy (image input, MCP tools) after it was built.
    copy = replace(policy, image_input=True)

    assert copy.real_root == policy.real_root
    assert copy.replaced_root() is not None
    assert policy.replaced_root() == copy.replaced_root()


def test_a_copy_with_another_root_pins_that_root(tmp_path: Path) -> None:
    from dataclasses import replace

    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()

    copy = replace(Policy(tools=FILE_TOOLS, root=first), root=second)

    assert copy.real_root == second.resolve()


def test_an_untouched_root_is_not_reported(tmp_path: Path) -> None:
    root, added = tmp_path / "R", tmp_path / "B"
    root.mkdir()
    added.mkdir()
    policy = Policy(tools=FILE_TOOLS, root=root, extra_roots=(added,))

    (root / "sub").mkdir()
    (root / "sub").rename(root / "moved")
    (added / "f").write_text("x", encoding="utf-8")

    assert policy.replaced_root() is None


def test_a_policy_without_a_root_has_nothing_to_pin() -> None:
    policy = Policy(tools=FILE_TOOLS)

    assert policy.real_root is None
    assert policy.real_extra_roots == ()
    assert policy.replaced_root() is None


def test_the_record_keeps_the_roots_as_given(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    recorded = Policy(tools=FILE_TOOLS, root=link).recorded()

    assert recorded["root"] == str(link)


# The file tools, for each way a root can be replaced.


async def test_a_root_swapped_for_a_link_refuses_the_file_tools(
    tmp_path: Path, outside: Path
) -> None:
    root = tmp_path / "R"
    root.mkdir()
    (root / "a").write_text("inside", encoding="utf-8")
    policy = Policy(tools=FILE_TOOLS, root=root)
    before = await dispatch(call("read", path="a"), policy)
    assert before["is_error"] is False

    swap_for_link(root, outside)

    read = await dispatch(call("read", path=str(root / "secret")), policy)
    relative = await dispatch(call("read", path="secret"), policy)
    wrote = await dispatch(call("write", path="f", content="x"), policy)
    listed = await dispatch(call("list", path="."), policy)

    for result in (read, relative, wrote, listed):
        assert refused(result), result
        assert SECRET not in result["content"]
    assert not (outside / "f").exists()


async def test_an_added_root_swapped_for_a_link_refuses_the_file_tools(
    tmp_path: Path, outside: Path
) -> None:
    root, added = tmp_path / "A", tmp_path / "B"
    root.mkdir()
    added.mkdir()
    (root / "a").write_text("inside", encoding="utf-8")
    policy = Policy(tools=FILE_TOOLS, root=root, extra_roots=(added,))

    swap_for_link(added, outside)

    read = await dispatch(call("read", path=str(added / "secret")), policy)
    wrote = await dispatch(call("write", path=str(added / "f"), content="x"), policy)
    # The run is over: even a path in the untouched root is refused now.
    untouched = await dispatch(call("read", path="a"), policy)

    for result in (read, wrote, untouched):
        assert refused(result), result
        assert SECRET not in result["content"]
    assert not (outside / "f").exists()


async def test_a_root_renamed_away_through_an_added_parent_refuses_the_file_tools(
    tmp_path: Path, outside: Path
) -> None:
    work = tmp_path / "work"
    app = work / "app"
    app.mkdir(parents=True)
    policy = Policy(tools=FILE_TOOLS, root=app, extra_roots=(work,))

    app.rename(work / "app2")
    app.symlink_to(outside, target_is_directory=True)

    read = await dispatch(call("read", path="secret"), policy)
    wrote = await dispatch(call("write", path="f", content="x"), policy)

    for result in (read, wrote):
        assert refused(result), result
        assert SECRET not in result["content"]
    assert not (outside / "f").exists()


async def test_a_root_moved_by_renaming_its_parent_refuses_the_file_tools(
    tmp_path: Path, outside: Path
) -> None:
    work = tmp_path / "work"
    root = work / "a" / "b"
    root.mkdir(parents=True)
    (outside / "b").mkdir()
    (outside / "b" / "secret").write_text(SECRET, encoding="utf-8")
    policy = Policy(tools=FILE_TOOLS, root=root, extra_roots=(work,))

    swap_for_link(work / "a", outside)

    read = await dispatch(call("read", path="secret"), policy)

    assert refused(read), read
    assert SECRET not in read["content"]


async def test_a_root_removed_refuses_the_file_tools(tmp_path: Path) -> None:
    root = tmp_path / "R"
    root.mkdir()
    policy = Policy(tools=FILE_TOOLS, root=root)

    root.rename(tmp_path / "old")
    wrote = await dispatch(call("write", path="f", content="x"), policy)

    assert refused(wrote), wrote
    assert not root.exists()


async def test_a_root_replaced_by_a_file_refuses_the_file_tools(
    tmp_path: Path,
) -> None:
    root = tmp_path / "R"
    root.mkdir()
    policy = Policy(tools=FILE_TOOLS, root=root)

    root.rename(tmp_path / "old")
    root.write_text("not a folder", encoding="utf-8")
    read = await dispatch(call("read", path=str(root)), policy)

    assert refused(read), read


async def test_a_folder_made_again_at_the_root_path_is_the_same_root(
    tmp_path: Path,
) -> None:
    # The path is pinned, not the inode: a folder made again there is confined
    # exactly as the first was, and some file systems do not keep a folder's
    # inode number steady.
    root = tmp_path / "R"
    root.mkdir()
    policy = Policy(tools=FILE_TOOLS, root=root)

    root.rename(tmp_path / "old")
    root.mkdir()
    wrote = await dispatch(call("write", path="f", content="x"), policy)

    assert wrote["is_error"] is False, wrote
    assert (root / "f").read_text(encoding="utf-8") == "x"
    assert policy.replaced_root() is None


async def test_a_replaced_root_stays_refused_after_it_is_put_back(
    tmp_path: Path, outside: Path
) -> None:
    root = tmp_path / "R"
    root.mkdir()
    policy = Policy(tools=FILE_TOOLS, root=root)
    aside = swap_for_link(root, outside)
    first = await dispatch(call("read", path="secret"), policy)

    root.unlink()
    aside.rename(root)
    again = await dispatch(call("write", path="f", content="x"), policy)

    assert refused(first)
    assert refused(again)
    assert not (root / "f").exists()


async def test_a_root_given_as_a_link_is_pinned_to_its_target(
    tmp_path: Path, outside: Path
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    policy = Policy(tools=FILE_TOOLS, root=link)

    first = await dispatch(call("write", path="f", content="one"), policy)
    # Pointing the link elsewhere later moves nothing: the target is pinned.
    link.unlink()
    link.symlink_to(outside, target_is_directory=True)
    second = await dispatch(call("write", path="g", content="two"), policy)
    read = await dispatch(call("read", path="secret"), policy)

    assert first["is_error"] is False
    assert second["is_error"] is False
    assert (real / "f").read_text(encoding="utf-8") == "one"
    assert (real / "g").read_text(encoding="utf-8") == "two"
    assert not (outside / "g").exists()
    assert read["is_error"] is True
    assert SECRET not in read["content"]


async def test_bash_runs_in_the_pinned_root(tmp_path: Path, outside: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    policy = Policy(tools=frozenset({"bash"}), root=link)
    link.unlink()
    link.symlink_to(outside, target_is_directory=True)

    result = await dispatch(call("bash", command="pwd -P; echo x > here"), policy)

    assert result["is_error"] is False, result
    assert str(real.resolve()) in result["content"]
    assert (real / "here").exists()
    assert not (outside / "here").exists()


async def test_a_swapped_root_refuses_bash_before_it_runs(
    tmp_path: Path, outside: Path
) -> None:
    root = tmp_path / "R"
    root.mkdir()
    policy = Policy(tools=frozenset({"bash"}), root=root)
    swap_for_link(root, outside)

    result = await dispatch(call("bash", command="echo x > f"), policy)

    assert refused(result), result
    assert not (outside / "f").exists()


async def test_a_root_swapped_while_the_call_waits_for_approval_is_refused(
    tmp_path: Path, outside: Path
) -> None:
    root = tmp_path / "R"
    root.mkdir()
    policy = Policy(tools=FILE_TOOLS | {"bash"}, root=root)

    def approve(tool: str, args: dict[str, Any]) -> bool:
        # A person can take minutes to answer; anything may move the root then.
        swap_for_link(root, outside)
        return True

    result = await dispatch(call("bash", command="echo x > f"), policy, approve)

    assert refused(result), result
    assert not (outside / "f").exists()


async def test_the_sandbox_profile_keeps_the_pinned_root(
    tmp_path: Path, outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sandbox, "availability", lambda: Availability(mechanism="seatbelt", reason="")
    )
    root, added = tmp_path / "R", tmp_path / "B"
    root.mkdir()
    added.mkdir()
    policy = Policy(root=root, extra_roots=(added,), bash_sandbox=True)
    pinned = (root.resolve(), added.resolve())

    swap_for_link(root, outside)
    swap_for_link(added, outside)
    confine = policy.confinement()

    assert confine is not None
    assert (confine.root, *confine.writable) == pinned
    assert policy.recorded()["bash_sandbox"]["writable"] == [str(p) for p in pinned]


# The run stops.


def lines(out: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def results(fake: FakeProvider, turn: int) -> list[dict[str, Any]]:
    """The tool results the model was sent before its turn `turn` (from 0)."""
    messages, _ = fake.calls[turn]
    content: list[dict[str, Any]] = messages[-1].content
    return content


def test_a_run_whose_root_is_replaced_stops_with_root_replaced(
    tmp_path: Path, outside: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "R"
    root.mkdir()
    # Without --bash-sandbox nothing stops bash from replacing the root; the
    # calls after it are what the pins refuse.
    swap = f"cd / && mv '{root}' '{root}.aside' && ln -s '{outside}' '{root}'"
    fake = FakeProvider(
        [
            calls_reply(
                ("bash", {"command": swap}),
                ("read", {"path": "secret"}),
                ("write", {"path": "f", "content": "x"}),
            ),
            # Never asked for: the run stops after the turn that replaced it.
            text_reply("done"),
        ]
    )

    saved = tmp_path / "session.json"

    code = main(
        ["run", "--yes", "--jsonl", "--root", str(root)]
        + ["--session", str(saved), "go"],
        transport=fake,
    )

    out = capsys.readouterr().out
    events = lines(out)
    result = events[-1]
    assert code == 1
    assert (result["status"], result["stop_reason"]) == ("error", "root_replaced")
    assert "--root" in result["error"] and "replaced" in result["error"]
    assert len(fake.calls) == 1
    assert root.is_symlink()
    assert not (outside / "f").exists()
    sent = [
        e["detail"]["tool_result"]
        for e in events
        if "tool_result" in e.get("detail", {})
    ]
    assert [r["is_error"] for r in sent] == [False, True, True]
    assert SECRET not in out
    assert SECRET not in saved.read_text(encoding="utf-8")


async def test_a_run_started_on_a_replaced_root_calls_no_model(
    tmp_path: Path, outside: Path
) -> None:
    root = tmp_path / "R"
    root.mkdir()
    policy = Policy(tools=FILE_TOOLS, root=root)
    swap_for_link(root, outside)
    fake = FakeProvider([tool_reply("read", {"path": "secret"}), text_reply("done")])
    session = new_session("go")

    [_ async for _ in run(session, transport=fake, policy=policy)]

    assert (session.status, session.stop_reason) == ("error", "root_replaced")
    assert fake.calls == []


async def test_a_later_prompt_in_the_same_process_stops_too(
    tmp_path: Path, outside: Path
) -> None:
    # A person's session (the TUI, or a line session) runs every prompt with
    # the policy it started with. Putting the root back does not reopen it.
    root = tmp_path / "R"
    root.mkdir()
    policy = Policy(tools=FILE_TOOLS, root=root)
    aside = swap_for_link(root, outside)
    fake = FakeProvider([text_reply("never asked")])
    session = new_session("go")
    [_ async for _ in run(session, transport=fake, policy=policy)]

    root.unlink()
    aside.rename(root)
    reopen(session, "try again")
    [_ async for _ in run(session, transport=fake, policy=policy)]

    assert (session.status, session.stop_reason) == ("error", "root_replaced")
    assert fake.calls == []


class SwapsWhileAnswering(FakeProvider):
    """Replaces the root while its last reply is in flight, as a process the
    run left running could."""

    def __init__(self, replies: list[Reply], root: Path, target: Path) -> None:
        super().__init__(replies)
        self.root, self.target = root, target

    async def turn(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        tool_choice: str | None = None,
    ) -> Reply:
        reply = await super().turn(messages, tools, tool_choice=tool_choice)
        if not reply.tool_calls:
            swap_for_link(self.root, self.target)
        return reply


async def test_a_root_replaced_before_the_answer_stops_the_run(
    tmp_path: Path, outside: Path
) -> None:
    root = tmp_path / "R"
    root.mkdir()
    policy = Policy(tools=FILE_TOOLS, root=root)
    fake = SwapsWhileAnswering(
        [tool_reply("write", {"path": "f", "content": "x"}), text_reply("done")],
        root,
        outside,
    )
    session = new_session("go")

    events = [e async for e in run(session, transport=fake, policy=policy)]

    assert (session.status, session.stop_reason) == ("error", "root_replaced")
    assert not any(e.type == "output" for e in events)
    assert (root.with_name("R.aside") / "f").exists()


def test_a_run_that_works_inside_its_roots_is_unaffected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, added = tmp_path / "R", tmp_path / "B"
    root.mkdir()
    added.mkdir()
    work = "mkdir -p sub && mv sub moved && echo one > moved/f && rm -r moved"
    fake = FakeProvider(
        [
            calls_reply(
                ("bash", {"command": work}),
                ("write", {"path": "a", "content": "one"}),
                ("write", {"path": str(added / "b"), "content": "two"}),
            ),
            calls_reply(
                ("edit", {"path": "a", "old": "one", "new": "uno"}),
                ("read", {"path": str(added / "b")}),
            ),
            text_reply("done"),
        ]
    )

    code = main(
        ["run", "--yes", "--jsonl", "--root", str(root)]
        + ["--add-root", str(added), "go"],
        transport=fake,
    )

    result = lines(capsys.readouterr().out)[-1]
    assert code == 0
    assert (result["status"], result["stop_reason"]) == ("done", "end_turn")
    assert (root / "a").read_text(encoding="utf-8") == "uno"
    assert (added / "b").read_text(encoding="utf-8") == "two"
    assert not any(r["is_error"] for r in results(fake, 1) + results(fake, 2))


def test_the_contract_lists_root_replaced_as_a_failure() -> None:
    kinds = {row["stop_reason"]: row for row in describe()["stop_reasons"]}

    assert kinds["root_replaced"]["kind"] == "failure"
    assert kinds["root_replaced"]["statuses"] == ["error"]


# The sandbox profile refuses to remove or rename a root.


def test_seatbelt_denies_removing_or_renaming_each_root(tmp_path: Path) -> None:
    work = tmp_path / "work"
    root, added, scratch = work / "a" / "b", work, tmp_path / "scratch"

    profile = sandbox.seatbelt_profile(
        root, scratch, (), network=False, writable=(added,)
    )

    deny = next(
        line
        for line in profile.splitlines()
        if line.startswith("(deny file-write-unlink")
    )
    # Each root, and a folder between two roots, which renaming would move the
    # inner root with. Nothing outside the roots: no write reaches it anyway.
    for path in (root, work / "a", work):
        assert f'(literal "{path}")' in deny
    assert f'(literal "{tmp_path}")' not in deny
    assert f'(literal "{scratch}")' not in deny
    assert profile.index(deny) > profile.index(f'(subpath "{added}")')


def test_the_profile_name_changes_with_its_rules() -> None:
    assert sandbox.PROFILE == "confined-2"


def test_bwrap_binds_outer_roots_first_and_pins_the_folders_between() -> None:
    argv = sandbox.bwrap_argv(
        Path("/work/a/b"),
        Path("/tmp/nare-bash-x"),
        (),
        network=False,
        command=["bash", "-c", "true"],
        writable=(Path("/work"),),
    )

    binds = [argv[i + 1 : i + 3] for i, a in enumerate(argv) if a == "--bind"]
    # A mount point cannot be removed or renamed, so binding each root, and the
    # folder between, after the root around it keeps them all in place.
    assert binds == [
        ["/tmp/nare-bash-x", "/tmp/nare-bash-x"],
        ["/work", "/work"],
        ["/work/a", "/work/a"],
        ["/work/a/b", "/work/a/b"],
    ]
    assert argv[argv.index("--chdir") + 1] == "/work/a/b"


def test_doc_names_the_pinned_roots() -> None:
    doc = (Path(__file__).parent.parent / "docs" / "contract.md").read_text(
        encoding="utf-8"
    )
    assert "`root_replaced`" in doc
    assert "pinned" in doc


def test_nothing_here_depends_on_the_test_runner_cwd(tmp_path: Path) -> None:
    # A relative root is resolved once, against the directory nare started in.
    root = tmp_path / "R"
    root.mkdir()
    here = os.getcwd()
    os.chdir(tmp_path)
    try:
        policy = Policy(tools=FILE_TOOLS, root=Path("R"))
    finally:
        os.chdir(here)

    assert policy.real_root == root.resolve()
    assert policy.resolve("f") == root.resolve() / "f"


# Review round 1.


async def test_a_schema_answer_after_the_swap_is_not_kept_as_output(
    tmp_path: Path, outside: Path
) -> None:
    root = tmp_path / "R"
    root.mkdir()
    policy = Policy(tools=FILE_TOOLS, root=root)
    fake = SwapsWhileAnswering(
        [tool_reply("write", {"path": "f", "content": "x"}), text_reply('{"a": 1}')],
        root,
        outside,
    )
    session = new_session("go")

    [
        _
        async for _ in run(
            session, transport=fake, policy=policy, schema={"type": "object"}
        )
    ]

    assert (session.status, session.stop_reason) == ("error", "root_replaced")
    assert session.output is None


def test_command_tools_run_in_the_pinned_root(
    tmp_path: Path, outside: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    tools = tmp_path / "tools.json"
    tools.write_text(
        json.dumps(
            {
                "where": {
                    "description": "Print the working directory.",
                    "input_schema": {"type": "object"},
                    "command": ["/bin/sh", "-c", "pwd -P"],
                }
            }
        ),
        encoding="utf-8",
    )
    # Pointing the link given as --root elsewhere leaves the pinned target,
    # which is untouched, so the run goes on, in the target.
    repoint = f"ln -sfn '{outside}' '{link}'"
    fake = FakeProvider(
        [
            calls_reply(("bash", {"command": repoint}), ("where", {})),
            text_reply("done"),
        ]
    )

    code = main(
        ["run", "--yes", "--jsonl", "--root", str(link)]
        + ["--command-tools", str(tools), "go"],
        transport=fake,
    )

    assert code == 0
    assert link.resolve() == outside.resolve()
    where = results(fake, 1)[1]
    assert where["is_error"] is False, where
    assert where["content"].strip() == str(real.resolve())


async def test_a_deferred_tool_search_after_the_swap_is_refused(
    tmp_path: Path, outside: Path
) -> None:
    import sys

    from nare import mcp_defer
    from nare.mcp import parse_servers

    server = str(Path(__file__).with_name("mcp_server.py"))
    mcp = parse_servers(
        {
            "local": {
                "command": sys.executable,
                "args": [server, str(tmp_path / "pid"), "--many", "3"],
                "timeout": 5,
            }
        }
    )
    root = tmp_path / "R"
    root.mkdir()
    swap = f"cd / && mv '{root}' '{root}.aside' && ln -s '{outside}' '{root}'"
    fake = FakeProvider(
        [
            calls_reply(
                ("bash", {"command": swap}),
                (mcp_defer.SEARCH, {"query": "echo"}),
                (mcp_defer.LOAD, {"names": ["local__echo"]}),
            ),
            text_reply("never asked"),
        ]
    )
    session = new_session("go")

    events = [
        e
        async for e in run(
            session,
            transport=fake,
            policy=Policy(tools=frozenset({"bash"}), root=root),
            mcp_servers=mcp,
            mcp_allow_all=True,
            mcp_defer=True,
        )
    ]

    assert (session.status, session.stop_reason) == ("error", "root_replaced")
    sent = session.messages[-1].content
    assert [r["is_error"] for r in sent] == [False, True, True]
    assert all("replaced" in r["content"] for r in sent[1:])
    assert session.mcp_loaded == {}
    assert not any("MCP tools loaded" in e.text for e in events)


def test_a_call_refused_for_a_replaced_root_is_not_counted(
    tmp_path: Path, outside: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "R"
    root.mkdir()
    swap = f"cd / && mv '{root}' '{root}.aside' && ln -s '{outside}' '{root}'"
    fake = FakeProvider(
        [
            calls_reply(
                ("bash", {"command": swap}),
                ("read", {"path": "secret"}),
                ("read", {"path": "secret"}),
            )
        ]
    )

    main(
        ["run", "--yes", "--jsonl", "--root", str(root)]
        + ["--max-tool-calls", "2", "go"],
        transport=fake,
    )

    result = lines(capsys.readouterr().out)[-1]
    assert result["stop_reason"] == "root_replaced"
    assert result["budget"]["used_tool_calls"] == 1


def test_a_root_made_after_the_policy_is_held_to_being_a_directory(
    tmp_path: Path,
) -> None:
    # A library caller may name a folder before making it.
    root = tmp_path / "later"
    policy = Policy(tools=FILE_TOOLS, root=root)
    root.mkdir()
    assert policy.replaced_root() is None

    root.rmdir()
    root.write_text("a file now", encoding="utf-8")

    assert policy.replaced_root() is not None
