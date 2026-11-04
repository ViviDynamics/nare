"""Answer validation: a documented subset of JSON Schema, and a loud refusal
outside it.

nare carries one runtime dependency on purpose, so it is nearly impossible to
conflict with when vendored into another project's container. A real validator
would be the fourth and fifth transitive dependency, so this module implements
the keywords a caller actually constrains an answer with, including what
schema generators emit (local `$ref`, `anyOf`/`oneOf`/`allOf`, `const`,
bounds, a schema-valued `additionalProperties` for a map), and REFUSES any
schema using a keyword it does not implement. docs/contract.md lists them.
Under-validating quietly would hand a caller a "validated" object that nothing
checked, which is the class of dishonesty nare exists to remove.
"""

from __future__ import annotations

import json
import operator
import re
import urllib.parse
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
        "anyOf",
        "oneOf",
        "allOf",
        "$ref",
        "$defs",
        "definitions",
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
    "additionalProperties": ((bool, dict), "true, false or a schema"),
    "uniqueItems": (bool, "true or false"),
    "pattern": (str, "a regular expression"),
    "$ref": (str, "a reference string"),
    "$defs": (dict, "an object of schemas"),
    "definitions": (dict, "an object of schemas"),
}

_COMPOSITIONS = ("anyOf", "oneOf", "allOf")
_DEFINITIONS = ("$defs", "definitions")
_COUNTS = frozenset({"minLength", "maxLength", "minItems", "maxItems"})
_NUMBER_BOUNDS = frozenset(
    {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}
)


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_supported(schema: Any) -> None:
    """Walk the schema and refuse anything this validator cannot enforce: a
    keyword outside SUPPORTED, a supported keyword carrying a shape the
    validator does not implement, a reference that is remote or points at no
    schema, or a reference cycle that would never reach a value.
    """
    refs: dict[str, dict[str, Any]] = {}
    _check(schema, "", schema, refs)
    _refuse_ref_cycles(refs)


