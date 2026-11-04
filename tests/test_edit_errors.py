"""When edit fails, the error says why old did not match and where it nearly
did, so the model can correct the call without reading the whole file again.
The edit still fails: an inexact match is never applied.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nare.tools import Policy, dispatch, edit_file
from nare.transport import ToolCall

SOURCE = """\
def greet(name):
    if name:
        return f"hello {name}"
    return "hello"


def part(name):
    return name
"""


def failure(target: Path, old: str) -> str:
    before = target.read_bytes()
    with pytest.raises(ValueError) as caught:
        edit_file(str(target), old, "REPLACED")
    assert target.read_bytes() == before
    return str(caught.value)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    target = tmp_path / "greet.py"
    target.write_text(SOURCE, encoding="utf-8")
    return target


def test_an_indentation_mismatch_says_so_and_shows_the_real_lines(
    source: Path,
) -> None:
    old = 'if name:\n    return f"hello {name}"'  # four spaces short

    message = failure(source, old)

    assert message.startswith(f"old not found in {source}")
    assert "matches lines 2 to 3 when whitespace is ignored" in message
    assert "    2:     if name:\n" in message
    assert '    3:         return f"hello {name}"' in message


def test_spacing_inside_a_line_is_whitespace_too(source: Path) -> None:
    message = failure(source, "def   greet( name ):")

    assert "matches lines 1 to 1 when whitespace is ignored" in message
    assert "    1: def greet(name):" in message


def test_a_line_that_changed_shows_the_closest_lines(source: Path) -> None:
    old = 'def greet(name):\n    if name:\n        return f"hi {name}"'

    message = failure(source, old)

    assert "when whitespace is ignored" not in message
    assert "closest lines are 1 to 3" in message
    assert '3:         return f"hello {name}"' in message
    assert "changed since you read it" in message


def test_nothing_close_says_so_without_listing_lines(source: Path) -> None:
    message = failure(source, "class Unrelated(Base):")

    assert "no line resembles" in message
    assert "1:" not in message


def test_duplicate_matches_report_their_line_numbers(source: Path) -> None:
    message = failure(source, "(name):")

    assert message == (
        f"old appears 2 times in {source}, at lines 1 and 7; include enough "
        "surrounding context to make it unique"
    )


def test_many_duplicates_are_listed_with_a_bound(tmp_path: Path) -> None:
    target = tmp_path / "many.txt"
    target.write_text("x\n" * 50, encoding="utf-8")

    message = failure(target, "x")

    assert message.startswith(f"old appears 50 times in {target}, at lines 1, 2, 3")
    assert "20, and 30 more;" in message


def test_the_shown_lines_are_bounded(tmp_path: Path) -> None:
    target = tmp_path / "long.py"
    body = "".join(f"    line_{n} = {n}\n" for n in range(100))
    target.write_text("def f():\n" + body, encoding="utf-8")
    old = "def f():\n" + "".join(f"line_{n} = {n}\n" for n in range(100))

    message = failure(target, old)

    assert "matches lines 1 to 101 when whitespace is ignored" in message
    assert "   10:     line_8 = 8" in message
    assert "   11:" not in message
    assert "91 more lines" in message


def test_a_long_line_is_cut_in_the_listing(tmp_path: Path) -> None:
    target = tmp_path / "wide.txt"
    target.write_text("  " + "w" * 5000 + "\n", encoding="utf-8")

    message = failure(target, "w" * 5000 + "x")

    assert "closest lines are 1 to 1" in message
    assert len(message) < 1000


def test_an_exact_unique_match_still_edits(source: Path) -> None:
    assert edit_file(str(source), "return name", "return name.upper()") == (
        f"edited {source}"
    )
    assert "return name.upper()" in source.read_text(encoding="utf-8")


async def test_an_unknown_tool_lists_only_the_tools_this_session_allows() -> None:
    policy = Policy(tools=frozenset({"read", "list"}))

    result = await dispatch(ToolCall("c1", "grep", {}), policy)

    assert result["is_error"] is True
    assert result["content"] == "unknown tool 'grep'; this session allows: list, read"


async def test_an_unknown_tool_in_a_session_with_no_tools_says_none() -> None:
    result = await dispatch(ToolCall("c1", "grep", {}), Policy(tools=frozenset()))

    assert result["content"] == "unknown tool 'grep'; this session allows: none"


def test_an_empty_old_is_refused_with_a_reason(source: Path) -> None:
    assert failure(source, "") == "old is empty; to replace a whole file, use write"
