"""The release version: `YYYY.M.N`, matching elm and conductor."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, cast

_MODULE = Path(__file__).parent.parent / ".github" / "scripts" / "next_calver.py"


class _NextCalver(Protocol):
    def __call__(self, tags: Iterable[str], *, year: int, month: int) -> str: ...


def _next_calver() -> _NextCalver:
    spec = importlib.util.spec_from_file_location("next_calver", _MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_NextCalver, module.next_calver)


def test_the_first_release_of_a_month_starts_at_zero() -> None:
    assert _next_calver()([], year=2026, month=9) == "2026.9.0"


def test_it_continues_the_months_counter() -> None:
    tags = ["2026.9.0", "2026.9.1", "2026.9.2"]

    assert _next_calver()(tags, year=2026, month=9) == "2026.9.3"


def test_it_ignores_other_months_and_years() -> None:
    tags = ["2026.8.41", "2025.9.99", "2026.10.3"]

    assert _next_calver()(tags, year=2026, month=9) == "2026.9.0"


def test_the_counter_is_numeric_not_lexical() -> None:
    tags = ["2026.9.8", "2026.9.9", "2026.9.10"]

    assert _next_calver()(tags, year=2026, month=9) == "2026.9.11"


def test_zero_padded_tags_are_not_candidates() -> None:
    tags = ["2026.09.01", "2026.09.01.3", "2026.9.0"]

    assert _next_calver()(tags, year=2026, month=9) == "2026.9.1"


def test_a_v_prefixed_tag_still_counts() -> None:
    assert _next_calver()(["v2026.9.4"], year=2026, month=9) == "2026.9.5"


def test_unrelated_tags_are_ignored() -> None:
    tags = ["latest", "nightly", "", "2026.9.x"]

    assert _next_calver()(tags, year=2026, month=9) == "2026.9.0"
