"""Added roots (--add-root): more directories the file tools may resolve into.

`--root` keeps its meaning, the base for relative paths and bash's working
directory. An added root only widens where an absolute path may land, under the
same symlink and `..` rules as the root.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fake_provider import FakeProvider, text_reply, tool_reply
from nare import sandbox
from nare.cli import build_parser, main, policy_from_args
from nare.contract import describe
from nare.loop import run
from nare.reads import build as build_reads
from nare.sandbox import Availability
from nare.session import Session, new_session
from nare.tools import Policy, dispatch
from nare.transport import ToolCall


def call(name: str, **args: object) -> ToolCall:
    return ToolCall(id="call_1", name=name, args=dict(args))


@pytest.fixture
def dirs(tmp_path: Path) -> dict[str, Path]:
    """The root A, an added root B beside it, and C, which is neither."""
    made = {name: tmp_path / name for name in ("A", "B", "C")}
    for path in made.values():
        path.mkdir()
    return made


def policy(dirs: dict[str, Path]) -> Policy:
    return Policy(root=dirs["A"], extra_roots=(dirs["B"],))


async def test_write_into_an_added_root_and_read_it_back(
    dirs: dict[str, Path],
) -> None:
    target = dirs["B"] / "x"

    wrote = await dispatch(call("write", path=str(target), content="hi"), policy(dirs))
    read = await dispatch(call("read", path=str(target)), policy(dirs))

    assert wrote["is_error"] is False
    assert target.read_text(encoding="utf-8") == "hi"
    assert read["is_error"] is False
    assert read["content"] == "hi"


async def test_edit_into_an_added_root(dirs: dict[str, Path]) -> None:
    target = dirs["B"] / "x"
    target.write_text("old", encoding="utf-8")

    result = await dispatch(
        call("edit", path=str(target), old="old", new="new"), policy(dirs)
    )

    assert result["is_error"] is False
    assert target.read_text(encoding="utf-8") == "new"


@pytest.mark.parametrize("tool", ["read", "write", "edit"])
async def test_a_path_in_neither_root_is_still_refused(
    tool: str, dirs: dict[str, Path]
) -> None:
    outside = dirs["C"] / "x"
    outside.write_text("secret", encoding="utf-8")
    args: dict[str, object] = {"path": str(outside)}
    if tool == "write":
        args["content"] = "x"
    if tool == "edit":
        args |= {"old": "secret", "new": "x"}

    result = await dispatch(call(tool, **args), policy(dirs))

    assert result["is_error"] is True
    assert "outside the root" in result["content"]
    assert outside.read_text(encoding="utf-8") == "secret"


async def test_dot_dot_out_of_an_added_root_is_refused(dirs: dict[str, Path]) -> None:
    sneaky = f"{dirs['B']}/../C/x"

    result = await dispatch(call("write", path=sneaky, content="x"), policy(dirs))

    assert result["is_error"] is True
    assert not (dirs["C"] / "x").exists()


async def test_a_symlink_in_an_added_root_pointing_outside_both_is_refused(
    dirs: dict[str, Path],
) -> None:
    (dirs["C"] / "secret.txt").write_text("secret", encoding="utf-8")
    (dirs["B"] / "link.txt").symlink_to(dirs["C"] / "secret.txt")

    result = await dispatch(
        call("read", path=str(dirs["B"] / "link.txt")), policy(dirs)
    )

    assert result["is_error"] is True
    assert "outside the root" in result["content"]


async def test_a_symlink_in_an_added_root_pointing_into_the_root_is_allowed(
    dirs: dict[str, Path],
) -> None:
    (dirs["A"] / "a.txt").write_text("in A", encoding="utf-8")
    (dirs["B"] / "link.txt").symlink_to(dirs["A"] / "a.txt")

    result = await dispatch(
        call("read", path=str(dirs["B"] / "link.txt")), policy(dirs)
    )

    assert result["is_error"] is False
    assert result["content"] == "in A"


async def test_a_relative_path_resolves_under_the_root_not_an_added_root(
    dirs: dict[str, Path],
) -> None:
    result = await dispatch(call("write", path="x", content="hi"), policy(dirs))

    assert result["is_error"] is False
    assert (dirs["A"] / "x").read_text(encoding="utf-8") == "hi"
    assert not (dirs["B"] / "x").exists()


async def test_bash_still_runs_in_the_root(dirs: dict[str, Path]) -> None:
    result = await dispatch(call("bash", command="pwd"), policy(dirs))

    assert result["is_error"] is False
    assert result["content"].splitlines()[-1] == str(dirs["A"].resolve())


@pytest.mark.parametrize("tool", ["list", "search"])
async def test_navigation_reaches_an_added_root(
    tool: str, dirs: dict[str, Path]
) -> None:
    (dirs["B"] / "found.txt").write_text("needle\n", encoding="utf-8")
    args: dict[str, object] = {"path": str(dirs["B"])}
    if tool == "search":
        args["pattern"] = "needle"

    result = await dispatch(call(tool, **args), policy(dirs))

    assert result["is_error"] is False
    assert "found.txt" in result["content"]


async def test_search_results_in_an_added_root_are_paths_read_accepts(
    dirs: dict[str, Path],
) -> None:
    (dirs["B"] / "found.txt").write_text("needle\n", encoding="utf-8")

    found = await dispatch(
        call("search", path=str(dirs["B"]), pattern="needle"), policy(dirs)
    )
    path = found["content"].splitlines()[1].split(":")[0]
    read = await dispatch(call("read", path=path), policy(dirs))

    assert read["is_error"] is False
    assert read["content"].startswith("needle")


def test_an_added_root_needs_a_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--add-root needs --root"):
        Policy(extra_roots=(tmp_path,))


def test_the_policy_records_the_added_roots(dirs: dict[str, Path]) -> None:
    recorded = policy(dirs).recorded()

    assert recorded["root"] == str(dirs["A"])
    assert recorded["extra_roots"] == [str(dirs["B"])]


def test_the_policy_records_no_added_roots_key_without_one(tmp_path: Path) -> None:
    assert "extra_roots" not in Policy(root=tmp_path).recorded()


# Under --bash-sandbox an added root is writable like the root.


@pytest.fixture
def available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sandbox, "availability", lambda: Availability(mechanism="seatbelt", reason="")
    )


def test_seatbelt_lets_bash_write_into_an_added_root(tmp_path: Path) -> None:
    root, added, scratch = tmp_path / "A", tmp_path / "B", tmp_path / "scratch"

    profile = sandbox.seatbelt_profile(
        root, scratch, (), network=False, writable=(added,)
    )

    assert f'(allow file-write* (subpath "{root}"))' in profile
    assert f'(allow file-write* (subpath "{added}"))' in profile


def test_seatbelt_denies_a_credential_after_the_added_root_allow(
    tmp_path: Path,
) -> None:
    added, secret = tmp_path / "B", tmp_path / "B-creds"

    profile = sandbox.seatbelt_profile(
        tmp_path / "A", tmp_path / "s", (secret,), network=False, writable=(added,)
    )

    allow = profile.index(f'(allow file-write* (subpath "{added}"))')
    assert profile.index(f'(deny file-write* (subpath "{secret}"))') > allow


def test_bwrap_binds_an_added_root_writable_before_the_masks(tmp_path: Path) -> None:
    secret = tmp_path / ".ssh"
    secret.mkdir()

    argv = sandbox.bwrap_argv(
        Path("/work/A"),
        Path("/tmp/nare-bash-x"),
        (secret,),
        network=False,
        command=["bash", "-c", "true"],
        writable=(Path("/work/B"),),
    )

    binds = [argv[i + 1 : i + 3] for i, a in enumerate(argv) if a == "--bind"]
    assert ["/work/B", "/work/B"] in binds
    last_bind = max(i for i, a in enumerate(argv) if a == "--bind")
    assert argv.index(str(secret)) > last_bind
    assert argv[argv.index("--chdir") + 1] == "/work/A"


def test_the_confinement_carries_the_added_roots(
    available: None, dirs: dict[str, Path]
) -> None:
    sandboxed = Policy(root=dirs["A"], extra_roots=(dirs["B"],), bash_sandbox=True)

    confine = sandboxed.confinement()

    assert confine is not None
    assert confine.root == dirs["A"].resolve()
    assert confine.writable == (dirs["B"].resolve(),)
    assert sandboxed.recorded()["bash_sandbox"]["writable"] == [
        str(dirs["A"].resolve()),
        str(dirs["B"].resolve()),
    ]


def test_an_added_root_that_contains_a_credential_is_refused(
    available: None, dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = dirs["B"] / ".ssh"
    secret.mkdir()
    monkeypatch.setattr(sandbox, "default_denied", lambda: (secret,))

    with pytest.raises(ValueError, match="--add-root .* contains the credential"):
        Policy(root=dirs["A"], extra_roots=(dirs["B"],), bash_sandbox=True)


def test_an_added_root_of_slash_is_refused_under_the_sandbox(
    available: None, dirs: dict[str, Path]
) -> None:
    with pytest.raises(ValueError, match="--add-root of /"):
        Policy(root=dirs["A"], extra_roots=(Path("/"),), bash_sandbox=True)


# The command line: --add-root PATH, repeatable.


def test_nare_run_writes_and_reads_in_an_added_root(
    dirs: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    target, refused = dirs["B"] / "x", dirs["C"] / "x"
    session_path = dirs["A"] / "s.json"

    code = main(
        [
            "run",
            "go",
            "--yes",
            "--jsonl",
            "--root",
            str(dirs["A"]),
            "--add-root",
            str(dirs["B"]),
            "--session",
            str(session_path),
        ],
        transport=FakeProvider(
            [
                tool_reply("write", {"path": str(target), "content": "hi"}),
                tool_reply("read", {"path": str(target)}, call_id="call_2"),
                tool_reply(
                    "write", {"path": str(refused), "content": "no"}, call_id="call_3"
                ),
                text_reply("done"),
            ]
        ),
    )

    capsys.readouterr()
    assert code == 0
    assert target.read_text(encoding="utf-8") == "hi"
    assert not refused.exists()
    saved = json.loads(session_path.read_text(encoding="utf-8"))["policy"]
    assert saved["extra_roots"] == [str(dirs["B"])]


def test_add_root_is_repeatable(dirs: dict[str, Path]) -> None:
    args = build_parser().parse_args(
        [
            "run",
            "--root",
            str(dirs["A"]),
            "--add-root",
            str(dirs["B"]),
            "--add-root",
            str(dirs["C"]),
            "x",
        ]
    )
    args.command_tools = []
    args.mcp_servers = {}

    assert policy_from_args(args).extra_roots == (dirs["B"], dirs["C"])


def test_add_root_without_root_exits_2_naming_both(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["run", "go", "--yes", "--add-root", str(tmp_path)],
        transport=FakeProvider([]),
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "--add-root" in captured.err
    assert "--root" in captured.err.replace("--add-root", "")


def test_an_added_root_that_is_not_a_directory_exits_2(
    dirs: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    missing = dirs["A"].parent / "nope"

    code = main(
        ["run", "go", "--yes", "--root", str(dirs["A"]), "--add-root", str(missing)],
        transport=FakeProvider([]),
    )

    captured = capsys.readouterr()
    assert code == 2
    assert f"--add-root {missing} is not a directory" in captured.err


def test_the_contract_lists_the_flag() -> None:
    assert describe()["flags"]["--add-root"] == {"takes_value": True, "choices": None}


def test_the_contract_says_an_added_root_is_writable_under_the_sandbox() -> None:
    assert "each --add-root" in describe()["bash_sandbox"]["writable"]


# Read requirements reach an added root, as the read tool does.


def _cites(path: str) -> str:
    return json.dumps({"checks": [{"path": path}]})


_CITES_SCHEMA = {
    "type": "object",
    "properties": {
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
    },
    "required": ["checks"],
}


async def _drain(session: Session, provider: FakeProvider, **kwargs: object) -> None:
    async for _ in run(session, transport=provider, **kwargs):  # type: ignore[arg-type]
        pass


async def test_a_required_path_in_an_added_root_is_met_by_reading_it(
    dirs: dict[str, Path],
) -> None:
    target = dirs["B"] / "x"
    target.write_text("hi", encoding="utf-8")
    provider = FakeProvider(
        [
            text_reply("too early"),
            tool_reply("read", {"path": str(target)}),
            text_reply("answer"),
        ]
    )
    session = new_session("go")

    await _drain(session, provider, policy=policy(dirs), require_read=[str(target)])

    assert session.status == "done", session.error
    held = json.dumps([b for m in provider.calls[1][0] for b in m.content])
    assert f"read {target.resolve()}" in held


def test_a_required_path_in_neither_root_is_refused_at_startup(
    dirs: dict[str, Path],
) -> None:
    with pytest.raises(ValueError, match="outside the root"):
        build_reads(0, [str(dirs["C"] / "x")], root=dirs["A"], extra_roots=(dirs["B"],))


async def test_a_cited_path_in_an_added_root_is_asked_to_be_read(
    dirs: dict[str, Path],
) -> None:
    target = dirs["B"] / "x"
    target.write_text("hi", encoding="utf-8")
    provider = FakeProvider(
        [
            text_reply(_cites(str(target))),
            tool_reply("read", {"path": str(target)}),
            text_reply(_cites(str(target))),
        ]
    )
    session = new_session("go")

    await _drain(
        session,
        provider,
        policy=policy(dirs),
        schema=_CITES_SCHEMA,
        require_cited_reads=["/checks/*/path"],
    )

    assert session.status == "done", session.error
    sent = json.dumps([b for m in provider.calls[1][0] for b in m.content])
    assert "which the answer cites" in sent
    assert "outside the root" not in sent
