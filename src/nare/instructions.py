"""A project's own instructions for the person-facing forms: AGENTS.md, with
CLAUDE.md where a directory has none.

Loaded in nare, nare "<task>", nare -p and nare exec, never in nare run unless
it asks with --project-instructions, so a caller's runs stay what it sent.
The files are NARE_HOME/AGENTS.md, then one per directory from the project
root (the nearest directory above with .git, else the working directory) down
to the working directory, outermost first, as Codex reads them. Their total is
capped at 32 KiB, as Codex caps it, and a cut says so.

A file is read only where it really is: one that resolves outside the folder
it was found for (the project root, or NARE_HOME for the home file), through a
symbolic link, is skipped, and the progress event names it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from nare.events import Event
from nare.session import nare_home

MAX_BYTES = 32 * 1024
NAMES = ("AGENTS.md", "CLAUDE.md")
CUT = "[nare: project instructions cut at 32 KiB]"
OUTSIDE = "outside the project"
HEADER = (
    "# Project instructions\n\n"
    "From this project's AGENTS.md files (CLAUDE.md where a directory has "
    "none), outermost first; a nearer file is more specific."
)


@dataclass(frozen=True)
class Instructions:
    text: str
    files: list[str]
    bytes: int
    truncated: bool
    skipped: list[str] = field(default_factory=list)

    def event(self) -> Event:
        cut = " (cut at 32 KiB)" if self.truncated else ""
        message = f"project instructions: {', '.join(self.files) or 'none'}{cut}"
        if self.skipped:
            message += (
                f"; skipped {', '.join(self.skipped)}: {OUTSIDE} (a link out of it)"
            )
        detail: dict[str, object] = {
            "files": self.files,
            "bytes": self.bytes,
            "truncated": self.truncated,
        }
        if self.skipped:
            detail["skipped"] = [
                {"file": path, "reason": OUTSIDE} for path in self.skipped
            ]
        return Event("progress", message, {"project_instructions": detail})


def project_root(cwd: str) -> Path:
    here = Path(os.path.realpath(cwd))
    for above in (here, *here.parents):
        if (above / ".git").exists():
            return above
    return here


def files_for(cwd: str) -> list[tuple[Path, Path]]:
    """Each instructions file for `cwd`, with the folder it must stay inside."""
    here = Path(os.path.realpath(cwd))
    root = project_root(cwd)
    chain = [
        root,
        *reversed([p for p in here.parents if p.is_relative_to(root) and p != root]),
    ]
    if here != root:
        chain.append(here)
    found: list[tuple[Path, Path]] = []
    home = nare_home()
    if home is not None and (home / "AGENTS.md").is_file():
        found.append((home / "AGENTS.md", home))
    for directory in chain:
        for name in NAMES:
            if (directory / name).is_file():
                found.append((directory / name, root))
                break
    return found


def contained(path: Path, base: Path) -> Path | None:
    """Where `path` really is, when that is inside `base`; else None."""
    real = Path(os.path.realpath(path))
    return real if real.is_relative_to(os.path.realpath(base)) else None


def read_head(path: Path, limit: int) -> bytes:
    """At most `limit` + 1 bytes of `path`: enough to tell a cut is needed."""
    with path.open("rb") as handle:
        return handle.read(limit + 1)


def load(cwd: str) -> Instructions | None:
    """The instructions for a run in `cwd`, or None when there are none."""
    blocks: list[str] = []
    loaded: list[str] = []
    skipped: list[str] = []
    used = 0
    truncated = False
    for path, base in files_for(cwd):
        real = contained(path, base)
        if real is None:
            skipped.append(str(path))
            continue
        room = MAX_BYTES - used
        try:
            data = read_head(real, room)
        except OSError:
            continue
        if len(data) > room:
            data, truncated = data[:room], True
        used += len(data)
        loaded.append(str(path))
        content = data.decode("utf-8", errors="ignore").rstrip()
        blocks.append(f"## {path}\n\n{content}")
        if truncated:
            blocks.append(CUT)
            break
    if not loaded and not skipped:
        return None
    return Instructions(
        text="\n\n".join([HEADER, *blocks]) if loaded else "",
        files=loaded,
        bytes=used,
        truncated=truncated,
        skipped=skipped,
    )
