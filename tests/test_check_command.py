"""A caller check the command line can run (#296), and the awaitable checks it is
built on."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider, text_reply, tool_reply
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


@pytest.fixture
def notes(tmp_path: Path) -> Policy:
    (tmp_path / "NOTES.txt").write_text("ada\n", encoding="utf-8")
    return Policy(tools=frozenset({"read"}), root=tmp_path)


async def test_an_async_check_error_reaches_the_model_as_a_correction(
    notes: Policy,
) -> None:
    async def check(answer: Any) -> list[Any]:
        if answer["name"] == "bob":
            return [{"code": "name_unknown", "location": ["name"], "message": "no bob"}]
        return []

    provider = FakeProvider(
        [text_reply('{"name": "bob"}'), text_reply('{"name": "ada"}')]
    )
    session = new_session("who?")

    events = await drain(session, provider, schema=PERSON, policy=notes, check=check)

    assert session.status == "done"
    assert session.output == {"name": "ada"}
    [progress] = [e for e in events if "schema correction" in e.text]
    assert progress.detail["errors"][0]["code"] == "name_unknown"


async def test_an_async_check_that_raises_ends_the_run(notes: Policy) -> None:
    async def check(answer: Any) -> list[Any]:
        raise RuntimeError("lookup failed")

    session = new_session("who?")

    await drain(
        session,
        FakeProvider([text_reply('{"name": "ada"}')]),
        schema=PERSON,
        policy=notes,
        check=check,
    )

    assert session.status == "error"
    assert session.stop_reason is None
    assert session.error == "check raised RuntimeError: lookup failed"


async def test_an_async_check_with_two_parameters_is_given_the_reads(
    notes: Policy,
) -> None:
    seen: list[Any] = []

    async def check(answer: Any, reads: list[dict[str, Any]]) -> list[Any]:
        seen.append([r["path"] for r in reads])
        return []

    provider = FakeProvider(
        [tool_reply("read", {"path": "NOTES.txt"}), text_reply('{"name": "ada"}')]
    )

    await drain(new_session("who?"), provider, schema=PERSON, policy=notes, check=check)

    assert seen == [["NOTES.txt"]]
