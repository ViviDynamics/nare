"""A run whose answer must be data: one reprompt, then a named terminal status."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fake_provider import FakeProvider, text_reply
from nare.cli import main
from nare.loop import run
from nare.session import Session, new_session

PERSON: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
}


async def drain(session: Session, provider: FakeProvider, **kwargs: Any) -> list[Any]:
    return [event async for event in run(session, transport=provider, **kwargs)]


async def test_a_validating_answer_is_parsed_onto_the_session() -> None:
    provider = FakeProvider([text_reply('{"name": "ada"}')])
    session = new_session("go")

    events = await drain(session, provider, schema=PERSON)

    assert session.status == "done"
    assert session.output == {"name": "ada"}
    output = [e for e in events if e.type == "output"][-1]
    assert output.detail == {"output": {"name": "ada"}}


async def test_a_violation_is_reprompted_once_and_then_accepted() -> None:
    provider = FakeProvider([text_reply('{"age": 1}'), text_reply('{"name": "ada"}')])
    session = new_session("go")

    await drain(session, provider, schema=PERSON)

    assert session.status == "done"
    assert session.output == {"name": "ada"}
    sent = provider.calls[1][0]
    assert "missing required property: name" in json.dumps(
        [block for message in sent for block in message.content]
    )


async def test_a_second_violation_ends_the_run_with_a_named_stop_reason() -> None:
    provider = FakeProvider([text_reply('{"age": 1}'), text_reply('{"age": 2}')])
    session = new_session("go")

    await drain(session, provider, schema=PERSON)

    assert session.status == "error"
    assert session.stop_reason == "schema_violation"
    assert session.output is None
    assert "missing required property: name" in (session.error or "")


async def test_an_answer_carrying_no_json_is_a_violation_too() -> None:
    provider = FakeProvider([text_reply("sorry, no"), text_reply("still no")])
    session = new_session("go")

    await drain(session, provider, schema=PERSON)

    assert session.status == "error"
    assert session.stop_reason == "schema_violation"


async def test_a_run_without_a_schema_is_unchanged() -> None:
    provider = FakeProvider([text_reply("just prose")])
    session = new_session("go")

    events = await drain(session, provider)

    assert session.status == "done"
    assert session.output is None
    assert [e for e in events if e.type == "output"][-1].detail == {}


def test_the_cli_refuses_a_schema_file_that_is_missing(
    tmp_path: Path, capsys: Any
) -> None:
    code = main(["run", "go", "--yes", "--schema", str(tmp_path / "nope.json")])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""


def test_the_cli_refuses_an_unsupported_schema_at_startup(
    tmp_path: Path, capsys: Any
) -> None:
    schema = tmp_path / "s.json"
    schema.write_text(json.dumps({"anyOf": [{"type": "string"}]}), encoding="utf-8")

    code = main(["run", "go", "--yes", "--schema", str(schema)])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "anyOf" in captured.err


def test_the_result_line_carries_the_validated_output(
    tmp_path: Path, capsys: Any
) -> None:
    schema = tmp_path / "s.json"
    schema.write_text(json.dumps(PERSON), encoding="utf-8")

    code = main(
        ["run", "go", "--yes", "--jsonl", "--schema", str(schema)],
        transport=FakeProvider([text_reply('{"name": "ada"}')]),
    )

    assert code == 0
    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert last["type"] == "result"
    assert last["output"] == {"name": "ada"}


def test_a_schema_violation_exits_one(tmp_path: Path, capsys: Any) -> None:
    schema = tmp_path / "s.json"
    schema.write_text(json.dumps(PERSON), encoding="utf-8")

    code = main(
        ["run", "go", "--yes", "--jsonl", "--schema", str(schema)],
        transport=FakeProvider([text_reply("no"), text_reply("still no")]),
    )

    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 1
    assert last["stop_reason"] == "schema_violation"
    assert last["output"] is None


def test_a_resumed_run_gets_its_own_correction_round(
    tmp_path: Path, capsys: Any
) -> None:
    schema = tmp_path / "s.json"
    schema.write_text(json.dumps(PERSON), encoding="utf-8")
    session_path = tmp_path / "session.json"
    main(
        ["run", "go", "--yes", "--schema", str(schema), "--session", str(session_path)],
        transport=FakeProvider([text_reply("no"), text_reply("still no")]),
    )
    capsys.readouterr()

    code = main(
        [
            "run",
            "try again",
            "--yes",
            "--jsonl",
            "--schema",
            str(schema),
            "--resume",
            str(session_path),
        ],
        transport=FakeProvider([text_reply("nope"), text_reply('{"name": "ada"}')]),
    )

    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0
    assert last["output"] == {"name": "ada"}


async def test_the_model_is_told_the_schema_before_it_answers() -> None:
    # nare validated the answer without ever stating the requirement, so the
    # first turn was wasted by construction: the model cannot satisfy a shape
    # it was never shown. Caught against a live model, not by these tests.
    provider = FakeProvider([text_reply('{"name": "ada"}')])
    session = new_session("go")

    await drain(session, provider, schema=PERSON)

    first_request = json.dumps(
        [block for message in provider.calls[0][0] for block in message.content]
    )
    assert "required" in first_request
    assert "name" in first_request
    assert "JSON Schema" in first_request


async def test_the_correction_restates_the_schema() -> None:
    provider = FakeProvider([text_reply("prose"), text_reply('{"name": "ada"}')])
    session = new_session("go")

    await drain(session, provider, schema=PERSON)

    correction = json.dumps(
        [block for message in provider.calls[1][0] for block in message.content]
    )
    assert "no JSON found" in correction
    assert "required" in correction


async def test_a_run_without_a_schema_says_nothing_extra() -> None:
    provider = FakeProvider([text_reply("just prose")])
    session = new_session("the whole prompt")

    await drain(session, provider)

    sent = json.dumps(
        [block for message in provider.calls[0][0] for block in message.content]
    )
    assert "JSON Schema" not in sent
