"""The answer validator: a documented subset, and a refusal outside it."""

from __future__ import annotations

import pytest

from nare.schema import UnsupportedSchema, extract_json, validate

PERSON = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
    "required": ["name"],
}


def test_a_matching_object_has_no_errors() -> None:
    assert validate({"name": "ada", "age": 36}, PERSON) == []


def test_a_missing_required_property_is_named() -> None:
    assert validate({"age": 36}, PERSON) == ["missing required property: name"]


def test_a_wrong_type_is_named_with_its_path() -> None:
    assert validate({"name": 7}, PERSON) == ["name: expected string, got integer"]


def test_an_integer_is_not_satisfied_by_a_float() -> None:
    assert validate({"name": "ada", "age": 1.5}, PERSON) == [
        "age: expected integer, got number"
    ]


def test_a_boolean_is_not_an_integer() -> None:
    assert validate({"name": "ada", "age": True}, PERSON) == [
        "age: expected integer, got boolean"
    ]


def test_every_error_is_reported_not_just_the_first() -> None:
    errors = validate({"age": "old"}, PERSON)

    assert len(errors) == 2


def test_enum_membership_is_checked() -> None:
    schema = {"type": "string", "enum": ["pass", "fail"]}

    assert validate("maybe", schema) == [
        "expected one of ['pass', 'fail'], got 'maybe'"
    ]


def test_array_items_are_checked_by_index() -> None:
    schema = {"type": "array", "items": {"type": "string"}}

    assert validate(["a", 2], schema) == ["[1]: expected string, got integer"]


def test_nested_objects_report_a_dotted_path() -> None:
    schema = {
        "type": "object",
        "properties": {"inner": PERSON},
        "required": ["inner"],
    }

    assert validate({"inner": {"age": 1}}, schema) == [
        "inner: missing required property: name"
    ]


def test_additional_properties_false_refuses_extras() -> None:
    schema = dict(PERSON, additionalProperties=False)

    assert validate({"name": "ada", "extra": 1}, schema) == [
        "unexpected property: extra"
    ]


def test_an_unsupported_keyword_is_refused_rather_than_ignored() -> None:
    with pytest.raises(UnsupportedSchema, match="anyOf"):
        validate({}, {"anyOf": [{"type": "string"}]})


def test_an_unsupported_keyword_nested_in_properties_is_also_refused() -> None:
    schema = {"type": "object", "properties": {"a": {"$ref": "#/definitions/x"}}}

    with pytest.raises(UnsupportedSchema, match=r"\$ref"):
        validate({}, schema)


def test_plain_json_is_extracted() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}


def test_a_fenced_block_is_extracted() -> None:
    text = 'Here is the answer:\n\n```json\n{"a": 1}\n```\n'

    assert extract_json(text) == {"a": 1}


def test_a_fenced_block_without_a_language_is_extracted() -> None:
    assert extract_json('```\n{"a": 1}\n```') == {"a": 1}


def test_text_carrying_no_json_raises() -> None:
    with pytest.raises(ValueError, match="no JSON"):
        extract_json("I could not do it, sorry.")
