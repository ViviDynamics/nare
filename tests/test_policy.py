"""The tool policy: an allowlist, and a root the file tools may not leave."""

from __future__ import annotations

from pathlib import Path

import pytest

from nare.tools import Policy, dispatch
from nare.transport import ToolCall


def call(name: str, **args: object) -> ToolCall:
    return ToolCall(id="call_1", name=name, args=dict(args))


async def test_an_allowed_tool_runs(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")

    result = await dispatch(
        call("read", path=str(target)), Policy(tools=frozenset({"read"}))
    )

    assert result["is_error"] is False
    assert result["content"] == "hello"


async def test_a_tool_outside_the_allowlist_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"

    result = await dispatch(
        call("write", path=str(target), content="x"), Policy(tools=frozenset({"read"}))
    )

    assert result["is_error"] is True
    assert "not allowed" in result["content"]
    assert not target.exists()


def test_schemas_carry_only_the_allowed_tools() -> None:
    names = {
        schema["name"] for schema in Policy(tools=frozenset({"read", "ask"})).schemas()
    }

    assert names == {"read", "ask"}


def test_an_unknown_tool_name_is_refused_when_the_policy_is_built() -> None:
    with pytest.raises(ValueError, match="unknown tool"):
        Policy(tools=frozenset({"read", "telepathy"}))


@pytest.mark.parametrize("tool", ["read", "write", "edit"])
async def test_a_path_outside_the_root_is_refused(tool: str, tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    args: dict[str, object] = {"path": str(outside)}
    if tool == "write":
        args["content"] = "x"
    if tool == "edit":
        args |= {"old": "secret", "new": "x"}

    result = await dispatch(call(tool, **args), Policy(root=root))

    assert result["is_error"] is True
    assert "outside the root" in result["content"]
    assert outside.read_text(encoding="utf-8") == "secret"


async def test_a_symlink_pointing_out_of_the_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (root / "link.txt").symlink_to(outside)

    result = await dispatch(
        call("read", path=str(root / "link.txt")), Policy(root=root)
    )

    assert result["is_error"] is True
    assert "outside the root" in result["content"]


async def test_a_path_inside_the_root_is_allowed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()

    result = await dispatch(
        call("write", path=str(root / "new" / "a.txt"), content="hi"), Policy(root=root)
    )

    assert result["is_error"] is False
    assert (root / "new" / "a.txt").read_text(encoding="utf-8") == "hi"


async def test_bash_runs_in_the_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()

    result = await dispatch(call("bash", command="pwd"), Policy(root=root))

    assert result["is_error"] is False
    assert str(root.resolve()) in result["content"]


async def test_an_approval_refusal_still_wins_over_an_allowed_tool(
    tmp_path: Path,
) -> None:
    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")

    result = await dispatch(
        call("read", path=str(target)), Policy(), approve=lambda *_: False
    )

    assert result["is_error"] is True
    assert "not approved" in result["content"]


def test_the_default_policy_allows_every_tool_and_sets_no_root() -> None:
    policy = Policy()

    assert {schema["name"] for schema in policy.schemas()} == {
        "read",
        "list",
        "search",
        "write",
        "edit",
        "bash",
        "ask",
    }
    assert policy.root is None


def test_the_policy_records_what_was_in_force() -> None:
    recorded = Policy(tools=frozenset({"read"}), root=Path("/tmp")).recorded()

    assert recorded == {"tools": ["read"], "root": "/tmp"}


@pytest.mark.parametrize("tool", ["list", "search"])
async def test_navigation_outside_the_root_is_refused(
    tool: str, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("needle", encoding="utf-8")

    result = await dispatch(call(tool, path=str(tmp_path)), Policy(root=root))

    assert result["is_error"] is True
    assert "outside the root" in result["content"]
    assert "secret" not in result["content"]


@pytest.mark.parametrize("tool", ["list", "search"])
async def test_navigation_through_a_symlink_out_of_the_root_is_refused(
    tool: str, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("needle", encoding="utf-8")
    (root / "escape").symlink_to(outside)

    result = await dispatch(call(tool, path="escape"), Policy(root=root))

    assert result["is_error"] is True
    assert "outside the root" in result["content"]


@pytest.mark.parametrize("args", [{}, {"path": None}, {"path": ""}])
async def test_list_defaults_to_the_root(
    args: dict[str, object], tmp_path: Path
) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")

    result = await dispatch(call("list", **args), Policy(root=tmp_path))

    assert result["is_error"] is False
    assert result["content"].splitlines() == [
        ".: entries 1-1 of 1",
        "a.txt  (file, 1 bytes)",
    ]


async def test_search_results_are_paths_read_accepts(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("needle\n", encoding="utf-8")
    policy = Policy(root=tmp_path)

    found = await dispatch(call("search", path="sub", pattern="needle"), policy)
    path = found["content"].splitlines()[1].split(":")[0]
    read = await dispatch(call("read", path=path), policy)

    assert path == "sub/a.txt"
    assert read["content"] == "needle"


async def test_a_model_cannot_move_the_base_of_navigation(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")

    result = await dispatch(call("list", base="/"), Policy(root=tmp_path))

    # Refused as a parameter list does not take, before anything is listed.
    assert result["is_error"] is True
    assert result["content"].startswith("list: unknown parameter base;")


async def test_without_a_root_navigation_is_relative_to_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("needle\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    listed = await dispatch(call("list", path="sub"), Policy())
    found = await dispatch(call("search", pattern="needle"), Policy())

    assert listed["content"].splitlines()[0] == "sub: entries 1-1 of 1"
    assert found["content"].splitlines()[1] == "sub/a.txt:1: needle"


@pytest.mark.parametrize("tool", ["list", "search"])
async def test_navigation_runs_only_when_the_allowlist_names_it(
    tool: str, tmp_path: Path
) -> None:
    result = await dispatch(
        call(tool, path=str(tmp_path)), Policy(tools=frozenset({"read"}))
    )

    assert result["is_error"] is True
    assert "not allowed" in result["content"]


# The bash environment: the provider credentials nare reads for its own
# requests stay out of every command the model writes, unless the policy opts in.
KEYS = {
    "ANTHROPIC_API_KEY": "sk-ant-withheld-1",
    "ANTHROPIC_AUTH_TOKEN": "withheld-2",
    "OPENAI_API_KEY": "sk-withheld-3",
}


def set_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in KEYS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("NARE_TEST_VISIBLE", "still-here")


async def test_bash_does_not_see_the_provider_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_keys(monkeypatch)

    result = await dispatch(call("bash", command="env"), Policy())

    assert result["is_error"] is False
    for name, value in KEYS.items():
        assert name not in result["content"]
        assert value not in result["content"]
    assert "NARE_TEST_VISIBLE=still-here" in result["content"]


async def test_the_policy_opt_in_gives_bash_the_provider_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_keys(monkeypatch)

    result = await dispatch(call("bash", command="env"), Policy(bash_credentials=True))

    for name, value in KEYS.items():
        assert f"{name}={value}" in result["content"]


async def test_the_model_cannot_opt_bash_in_through_its_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_keys(monkeypatch)

    result = await dispatch(call("bash", command="env", credentials=True), Policy())

    for value in KEYS.values():
        assert value not in result["content"]


async def test_run_bash_withholds_the_keys_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nare.tools import run_bash

    set_keys(monkeypatch)

    output = await run_bash("env")

    for value in KEYS.values():
        assert value not in output


def test_the_policy_records_the_opt_in_only_when_it_is_given() -> None:
    assert "bash_credentials" not in Policy().recorded()
    assert Policy(bash_credentials=True).recorded()["bash_credentials"] is True


def test_every_credential_a_transport_reads_is_withheld() -> None:
    """A credential variable added to a transport later is withheld too, or
    this fails: the list is the transports' own, not a copy of it.
    """
    import re

    import nare.transport
    from nare.transport import CREDENTIAL_ENV

    read: set[str] = set()
    for source in Path(nare.transport.__file__).parent.glob("*.py"):
        text = source.read_text(encoding="utf-8")
        read |= set(re.findall(r"environ(?:\.get\(|\[)\s*\"([A-Z0-9_]+)\"", text))
    credentials = {name for name in read if re.search("KEY|TOKEN|SECRET", name)}

    assert credentials
    assert credentials <= CREDENTIAL_ENV
    # Read by the Anthropic SDK itself, not by nare's code.
    assert {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"} <= CREDENTIAL_ENV
