"""What the agent left behind in its container, read once nare has exited.

A check runs in a fresh container over the working directory, so by then
every process the agent left running and every file it wrote outside /work
is gone. The agent's script runs this file as its last step and writes
/out/after.json for the checks. The image holds nare, not the benchmarks, so
the source travels in an environment variable: standard library only.

The end state, not the commands: pg_ctl calls setsid and escapes nare's
process-group probe, and a database can be installed or started under more
spellings than any pattern lists. What is left is the same however it got
there.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable
from pathlib import Path

# Only a PostgreSQL server install or data directory has these; initdb writes
# PG_VERSION. A client library, psycopg's libpq included, has none of them.
DATABASE_FILES = frozenset({"initdb", "pg_ctl", "postgres", "PG_VERSION"})


def _stat(proc: Path, pid: int) -> tuple[str, int, int] | None:
    """State, parent and process group, or None for a pid that is gone."""
    try:
        raw = (proc / str(pid) / "stat").read_bytes()
    except OSError:
        return None
    # comm, in parentheses, can hold spaces and ")": split after the last.
    state, ppid, pgrp, *_ = raw[raw.rindex(b")") + 2 :].split()
    return state.decode(), int(ppid), int(pgrp)


def harness(proc: Path, pid: int) -> set[int]:
    """This process and everything above it: the agent's script, not its work."""
    seen: set[int] = set()
    while pid > 0 and pid not in seen:
        seen.add(pid)
        stat = _stat(proc, pid)
        if stat is None:
            break
        pid = stat[1]
    return seen


def processes(proc: Path, skip: set[int]) -> list[dict[str, object]]:
    """Every process still running, but for those in `skip`.

    A zombie is not running. The script's shell is PID 1 and reaps nothing,
    so an orphan that finished stays listed as one.
    """
    found: list[dict[str, object]] = []
    for pid in sorted(
        int(entry.name) for entry in proc.iterdir() if entry.name.isdigit()
    ):
        stat = _stat(proc, pid)
        if pid in skip or stat is None or stat[0] in ("Z", "X"):
            continue
        try:
            raw = (proc / str(pid) / "cmdline").read_bytes()
        except OSError:
            continue
        args = " ".join(
            part.decode(errors="replace") for part in raw.split(b"\0") if part
        )
        found.append({"pid": pid, "pgid": stat[2], "args": args[:200]})
    return found


def database_files(root: Path, skip: Iterable[Path]) -> list[str]:
    """Every file under `root` with a name only a PostgreSQL server has."""
    skipped = {str(path) for path in skip}
    found: list[str] = []
    for top, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if os.path.join(top, d) not in skipped]
        found += [os.path.join(top, name) for name in files if name in DATABASE_FILES]
    return sorted(found)


def snapshot(
    out: Path,
    *,
    proc: Path = Path("/proc"),
    root: Path = Path("/"),
    pid: int | None = None,
) -> None:
    me = os.getpid() if pid is None else pid
    after = {
        "processes": processes(proc, skip=harness(proc, me)),
        "database_files": database_files(root, skip=(proc, root / "sys", root / "dev")),
    }
    out.write_text(json.dumps(after, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    snapshot(Path(sys.argv[1]))
