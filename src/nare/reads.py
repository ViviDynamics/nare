"""Read requirements: the reads a run must make before its final answer is taken.

A caller sets `--require-reads N` (at least N distinct files) and `--require-read
PATH` (each named file), or `run(require_reads=..., require_read=...)`. A final
answer that would otherwise be accepted is held back while a requirement is
unmet, and the model is told what is missing. Each hold is a correction charged
against the correction budget (corrections.py).

Only a read that succeeded and stayed inside the root counts: those, and only
those, leave an evidence record with the tool `read` (tools.py). A failed read
or one refused as outside the root leaves none.

The surface for other modules is `ReadRequirement`, `build()` to make one from
caller input, `unmet()` for the gaps in a session, and `read_record()` for the
result's `reads` list.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nare.session import Session


@dataclass(frozen=True)
class ReadRequirement:
    """What a run must have read: a count of distinct files, and named paths.

    Paths are root-relative POSIX strings, as evidence records them. An inactive
    requirement (count 0, no paths) holds nothing.
    """

    count: int = 0
    paths: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return self.count > 0 or bool(self.paths)


NO_READS = ReadRequirement()


def build(
    count: int,
    paths: Sequence[str],
    *,
    root: Path | None,
) -> ReadRequirement:
    """A requirement from caller input, each path in the form evidence records.

    With a root, the read tool resolves a path (following symlinks) before it
    reads, and records it relative to the resolved root, so a required path is
    resolved the same way: a link inside the root counts as its target, and a
    link that points outside the root is refused here. Without a root the tool
    reads any path and records it relative to the working directory when it is
    under it, and absolute otherwise.

    Refuses a count that is not a non-negative integer, and an empty path.
    """
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("require_reads must be a non-negative integer")
    normal: list[str] = []
    for given in paths:
        if not isinstance(given, str) or not given.strip():
            raise ValueError("require_read needs a non-empty path")
        if root is None:
            base = os.getcwd()
            absolute = os.path.abspath(os.path.join(base, given))
            shown = (
                os.path.relpath(absolute, base).replace(os.sep, "/")
                if absolute.startswith(base.rstrip(os.sep) + os.sep)
                else absolute
            )
        else:
            base_path = root.resolve()
            candidate = Path(given)
            if not candidate.is_absolute():
                candidate = base_path / candidate
            resolved = candidate.resolve()
            if not resolved.is_relative_to(base_path):
                raise ValueError(
                    f"required path {given} is outside the root {base_path}"
                )
            shown = resolved.relative_to(base_path).as_posix()
        if shown not in normal:
            normal.append(shown)
    return ReadRequirement(count=count, paths=tuple(normal))


def read_paths(s: Session) -> dict[str, dict[str, Any]]:
    """Each distinct file the session has read, once, with its latest digest."""
    latest: dict[str, dict[str, Any]] = {}
    for record in s.evidence:
        if record.get("tool") == "read":
            latest[record["path"]] = {
                "path": record["path"],
                "sha256": record["sha256"],
                "size": record["size"],
            }
    return latest


def unmet(s: Session, requirement: ReadRequirement) -> list[str]:
    """What the session still has to read, in words the model can act on."""
    read = read_paths(s)
    gaps: list[str] = []
    if requirement.count and len(read) < requirement.count:
        gaps.append(
            f"read at least {requirement.count} distinct files "
            f"(read {len(read)} so far)"
        )
    gaps.extend(f"read {path}" for path in requirement.paths if path not in read)
    return gaps


def read_record(s: Session) -> list[dict[str, Any]]:
    """The result's `reads`: each distinct file read, with its digest."""
    return list(read_paths(s).values())
