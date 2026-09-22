"""Answer validation: a documented subset of JSON Schema, and a loud refusal
outside it.

nare carries one runtime dependency on purpose, so it is nearly impossible to
conflict with when vendored into another project's container. A real validator
would be the fourth and fifth transitive dependency, so this module implements
the keywords a caller actually constrains an answer with, and REFUSES any
schema using a keyword it does not implement. Under-validating quietly would
hand a caller a "validated" object that nothing checked, which is the class of
dishonesty nare exists to remove.
"""

from __future__ import annotations

import json
import re
from typing import Any

SUPPORTED = frozenset(
    {
        "type",
        "properties",
        "required",
        "items",
        "enum",
        "additionalProperties",
        "description",
        "title",
        "$schema",
    }
)

_FENCE = re.compile(r"```(?:[a-zA-Z0-9_-]+)?\s*\n(.*?)```", re.DOTALL)


class UnsupportedSchema(Exception):
    """The schema uses a keyword this validator does not implement."""


# A keyword whose VALUE is not the shape this validator implements. Checking
# the name alone let a schema through that _validate then silently skipped:
# `"required": "name"` iterated characters, `"enum": "valid"` tested substrings,
# and `"items": "string"` checked no item at all. A schema nare cannot enforce
# is refused whole, never enforced in part.
_SHAPES: dict[str, tuple[type | tuple[type, ...], str]] = {
    "type": (str, "a type name"),
    "properties": (dict, "an object of property schemas"),
    "required": (list, "a list of property names"),
    "items": (dict, "a schema"),
    "enum": (list, "a list of allowed values"),
    "additionalProperties": (bool, "true or false"),
}


def check_supported(schema: Any, path: str = "") -> None:
    """Walk the schema and refuse anything this validator cannot enforce: a
    keyword outside SUPPORTED, or a supported keyword carrying a shape the
    validator does not implement.
    """
    where = f" at {path}" if path else ""
    if not isinstance(schema, dict):
        raise UnsupportedSchema(
            f"schema{where} must be an object, got {_type_of(schema)}"
        )
    for key, value in schema.items():
        if key not in SUPPORTED:
            raise UnsupportedSchema(
                f"schema keyword {key!r}{where} is not supported by nare's validator; "
                f"supported: {', '.join(sorted(SUPPORTED))}"
            )
        shape = _SHAPES.get(key)
        if shape is not None and not isinstance(value, shape[0]):
            raise UnsupportedSchema(
                f"schema keyword {key!r}{where} must be {shape[1]}, "
                f"got {_type_of(value)}; nare refuses a schema it cannot enforce whole"
            )
    for name, sub in schema.get("properties", {}).items():
        check_supported(sub, f"{path}.{name}" if path else name)
    if "items" in schema:
        check_supported(schema["items"], f"{path}[]" if path else "[]")


def _type_of(value: Any) -> str:
    # bool before int: in Python True is an int, and a caller asking for an
    # integer that receives true has not received an integer.
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return type(value).__name__


def _matches(actual: str, expected: str) -> bool:
    if expected == "number":
        return actual in {"number", "integer"}
    return actual == expected


def _at(path: str, message: str) -> str:
    return f"{path}: {message}" if path else message


def _validate(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    expected = schema.get("type")
    actual = _type_of(value)
    if isinstance(expected, str) and not _matches(actual, expected):
        return [_at(path, f"expected {expected}, got {actual}")]

    if "enum" in schema and value not in schema["enum"]:
        errors.append(_at(path, f"expected one of {schema['enum']!r}, got {value!r}"))

    if actual == "object":
        properties = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in value:
                errors.append(_at(path, f"missing required property: {name}"))
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    errors.append(_at(path, f"unexpected property: {name}"))
        for name, sub in properties.items():
            if name in value:
                errors += _validate(
                    value[name], sub, f"{path}.{name}" if path else name
                )

    if actual == "array" and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            errors += _validate(item, schema["items"], f"{path}[{index}]")

    return errors


def validate(value: Any, schema: dict[str, Any]) -> list[str]:
    """Every error, not just the first: one reprompt carries them all back."""
    check_supported(schema)
    return _validate(value, schema, "")


def extract_json(text: str) -> Any:
    """The answer as data. A model that was asked for JSON often still wraps it
    in a fence or a sentence, and refusing that is pedantry rather than rigour.
    """
    candidates = [text.strip(), *(m.group(1).strip() for m in _FENCE.finditer(text))]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    raise ValueError("no JSON found in the answer")