def _check(schema: Any, path: str, root: Any, refs: dict[str, dict[str, Any]]) -> None:
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
        if key in _COMPOSITIONS and not (
            isinstance(value, list)
            and value
            and all(isinstance(sub, dict) for sub in value)
        ):
            raise UnsupportedSchema(
                f"schema keyword {key!r}{where} must be a non-empty list of schemas, "
                f"got {_type_of(value)}; nare refuses a schema it cannot enforce whole"
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
        _check(sub, f"{path}.{name}" if path else name, root, refs)
    if "items" in schema:
        _check(schema["items"], f"{path}[]" if path else "[]", root, refs)
    if isinstance(schema.get("additionalProperties"), dict):
        label = "additionalProperties"
        _check(
            schema["additionalProperties"],
            f"{path}.{label}" if path else label,
            root,
            refs,
        )
    for keyword in _COMPOSITIONS:
        for index, sub in enumerate(schema.get(keyword, [])):
            label = f"{keyword}[{index}]"
            _check(sub, f"{path}.{label}" if path else label, root, refs)
    for keyword in _DEFINITIONS:
        for name, sub in schema.get(keyword, {}).items():
            _check(sub, f"{keyword}.{name}", root, refs)
    ref = schema.get("$ref")
    if ref is not None and ref not in refs:
        # Recorded before the walk so a recursive reference checks once.
        refs[ref] = target = _resolve(ref, root, where)
        _check(target, ref, root, refs)


def _resolve(ref: str, root: Any, where: str = "") -> dict[str, Any]:
    """A local JSON pointer into the root schema. Anything else is refused:
    fetching a remote schema would be network access nare never does, and a
    reference to nothing could only be skipped.
    """
    if not ref.startswith("#"):
        raise UnsupportedSchema(
            f"schema keyword '$ref'{where} is {ref!r}, a remote reference; nare "
            "resolves only local references starting with '#'"
        )
    target = root
    pointer = urllib.parse.unquote(ref[1:])
    if pointer and not pointer.startswith("/"):
        target = None
    else:
        for token in pointer.split("/")[1:]:
            key = token.replace("~1", "/").replace("~0", "~")
            if isinstance(target, dict) and key in target:
                target = target[key]
            elif isinstance(target, list) and key.isdigit() and int(key) < len(target):
                target = target[int(key)]
            else:
                target = None
                break
    if not isinstance(target, dict):
        raise UnsupportedSchema(
            f"schema keyword '$ref'{where} is {ref!r}, which does not point at a "
            "schema in this document"
        )
    return target


def _same_position_refs(schema: dict[str, Any]) -> list[str]:
    """References applied to the same value as this schema, without first
    descending into a property or an item.
    """
    found = [schema["$ref"]] if "$ref" in schema else []
    for keyword in _COMPOSITIONS:
        for sub in schema.get(keyword, []):
            found += _same_position_refs(sub)
    return found


def _refuse_ref_cycles(refs: dict[str, dict[str, Any]]) -> None:
    """A recursive reference is fine while each round trip descends into the
    value, because the value is finite. One that returns to itself at the same
    value would recurse forever, so it is refused before any run starts.
    """
    done: set[str] = set()

    def visit(ref: str, chain: list[str]) -> None:
        if ref in chain:
            loop = " -> ".join([*chain[chain.index(ref) :], ref])
            raise UnsupportedSchema(
                f"schema has a '$ref' cycle that never reaches a nested value ({loop})"
            )
        if ref in done:
            return
        for nxt in _same_position_refs(refs[ref]):
            visit(nxt, [*chain, ref])
        done.add(ref)

    for ref in refs:
        visit(ref, [])


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


def _validate(value: Any, schema: dict[str, Any], path: str, root: Any) -> list[str]:
    errors: list[str] = []
    if "$ref" in schema:
        errors += _validate(value, _resolve(schema["$ref"], root), path, root)
    expected = schema.get("type")
    actual = _type_of(value)
    if isinstance(expected, str) and not _matches(actual, expected):
        return [*errors, _at(path, f"expected {expected}, got {actual}")]

    if "enum" in schema and _canonical(value) not in {
        _canonical(option) for option in schema["enum"]
    }:
        errors.append(_at(path, f"expected one of {schema['enum']!r}, got {value!r}"))
    if "const" in schema and _canonical(value) != _canonical(schema["const"]):
        errors.append(_at(path, f"expected {schema['const']!r}, got {value!r}"))

    errors += _composition(value, schema, path, root)

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
        additional = schema.get("additionalProperties")
        for name in value:
            if name in properties:
                continue
            if additional is False:
                errors.append(_at(path, f"unexpected property: {name}"))
            elif isinstance(additional, dict):
                # A map: every property the schema does not name is a value
                # of the map, checked against the one value schema.
                errors += _validate(
                    value[name], additional, f"{path}.{name}" if path else name, root
                )
        for name, sub in properties.items():
            if name in value:
                errors += _validate(
                    value[name], sub, f"{path}.{name}" if path else name, root
                )

    if actual == "array" and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            errors += _validate(item, schema["items"], f"{path}[{index}]", root)

    return errors


def _composition(value: Any, schema: dict[str, Any], path: str, root: Any) -> list[str]:
    errors: list[str] = []
    for sub in schema.get("allOf", []):
        errors += _validate(value, sub, path, root)
    for keyword in ("anyOf", "oneOf"):
        if keyword not in schema:
            continue
        outcomes = [_validate(value, sub, path, root) for sub in schema[keyword]]
        matched = [index for index, found in enumerate(outcomes) if not found]
        if not matched:
            detail = "; ".join(
                f"{keyword}[{index}]: {'; '.join(found)}"
                for index, found in enumerate(outcomes)
            )
            errors.append(_at(path, f"matches no {keyword} alternative ({detail})"))
        elif keyword == "oneOf" and len(matched) > 1:
            which = ", ".join(str(index) for index in matched[:-1])
            errors.append(
                _at(
                    path,
                    f"matches oneOf alternatives {which} and {matched[-1]}, "
                    "expected exactly one",
                )
            )
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
    return _validate(value, schema, "", schema)


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
