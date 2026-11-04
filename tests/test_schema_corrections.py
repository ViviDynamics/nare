"""Schema corrections: a budget that allows several, a free one when an answer
makes progress, and earlier fixes named so a correction does not undo them."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider, text_reply
from nare.cli import main
from nare.loop import run
from nare.session import Session, dumps, loads, new_session, reopen

PERSON: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "status": {"type": "string"},
        "age": {"type": "integer"},
    },
    "required": ["name", "status", "age"],
    "additionalProperties": False,
}
FIELDS = ["name", "status", "age"]
VALID = json.dumps({"name": "ada", "status": "open", "age": 36})


def answer_with(errors: int) -> str:
    """An answer with exactly `errors` distinct errors: that many required
    fields are absent, and the rest are present and valid."""
    present = {"name": "ada", "status": "open", "age": 36}
    return json.dumps({k: present[k] for k in FIELDS[errors:]})


async def drain(session: Session, provider: FakeProvider, **kwargs: Any) -> list[Any]:
    return [event async for event in run(session, transport=provider, **kwargs)]


def sent_text(provider: FakeProvider, call: int) -> str:
    """Every block the model was sent on one call, as one string."""
    messages = provider.calls[call][0]
    return json.dumps([block for message in messages for block in message.content])


def schema_file(tmp_path: Path) -> Path:
    path = tmp_path / "person.schema.json"
    path.write_text(json.dumps(PERSON), encoding="utf-8")
    return path


def test_answer_with_helper_counts_its_errors() -> None:
    # The helper must be trustworthy before the tests rely on it.
    from nare.schema import validate

    assert len(validate(json.loads(answer_with(3)), PERSON)) == 3
    assert len(validate(json.loads(answer_with(2)), PERSON)) == 2
    assert len(validate(json.loads(answer_with(1)), PERSON)) == 1
    assert validate(json.loads(answer_with(0)), PERSON) == []


async def test_a_free_correction_at_a_new_low_is_not_charged() -> None:
    # Failing answers with 3, 2, 2 and 1 errors, then a valid one. Corrections:
    # the first is charged, the second is free (a new low), the third is
    # charged (no new low), the fourth is free again (a new low).
    provider = FakeProvider(
        [
            text_reply(answer_with(3)),
            text_reply(answer_with(2)),
            text_reply(answer_with(2)),
            text_reply(answer_with(1)),
            text_reply(VALID),
        ]
    )
    session = new_session("go")

    await drain(session, provider, schema=PERSON, max_corrections=2)

    assert session.status == "done"
    assert session.output == {"name": "ada", "status": "open", "age": 36}
    assert [e["charged"] for e in session.schema_correction_log] == [
        True,
        False,
        True,
        False,
    ]
    assert session.schema_corrections == 2


async def test_one_allowed_correction_stops_after_it_as_today() -> None:
    provider = FakeProvider([text_reply(answer_with(3)), text_reply(answer_with(2))])
    session = new_session("go")

    await drain(session, provider, schema=PERSON, max_corrections=1)

    assert session.status == "error"
    assert session.stop_reason == "schema_violation"
    assert len(provider.calls) == 2
    assert [e["charged"] for e in session.schema_correction_log] == [True]


async def test_the_budget_is_spent_when_no_new_low_follows() -> None:
    # Same count twice: the second correction is charged, and N=2 is spent.
    provider = FakeProvider(
        [
            text_reply(answer_with(2)),
            text_reply(answer_with(2)),
            text_reply(answer_with(2)),
        ]
    )
    session = new_session("go")

    await drain(session, provider, schema=PERSON, max_corrections=2)

    assert session.stop_reason == "schema_violation"
    assert session.schema_corrections == 2
    assert [e["charged"] for e in session.schema_correction_log] == [True, True]


async def test_a_correction_names_the_earlier_error_it_must_keep_fixed() -> None:
    # The first answer lacks `status`. The second supplies `status` and drops
    # `age`. The second correction must name the `status` error as one to keep.
    provider = FakeProvider(
        [
            text_reply(json.dumps({"name": "ada", "age": 36})),
            text_reply(json.dumps({"name": "ada", "status": "open"})),
            text_reply(VALID),
        ]
    )
    session = new_session("go")

    events = await drain(session, provider, schema=PERSON, max_corrections=2)

    assert session.status == "done"
    [first, second] = [e for e in events if "schema_correction" in e.detail]
    assert first.detail["schema_correction"]["earlier_corrections"] == []
    assert second.detail["schema_correction"]["earlier_corrections"] == [
        "missing required property: status"
    ]
    assert "Keep these earlier fixes" in sent_text(provider, 2)
    assert "missing required property: status" in sent_text(provider, 2)


async def test_the_first_correction_names_no_earlier_errors() -> None:
    provider = FakeProvider([text_reply(answer_with(1)), text_reply(VALID)])
    session = new_session("go")

    await drain(session, provider, schema=PERSON, max_corrections=2)

    assert "Keep these earlier fixes" not in sent_text(provider, 1)


async def test_each_correction_is_a_progress_event_with_its_charge() -> None:
    provider = FakeProvider(
        [text_reply(answer_with(3)), text_reply(answer_with(1)), text_reply(VALID)]
    )
    session = new_session("go")

    events = await drain(session, provider, schema=PERSON, max_corrections=3)

    marks = [e for e in events if "schema_correction" in e.detail]
    assert [m.detail["schema_correction"]["charged"] for m in marks] == [True, False]
    assert [m.detail["schema_correction"]["number"] for m in marks] == [1, 2]
    assert marks[0].detail["schema_correction"]["limit"] == 3
    assert marks[0].detail["schema_correction"]["codes"] == [
        "missing required property: name",
        "missing required property: status",
        "missing required property: age",
    ]


async def test_the_default_keeps_todays_one_correction() -> None:
    provider = FakeProvider([text_reply(answer_with(3)), text_reply(answer_with(1))])
    session = new_session("go")

    await drain(session, provider, schema=PERSON)

    assert session.status == "error"
    assert session.stop_reason == "schema_violation"
    assert len(provider.calls) == 2


async def test_reopen_gives_the_resumed_run_its_own_budget() -> None:
    provider = FakeProvider([text_reply(answer_with(3)), text_reply(answer_with(2))])
    session = new_session("go")
    await drain(session, provider, schema=PERSON, max_corrections=1)
    assert session.schema_corrections == 1

    reopen(session, "try again")

    assert session.schema_corrections == 0
    assert session.schema_correction_log == []
    assert session.schema_fewest_errors is None


def test_a_file_saved_with_a_spent_correction_still_has_it_spent() -> None:
    raw = json.loads(dumps(new_session("go")))
    for key in (
        "schema_corrections",
        "schema_correction_log",
        "schema_errors_seen",
        "schema_fewest_errors",
    ):
        raw.pop(key, None)
    raw["schema_retried"] = True

    loaded = loads(json.dumps(raw))

    assert loaded.schema_corrections == 1
    assert loaded.schema_retried is True


def test_the_cli_reports_the_budget_and_each_correction(
    tmp_path: Path, capsys: Any
) -> None:
    code = main(
        [
            "run",
            "go",
            "--yes",
            "--jsonl",
            "--schema",
            str(schema_file(tmp_path)),
            "--max-corrections",
            "2",
        ],
        transport=FakeProvider(
            [
                text_reply(answer_with(3)),
                text_reply(answer_with(2)),
                text_reply(answer_with(2)),
                text_reply(VALID),
            ]
        ),
    )

    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0
    assert last["status"] == "done"
    assert last["budget"]["schema_corrections"] == 2
    assert last["budget"]["used_schema_corrections"] == 2
    assert [c["charged"] for c in last["corrections"]] == [True, False, True]
    assert all(c["codes"] for c in last["corrections"])


def test_the_cli_result_has_no_corrections_list_when_none_was_needed(
    tmp_path: Path, capsys: Any
) -> None:
    code = main(
        ["run", "go", "--yes", "--jsonl", "--schema", str(schema_file(tmp_path))],
        transport=FakeProvider([text_reply(VALID)]),
    )

    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0
    assert "corrections" not in last
    assert last["budget"]["schema_corrections"] == 1
    assert last["budget"]["used_schema_corrections"] == 0


@pytest.mark.parametrize("value", ["0", "-1"])
def test_a_non_positive_budget_never_starts(
    tmp_path: Path, capsys: Any, value: str
) -> None:
    code = main(
        [
            "run",
            "go",
            "--yes",
            "--jsonl",
            "--schema",
            str(schema_file(tmp_path)),
            f"--max-corrections={value}",
        ],
        transport=FakeProvider([text_reply(VALID)]),
    )

    assert code == 2
    assert capsys.readouterr().out == ""


def test_max_corrections_without_a_schema_never_starts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        ["run", "go", "--yes", "--jsonl", "--max-corrections", "2"],
        transport=FakeProvider([text_reply(VALID)]),
    )

    assert code == 2
    assert capsys.readouterr().out == ""
