"""Shared test setup.

Every test gets its own NARE_HOME, so a run that saves a session without
--session writes under the test's temporary directory, never under the
developer's real ~/.nare.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_nare_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARE_HOME", str(tmp_path / "nare-home"))
