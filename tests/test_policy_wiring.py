"""The policy reaches the model, the session, and the command line."""

from __future__ import annotations

import json
from pathlib import Path

from fake_provider import FakeProvider, text_reply, tool_reply
from nare.cli import main
from nare.loop import run
from nare.session import dumps, loads, new_session
from nare.tools import Policy


async def drain(session: object, provider: FakeProvider, **kwargs: object) -> None:
    async for _ in run(session, transport=provider, **kwargs):  # type: ignore[arg-type]
        pass


async def test_the_model_is_offered_only_the_allowed_tools() -> None:
    provider = FakeProvider([text_reply("done")])
    session = new_session("go")

    await drain(session, provider, policy=Policy(tools=frozenset({"read"})))

    _, tools = provider.calls[0]
    assert [tool["name"] for tool in tools] == ["read"]


async def test_a_refused_tool_leaves_the_session_running(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            tool_reply("write", {"path": str(tmp_path / "a.txt"), "content": "x"}),
            text_reply("understood"),
        ]
    )
    session = new_session("go")

    await drain(session, provider, policy=Policy(tools=frozenset({"read"})))

    assert session.status == "done"
    results = [
        b for m in session.messages for b in m.content if b.get("type") == "tool_result"
    ]
    assert results[0]["is_error"] is True
    assert not (tmp_path / "a.txt").exists()


async def test_a_session_with_no_tools_finishes_in_one_turn() -> None:
    provider = FakeProvider([text_reply("just an answer")])
    session = new_session("go")

    await drain(session, provider, policy=Policy(tools=frozenset()))

    _, tools = provider.calls[0]
    assert tools == []
    assert session.status == "done"
    assert session.turns == 1


async def test_the_session_records_the_policy_in_force(tmp_path: Path) -> None:
    provider = FakeProvider([text_reply("done")])
    session = new_session("go")

    await drain(
        session, provider, policy=Policy(tools=frozenset({"read"}), root=tmp_path)
    )

    assert session.policy == {"tools": ["read"], "root": str(tmp_path)}
    assert loads(dumps(session)).policy == session.policy


def test_the_cli_refuses_an_unknown_tool_name(capsys: object) -> None:
    code = main(["run", "go", "--yes", "--tools", "read,telepathy"])

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert code == 2
    assert captured.out == ""
    assert "unknown tool" in captured.err


def test_the_cli_refuses_a_root_that_is_not_a_directory(
    tmp_path: Path, capsys: object
) -> None:
    missing = tmp_path / "nope"

    code = main(["run", "go", "--yes", "--root", str(missing)])

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert code == 2
    assert captured.out == ""
    assert "not a directory" in captured.err


def test_the_cli_passes_the_policy_through_to_the_session(
    tmp_path: Path, capsys: object
) -> None:
    session_path = tmp_path / "s.json"

    code = main(
        [
            "run",
            "go",
            "--yes",
            "--jsonl",
            "--tools",
            "none",
            "--root",
            str(tmp_path),
            "--session",
            str(session_path),
        ],
        transport=FakeProvider([text_reply("done")]),
    )

    assert code == 0
    capsys.readouterr()  # type: ignore[attr-defined]
    assert json.loads(session_path.read_text(encoding="utf-8"))["policy"] == {
        "tools": [],
        "root": str(tmp_path),
    }
