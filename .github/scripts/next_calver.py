"""The next CalVer for this repository: `YYYY.M.N`.

The same scheme elm and conductor mint. Month is unpadded and the counter runs
per month, which keeps the result valid SemVer.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable
from datetime import UTC, datetime

# No leading zeros, SemVer's own rule: a looser pattern reads `2026.09.01` as
# month 9 patch 1 and mints a version that skips or collides.
_TAG = re.compile(r"^v?(\d{4})\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def next_calver(tags: Iterable[str], *, year: int, month: int) -> str:
    patches = []
    for tag in tags:
        match = _TAG.match((tag or "").strip())
        if not match:
            continue
        tag_year, tag_month, patch = (int(group) for group in match.groups())
        if tag_year == year and tag_month == month:
            patches.append(patch)
    return f"{year}.{month}.{max(patches) + 1 if patches else 0}"


if __name__ == "__main__":
    now = datetime.now(UTC)
    existing = subprocess.run(
        ["git", "tag", "-l"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    sys.stdout.write(next_calver(existing, year=now.year, month=now.month) + "\n")
