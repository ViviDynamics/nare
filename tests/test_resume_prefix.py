"""A resume that changes the tool set or the system prompt drops signed thinking.

Signed thinking binds to the prefix it was made against (the system prompt and
the tools array). Sending it after either changed is rejected by the vendor, so
the resume drops the signed blocks and says what changed (issue 100, change 2).
Unsigned thinking is never sent and stays, as it always has.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider, text_reply
from nare.cli import main
from nare.loop import prefix_changes, run
from nare.mcp import ExternalTool
from nare.session import Message, Session, dumps, loads, new_session, save
from nare.tools import Policy, approve_all


def signed_history() -> list[Message]:
    return [
        Message("user", [{"type": "text", "text": "go"}]),
        Message(
            "assistant",
            [
                {"type": "thinking", "thinking": "signed", "signature": "sig"},
                {"type": "thinking", "thinking": "unsigned"},
                {"type": "text", "text": "reading"},
            ],
        ),
        Message("user", [{"type": "text", "text": "more"}]),
    ]


def session_with(tools: frozenset[str], system: str | None = None) -> Session:
    session = new_session("go")
    session.messages = signed_history()
    session.policy = Policy(tools=tools).recorded()
    session.system = system
    session.status = "working"
    return session


def signed_blocks(messages: list[Message]) -> int:
    return sum(
        1
        for message in messages
        for block in message.content
        if block.get("type") == "thinking" and block.get("signature")
    )


def test_prefix_changes_names_what_moved() -> None:
    before = Policy(tools=frozenset({"read"})).recorded()
    assert prefix_changes(before, Policy(tools=frozenset({"read"})).recorded()) == []
    assert prefix_changes(
        before, Policy(tools=frozenset({"read", "bash"})).recorded()
    ) == ["tools added bash"]
    assert prefix_changes(before, Policy(tools=frozenset({"bash"})).recorded()) == [
        "tools added bash and removed read"
    ]


def test_a_session_with_no_record_is_not_read_as_changed() -> None:
    assert prefix_changes({}, Policy(tools=frozenset({"read"})).recorded()) == []


def mcp_policy(schema: dict[str, Any]) -> Policy:
    async def call(args: dict[str, Any]) -> tuple[str, bool]:
        return "ok", False

    tool = ExternalTool(schema=schema, call=call)
    return Policy(tools=frozenset({"read", "lookup"}), external={"lookup": tool})


def test_a_changed_mcp_definition_with_the_same_name_is_a_change() -> None:
    old = {"name": "lookup", "input_schema": {"type": "object"}}
    new = {"name": "lookup", "input_schema": {"type": "object", "required": ["q"]}}
    recorded = mcp_policy(old).recorded()

    assert "external_sha256" in recorded
    assert prefix_changes(recorded, mcp_policy(old).recorded()) == []
    assert prefix_changes(recorded, mcp_policy(new).recorded()) == [
        "the definitions of MCP tools"
    ]


async def test_a_resume_with_new_tools_drops_signed_thinking_and_says_so() -> None:
    session = session_with(frozenset({"read"}))
    fake = FakeProvider([text_reply("with bash now")])

    events = [
        event
        async for event in run(
            session,
            transport=fake,
            approve=approve_all,
            policy=Policy(tools=frozenset({"read", "bash"})),
        )
    ]

    sent = fake.calls[0][0]
    assert signed_blocks(sent) == 0
    # Unsigned thinking and the visible text are kept.
    kinds = [b.get("type") for b in sent[1].content]
    assert kinds == ["thinking", "text"]
    [change] = [e for e in events if e.detail.get("tools")]
    assert change.detail["tools"]["changed"] == ["tools added bash"]
    assert change.detail["tools"]["thinking_dropped"] == 1
    assert "Signed thinking dropped from 1 blocks" in change.text
    assert session.policy["tools"] == ["bash", "read"]


async def test_a_resume_with_the_same_tools_keeps_signed_thinking() -> None:
    session = session_with(frozenset({"read"}))
    fake = FakeProvider([text_reply("same tools")])

    events = [
        event
        async for event in run(
            session,
            transport=fake,
            approve=approve_all,
            policy=Policy(tools=frozenset({"read"})),
        )
    ]

    assert signed_blocks(fake.calls[0][0]) == 1
    assert not [e for e in events if e.detail.get("tools")]


async def test_a_session_saved_before_the_record_keeps_its_thinking() -> None:
    session = session_with(frozenset({"read"}))
    session.policy = {}
    fake = FakeProvider([text_reply("no record")])

    _ = [
        event
        async for event in run(
            session,
            transport=fake,
            approve=approve_all,
            policy=Policy(tools=frozenset({"read", "bash"})),
        )
    ]

    assert signed_blocks(fake.calls[0][0]) == 1


def test_the_dropped_thinking_stays_dropped_after_a_save_and_load() -> None:
    session = session_with(frozenset({"read"}))
    session.messages[1].content = [{"type": "text", "text": "reading"}]

    assert signed_blocks(loads(dumps(session)).messages) == 0


@pytest.fixture
def systems(monkeypatch: pytest.MonkeyPatch) -> list[FakeProvider]:
    made: list[FakeProvider] = []

    def factory(provider: str, **kwargs: Any) -> FakeProvider:
        fake = FakeProvider([text_reply("done")])
        made.append(fake)
        return fake

    monkeypatch.setattr("nare.cli.make_transport", factory)
    return made


def test_a_resume_with_a_different_system_prompt_drops_signed_thinking(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], systems: list[FakeProvider]
) -> None:
    saved = tmp_path / "s.json"
    session = session_with(frozenset({"read"}), system="persona")
    save(session, saved)

    assert (
        main(
            [
                "run",
                "--yes",
                "--jsonl",
                "--tools",
                "read",
                "--resume",
                str(saved),
                "--system",
                "other persona",
                "and more",
            ]
        )
        == 0
    )

    assert signed_blocks(systems[0].calls[0][0]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [e["detail"] for e in lines if "system_prompt" in e.get("detail", {})] == [
        {"system_prompt": {"changed": True}}
    ]
    [dropped] = [e for e in lines if "signed_thinking" in e.get("detail", {})]
    assert dropped["detail"] == {"signed_thinking": {"dropped": 1, "changed": "system"}}
    assert "bound to the system prompt" in dropped["text"]


def two_servers(
    first: dict[str, Any], second: dict[str, Any]
) -> dict[str, ExternalTool]:
    async def call(args: dict[str, Any]) -> tuple[str, bool]:
        return "ok", False

    return {
        "alpha": ExternalTool(schema=first, call=call),
        "beta": ExternalTool(schema=second, call=call),
    }


def test_a_connected_tool_the_policy_does_not_offer_is_not_a_change() -> None:
    # --tools read: the lookup tool is connected but never sent, so its
    # definition changing is no change to the request's prefix.
    old = {"name": "lookup", "input_schema": {"type": "object"}}
    new = {"name": "lookup", "input_schema": {"type": "object", "required": ["q"]}}
    before = Policy(
        tools=frozenset({"read"}), external=mcp_policy(old).external
    ).recorded()
    now = Policy(
        tools=frozenset({"read"}), external=mcp_policy(new).external
    ).recorded()

    assert "external_sha256" not in before
    assert prefix_changes(before, now) == []


def test_reordered_mcp_tools_are_a_change() -> None:
    a = {"name": "alpha", "input_schema": {"type": "object"}}
    b = {"name": "beta", "input_schema": {"type": "object"}}
    names = frozenset({"alpha", "beta"})
    before = Policy(tools=names, external=two_servers(a, b)).recorded()
    reordered = {"beta": two_servers(a, b)["beta"], "alpha": two_servers(a, b)["alpha"]}
    now = Policy(tools=names, external=reordered).recorded()

    assert prefix_changes(before, now) == ["the definitions of MCP tools"]


async def test_a_change_with_no_signed_thinking_says_nothing_was_dropped() -> None:
    session = session_with(frozenset({"read"}))
    session.messages[1].content = [{"type": "text", "text": "reading"}]
    fake = FakeProvider([text_reply("ok")])

    events = [
        event
        async for event in run(
            session,
            transport=fake,
            approve=approve_all,
            policy=Policy(tools=frozenset({"read", "bash"})),
        )
    ]

    [change] = [e for e in events if e.detail.get("tools")]
    assert change.detail["tools"]["thinking_dropped"] == 0
    assert change.text.endswith("No signed thinking to drop")
