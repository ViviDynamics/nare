"""Constrained answers: tool turns stay free, the correction is constrained."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fake_provider import (
    FakeProvider,
    StreamingStructuredProvider,
    StrictArityProvider,
    StructuredProvider,
    text_reply,
    tool_reply,
)
from nare.events import Event
from nare.loop import run
from nare.session import Session, new_session
from nare.tools import Policy

PERSON: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
    "additionalProperties": False,
}


async def drain(session: Session, provider: Any, **kwargs: Any) -> list[Event]:
    return [event async for event in run(session, transport=provider, **kwargs)]


def constrained_events(events: list[Event]) -> list[Event]:
    return [e for e in events if "structured_output" in e.detail]


@pytest.fixture
def notes(tmp_path: Path) -> Policy:
    (tmp_path / "NOTES.txt").write_text("ada\n", encoding="utf-8")
    return Policy(tools=frozenset({"read"}), root=tmp_path)


async def test_tool_turns_stay_free_and_the_correction_is_constrained(
    notes: Policy,
) -> None:
    provider = StructuredProvider(
        [
            tool_reply("read", {"path": "NOTES.txt"}),
            text_reply('{"name": "ada"} trailing words'),
            text_reply('{"name": "ada"}'),
        ]
    )
    session = new_session("who?")

    events = await drain(
        session, provider, schema=PERSON, policy=notes, structured_output="json_schema"
    )

    assert session.status == "done"
    assert session.output == {"name": "ada"}
    assert provider.constraints == [None, None, PERSON]
    assert [bool(tools) for _, tools in provider.calls] == [True, True, False]
    [marker] = constrained_events(events)
    assert marker.type == "progress"
    assert marker.detail == {
        "structured_output": {"mode": "json_schema", "tools_withheld": True}
    }


async def test_a_valid_first_answer_never_asks_for_the_constraint(
    notes: Policy,
) -> None:
    provider = StructuredProvider(
        [tool_reply("read", {"path": "NOTES.txt"}), text_reply('{"name": "ada"}')]
    )
    session = new_session("who?")

    events = await drain(
        session, provider, schema=PERSON, policy=notes, structured_output="json_schema"
    )

    assert session.status == "done"
    assert provider.constraints == [None, None]
    assert constrained_events(events) == []


async def test_a_constrained_answer_is_still_validated(notes: Policy) -> None:
    provider = StructuredProvider([text_reply('{"age": 1}'), text_reply('{"name": 7}')])
    session = new_session("who?")

    await drain(
        session, provider, schema=PERSON, policy=notes, structured_output="json_schema"
    )

    assert provider.constraints == [None, PERSON]
    assert session.status == "error"
    assert session.stop_reason == "schema_violation"
    assert session.output is None
    assert "name: expected string, got integer" in (session.error or "")


async def test_off_calls_the_transport_exactly_as_before(notes: Policy) -> None:
    provider = StrictArityProvider(
        [
            tool_reply("read", {"path": "NOTES.txt"}),
            text_reply("not json"),
            text_reply('{"name": "ada"}'),
        ]
    )
    session = new_session("who?")

    await drain(session, provider, schema=PERSON, policy=notes)

    assert session.status == "done"
    assert all(tools for _, tools in provider.calls)


async def test_a_streamed_correction_is_constrained_too(notes: Policy) -> None:
    provider = StreamingStructuredProvider(
        [
            tool_reply("read", {"path": "NOTES.txt"}),
            text_reply("[1, 2"),
            text_reply('{"name": "ada"}'),
        ]
    )
    session = new_session("who?")

    events = await drain(
        session, provider, schema=PERSON, policy=notes, structured_output="json_schema"
    )

    assert session.status == "done"
    assert provider.constraints == [None, None, PERSON]
    assert [bool(tools) for _, tools in provider.calls] == [True, True, False]
    assert len(constrained_events(events)) == 1


async def test_a_resumed_run_offers_tools_again(notes: Policy) -> None:
    from nare.session import reopen

    provider = StructuredProvider([text_reply("nope"), text_reply("still nope")])
    session = new_session("who?")
    await drain(
        session, provider, schema=PERSON, policy=notes, structured_output="json_schema"
    )
    assert session.stop_reason == "schema_violation"

    reopen(session, "try again")
    again = StructuredProvider([text_reply('{"name": "ada"}')])
    await drain(
        session, again, schema=PERSON, policy=notes, structured_output="json_schema"
    )

    assert session.status == "done"
    assert again.constraints == [None]
    assert again.calls[0][1]


async def test_a_run_offering_no_tools_constrains_every_turn() -> None:
    provider = StructuredProvider([text_reply('{"name": "ada"}')])
    session = new_session("who?")

    events = await drain(
        session,
        provider,
        schema=PERSON,
        policy=Policy(tools=frozenset()),
        structured_output="json_schema",
    )

    assert session.status == "done"
    assert provider.constraints == [PERSON]
    [marker] = constrained_events(events)
    assert marker.detail == {
        "structured_output": {"mode": "json_schema", "tools_withheld": False}
    }


async def test_a_transport_without_support_is_refused_before_any_turn() -> None:
    provider = FakeProvider([text_reply('{"name": "ada"}')])

    with pytest.raises(ValueError, match="does not support structured output"):
        await drain(
            new_session("who?"),
            provider,
            schema=PERSON,
            structured_output="json_schema",
        )

    assert provider.calls == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"structured_output": "json_schema"}, "requires a schema"),
        ({"schema": PERSON, "structured_output": "grammar"}, "unknown structured"),
    ],
)
async def test_structured_output_needs_a_schema_and_a_known_mode(
    kwargs: dict[str, Any], message: str
) -> None:
    provider = StructuredProvider([text_reply('{"name": "ada"}')])

    with pytest.raises(ValueError, match=message):
        await drain(new_session("who?"), provider, **kwargs)

    assert provider.calls == []
