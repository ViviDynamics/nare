"""The release record a caller relies on: what the README pins, and what was
withdrawn.

A published release is never deleted or moved (docs/contract.md, Releases).
When one went anyway, docs/releases.md lists it with its replacement. These
tests keep the two documents honest with each other, offline: the README never
pins a withdrawn version, and every withdrawn version names a replacement that
was not withdrawn too.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CALVER = re.compile(r"^(\d{4})\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def calver(version: str) -> tuple[int, int, int]:
    match = CALVER.match(version)
    assert match, f"{version!r} is not a CalVer release"
    year, month, patch = (int(group) for group in match.groups())
    return year, month, patch


def withdrawn() -> dict[str, str]:
    """Each withdrawn version, mapped to the release that replaces it."""
    table = (ROOT / "docs" / "releases.md").read_text(encoding="utf-8")
    rows: dict[str, str] = {}
    for line in table.splitlines():
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and CALVER.match(cells[0]):
            assert cells[0] not in rows, f"{cells[0]} is listed twice"
            rows[cells[0]] = cells[-1]
    return rows


def readme_pin() -> str:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    pins = re.findall(r"^\s*V=(\S+)$", readme, flags=re.MULTILINE)
    assert len(pins) == 1, pins
    return pins[0]


def test_the_withdrawn_table_lists_versions() -> None:
    rows = withdrawn()
    assert "2026.9.7" in rows
    assert "2026.10.32" in rows


def test_every_withdrawn_version_names_a_replacement_that_survives() -> None:
    rows = withdrawn()
    for version, replacement in rows.items():
        calver(replacement)
        assert replacement not in rows, (
            f"{version} is replaced by withdrawn {replacement}"
        )
        assert calver(replacement) > calver(version), (version, replacement)


def test_the_readme_pins_a_release_that_was_not_withdrawn() -> None:
    pin = readme_pin()
    calver(pin)
    assert pin not in withdrawn()


def test_the_readme_install_commands_use_the_pin() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "releases/download/$V/nare-$V-py3-none-any.whl" in readme
    assert "ghcr.io/vividynamics/nare:$V" in readme


def test_the_contract_states_the_policy_and_links_the_record() -> None:
    contract = (ROOT / "docs" / "contract.md").read_text(encoding="utf-8")
    assert "## Releases" in contract
    assert "never deleted or moved" in contract
    assert "(releases.md)" in contract
