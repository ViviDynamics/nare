"""The answer validator: a documented subset, and a refusal outside it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from nare.schema import SUPPORTED, UnsupportedSchema, extract_json, validate

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
    with pytest.raises(UnsupportedSchema, match="'not'"):
        validate({}, {"not": {"type": "string"}})


def test_an_unsupported_keyword_nested_in_properties_is_also_refused() -> None:
    schema = {"type": "object", "properties": {"a": {"format": "date"}}}

    with pytest.raises(UnsupportedSchema, match="'format' at a"):
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


@pytest.mark.parametrize("value", ["string", 1, None, ["a"]])
def test_additional_properties_that_is_not_a_boolean_or_schema_is_refused(
    value: object,
) -> None:
    with pytest.raises(UnsupportedSchema, match="additionalProperties"):
        validate({}, {"type": "object", "additionalProperties": value})


MAP = {"type": "object", "additionalProperties": {"type": "string"}}


def test_a_schema_valued_additional_properties_checks_every_map_value() -> None:
    assert validate({"HOME": "/h", "SHELL": "sh"}, MAP) == []
    assert validate({}, MAP) == []


def test_a_map_value_of_the_wrong_type_names_its_path() -> None:
    schema = {"type": "object", "properties": {"env": MAP}}

    assert validate({"env": {"HOME": 1, "USER": "u", "N": None}}, schema) == [
        "env.HOME: expected string, got integer",
        "env.N: expected string, got null",
    ]


def test_named_properties_are_not_checked_against_additional_properties() -> None:
    schema = {
        "type": "object",
        "properties": {"count": {"type": "integer"}},
        "additionalProperties": {"type": "string"},
    }

    assert validate({"count": 3, "x": "y"}, schema) == []
    assert validate({"count": 3, "x": 4}, schema) == ["x: expected string, got integer"]


def test_a_map_of_arrays_checks_each_item_by_path() -> None:
    schema = {
        "type": "object",
        "additionalProperties": {"type": "array", "items": {"type": "string"}},
    }

    assert validate({"a": ["b", 2]}, schema) == ["a[1]: expected string, got integer"]


def test_a_map_value_ref_resolves_like_any_local_reference() -> None:
    schema = {
        "$defs": {"Item": {"type": "object", "required": ["id"]}},
        "type": "object",
        "additionalProperties": {"$ref": "#/$defs/Item"},
    }

    assert validate({"a": {"id": 1}}, schema) == []
    assert validate({"a": {}}, schema) == ["a: missing required property: id"]


def test_a_recursive_map_through_a_ref_validates_and_terminates() -> None:
    schema = {
        "$defs": {
            "Tree": {"type": "object", "additionalProperties": {"$ref": "#/$defs/Tree"}}
        },
        "$ref": "#/$defs/Tree",
    }

    assert validate({"a": {"b": {}}}, schema) == []
    assert validate({"a": {"b": 1}}, schema) == ["a.b: expected object, got integer"]


def test_a_map_value_schema_is_checked_for_unsupported_keywords() -> None:
    schema = {"type": "object", "additionalProperties": {"format": "date"}}

    with pytest.raises(UnsupportedSchema, match="'format' at additionalProperties"):
        validate({}, schema)


def test_a_dangling_ref_in_a_map_value_schema_is_refused() -> None:
    schema = {"type": "object", "additionalProperties": {"$ref": "#/$defs/Nope"}}

    with pytest.raises(UnsupportedSchema, match="does not point at a schema"):
        validate({}, schema)


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


def test_const_fails_with_the_path() -> None:
    schema = {"type": "object", "properties": {"kind": {"const": "finding"}}}

    assert validate({"kind": "finding"}, schema) == []
    assert validate({"kind": "note"}, schema) == [
        "kind: expected 'finding', got 'note'"
    ]


def test_const_uses_json_equality() -> None:
    assert validate(1.0, {"const": 1}) == []
    assert validate(True, {"const": 1}) == ["expected 1, got True"]
    assert validate({"a": [1, 2]}, {"const": {"a": [1, 2]}}) == []


def test_enum_uses_json_equality() -> None:
    assert validate(True, {"enum": [1, 2]}) == ["expected one of [1, 2], got True"]
    assert validate(2.0, {"enum": [1, 2]}) == []


def test_default_is_an_annotation_and_never_applied() -> None:
    schema = {
        "type": "object",
        "properties": {"line": {"type": "integer", "default": "not an integer"}},
        "required": ["line"],
    }
    value: dict[str, object] = {}

    assert validate(value, schema) == ["missing required property: line"]
    assert value == {}


OPTIONAL_LINE = {
    "type": "object",
    "properties": {
        "line": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]}
    },
}


def test_any_of_passes_when_one_alternative_matches() -> None:
    assert validate({"line": 3}, OPTIONAL_LINE) == []
    assert validate({"line": None}, OPTIONAL_LINE) == []


def test_any_of_reports_every_alternative_under_the_path() -> None:
    assert validate({"line": 0}, OPTIONAL_LINE) == [
        "line: matches no anyOf alternative "
        "(anyOf[0]: line: expected a number >= 1, got 0; "
        "anyOf[1]: line: expected null, got integer)"
    ]


def test_one_of_needs_exactly_one_match() -> None:
    schema = {"oneOf": [{"type": "integer"}, {"type": "number", "maximum": 10}]}

    assert validate(11, schema) == []
    assert validate(1.5, schema) == []
    assert validate(3, schema) == [
        "matches oneOf alternatives 0 and 1, expected exactly one"
    ]
    assert validate("x", schema) == [
        "matches no oneOf alternative "
        "(oneOf[0]: expected integer, got string; "
        "oneOf[1]: expected number, got string)"
    ]


def test_all_of_reports_each_failing_part() -> None:
    schema = {
        "type": "object",
        "properties": {
            "name": {"allOf": [{"type": "string"}, {"minLength": 2, "maxLength": 3}]}
        },
    }

    assert validate({"name": "ab"}, schema) == []
    assert validate({"name": "abcd"}, schema) == [
        "name: expected at most 3 characters, got 4"
    ]


def test_composition_is_checked_for_unsupported_keywords_too() -> None:
    schema = {"anyOf": [{"type": "string"}, {"format": "date"}]}

    with pytest.raises(UnsupportedSchema, match=r"'format' at anyOf\[1\]"):
        validate("x", schema)


@pytest.mark.parametrize("keyword", ["anyOf", "oneOf", "allOf"])
@pytest.mark.parametrize("value", [[], {"type": "string"}, ["string"]])
def test_composition_that_is_not_a_list_of_schemas_is_refused(
    keyword: str, value: object
) -> None:
    with pytest.raises(UnsupportedSchema, match=f"'{keyword}' must be"):
        validate("x", {keyword: value})


NAMED = {
    "type": "object",
    "properties": {
        "short": {"$ref": "#/$defs/Short"},
        "legacy": {"$ref": "#/definitions/Legacy"},
        "slashed": {"$ref": "#/$defs/a~1b"},
    },
    "$defs": {"Short": {"type": "string", "maxLength": 2}, "a/b": {"const": 1}},
    "definitions": {"Legacy": {"type": "integer"}},
}


def test_a_local_ref_resolves_and_reports_the_value_path() -> None:
    assert validate({"short": "ab", "legacy": 1, "slashed": 1}, NAMED) == []
    assert validate({"short": "abc", "legacy": "1", "slashed": 2}, NAMED) == [
        "short: expected at most 2 characters, got 3",
        "legacy: expected integer, got string",
        "slashed: expected 1, got 2",
    ]


def test_a_ref_to_any_local_pointer_resolves() -> None:
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string", "maxLength": 1},
            "b": {"$ref": "#/properties/a"},
            "c": {"type": "array", "items": {"anyOf": [{"$ref": "#/properties/a"}]}},
        },
    }

    assert validate({"a": "x", "b": "y", "c": ["z"]}, schema) == []
    assert validate({"b": "yy"}, schema) == ["b: expected at most 1 characters, got 2"]


def test_keywords_beside_a_ref_still_apply() -> None:
    schema = {"$defs": {"S": {"type": "string"}}, "$ref": "#/$defs/S", "maxLength": 1}

    assert validate("ab", schema) == ["expected at most 1 characters, got 2"]
    assert validate(1, schema) == ["expected string, got integer"]


TREE = {
    "type": "object",
    "properties": {
        "value": {"type": "integer"},
        "children": {"type": "array", "items": {"$ref": "#"}},
    },
    "required": ["value"],
}


def test_a_recursive_ref_to_the_root_validates_and_terminates() -> None:
    tree = {"value": 1, "children": [{"value": 2, "children": [{"value": "x"}]}]}

    assert validate(tree, TREE) == [
        "children[0].children[0].value: expected integer, got string"
    ]


def test_a_recursive_ref_through_defs_validates_and_terminates() -> None:
    schema = {
        "$ref": "#/$defs/Node",
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "next": {"anyOf": [{"$ref": "#/$defs/Node"}, {"type": "null"}]},
                },
                "required": ["name", "next"],
            }
        },
    }
    chain: dict[str, object] = {"name": "c", "next": None}
    for name in "ba":
        chain = {"name": name, "next": chain}

    assert validate(chain, schema) == []
    assert validate({"name": "a", "next": {"name": 1, "next": None}}, schema) == [
        "next: matches no anyOf alternative "
        "(anyOf[0]: next.name: expected string, got integer; "
        "anyOf[1]: next: expected null, got object)"
    ]


@pytest.mark.parametrize(
    "ref",
    ["other.json#/$defs/A", "https://example.com/s.json", "/abs/path#/x"],
)
def test_a_remote_ref_is_refused_as_unsupported(ref: str) -> None:
    with pytest.raises(UnsupportedSchema, match="remote"):
        validate({}, {"type": "object", "properties": {"a": {"$ref": ref}}})


@pytest.mark.parametrize(
    "ref", ["#/$defs/Missing", "#/$defs/S/enum", "#anchor", "#/$defs/S/x/0"]
)
def test_a_ref_that_does_not_point_at_a_schema_is_refused(ref: str) -> None:
    schema = {"$defs": {"S": {"enum": ["a"]}}, "$ref": ref}

    with pytest.raises(UnsupportedSchema, match=r"\$ref"):
        validate("a", schema)


def test_a_ref_that_is_not_a_string_is_refused() -> None:
    with pytest.raises(UnsupportedSchema, match=r"'\$ref' must be"):
        validate("a", {"$ref": {"type": "string"}})


@pytest.mark.parametrize("keyword", ["$defs", "definitions"])
def test_defs_must_hold_schemas_and_are_checked(keyword: str) -> None:
    with pytest.raises(UnsupportedSchema, match=re.escape(f"'{keyword}' must be")):
        validate("a", {keyword: ["S"]})
    with pytest.raises(UnsupportedSchema, match="'format'"):
        validate("a", {keyword: {"S": {"format": "date"}}})


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "#"},
        {"$ref": "#/$defs/A", "$defs": {"A": {"$ref": "#/$defs/A"}}},
        {
            "$ref": "#/$defs/A",
            "$defs": {
                "A": {"anyOf": [{"type": "string"}, {"$ref": "#/$defs/B"}]},
                "B": {"allOf": [{"$ref": "#/$defs/A"}]},
            },
        },
    ],
)
def test_a_ref_cycle_that_never_reaches_a_value_is_refused(
    schema: dict[str, object],
) -> None:
    with pytest.raises(UnsupportedSchema, match="cycle"):
        validate(1, schema)


def test_the_contract_lists_every_supported_keyword() -> None:
    contract = Path(__file__).parents[1] / "docs" / "contract.md"
    text = contract.read_text(encoding="utf-8")
    section = text.split("## Answer schemas", 1)[1].split("\n## ", 1)[0]

    assert {k for k in SUPPORTED if f"`{k}`" not in section} == set()
