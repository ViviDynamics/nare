"""list and search: read-only navigation confined to the root, with no bash."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from nare.tools import MAX_PAGE, MAX_TOOL_OUTPUT, TOOL_SCHEMAS, list_dir


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


def cursor_of(page: str) -> str | None:
    found = re.search(r'\[next page: cursor "([^"]+)"\]$', page)
    return found[1] if found else None


def names_in(page: str) -> list[str]:
    """The entry names of a list page: every line but the header and footer."""
    lines = page.splitlines()[1:]
    return [line.split("  (")[0] for line in lines if not line.startswith("[")]


def test_list_reports_each_entry_sorted_with_its_kind_size_and_binary(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "b.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "a.bin").write_bytes(b"\x89PNG\x00\x01")
    (tmp_path / "link").symlink_to(tmp_path / "b.txt")

    page = list_dir(str(tmp_path), base=str(tmp_path))

    assert page.splitlines() == [
        ".: entries 1-4 of 4",
        "a.bin  (file, 6 bytes, binary)",
        "b.txt  (file, 5 bytes)",
        "link  (symlink)",
        "src/  (dir)",
    ]


def test_list_of_an_empty_directory_says_so(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()

    assert list_dir(str(tmp_path / "empty"), base=str(tmp_path)) == (
        "empty: empty directory"
    )


def test_a_symlink_out_of_the_root_is_listed_and_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (root / "escape").symlink_to(outside)

    page = list_dir(str(root), base=str(root))

    assert "escape  (symlink)" in page
    assert "secret" not in page


def test_list_pages_with_a_cursor_until_every_entry_is_seen(tmp_path: Path) -> None:
    for name in "edcba":
        (tmp_path / f"{name}.txt").write_text(name, encoding="utf-8")
    seen: list[str] = []
    cursor: str | None = None
    pages = 0

    while True:
        page = list_dir(str(tmp_path), cursor, 2, base=str(tmp_path))
        pages += 1
        seen += names_in(page)
        cursor = cursor_of(page)
        if cursor is None:
            break

    assert seen == ["a.txt", "b.txt", "c.txt", "d.txt", "e.txt"]
    assert pages == 3
    assert page.splitlines()[0] == ".: entries 5-5 of 5"


def test_list_is_identical_for_the_same_tree(tmp_path: Path) -> None:
    for name in ("b", "a", "c"):
        (tmp_path / name).write_text(name, encoding="utf-8")

    first = list_dir(str(tmp_path), None, 2, base=str(tmp_path))

    assert list_dir(str(tmp_path), None, 2, base=str(tmp_path)) == first
    cursor = cursor_of(first)
    assert cursor is not None
    assert list_dir(str(tmp_path), cursor, 2, base=str(tmp_path)) == list_dir(
        str(tmp_path), cursor, 2, base=str(tmp_path)
    )


def test_a_cursor_from_another_directory_is_refused(tmp_path: Path) -> None:
    for folder in ("one", "two"):
        (tmp_path / folder).mkdir()
        for name in ("a", "b"):
            (tmp_path / folder / name).write_text(name, encoding="utf-8")
    cursor = cursor_of(list_dir(str(tmp_path / "one"), None, 1, base=str(tmp_path)))
    assert cursor is not None

    with pytest.raises(ValueError, match="different call"):
        list_dir(str(tmp_path / "two"), cursor, 1, base=str(tmp_path))


def test_a_broken_cursor_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid cursor"):
        list_dir(str(tmp_path), "not-a-cursor", base=str(tmp_path))


def test_list_refuses_a_file(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        list_dir(str(tmp_path / "a.txt"), base=str(tmp_path))


def test_a_limit_out_of_range_is_clamped(tmp_path: Path) -> None:
    for i in range(3):
        (tmp_path / f"{i}.txt").write_text("x", encoding="utf-8")

    assert len(names_in(list_dir(str(tmp_path), None, 0, base=str(tmp_path)))) == 1
    assert len(names_in(list_dir(str(tmp_path), None, -5, base=str(tmp_path)))) == 1


def test_a_page_of_long_names_stops_before_the_output_cap(tmp_path: Path) -> None:
    names = sorted(f"{i:04d}" + "x" * 200 for i in range(400))
    for name in names:
        (tmp_path / name).write_text("x", encoding="utf-8")
    seen: list[str] = []
    cursor: str | None = None

    while True:
        page = list_dir(str(tmp_path), cursor, MAX_PAGE, base=str(tmp_path))
        assert len(page) <= MAX_TOOL_OUTPUT
        assert "[truncated" not in page
        seen += names_in(page)
        cursor = cursor_of(page)
        if cursor is None:
            break

    assert seen == names
