"""A resume that passes a different --schema tells the model, once."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider, text_reply
from nare.cli import main
from nare.loop import run, schema_digest
from nare.session import Session, dumps, loads, new_session, reopen

PERSON: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
}
# Stricter: the name must be at least four characters.
STRICT: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string", "minLength": 4}},
    "required": ["name"],
}
CHANGED = "The required JSON Schema has changed."


async def drain(session: Session, provider: FakeProvider, **kwargs: Any) -> list[Any]:
    return [event async for event in run(session, transport=provider, **kwargs)]


def said(session: Session, text: str) -> int:
    """How many user text blocks in the transcript contain `text`."""
    return sum(
        text in block.get("text", "")
        for message in session.messages
        if message.role == "user"
        for block in message.content
        if block.get("type") == "text"
    )


def changes(events: list[Any]) -> list[Any]:
    return [e for e in events if "schema" in e.detail]


def test_the_digest_ignores_key_order_and_whitespace() -> None:
    reordered = json.loads(json.dumps(PERSON, sort_keys=True))
    assert list(reordered) != list(PERSON)
    assert schema_digest(reordered) == schema_digest(PERSON)
    canonical = json.dumps(PERSON, sort_keys=True, separators=(",", ":"))
    assert schema_digest(PERSON) == hashlib.sha256(canonical.encode()).hexdigest()
    assert schema_digest(STRICT) != schema_digest(PERSON)


async def test_the_first_statement_records_the_digest() -> None:
    session = new_session("go")
    await drain(session, FakeProvider([text_reply('{"name": "ada"}')]), schema=PERSON)
    assert session.schema_stated
    assert session.schema_sha256 == schema_digest(PERSON)
    assert loads(dumps(session)).schema_sha256 == schema_digest(PERSON)


async def test_a_changed_schema_is_stated_once_before_the_turn() -> None:
    session = new_session("go")
    await drain(session, FakeProvider([text_reply('{"name": "ada"}')]), schema=PERSON)

    reopen(session, "the name must be the full name")
    provider = FakeProvider([text_reply('{"name": "Ada Lovelace"}')])
    events = await drain(session, provider, schema=STRICT)

    assert session.status == "done"
    assert session.output == {"name": "Ada Lovelace"}
    assert said(session, CHANGED) == 1
    assert said(session, json.dumps(STRICT)) == 1
    assert session.schema_sha256 == schema_digest(STRICT)
    [event] = changes(events)
    assert event.type == "progress"
    assert event.detail == {"schema": {"changed": True}}
    # The model saw the new schema in the request that answered it.
    [(messages, _)] = provider.calls
    assert any(
        json.dumps(STRICT) in block.get("text", "")
        for message in messages
        for block in message.content
    )

    reopen(session, "once more")
    events = await drain(
        session, FakeProvider([text_reply('{"name": "Ada Lovelace"}')]), schema=STRICT
    )
    assert said(session, CHANGED) == 1
    assert changes(events) == []


async def test_the_same_schema_on_resume_adds_nothing() -> None:
    session = new_session("go")
    await drain(session, FakeProvider([text_reply('{"name": "ada"}')]), schema=PERSON)
    before = len(session.messages)

    reopen(session)
    reordered = json.loads(json.dumps(PERSON, sort_keys=True))
    events = await drain(
        session, FakeProvider([text_reply('{"name": "ada"}')]), schema=reordered
    )

    assert said(session, CHANGED) == 0
    assert said(session, "JSON Schema") == 1
    assert changes(events) == []
    # Only the new turn's reply was added.
    assert len(session.messages) == before + 1


async def test_a_retained_output_failing_the_new_schema_is_dropped() -> None:
    session = new_session("go")
    await drain(session, FakeProvider([text_reply('{"name": "ada"}')]), schema=PERSON)
    assert session.output == {"name": "ada"}

    reopen(session, "longer")
    await drain(
        session, FakeProvider([text_reply('{"name": "al"}')] * 2), schema=STRICT
    )

    assert session.stop_reason == "schema_violation"
    # "ada" passed the old schema, not this one: it is not this run's answer.
    assert session.output is None


async def test_a_retained_output_passing_the_new_schema_is_kept() -> None:
    session = new_session("go")
    first = '{"name": "Ada Lovelace"}'
    await drain(session, FakeProvider([text_reply(first)]), schema=PERSON)

    reopen(session, "check it")
    await drain(session, FakeProvider([text_reply("no")] * 2), schema=STRICT)

    assert session.stop_reason == "schema_violation"
    assert session.output == {"name": "Ada Lovelace"}


async def test_a_session_without_a_digest_states_nothing_new() -> None:
    session = new_session("go")
    await drain(session, FakeProvider([text_reply('{"name": "ada"}')]), schema=PERSON)
    raw = json.loads(dumps(session))
    del raw["schema_sha256"]  # a file saved before nare recorded it
    old = loads(json.dumps(raw))
    assert old.schema_stated and old.schema_sha256 is None

    reopen(old, "again")
    events = await drain(
        old, FakeProvider([text_reply('{"name": "Ada Lovelace"}')]), schema=STRICT
    )

    assert said(old, CHANGED) == 0
    assert changes(events) == []
    # Recorded from then on, so the next change is stated.
    assert old.schema_sha256 == schema_digest(STRICT)


def test_the_cli_states_a_changed_schema_in_the_saved_transcript(
    tmp_path: Path, capsys: Any
) -> None:
    person = tmp_path / "person.json"
    person.write_text(json.dumps(PERSON), encoding="utf-8")
    strict = tmp_path / "strict.json"
    strict.write_text(json.dumps(STRICT), encoding="utf-8")
    path = tmp_path / "session.json"
    assert (
        main(
            ["run", "go", "--yes", "--schema", str(person), "--session", str(path)],
            transport=FakeProvider([text_reply('{"name": "ada"}')]),
        )
        == 0
    )
    capsys.readouterr()

    code = main(
        [
            "run",
            "use the full name",
            "--yes",
            "--jsonl",
            "--tools",
            "none",
            "--max-turns",
            "2",
            "--schema",
            str(strict),
            "--resume",
            str(path),
        ],
        transport=FakeProvider([text_reply('{"name": "Ada Lovelace"}')]),
    )

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert code == 0
    assert lines[-1]["output"] == {"name": "Ada Lovelace"}
    assert [e["detail"] for e in lines if "schema" in e.get("detail", {})] == [
        {"schema": {"changed": True}}
    ]
    saved = loads(path.read_text(encoding="utf-8"))
    assert said(saved, CHANGED) == 1
    assert said(saved, json.dumps(STRICT)) == 1


def test_a_digest_that_is_not_a_string_is_malformed() -> None:
    raw = json.loads(dumps(new_session("go")))
    raw["schema_sha256"] = 7
    with pytest.raises(ValueError, match="schema_sha256"):
        loads(json.dumps(raw))
