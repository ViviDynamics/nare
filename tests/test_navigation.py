"""list and search: read-only navigation confined to the root, with no bash."""

from __future__ import annotations

from typing import Any

from nare.tools import TOOL_SCHEMAS


def schema_of(name: str) -> dict[str, Any]:
    return next(s for s in TOOL_SCHEMAS if s["name"] == name)


def test_list_takes_a_path_a_cursor_and_a_limit() -> None:
    schema = schema_of("list")["input_schema"]

    assert set(schema["properties"]) == {"path", "cursor", "limit"}
    assert schema.get("required", []) == []


def test_search_takes_a_path_a_glob_a_pattern_a_cursor_and_a_limit() -> None:
    schema = schema_of("search")["input_schema"]

    assert set(schema["properties"]) == {"path", "glob", "pattern", "cursor", "limit"}
    assert schema.get("required", []) == []


def test_the_navigation_descriptions_say_they_run_nothing() -> None:
    for name in ("list", "search"):
        assert "runs nothing" in schema_of(name)["description"]
