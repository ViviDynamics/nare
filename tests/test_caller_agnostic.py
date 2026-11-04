"""nare names no caller (docs/adr/0008-caller-agnostic.md).

A generic tool stops being generic one caller's word at a time: in a flag, a
docstring, a comment explaining a behaviour by what one caller does with it.
This test makes that cheap to notice. It fails when another product's name
appears in the source, a flag, a tool name, the living documentation, the
tests or the benchmarks.

History is out of scope: decision records written before ADR 0008, plans,
specs and evidence describe what was true when they were written.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from nare.cli import build_parser
from nare.tools import TOOL_SCHEMAS, TOOLS

ROOT = Path(__file__).resolve().parent.parent

# The organization's other products. A caller's adapter may use these names;
# nare does not. noul is not here: the benchmark judge's wire format uses it
# as an answer type.
CALLERS = (
    "coordinare",
    "conductor",
    "qare",
    "progettare",
    "scrutare",
    "steward",
    "dettmore",
    "reusify",
    "elm",
)

NAMES = re.compile(
    r"(?<![a-z])(" + "|".join(CALLERS) + r")(?:'?s)?(?![a-z])", re.IGNORECASE
)

SCANNED = ("src", "tests", "benchmarks", "docs", "README.md")

# Written before ADR 0008 and kept as written.
HISTORY = (
    "docs/plans/",
    "docs/superpowers/",
    "docs/evidence/",
    *(f"docs/adr/000{n}-" for n in range(1, 8)),
)

THIS_FILE = Path(__file__).resolve()


def names_in(text: str) -> list[str]:
    return [match.group(0) for match in NAMES.finditer(text)]


def scanned_files(root: Path) -> Iterator[Path]:
    for entry in SCANNED:
        path = root / entry
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        for candidate in candidates:
            relative = candidate.relative_to(root).as_posix()
            if (
                not candidate.is_file()
                or candidate.resolve() == THIS_FILE
                or relative.startswith(HISTORY)
                or "__pycache__" in relative
            ):
                continue
            yield candidate


def hits(root: Path) -> list[str]:
    found = []
    for path in scanned_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for name in names_in(line):
                found.append(f"{path.relative_to(root)}:{number}: {name}")
    return found


def option_strings(parser: argparse.ArgumentParser) -> Iterator[str]:
    for action in parser._actions:
        yield from action.option_strings
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                yield name
                yield from option_strings(sub)


@pytest.mark.parametrize(
    "text",
    [
        "Conductor resumes the session",
        "conductor's relay_feedback",
        "test_event_fields_match_conductors_backend_event",
        "QARE_TOKEN",
        "a scrutare-style review",
        "made for Coordinare",
    ],
)
def test_a_caller_name_is_found(text: str) -> None:
    assert names_in(text)


@pytest.mark.parametrize(
    "text",
    ["helm chart", "elements", "squared", "the conductance", "stewardship"],
)
def test_an_ordinary_word_is_not(text: str) -> None:
    assert names_in(text) == []


def test_a_name_planted_in_src_fails_the_guard(tmp_path: Path) -> None:
    (tmp_path / "src" / "nare").mkdir(parents=True)
    (tmp_path / "src" / "nare" / "loop.py").write_text(
        "# The conductor adapter maps done to completed.\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "old.md").write_text("Conductor\n", "utf-8")
    assert hits(tmp_path) == ["src/nare/loop.py:1: conductor"]


def test_no_caller_is_named_in_the_source_docs_tests_or_benchmarks() -> None:
    assert hits(ROOT) == []


def test_no_flag_names_a_caller() -> None:
    flags = list(option_strings(build_parser()))
    assert "--provider" in flags
    assert [flag for flag in flags if names_in(flag)] == []


def test_no_tool_names_a_caller() -> None:
    names = set(TOOLS) | {schema["name"] for schema in TOOL_SCHEMAS}
    assert "read" in names
    assert [name for name in names if names_in(name)] == []
