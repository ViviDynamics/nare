"""Constrained answers: tool turns stay free, the correction is constrained."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fake_provider import (
    FakeProvider,
    StreamingProvider,
    StreamingStructuredProvider,
    StrictArityProvider,
    StructuredProvider,
    text_reply,
    tool_reply,
)
from nare import Tool
from nare.events import Event
from nare.loop import run
from nare.schema import SchemaError
from nare.session import Session, Usage, new_session
from nare.tools import Policy
from nare.transport import Reply, StopReason, ToolCall

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
            text_reply('{"name": "ada"} {"name": "bob"}'),
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


async def test_a_repeated_key_is_a_correction_not_a_silent_answer(
    notes: Policy,
) -> None:
    provider = FakeProvider(
        [text_reply('{"name": "ada", "name": "bob"}'), text_reply('{"name": "ada"}')]
    )
    session = new_session("who?")

    await drain(session, provider, schema=PERSON, policy=notes)

    assert session.status == "done"
    assert session.output == {"name": "ada"}
    # The refusal reaches the model as the correction, not as a silent collapse.
    corrections = [str(m.content) for m in session.messages if m.role == "user"]
    assert any("duplicate key: name" in text for text in corrections)


async def test_a_check_error_reaches_the_model_as_the_next_correction(
    notes: Policy,
) -> None:
    from nare.schema import SchemaError

    def check(answer: Any) -> list[SchemaError]:
        if answer["name"] == "bob":
            return [SchemaError("name_unknown", ("name",), "no such person: bob")]
        return []

    provider = FakeProvider(
        [text_reply('{"name": "bob"}'), text_reply('{"name": "ada"}')]
    )
    session = new_session("who?")

    events = await drain(session, provider, schema=PERSON, policy=notes, check=check)

    assert session.status == "done"
    assert session.output == {"name": "ada"}
    corrections = [str(m.content) for m in session.messages if m.role == "user"]
    assert any("name: no such person: bob" in text for text in corrections)
    [progress] = [e for e in events if "schema correction" in e.text]
    assert progress.detail["errors"][0]["code"] == "name_unknown"
    assert progress.detail["errors"][0]["location"] == ["name"]


async def test_a_check_that_raises_ends_the_run_with_an_error_event(
    notes: Policy,
) -> None:
    def check(answer: Any) -> list[Any]:
        raise RuntimeError("lookup failed")

    session = new_session("who?")

    events = await drain(
        session,
        FakeProvider([text_reply('{"name": "ada"}')]),
        schema=PERSON,
        policy=notes,
        check=check,
    )

    assert session.status == "error"
    assert session.output is None
    assert session.error == "check raised RuntimeError: lookup failed"
    assert events[-1].type == "error"
    assert events[-1].text == session.error


async def test_a_check_runs_only_on_an_answer_that_passes_the_schema(
    notes: Policy,
) -> None:
    seen: list[Any] = []

    def check(answer: Any) -> list[Any]:
        seen.append(answer)
        return []

    provider = FakeProvider([text_reply('{"age": 1}'), text_reply('{"name": "ada"}')])

    await drain(new_session("who?"), provider, schema=PERSON, policy=notes, check=check)

    assert seen == [{"name": "ada"}]


async def test_a_check_without_a_schema_is_refused(notes: Policy) -> None:
    with pytest.raises(ValueError, match="check requires a schema"):
        await drain(
            new_session("who?"),
            FakeProvider([text_reply("{}")]),
            policy=notes,
            check=lambda answer: [],
        )


async def test_an_unexpected_property_correction_says_to_remove_the_key(
    notes: Policy,
) -> None:
    provider = FakeProvider(
        [
            text_reply('{"name": "ada", "note": "kept beside it"}'),
            text_reply('{"name": "ada"}'),
        ]
    )
    session = new_session("who?")

    await drain(session, provider, schema=PERSON, policy=notes)

    assert session.status == "done"
    corrections = [str(m.content) for m in session.messages if m.role == "user"]
    assert any('remove the key "note"' in text for text in corrections)


async def test_a_schema_violation_error_event_carries_the_structured_errors(
    notes: Policy,
) -> None:
    provider = FakeProvider([text_reply('{"age": 1}'), text_reply('{"name": 7}')])

    events = await drain(new_session("who?"), provider, schema=PERSON, policy=notes)

    final = events[-1]
    assert final.type == "error"
    assert [e["code"] for e in final.detail["errors"]] == ["type"]
    assert final.detail["errors"][0]["location"] == ["name"]


async def test_a_repeated_key_is_a_structured_duplicate_key_error(
    notes: Policy,
) -> None:
    provider = FakeProvider(
        [text_reply('{"name": "a", "name": "b"}'), text_reply('{"name": "a"}')]
    )

    events = await drain(new_session("who?"), provider, schema=PERSON, policy=notes)

    [progress] = [e for e in events if "schema correction" in e.text]
    assert progress.detail["errors"] == [
        {
            "code": "duplicate_key",
            "location": [],
            "message": "duplicate key: name",
            "guidance": None,
        }
    ]


def prose_tool_reply(
    text: str,
    name: str = "read",
    args: dict[str, Any] | None = None,
    *,
    stop_reason: StopReason = "tool_use",
) -> Reply:
    """A tool turn whose text is prose, not a JSON answer."""
    call_args = {"path": "NOTES.txt"} if args is None else args
    return Reply(
        content=[
            {"type": "text", "text": text},
            {"type": "tool_use", "id": "call_1", "name": name, "input": call_args},
        ],
        tool_calls=[ToolCall(id="call_1", name=name, args=call_args)],
        usage=Usage(input=10, output=5),
        stop_reason=stop_reason,
    )


def recording_check(seen: list[Any], raises_on: str | None = None) -> Any:
    def check(answer: Any) -> list[SchemaError]:
        seen.append(answer)
        if raises_on is not None and answer["name"] == raises_on:
            raise RuntimeError("lookup failed")
        return []

    return check


@pytest.mark.parametrize("streaming", [False, True], ids=["unstreamed", "streamed"])
async def test_a_tool_turn_with_prose_and_no_json_does_not_end_the_run(
    notes: Policy, streaming: bool
) -> None:
    # The #192 reproduction: prose and a tool call, no JSON, then a final answer.
    seen: list[Any] = []
    provider_type = StreamingProvider if streaming else FakeProvider
    provider = provider_type(
        [
            prose_tool_reply("Let me read the notes first."),
            text_reply('{"name": "ada"}'),
        ]
    )
    session = new_session("who?")

    await drain(
        session, provider, schema=PERSON, policy=notes, check=recording_check(seen)
    )

    assert session.status == "done", session.error
    assert session.output == {"name": "ada"}
    assert session.tool_calls == 1
    # The check runs on the final answer only, never on the tool turn.
    assert seen == [{"name": "ada"}]


@pytest.mark.parametrize("streaming", [False, True], ids=["unstreamed", "streamed"])
async def test_a_tool_turn_with_an_answer_is_not_checked(
    notes: Policy, streaming: bool
) -> None:
    # A tool turn whose text is valid JSON still is not a candidate final answer,
    # so a check that would raise on it never runs and never ends the run.
    seen: list[Any] = []
    provider_type = StreamingProvider if streaming else FakeProvider
    provider = provider_type(
        [
            prose_tool_reply('{"name": "bob"}'),
            text_reply('{"name": "ada"}'),
        ]
    )
    session = new_session("who?")

    await drain(
        session,
        provider,
        schema=PERSON,
        policy=notes,
        check=recording_check(seen, raises_on="bob"),
    )

    assert session.status == "done", session.error
    assert session.output == {"name": "ada"}
    assert seen == [{"name": "ada"}]


async def test_a_cut_off_tool_turn_with_prose_does_not_run_the_check(
    notes: Policy,
) -> None:
    seen: list[Any] = []
    provider = FakeProvider(
        [
            prose_tool_reply("partial thought", stop_reason="max_tokens"),
            text_reply('{"name": "ada"}'),
        ]
    )
    session = new_session("who?")

    await drain(
        session,
        provider,
        schema=PERSON,
        policy=notes,
        check=recording_check(seen),
        max_cutoffs=1,
    )

    assert session.status == "done", session.error
    assert session.output == {"name": "ada"}
    assert seen == [{"name": "ada"}]


@pytest.mark.parametrize("streaming", [False, True], ids=["unstreamed", "streamed"])
async def test_a_caller_tool_turn_with_prose_does_not_end_the_run(
    notes: Policy, streaming: bool
) -> None:
    seen: list[Any] = []
    lookup = Tool(
        name="lookup",
        description="Fetch one record by id.",
        input_schema={"type": "object", "properties": {"id": {"type": "integer"}}},
        call=lambda args: f"record {args['id']}",
    )
    policy = Policy(
        tools=frozenset({"read", "lookup"}),
        pending=frozenset({"lookup"}),
        root=notes.root,
    )
    provider_type = StreamingProvider if streaming else FakeProvider
    provider = provider_type(
        [
            prose_tool_reply("Looking that up.", name="lookup", args={"id": 1}),
            text_reply('{"name": "ada"}'),
        ]
    )
    session = new_session("who?")

    await drain(
        session,
        provider,
        schema=PERSON,
        policy=policy,
        check=recording_check(seen),
        tools=[lookup],
    )

    assert session.status == "done", session.error
    assert session.tool_calls == 1
    assert seen == [{"name": "ada"}]


async def test_a_read_hold_before_the_answer_does_not_end_the_run(
    notes: Policy,
) -> None:
    # A final answer held for a read is a candidate that passed the schema. The
    # hold is a correction, and the run continues to the answer after the read.
    seen: list[Any] = []
    provider = FakeProvider(
        [
            text_reply('{"name": "ada"}'),
            tool_reply("read", {"path": "NOTES.txt"}),
            text_reply('{"name": "ada"}'),
        ]
    )
    session = new_session("who?")

    await drain(
        session,
        provider,
        schema=PERSON,
        policy=notes,
        check=recording_check(seen),
        require_reads=1,
    )

    assert session.status == "done", session.error
    assert session.output == {"name": "ada"}
    assert seen == [{"name": "ada"}, {"name": "ada"}]


@pytest.mark.parametrize("streaming", [False, True], ids=["unstreamed", "streamed"])
async def test_a_check_that_raises_on_a_final_answer_is_an_error_event(
    notes: Policy, streaming: bool
) -> None:
    def check(answer: Any) -> list[Any]:
        raise RuntimeError("lookup failed")

    provider_type = StreamingProvider if streaming else FakeProvider
    session = new_session("who?")

    events = await drain(
        session,
        provider_type([prose_tool_reply("Reading."), text_reply('{"name": "ada"}')]),
        schema=PERSON,
        policy=notes,
        check=check,
    )

    assert session.status == "error"
    assert session.error == "check raised RuntimeError: lookup failed"
    assert events[-1].type == "error"
