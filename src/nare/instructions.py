"""A project's own instructions for the person-facing forms: AGENTS.md, with
CLAUDE.md where a directory has none.

Loaded in nare, nare "<task>", nare -p and nare exec, never in nare run unless
it asks with --project-instructions, so a caller's runs stay what it sent.
The files are NARE_HOME/AGENTS.md, then one per directory from the project
root (the nearest directory above with .git, else the working directory) down
to the working directory, outermost first, as Codex reads them. Their total is
capped at 32 KiB, as Codex caps it, and a cut says so.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from nare.events import Event
from nare.session import nare_home

MAX_BYTES = 32 * 1024
NAMES = ("AGENTS.md", "CLAUDE.md")
CUT = "[nare: project instructions cut at 32 KiB]"
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

    def event(self) -> Event:
        cut = " (cut at 32 KiB)" if self.truncated else ""
        return Event(
            "progress",
            f"project instructions: {', '.join(self.files)}{cut}",
            {
                "project_instructions": {
                    "files": self.files,
                    "bytes": self.bytes,
                    "truncated": self.truncated,
                }
            },
        )


def project_root(cwd: str) -> Path:
    here = Path(os.path.realpath(cwd))
    for above in (here, *here.parents):
        if (above / ".git").exists():
            return above
    return here


def files_for(cwd: str) -> list[Path]:
    here = Path(os.path.realpath(cwd))
    root = project_root(cwd)
    chain = [
        root,
        *reversed([p for p in here.parents if p.is_relative_to(root) and p != root]),
    ]
    if here != root:
        chain.append(here)
    found: list[Path] = []
    home = nare_home()
    if home is not None and (home / "AGENTS.md").is_file():
        found.append(home / "AGENTS.md")
    for directory in chain:
        for name in NAMES:
            if (directory / name).is_file():
                found.append(directory / name)
                break
    return found


def load(cwd: str) -> Instructions | None:
    """The instructions for a run in `cwd`, or None when there are none."""
    files = files_for(cwd)
    blocks: list[str] = []
    loaded: list[str] = []
    used = 0
    truncated = False
    for path in files:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        room = MAX_BYTES - used
        if len(data) > room:
            data, truncated = data[:room], True
        used += len(data)
        loaded.append(str(path))
        content = data.decode("utf-8", errors="ignore").rstrip()
        blocks.append(f"## {path}\n\n{content}")
        if truncated:
            blocks.append(CUT)
            break
    if not loaded:
        return None
    return Instructions(
        text="\n\n".join([HEADER, *blocks]),
        files=loaded,
        bytes=used,
        truncated=truncated,
    )
