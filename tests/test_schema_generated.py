"""Generated schemas validate as they come (#123).

`pydantic_report.schema.json` is `Report.model_json_schema()` from pydantic
2.13 for the models below, saved unedited so the exact error messages do not
move when pydantic does. The live test regenerates the schema with the
installed pydantic, which is here only because the provider SDKs require it;
nare's validator does not import it.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from fake_provider import FakeProvider, text_reply
from nare.cli import main
from nare.schema import validate

SCHEMA_FILE = Path(__file__).parent / "pydantic_report.schema.json"
SCHEMA: dict[str, Any] = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


class Severity(StrEnum):
    low = "low"
    high = "high"


class Location(BaseModel):
    path: str = Field(min_length=1, max_length=200)
    line: int | None = Field(default=None, ge=1)


class Finding(BaseModel):
    kind: Literal["finding"] = "finding"
    severity: Severity
    title: str = Field(max_length=80)
    location: Location | None = None
    tags: list[str] = Field(default_factory=list, max_length=5)


class Report(BaseModel):
    summary: str
    findings: list[Finding] = Field(max_length=10)
    confidence: float = Field(ge=0, le=1)


def report(**finding: Any) -> dict[str, Any]:
    base = {"kind": "finding", "severity": "high", "title": "t", "tags": []}
    return {"summary": "s", "findings": [{**base, **finding}], "confidence": 0.5}


def test_the_generated_schema_is_used_as_is_and_a_valid_answer_passes() -> None:
    assert validate(report(), SCHEMA) == []
    assert validate(report(location=None), SCHEMA) == []
    assert validate(report(location={"path": "a.py", "line": 3}), SCHEMA) == []
    assert validate(report(location={"path": "a.py", "line": None}), SCHEMA) == []


def test_a_value_outside_the_generated_constraints_names_its_path() -> None:
    errors = validate(
        report(kind="note", severity="low!", title="x" * 81, tags=list("abcdef")),
        SCHEMA,
    )

    assert errors == [
        "findings[0].kind: expected 'finding', got 'note'",
        "findings[0].severity: expected one of ['low', 'high'], got 'low!'",
        "findings[0].title: expected at most 80 characters, got 81",
        "findings[0].tags: expected at most 5 items, got 6",
    ]


def test_a_bad_optional_nested_model_reports_both_alternatives() -> None:
    errors = validate(report(location={"path": "", "line": 0}), SCHEMA)

    assert errors == [
        "findings[0].location: matches no anyOf alternative "
        "(anyOf[0]: findings[0].location.path: expected at least 1 characters, "
        "got 0; findings[0].location.line: matches no anyOf alternative "
        "(anyOf[0]: findings[0].location.line: expected a number >= 1, got 0; "
        "anyOf[1]: findings[0].location.line: expected null, got integer); "
        "anyOf[1]: findings[0].location: expected null, got object)"
    ]


def test_bounds_on_the_root_and_its_arrays_hold() -> None:
    answer = report()
    answer["confidence"] = 1.5
    answer["findings"] = answer["findings"] * 11

    assert validate(answer, SCHEMA) == [
        "findings: expected at most 10 items, got 11",
        "confidence: expected a number <= 1, got 1.5",
    ]


def test_the_cli_accepts_the_generated_schema_and_returns_the_answer(
    capsys: Any,
) -> None:
    answer = report(location={"path": "a.py"})

    code = main(
        ["run", "go", "--yes", "--jsonl", "--schema", str(SCHEMA_FILE)],
        transport=FakeProvider([text_reply(json.dumps(answer))]),
    )

    assert code == 0
    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert last["output"] == answer


def test_the_installed_pydantic_schema_validates_without_edits() -> None:
    schema = Report.model_json_schema()
    good = Report(
        summary="s",
        findings=[Finding(severity=Severity.high, title="t", location=None)],
        confidence=1,
    ).model_dump(mode="json")

    assert validate(good, schema) == []
    assert validate({**good, "confidence": 2}, schema) == [
        "confidence: expected a number <= 1, got 2"
    ]


class Service(BaseModel):
    image: str


class Manifest(BaseModel):
    """Map fields (#131): every generator emits a schema-valued
    additionalProperties for these.
    """

    env: dict[str, str]
    depends_on: dict[str, list[str]]
    services: dict[str, Service]


def manifest() -> dict[str, Any]:
    return {
        "env": {"HOME": "/home/app"},
        "depends_on": {"web": ["db"]},
        "services": {"db": {"image": "postgres"}},
    }


def test_a_generated_schema_with_map_fields_validates_without_edits() -> None:
    schema = Manifest.model_json_schema()

    assert validate(manifest(), schema) == []
    assert validate(Manifest(**manifest()).model_dump(mode="json"), schema) == []


def test_a_generated_map_value_of_the_wrong_type_names_its_path() -> None:
    answer = manifest()
    answer["env"]["HOME"] = 1
    answer["depends_on"]["web"] = ["db", 2]
    answer["services"]["db"] = {}

    assert validate(answer, Manifest.model_json_schema()) == [
        "env.HOME: expected string, got integer",
        "depends_on.web[1]: expected string, got integer",
        "services.db: missing required property: image",
    ]


def test_a_map_value_error_goes_back_in_the_one_correction_turn(
    tmp_path: Path, capsys: Any
) -> None:
    schema_file = tmp_path / "manifest.schema.json"
    schema_file.write_text(json.dumps(Manifest.model_json_schema()), encoding="utf-8")
    wrong = manifest()
    wrong["env"]["HOME"] = 1
    provider = FakeProvider(
        [text_reply(json.dumps(wrong)), text_reply(json.dumps(manifest()))]
    )

    code = main(
        ["run", "go", "--yes", "--jsonl", "--schema", str(schema_file)],
        transport=provider,
    )

    assert code == 0
    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert last["output"] == manifest()
    correction = json.dumps(
        [block for message in provider.calls[1][0] for block in message.content]
    )
    assert "env.HOME: expected string, got integer" in correction
