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
        "write",
        "edit",
        "bash",
        "ask",
    }
    assert policy.root is None


def test_the_policy_records_what_was_in_force() -> None:
    recorded = Policy(tools=frozenset({"read"}), root=Path("/tmp")).recorded()

    assert recorded == {"tools": ["read"], "root": "/tmp"}
