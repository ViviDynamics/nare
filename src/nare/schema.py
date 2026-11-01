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
import operator
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
        "default",
        "const",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
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
    "uniqueItems": (bool, "true or false"),
    "pattern": (str, "a regular expression"),
}

_COUNTS = frozenset({"minLength", "maxLength", "minItems", "maxItems"})
_NUMBER_BOUNDS = frozenset(
    {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}
)


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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
        if key in _COUNTS and not _is_count(value):
            raise UnsupportedSchema(
                f"schema keyword {key!r}{where} must be a non-negative integer, "
                f"got {value!r}; nare refuses a schema it cannot enforce whole"
            )
        if key in _NUMBER_BOUNDS and not _is_number(value):
            raise UnsupportedSchema(
                f"schema keyword {key!r}{where} must be a number, got {value!r}; "
                "nare refuses a schema it cannot enforce whole"
            )
        if key == "pattern":
            try:
                re.compile(value)
            except re.error as exc:
                raise UnsupportedSchema(
                    f"schema keyword {key!r}{where} must be a regular expression "
                    f"Python can compile ({exc}); nare refuses a schema it cannot "
                    "enforce whole"
                ) from None
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

    if "enum" in schema and _canonical(value) not in {
        _canonical(option) for option in schema["enum"]
    }:
        errors.append(_at(path, f"expected one of {schema['enum']!r}, got {value!r}"))
    if "const" in schema and _canonical(value) != _canonical(schema["const"]):
        errors.append(_at(path, f"expected {schema['const']!r}, got {value!r}"))

    if actual == "string":
        errors += _string_bounds(value, schema, path)
    if actual in {"integer", "number"}:
        errors += _number_bounds(value, schema, path)
    if actual == "array":
        errors += _array_bounds(value, schema, path)

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


def _string_bounds(value: str, schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    # len() counts code points, which is what JSON Schema means by length.
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        errors.append(
            _at(
                path,
                f"expected at most {schema['maxLength']} characters, got {len(value)}",
            )
        )
    if "minLength" in schema and len(value) < schema["minLength"]:
        errors.append(
            _at(
                path,
                f"expected at least {schema['minLength']} characters, got {len(value)}",
            )
        )
    if "pattern" in schema and re.search(schema["pattern"], value) is None:
        errors.append(
            _at(
                path,
                f"expected a string matching {schema['pattern']!r}, got {value!r}",
            )
        )
    return errors


_COMPARISONS = (
    ("minimum", ">=", operator.ge),
    ("maximum", "<=", operator.le),
    ("exclusiveMinimum", ">", operator.gt),
    ("exclusiveMaximum", "<", operator.lt),
)


def _number_bounds(value: float, schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    for keyword, symbol, holds in _COMPARISONS:
        if keyword in schema and not holds(value, schema[keyword]):
            errors.append(
                _at(path, f"expected a number {symbol} {schema[keyword]}, got {value}")
            )
    return errors


def _array_bounds(value: list[Any], schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        errors.append(
            _at(path, f"expected at most {schema['maxItems']} items, got {len(value)}")
        )
    if "minItems" in schema and len(value) < schema["minItems"]:
        errors.append(
            _at(path, f"expected at least {schema['minItems']} items, got {len(value)}")
        )
    if schema.get("uniqueItems") is True:
        first: dict[Any, int] = {}
        for index, item in enumerate(value):
            key = _canonical(item)
            if key in first:
                errors.append(
                    _at(
                        path,
                        f"expected unique items, [{index}] repeats [{first[key]}]",
                    )
                )
            else:
                first[key] = index
    return errors


def _canonical(value: Any) -> Any:
    """A hashable form under which two values are equal exactly when JSON calls
    them equal: 1 and 1.0 are the same number, and true is not 1.
    """
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float)):
        return ("number", value)
    if isinstance(value, list):
        return ("array", tuple(_canonical(item) for item in value))
    if isinstance(value, dict):
        return (
            "object",
            frozenset((key, _canonical(item)) for key, item in value.items()),
        )
    return (_type_of(value), value)


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
