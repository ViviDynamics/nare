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


def test_a_type_that_is_not_a_string_is_refused() -> None:
    with pytest.raises(UnsupportedSchema, match="type"):
        validate(True, {"type": ["string", "number"]})


def test_required_that_is_not_a_list_is_refused() -> None:
    with pytest.raises(UnsupportedSchema, match="required"):
        validate({}, {"type": "object", "required": "name"})


def test_an_enum_that_is_not_a_list_is_refused() -> None:
    with pytest.raises(UnsupportedSchema, match="enum"):
        validate("val", {"enum": "valid"})


def test_items_that_are_not_a_schema_are_refused() -> None:
    with pytest.raises(UnsupportedSchema, match="items"):
        validate([1], {"type": "array", "items": "string"})


def test_additional_properties_that_is_not_a_boolean_is_refused() -> None:
    with pytest.raises(UnsupportedSchema, match="additionalProperties"):
        validate({}, {"type": "object", "additionalProperties": {"type": "string"}})


def test_properties_that_are_not_a_mapping_are_refused() -> None:
    with pytest.raises(UnsupportedSchema, match="properties"):
        validate({}, {"type": "object", "properties": "name"})


def test_a_refused_schema_shape_names_where_it_is() -> None:
    schema = {"type": "object", "properties": {"a": {"type": ["string"]}}}

    with pytest.raises(UnsupportedSchema, match="at a"):
        validate({}, schema)


# Bounds (#123). A generated schema carries them, and dropping them would let an
# out-of-range answer through as validated.

REPORT = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 5},
        "code": {"type": "string", "pattern": "^[A-Z]+-[0-9]+$"},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 2,
            "uniqueItems": True,
        },
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "count": {"type": "integer", "exclusiveMinimum": 0, "exclusiveMaximum": 10},
    },
}


def test_values_inside_every_bound_pass() -> None:
    value = {"title": "ok", "code": "AB-1", "tags": ["a"], "score": 1, "count": 9}

    assert validate(value, REPORT) == []


def test_max_length_fails_with_the_path() -> None:
    assert validate({"title": "toolong"}, REPORT) == [
        "title: expected at most 5 characters, got 7"
    ]


def test_min_length_fails_with_the_path() -> None:
    assert validate({"title": ""}, REPORT) == [
        "title: expected at least 1 characters, got 0"
    ]


def test_length_counts_characters_not_bytes() -> None:
    assert validate({"title": "ééééé"}, REPORT) == []


def test_pattern_is_searched_and_fails_with_the_path() -> None:
    assert validate({"code": "ab-1"}, REPORT) == [
        "code: expected a string matching '^[A-Z]+-[0-9]+$', got 'ab-1'"
    ]


def test_pattern_is_unanchored_like_json_schema() -> None:
    assert validate("xx42yy", {"type": "string", "pattern": "[0-9]+"}) == []


def test_max_items_fails_with_the_path() -> None:
    assert validate({"tags": ["a", "b", "c"]}, REPORT) == [
        "tags: expected at most 2 items, got 3"
    ]


def test_min_items_fails_with_the_path() -> None:
    assert validate({"tags": []}, REPORT) == ["tags: expected at least 1 items, got 0"]


def test_unique_items_names_the_repeat() -> None:
    assert validate({"tags": ["a", "a"]}, REPORT) == [
        "tags: expected unique items, [1] repeats [0]"
    ]


def test_unique_items_uses_json_equality() -> None:
    schema = {"type": "array", "uniqueItems": True}

    assert validate([1, True], schema) == []
    assert validate([1, 1.0], schema) == ["expected unique items, [1] repeats [0]"]
    assert validate([{"a": [1]}, {"a": [1]}], schema) == [
        "expected unique items, [1] repeats [0]"
    ]


def test_number_bounds_fail_with_the_path() -> None:
    assert validate({"score": 1.5, "count": 0}, REPORT) == [
        "score: expected a number <= 1, got 1.5",
        "count: expected a number > 0, got 0",
    ]
    assert validate({"score": -1, "count": 10}, REPORT) == [
        "score: expected a number >= 0, got -1",
        "count: expected a number < 10, got 10",
    ]


def test_a_bound_only_applies_to_its_own_type() -> None:
    schema = {"maxLength": 1, "maxItems": 1, "maximum": 1}

    assert validate(5, {"maxLength": 1, "maxItems": 1}) == []
    assert validate("long", {"maxItems": 1, "maximum": 1}) == []
    assert validate([1, 2], {"maxLength": 1, "maximum": 1}) == []
    assert validate(True, schema) == []


@pytest.mark.parametrize(
    "schema",
    [
        {"maxLength": -1},
        {"minLength": 1.5},
        {"maxItems": True},
        {"minItems": "1"},
        {"uniqueItems": "yes"},
        {"minimum": "0"},
        {"maximum": False},
        {"exclusiveMinimum": True},
        {"exclusiveMaximum": None},
        {"pattern": 5},
        {"pattern": "("},
    ],
)
def test_a_bound_with_a_shape_nare_cannot_enforce_is_refused(
    schema: dict[str, object],
) -> None:
    keyword = next(iter(schema))

    with pytest.raises(UnsupportedSchema, match=f"'{keyword}' must be"):
        validate("x", schema)
