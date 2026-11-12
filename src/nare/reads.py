"""Read requirements: the reads a run must make before its final answer is taken.

A caller sets `--require-reads N` (at least N distinct files), `--require-read
PATH` (each named file) and `--require-cited-reads POINTER` (each path a typed
answer cites, found by a JSON pointer into it), or the matching arguments of
`run()`. A final answer that would otherwise be accepted is held back while a
requirement is unmet, and the model is told what is missing. Each hold is a
correction charged against the correction budget (corrections.py).

Only a read that succeeded and stayed inside the root counts: those, and only
those, leave an evidence record with the tool `read` (tools.py). A failed read
or one refused as outside the root leaves none.

The surface for other modules is `ReadRequirement`, `build()` to make one from
caller input, `unmet()` and `cited_unmet()` for the gaps in a session, and
`read_record()` for the result's `reads` list.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nare.session import Session


@dataclass(frozen=True)
class ReadRequirement:
    """What a run must have read: a count of distinct files, named paths, and
    the paths a typed answer cites.

    Paths are root-relative POSIX strings, as evidence records them. `cited`
    holds JSON pointers (see `parse_pointer`), resolved against the answer when
    it is checked; `root` is the directory those paths are relative to, or None
    for the working directory. `cited_map` maps a cited value, exactly as the
    answer holds it, to the paths any one of which satisfies it (#297). An
    inactive requirement (count 0, no paths, no pointers) holds nothing.
    """

    count: int = 0
    paths: tuple[str, ...] = ()
    cited: tuple[str, ...] = ()
    root: Path | None = None
    cited_map: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.count > 0 or bool(self.paths) or bool(self.cited)


NO_READS = ReadRequirement()


def parse_pointer(pointer: str) -> tuple[str, ...]:
    """The tokens of a JSON pointer, with `*` kept as the wildcard.

    RFC 6901 syntax: `/` separates tokens, `~1` is `/` and `~0` is `~`. A pointer
    names at least one token; an empty token and an unknown escape are refused.
    """
    if not pointer.startswith("/"):
        raise ValueError(f"pointer {pointer!r} must start with /")
    tokens: list[str] = []
    for raw in pointer[1:].split("/"):
        if raw == "":
            raise ValueError(f"pointer {pointer!r} has an empty token")
        token = raw.replace("~1", "/")
        if "~" in token.replace("~0", ""):
            raise ValueError(f"pointer {pointer!r} has an unknown ~ escape")
        tokens.append(token.replace("~0", "~"))
    return tuple(tokens)


def cited_values(answer: Any, pointers: Sequence[str]) -> list[str]:
    """Each string the answer holds at a pointer, once, in the order found.

    A `*` token matches every array index or object key. A pointer that matches
    nothing matches nothing. A value that is not a string is not a path, and is
    skipped: the schema is what types it.
    """
    found: list[str] = []
    for pointer in pointers:
        for value in _walk(answer, parse_pointer(pointer)):
            if value not in found:
                found.append(value)
    return found


def _walk(node: Any, tokens: tuple[str, ...]) -> Iterator[str]:
    if not tokens:
        if isinstance(node, str):
            yield node
        return
    head, rest = tokens[0], tokens[1:]
    children: list[Any]
    if head == "*":
        if isinstance(node, dict):
            children = list(node.values())
        elif isinstance(node, list):
            children = list(node)
        else:
            return
    elif isinstance(node, dict):
        children = [node[head]] if head in node else []
    elif isinstance(node, list) and head.isdigit() and int(head) < len(node):
        children = [node[int(head)]]
    else:
        return
    for child in children:
        yield from _walk(child, rest)


def shown(given: str, root: Path | None) -> str | None:
    """A path in the form evidence records it: relative to the root, or to the
    working directory when there is no root and the path is under it, absolute
    otherwise. None when the path lies outside the root or cannot be resolved.

    A link inside the root counts as its target: the read tool resolves a path
    (following symlinks) before it reads, so a required path is resolved the same
    way. Without a root the read tool reads any path.
    """
    try:
        if root is None:
            base = os.getcwd()
            absolute = os.path.abspath(os.path.join(base, given))
            if absolute.startswith(base.rstrip(os.sep) + os.sep):
                return os.path.relpath(absolute, base).replace(os.sep, "/")
            return absolute
        base_path = root.resolve()
        candidate = Path(given)
        if not candidate.is_absolute():
            candidate = base_path / candidate
        resolved = candidate.resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    if not resolved.is_relative_to(base_path):
        return None
    return resolved.relative_to(base_path).as_posix()


def build(
    count: int,
    paths: Sequence[str],
    *,
    root: Path | None,
    cited: Sequence[str] = (),
    cited_map: Mapping[str, str | Sequence[str]] | None = None,
) -> ReadRequirement:
    """A requirement from caller input, each path in the form evidence records.

    Refuses a count that is not a non-negative integer, an empty path, a path
    outside the root, a malformed pointer, and a malformed cited read map or
    one given without cited pointers.
    """
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("require_reads must be a non-negative integer")
    normal: list[str] = []
    for given in paths:
        if not isinstance(given, str) or not given.strip():
            raise ValueError("require_read needs a non-empty path")
        path = shown(given, root)
        if path is None:
            base_path = root.resolve() if root is not None else os.getcwd()
            raise ValueError(f"required path {given} is outside the root {base_path}")
        if path not in normal:
            normal.append(path)
    pointers: list[str] = []
    for pointer in cited:
        parse_pointer(pointer)
        if pointer not in pointers:
            pointers.append(pointer)
    return ReadRequirement(
        count=count,
        paths=tuple(normal),
        cited=tuple(pointers),
        root=root,
        cited_map=_build_cited_map(cited_map, root, bool(pointers)),
    )


def _build_cited_map(
    given: Any, root: Path | None, has_pointers: bool
) -> dict[str, tuple[str, ...]]:
    """The cited read map with each target in the form evidence records."""
    if given is None:
        return {}
    if not isinstance(given, Mapping):
        raise ValueError("cited read map must be an object of cited value to path")
    if not has_pointers:
        raise ValueError("a cited read map needs cited pointers to apply to")
    built: dict[str, tuple[str, ...]] = {}
    for key, value in given.items():
        if not isinstance(key, str) or not key:
            raise ValueError("cited read map has an empty key")
        targets = [value] if isinstance(value, str) else value
        if (
            not isinstance(targets, Sequence)
            or isinstance(targets, str)
            or not targets
            or not all(isinstance(t, str) and t.strip() for t in targets)
        ):
            raise ValueError(
                f"cited read map entry {key} must be a path or a non-empty list "
                "of paths"
            )
        normal: list[str] = []
        for target in targets:
            path = shown(target, root)
            if path is None:
                base = root.resolve() if root is not None else os.getcwd()
                raise ValueError(
                    f"cited read map path {target} for {key} is outside the root {base}"
                )
            if path not in normal:
                normal.append(path)
        built[key] = tuple(normal)
    return built


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


def cited_unmet(s: Session, requirement: ReadRequirement, answer: Any) -> list[str]:
    """The paths the answer cites that the session has not read.

    A cited path outside the root can never be read, so the gap asks the model
    to remove it rather than to read it.
    """
    read = read_paths(s)
    gaps: list[str] = []
    for given in cited_values(answer, requirement.cited):
        targets = requirement.cited_map.get(given)
        if targets is not None:
            if not any(target in read for target in targets):
                which = (
                    targets[0] if len(targets) == 1 else "one of " + ", ".join(targets)
                )
                gaps.append(f"read {which}, which the answer cites as {given}")
            continue
        path = shown(given, requirement.root)
        if path is None:
            gaps.append(f"remove {given}, which is outside the root and cannot be read")
        elif path not in read:
            gaps.append(f"read {path}, which the answer cites")
    return gaps


def read_record(s: Session) -> list[dict[str, Any]]:
    """The result's `reads`: each distinct file read, with its digest."""
    return list(read_paths(s).values())
