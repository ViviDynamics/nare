"""list and search: read-only navigation confined to the root, with no bash."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest

from nare.tools import (
    MATCH_WIDTH,
    MAX_PAGE,
    MAX_TOOL_OUTPUT,
    TOOL_SCHEMAS,
    list_dir,
    search_files,
)


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


def results_in(page: str) -> list[str]:
    """The result lines of a search page: every line but the header and footer."""
    return [line for line in page.splitlines()[1:] if not line.startswith("[")]


def everything(path: Path, base: Path, **args: Any) -> list[str]:
    """Every result of a search, page by page."""
    seen: list[str] = []
    cursor: str | None = None
    while True:
        page = search_files(str(path), cursor=cursor, base=str(base), **args)
        assert len(page) <= MAX_TOOL_OUTPUT
        seen += results_in(page)
        cursor = cursor_of(page)
        if cursor is None:
            return seen


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "src" / "deep").mkdir(parents=True)
    (tmp_path / "a.py").write_text("import os\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text(
        "def needle():\n    return 1\n# needle again\n", encoding="utf-8"
    )
    (tmp_path / "src" / "c.txt").write_text("no match here\n", encoding="utf-8")
    (tmp_path / "src" / "deep" / "d.py").write_text("needle = 2\n", encoding="utf-8")
    return tmp_path


def test_search_by_a_name_glob_finds_files_anywhere_in_walk_order(tree: Path) -> None:
    page = search_files(str(tree), glob="*.py", base=str(tree))

    assert page.splitlines() == [".: 3 files", "a.py", "src/b.py", "src/deep/d.py"]


def test_a_glob_with_a_slash_matches_the_path_below_the_directory(tree: Path) -> None:
    page = search_files(str(tree), glob="src/**/*.py", base=str(tree))

    assert results_in(page) == ["src/b.py", "src/deep/d.py"]


def test_search_with_no_glob_and_no_pattern_finds_every_file(tree: Path) -> None:
    page = search_files(str(tree), base=str(tree))

    assert results_in(page) == ["a.py", "src/b.py", "src/c.txt", "src/deep/d.py"]


def test_a_pattern_reports_path_line_and_text(tree: Path) -> None:
    page = search_files(str(tree), pattern="needle", base=str(tree))

    assert page.splitlines() == [
        ".: 3 matching lines in 2 files",
        "src/b.py:1: def needle():",
        "src/b.py:3: # needle again",
        "src/deep/d.py:1: needle = 2",
    ]


def test_a_pattern_and_a_glob_narrow_each_other(tree: Path) -> None:
    page = search_files(str(tree), glob="d.py", pattern="needle", base=str(tree))

    assert results_in(page) == ["src/deep/d.py:1: needle = 2"]


def test_search_below_a_subdirectory_shows_paths_from_the_base(tree: Path) -> None:
    page = search_files(str(tree / "src" / "deep"), pattern="needle", base=str(tree))

    assert results_in(page) == ["src/deep/d.py:1: needle = 2"]


def test_search_of_one_file_searches_that_file(tree: Path) -> None:
    page = search_files(str(tree / "src" / "b.py"), pattern="again", base=str(tree))

    assert results_in(page) == ["src/b.py:3: # needle again"]


def test_no_match_says_so(tree: Path) -> None:
    assert search_files(str(tree), pattern="absent", base=str(tree)) == (
        ".: no matching lines"
    )
    assert search_files(str(tree), glob="*.rs", base=str(tree)) == ".: no files"


def test_binary_files_are_not_searched(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"needle\x00\x01")
    (tmp_path / "text.txt").write_text("needle\n", encoding="utf-8")

    page = search_files(str(tmp_path), pattern="needle", base=str(tmp_path))

    assert results_in(page) == ["text.txt:1: needle"]


def test_dot_git_is_never_walked(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("needle\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("needle\n", encoding="utf-8")

    assert results_in(search_files(str(tmp_path), base=str(tmp_path))) == ["a.txt"]
    assert results_in(
        search_files(str(tmp_path), pattern="needle", base=str(tmp_path))
    ) == ["a.txt:1: needle"]


def test_symlinks_are_never_walked_or_searched(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "real").mkdir(parents=True)
    (root / "real" / "inside.txt").write_text("needle\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("needle\n", encoding="utf-8")
    (root / "escape").symlink_to(outside)
    (root / "secret.txt").symlink_to(outside / "secret.txt")
    (root / "again").symlink_to(root / "real")
    (root / "loop").symlink_to(root)

    files = results_in(search_files(str(root), base=str(root)))
    lines = results_in(search_files(str(root), pattern="needle", base=str(root)))

    assert files == ["real/inside.txt"]
    assert lines == ["real/inside.txt:1: needle"]


def test_a_long_matched_line_is_cut_to_a_fixed_width(tmp_path: Path) -> None:
    (tmp_path / "min.js").write_text("needle" + "x" * 5000 + "\n", encoding="utf-8")

    [result] = results_in(
        search_files(str(tmp_path), pattern="needle", base=str(tmp_path))
    )

    text = result.split(": ", 1)[1]
    assert text == "needle" + "x" * (MATCH_WIDTH - 6) + "..."


def test_a_control_character_in_a_line_cannot_break_the_page(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("needle\x1b[2J\tend\n", encoding="utf-8")

    [result] = results_in(
        search_files(str(tmp_path), pattern="needle", base=str(tmp_path))
    )

    assert result == "a.txt:1: needle\\x1b[2J\tend"


def test_search_pages_through_matches_within_and_across_files(tmp_path: Path) -> None:
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text("hit\nmiss\nhit\nhit\n", encoding="utf-8")

    seen = everything(tmp_path, tmp_path, pattern="hit", limit=2)

    assert seen == [
        "a.txt:1: hit",
        "a.txt:3: hit",
        "a.txt:4: hit",
        "b.txt:1: hit",
        "b.txt:3: hit",
        "b.txt:4: hit",
    ]


def test_search_pages_through_files_across_directories(tree: Path) -> None:
    assert everything(tree, tree, limit=1) == [
        "a.py",
        "src/b.py",
        "src/c.txt",
        "src/deep/d.py",
    ]


def test_search_is_identical_for_the_same_tree(tree: Path) -> None:
    first = search_files(str(tree), pattern="needle", limit=1, base=str(tree))
    cursor = cursor_of(first)
    assert cursor is not None

    assert search_files(str(tree), pattern="needle", limit=1, base=str(tree)) == first
    assert search_files(
        str(tree), pattern="needle", cursor=cursor, limit=1, base=str(tree)
    ) == search_files(
        str(tree), pattern="needle", cursor=cursor, limit=1, base=str(tree)
    )


def test_a_cursor_from_another_pattern_is_refused(tree: Path) -> None:
    cursor = cursor_of(
        search_files(str(tree), pattern="needle", limit=1, base=str(tree))
    )
    assert cursor is not None

    with pytest.raises(ValueError, match="different call"):
        search_files(str(tree), pattern="import", cursor=cursor, base=str(tree))


def test_an_invalid_pattern_is_named(tree: Path) -> None:
    with pytest.raises(ValueError, match=r"invalid pattern '\('"):
        search_files(str(tree), pattern="(", base=str(tree))


@pytest.mark.parametrize(
    ("glob", "found"),
    [
        ("src/*.py", ["src/b.py"]),
        ("**/d.py", ["src/deep/d.py"]),
        ("./a.py", ["a.py"]),
        ("src/[bc].*", ["src/b.py", "src/c.txt"]),
        ("src/[!b].*", ["src/c.txt"]),
        ("*.t?t", ["src/c.txt"]),
    ],
)
def test_glob_forms(tree: Path, glob: str, found: list[str]) -> None:
    assert results_in(search_files(str(tree), glob=glob, base=str(tree))) == found


def test_search_names_a_path_that_is_neither_file_nor_directory(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "pipe")

    with pytest.raises(ValueError, match="not a regular file or a directory"):
        search_files(str(tmp_path / "pipe"), pattern="x", base=str(tmp_path))
    with pytest.raises(FileNotFoundError):
        search_files(str(tmp_path / "missing"), base=str(tmp_path))
