"""Pure diagnostics share the strict answer correction path, never admission."""

from __future__ import annotations

import asyncio
import json
from contextlib import aclosing
from typing import Any

import pytest

from fake_provider import FakeProvider, StreamingProvider, text_reply, tool_reply
from nare import Policy, Tool, dumps, loads, new_session, run

SCHEMA = {
    "type": "object",
    "properties": {
        "rationale": {"type": "string", "maxLength": 8},
        "source": {"type": "string"},
    },
    "required": ["rationale", "source"],
    "additionalProperties": False,
}
BAD = '{"rationale":"far too much detail","source":"unread.txt"}'
GOOD = '{"rationale":"checked","source":"read.txt"}'


async def citations(answer: Any) -> list[Any]:
    if isinstance(answer, dict) and answer.get("source") != "read.txt":
        return [
            {
                "code": "unread_citation",
                "location": ["source"],
                "message": "Inspect the cited source.",
            }
        ]
    return []


@pytest.mark.parametrize("provider_type", [FakeProvider, StreamingProvider])
async def test_combines_errors_in_one_correction_before_admission(
    provider_type: Any,
) -> None:
    admitted = []

    async def check(answer: Any) -> list[Any]:
        admitted.append(answer)
        return []

    provider = provider_type([text_reply(BAD), text_reply(GOOD)])
    session = new_session("Review the pinned evidence.")
    events = [
        event
        async for event in run(
            session,
            transport=provider,
            policy=Policy(tools=frozenset()),
            schema=SCHEMA,
            diagnose=citations,
            check=check,
            max_corrections=1,
        )
    ]
    corrections = [event for event in events if "schema_correction" in event.detail]
    assert len(corrections) == 1
    assert {error["code"] for error in corrections[0].detail["errors"]} == {
        "max_length",
        "unread_citation",
    }
    assert session.schema_corrections == 1
    assert admitted == [json.loads(GOOD)]
    assert session.output == json.loads(GOOD)
    assert session.status == "done"


async def test_diagnostics_cannot_mutate_the_admitted_candidate() -> None:
    async def mutate(answer: Any) -> list[Any]:
        answer["source"] = "mutated.txt"
        return []

    provider = FakeProvider([text_reply(GOOD)])
    session = new_session("Review")
    [
        event
        async for event in run(
            session, transport=provider, schema=SCHEMA, diagnose=mutate
        )
    ]
    assert session.status == "done"
    assert session.output == json.loads(GOOD)


async def test_diagnostics_exceptions_never_reach_admission() -> None:
    admitted = []

    async def broken(answer: Any) -> list[Any]:
        raise RuntimeError("diagnostics failed")

    async def check(answer: Any) -> list[Any]:
        admitted.append(answer)
        return []

    session = new_session("Review")
    [
        event
        async for event in run(
            session,
            transport=FakeProvider([text_reply(GOOD)]),
            schema=SCHEMA,
            diagnose=broken,
            check=check,
        )
    ]
    assert session.status == "error"
    assert session.output is None
    assert admitted == []


@pytest.mark.parametrize(
    "reply",
    [
        text_reply("not JSON"),
        text_reply('{"source":"a","source":"b"}'),
        text_reply(GOOD, stop_reason="max_tokens"),
    ],
)
async def test_diagnostics_do_not_run_on_invalid_or_incomplete_json(reply: Any) -> None:
    diagnosed = []

    async def diagnose(answer: Any) -> list[Any]:
        diagnosed.append(answer)
        return []

    session = new_session("Review")
    [
        event
        async for event in run(
            session,
            transport=FakeProvider([reply, reply]),
            schema=SCHEMA,
            diagnose=diagnose,
            max_corrections=1,
        )
    ]
    assert session.status == "error"
    assert diagnosed == []


async def test_resume_preserves_the_shared_correction_allowance() -> None:
    session = new_session("Review")
    provider = FakeProvider([text_reply(BAD)])
    async with aclosing(
        run(
            session,
            transport=provider,
            schema=SCHEMA,
            diagnose=citations,
            max_corrections=1,
        )
    ) as events:
        async for event in events:
            if "schema_correction" in event.detail:
                break
    restored = loads(dumps(session))
    provider = FakeProvider([text_reply(BAD)])
    [
        event
        async for event in run(
            restored,
            transport=provider,
            schema=SCHEMA,
            diagnose=citations,
            max_corrections=1,
        )
    ]
    assert restored.status == "error"
    assert restored.stop_reason == "schema_violation"
    assert restored.schema_corrections == 1
    assert len(provider.calls) == 1


async def test_cancellation_during_diagnostics_does_not_invent_a_correction() -> None:
    reached = asyncio.Event()

    async def waiting(answer: Any) -> list[Any]:
        reached.set()
        await asyncio.Future()
        return []

    session = new_session("Review")

    async def drain() -> None:
        [
            event
            async for event in run(
                session,
                transport=FakeProvider([text_reply(BAD)]),
                schema=SCHEMA,
                diagnose=waiting,
            )
        ]

    task = asyncio.create_task(drain())
    await asyncio.wait_for(reached.wait(), timeout=2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert session.schema_corrections == 0
    assert session.output is None


async def test_tool_turns_do_not_run_diagnostics_or_retain_unchecked_answer() -> None:
    diagnosed = []

    async def diagnose(answer: Any) -> list[Any]:
        diagnosed.append(answer)
        return []

    async def read(args: Any) -> str:
        assert session.output is None
        return "evidence"

    session = new_session("Review")
    mixed = tool_reply("read_evidence", {})
    mixed.content.insert(0, {"type": "text", "text": GOOD})
    provider = FakeProvider([mixed, text_reply(GOOD)])
    [
        event
        async for event in run(
            session,
            transport=provider,
            schema=SCHEMA,
            diagnose=diagnose,
            tools=[
                Tool(
                    name="read_evidence",
                    description="Read",
                    input_schema={"type": "object"},
                    call=read,
                )
            ],
            policy=Policy(
                tools=frozenset({"read_evidence"}), pending=frozenset({"read_evidence"})
            ),
        )
    ]
    assert diagnosed == [json.loads(GOOD)]
    assert session.output == json.loads(GOOD)


@pytest.mark.parametrize(
    "options", [{"diagnose": citations}, {"diagnose": "not callable", "schema": SCHEMA}]
)
async def test_invalid_diagnostics_registration_fails_before_inference(
    options: Any,
) -> None:
    provider = FakeProvider([])
    with pytest.raises(ValueError):
        [
            event
            async for event in run(new_session("Review"), transport=provider, **options)
        ]
    assert provider.calls == []


async def test_summary_does_not_spend_diagnostic_correction() -> None:
    from nare import approve_all
    from nare.loop import step

    diagnosed = []

    async def diagnose(answer: Any) -> list[Any]:
        diagnosed.append(answer)
        return []

    session = new_session("Review")
    await step(
        session,
        FakeProvider([text_reply(BAD)]),
        approve_all,
        Policy(tools=frozenset()),
        schema=SCHEMA,
        diagnose=diagnose,
        summary=True,
    )
    assert diagnosed == []
    assert session.schema_corrections == 0
    assert session.output is None
