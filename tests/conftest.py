"""Shared test setup.

Every test gets its own NARE_HOME, so a run that saves a session without
--session writes under the test's temporary directory, never under the
developer's real ~/.nare.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nare.transport import _DISCOVERED


@pytest.fixture(autouse=True)
def _isolated_nare_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARE_HOME", str(tmp_path / "nare-home"))


@pytest.fixture(autouse=True)
def _fresh_model_discovery() -> None:
    # Window and price discovery is cached per process; no test sees another's.
    _DISCOVERED.clear()
