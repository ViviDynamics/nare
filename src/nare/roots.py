"""The roots a policy confines to, pinned when the policy is built.

`--root` and each `--add-root` are resolved once, following any symlink in the
path given, and the real path reached is kept. Everything that confines to the
roots reads the pinned paths: the file tools' path check, bash's working
directory, and the bash sandbox's profile. Resolving a root again at each call
would let anything that replaced it with a link (a shell command removing the
root and linking another folder in its place, or renaming a root away through
a writable parent) move the confinement to wherever the link points.

Before each tool call the pins are checked. A root that no longer resolves to
its pinned path, or is no longer a directory there, was replaced under the run,
and nothing reached through it is trusted again: the check fails from then on,
for every copy of the policy, so the run stops (`stop_reason` `root_replaced`,
docs/contract.md).

The path is what is pinned, not the directory's inode: a directory made again
at the same path is confined exactly as the first one was, and some file
systems (an overlay across file systems, some FUSE mounts) do not keep a
directory's inode number steady, which would stop runs that changed nothing.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


def _is_directory(path: Path) -> bool:
    """Whether `path` itself is a directory: a link there is not followed."""
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except (OSError, ValueError):
        return False


@dataclass(frozen=True)
class Pin:
    """One root as it was when the policy was built.

    `directory` is False when nothing was there yet (a library caller may name
    a folder before making it); only its real path is held to then.
    """

    flag: str
    given: Path
    real: Path
    directory: bool

    @classmethod
    def take(cls, flag: str, given: Path) -> Pin:
        real = given.resolve()
        return cls(flag, given, real, _is_directory(real))

    def problem(self) -> str | None:
        """How the root changed since it was pinned, or None when it has not."""
        try:
            now = self.real.resolve()
        except (OSError, RuntimeError) as exc:
            return f"{self.real} no longer resolves ({exc})"
        if now != self.real:
            return f"{self.real} now leads to {now}"
        if self.directory and not _is_directory(self.real):
            return f"{self.real} is no longer a directory"
        return None


class RootPins:
    """The pinned root and added roots, and whether one was replaced.

    One instance is shared by every copy of a policy (the loop copies it with
    dataclasses.replace), so a replacement seen through one copy is seen by
    all of them, and stays seen: putting the root back does not undo what
    was reachable while it was replaced.
    """

    def __init__(self, root: Path, extra_roots: Sequence[Path]) -> None:
        self.root = Pin.take("--root", root)
        self.extra = tuple(Pin.take("--add-root", path) for path in extra_roots)
        self.replaced: str | None = None

    def holds(self, root: Path, extra_roots: Sequence[Path]) -> bool:
        """Whether these pins are for the roots given."""
        return self.root.given == root and tuple(p.given for p in self.extra) == tuple(
            extra_roots
        )

    def check(self) -> str | None:
        """Why the run may no longer use its roots, or None while it may."""
        if self.replaced is None:
            for pin in (self.root, *self.extra):
                problem = pin.problem()
                if problem is not None:
                    self.replaced = (
                        f"{pin.flag} {pin.given} was replaced during the run: {problem}"
                    )
                    break
        return self.replaced
