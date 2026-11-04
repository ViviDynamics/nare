"""The turn countdown reaches the model only when it has tools to wrap up (#139).

A tool-free run's every turn is an answer, so "N turns left: wrap up" is an
instruction the caller did not write, ahead of the answer, that also makes
the request depend on the turn count. A watching caller still sees the count
as a `progress` event.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider, text_reply, tool_reply
from nare.cli import main
from nare.loop import run
from nare.mcp import parse_servers
from nare.session import Message, new_session
from nare.tools import Policy
from test_mcp import config

PERSON = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
}


def sent(messages: list[Message]) -> str:
    return json.dumps([block for message in messages for block in message.content])


def countdown(events: list[Any]) -> list[str]:
    return [e.text for e in events if e.type == "progress" and "turns left" in e.text]


async def test_a_tool_free_run_sends_no_countdown_but_still_reports_it() -> None:
    provider = FakeProvider([text_reply('{"age": 1}'), text_reply('{"name": "ada"}')])
    session = new_session("who?")

    events = [
        e
        async for e in run(
            session,
            transport=provider,
            policy=Policy(tools=frozenset()),
            schema=PERSON,
            max_turns=2,
        )
    ]

    assert session.status == "done"
    assert len(provider.calls) == 2
    for messages, tools in provider.calls:
        assert tools == []
        assert "turns left" not in sent(messages)
    assert countdown(events) == [
        "2 turns left: wrap up and summarise.",
        "1 turns left: wrap up and summarise.",
    ]


def test_the_cli_with_tools_none_sends_no_countdown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    schema = tmp_path / "person.json"
    schema.write_text(json.dumps(PERSON), encoding="utf-8")
    provider = FakeProvider([text_reply('{"name": "ada"}')])

    code = main(
        ["run", "--yes", "--jsonl", "--tools", "none", "--max-turns", "2",
         "--schema", str(schema), "go"],
        transport=provider,
    )  # fmt: skip

    assert code == 0
    assert "turns left" not in sent(provider.calls[0][0])
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert any(
        line.get("type") == "progress" and "2 turns left" in line["text"]
        for line in lines
    )


async def test_a_run_with_built_in_tools_still_sends_the_countdown() -> None:
    provider = FakeProvider(
        [tool_reply("read", {"path": "/missing"}), text_reply("done")]
    )
    session = new_session("go")

    events = [
        e
        async for e in run(
            session,
            transport=provider,
            policy=Policy(tools=frozenset({"read"})),
            max_turns=2,
        )
    ]

    assert "2 turns left: wrap up and summarise." in sent(provider.calls[0][0])
    assert "1 turns left: wrap up and summarise." in sent(provider.calls[1][0])
    assert len(countdown(events)) == 2


async def test_a_run_with_only_mcp_tools_still_sends_the_countdown(
    tmp_path: Path,
) -> None:
    path, _ = config(tmp_path)
    provider = FakeProvider([text_reply("done")])
    session = new_session("go")

    events = [
        e
        async for e in run(
            session,
            transport=provider,
            # MCP tools only: no built-in tool, as `--tools local__echo` makes.
            policy=Policy(
                tools=frozenset({"local__echo"}), pending=frozenset({"local__echo"})
            ),
            mcp_servers=parse_servers(json.loads(path.read_text())),
            max_turns=2,
        )
    ]

    assert [tool["name"] for tool in provider.calls[0][1]] == ["local__echo"]
    assert "2 turns left: wrap up and summarise." in sent(provider.calls[0][0])
    assert len(countdown(events)) == 1
