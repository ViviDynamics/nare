"""Arguments a case or a run passes to `nare run`, and the key they make.

They are checked against nare's contract before anything is spent, and keyed
so that results from other run arguments never compare by default. Pure except
`stage_inputs`, its one filesystem function: no Docker, no network, no clock.
This module imports no other runner module, since `config` imports it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Pair = tuple[str, str | None]
"""A flag and its value: `None` for a flag that takes none."""

_RUNNER = "the runner, which drives nare and reads its stream and session"
_ENVIRONMENT = "the environment, as `case.py` already refuses them by name"

# Refused in both lists, each with the owner the refusal names.
RESERVED: dict[str, str] = {
    **dict.fromkeys(("--yes", "--jsonl", "--session"), _RUNNER),
    "--max-turns": "the case's `max_turns` field",
    **dict.fromkeys(("--model", "--provider", "--base-url"), _ENVIRONMENT),
    "--max-tokens": (
        "`NARE_BENCH_MAX_TOKENS`, which is already part of the baseline key"
    ),
    "--prompt-file": "the case's `prompt`",
    "--resume": "nobody: every rep starts from a clean fixture",
    "--replay": "nobody: the fake provider is never benchmarked",
    "--stream": "`NARE_STREAM`, which changes how a turn arrives, not what is measured",
    "--contract": "the runner, which owns the stream it reads",
    "--bash-credentials": (
        "nobody: it would hand the provider key to model-written commands"
    ),
}
# The system prompt is the variant under test: a case that set one would stack
# silently on every variant.
RUN_ONLY = frozenset(
    {"--system", "--system-file", "--append-system", "--append-system-file"}
)
# Paths in the work tree, relative to a fixture, which a run has none of.
CASE_ONLY = frozenset({"--schema", "--mcp-config", "--require-read", "--root"})
# Flags whose value is a path on the host: keyed by digest, staged for nare.
HOST_FILE_FLAGS = frozenset({"--system-file", "--append-system-file"})
# Where each rep mounts the staged copies, read-only.
INPUTS = "/in"


class ArgsError(Exception):
    """Arguments nare or the benchmark would refuse. Always exit 2."""


@dataclass(frozen=True)
class RunArgs:
    """A run's arguments to nare: as a results line records them, and as the
    container gets them."""

    recorded: tuple[str, ...] = ()
    # Each recorded file path to the digest of the copy that was measured.
    files: dict[str, str] = field(default_factory=dict)
    # File values rewritten to a path under /in.
    passed: tuple[str, ...] = ()
    # The host directory mounted at /in, or None when no file was staged.
    inputs: Path | None = None


def check_args(
    tokens: Sequence[str],
    flags: Mapping[str, Mapping[str, Any]],
    tools: Collection[str],
    *,
    case_id: str | None = None,
) -> list[Pair]:
    """Parse one list as `nare run` would, refusing what it or we would refuse.

    `flags` and `tools` are nare's contract. `case_id` None means the run
    arguments. Flags match by exact long name: nare's parser expands an
    abbreviation, so accepting one here would let a reserved flag through.
    """
    where = "run arguments" if case_id is None else f"case {case_id}"
    found: list[Pair] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if token == "--":
            raise ArgsError(f"{where}: a bare '--'; the prompt is the case's")
        if not token.startswith("-") or token == "-":
            raise ArgsError(
                f"{where}: stray {token!r}; only flags and their values, "
                "since the prompt is the case's"
            )
        flag, equals, inline = token.partition("=")
        if flag in RESERVED:
            raise ArgsError(f"{where}: {flag} is reserved for {RESERVED[flag]}")
        if flag in RUN_ONLY and case_id is not None:
            raise ArgsError(
                f"{where}: {flag} is a run argument, since the system prompt is "
                "the variant under test; pass it after -- on bin/bench run"
            )
        if flag in CASE_ONLY and case_id is None:
            raise ArgsError(
                f"{where}: {flag} names a path in the work tree, "
                "so it belongs to a case's args"
            )
        if flag not in flags:
            raise ArgsError(
                f"{where}: unknown flag {flag!r}; nare run has no such option "
                "(abbreviations are not accepted)"
            )
        spec = flags[flag]
        if not spec["takes_value"]:
            if equals:
                raise ArgsError(f"{where}: {flag} takes no value")
            found.append((flag, None))
            continue
        if equals:
            value = inline
        elif i < len(tokens) and not tokens[i].startswith("-"):
            value = tokens[i]
            i += 1
        else:
            raise ArgsError(
                f"{where}: {flag} needs a value "
                f"(write {flag}=VALUE for one that starts with -)"
            )
        choices = spec.get("choices")
        if choices is not None and value not in choices:
            raise ArgsError(
                f"{where}: {flag} {value!r} is not one of {', '.join(choices)}"
            )
        if flag == "--tools":
            _check_tools(where, value, tools)
        found.append((flag, value))
    return found


def _check_tools(where: str, value: str, tools: Collection[str]) -> None:
    names = [name.strip() for name in value.split(",") if name.strip()]
    if names == ["none"]:
        return
    for name in names:
        if name not in tools:
            raise ArgsError(
                f"{where}: --tools names {name!r}, which nare does not list; "
                f"it lists {', '.join(sorted(tools))}, or none alone"
            )


def check_invocation(
    run: Sequence[str],
    cases: Mapping[str, Sequence[str]],
    flags: Mapping[str, Mapping[str, Any]],
    tools: Collection[str],
) -> None:
    """The run arguments, then each case's, then each flag in both lists.

    Repeats within one list are left alone: `--require-read` repeats by design.
    Across lists nare would take the last one, so a case's flag and the run's
    would silently decide between them.
    """
    run_flags = {flag for flag, _ in check_args(run, flags, tools)}
    checked = {
        case_id: check_args(tokens, flags, tools, case_id=case_id)
        for case_id, tokens in cases.items()
    }
    for case_id, found in checked.items():
        for flag, _ in found:
            if flag in run_flags:
                raise ArgsError(
                    f"{flag} is in the run arguments and in case {case_id}'s args; "
                    "nare would take the last one, so pass it in one place"
                )


def nare_contract() -> tuple[dict[str, dict[str, Any]], list[str]]:
    """The flags and tools of `nare run`, as the agent in the image will speak.

    Read in process: `bin/bench` runs from the working tree the image installs.
    """
    from nare.contract import describe

    contract = describe()
    return contract["flags"], contract["tools"]


def pairs(tokens: Sequence[str]) -> list[Pair]:
    """Split a list `check_args` accepted, without the contract.

    A flag's value is its `=` part, else the next token unless that starts
    with `-`. On a checked list the split is exact, and a nare flag renamed
    later cannot change an old baseline's key.
    """
    found: list[Pair] = []
    i = 0
    while i < len(tokens):
        flag, equals, inline = tokens[i].partition("=")
        i += 1
        if equals:
            found.append((flag, inline))
        elif i < len(tokens) and not tokens[i].startswith("-"):
            found.append((flag, tokens[i]))
            i += 1
        else:
            found.append((flag, None))
    return found


def variant(args: Sequence[str], files: Mapping[str, str]) -> tuple[Pair, ...]:
    """The key two sets of results compare under: the run arguments, normalized.

    Each file flag's value becomes its digest from `files`, so one prompt under
    two paths is one variant and an edited prompt at one path is two. Pairs sort
    by flag name; a stable sort keeps the order of repeats.
    """
    keyed = [
        (flag, files.get(value, value))
        if flag in HOST_FILE_FLAGS and value is not None
        else (flag, value)
        for flag, value in pairs(args)
    ]
    return tuple(sorted(keyed, key=lambda pair: pair[0]))


def stage_inputs(
    tokens: Sequence[str], *, base: Path, root: Path, dest: Path
) -> RunArgs:
    """Copy each system prompt file once, hash the copy, and hand nare its path.

    The one function in this module that touches the filesystem. A relative
    value is read from `base`. It is recorded relative to `root` when it is
    inside, else by its absolute path. The copy goes to `dest/<n>-<basename>`,
    `n` counting the run's files from 0, so two files with one basename cannot
    collide; nare is given `/in/<n>-<basename>`. The digest is of the copy, so a
    prompt edited mid-run does not change the reps after it. Each value keeps
    the operator's form, `--flag value` or `--flag=value`.
    """
    recorded: list[str] = []
    passed: list[str] = []
    files: dict[str, str] = {}
    count = 0
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        flag, equals, inline = token.partition("=")
        if flag not in HOST_FILE_FLAGS or not (equals or i < len(tokens)):
            recorded.append(token)
            passed.append(token)
            continue
        if equals:
            value = inline
        else:
            value = tokens[i]
            i += 1
        source = (base / value).resolve()
        try:
            data = source.read_bytes()
        except OSError as exc:
            raise ArgsError(
                f"run arguments: {flag} {value}: cannot read it "
                f"({exc.strerror or type(exc).__name__})"
            ) from exc
        name = f"{count}-{Path(value).name}"
        count += 1
        dest.mkdir(parents=True, exist_ok=True)
        (dest / name).write_bytes(data)
        try:
            shown = source.relative_to(root.resolve()).as_posix()
        except ValueError:
            shown = source.as_posix()
        files[shown] = "sha256:" + hashlib.sha256(data).hexdigest()
        for out, text in ((recorded, shown), (passed, f"{INPUTS}/{name}")):
            out.extend([f"{flag}={text}"] if equals else [flag, text])
    return RunArgs(
        recorded=tuple(recorded),
        files=files,
        passed=tuple(passed),
        inputs=dest if files else None,
    )
