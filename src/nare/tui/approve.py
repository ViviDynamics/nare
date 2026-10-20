"""The TUI's approver: y, n, or always for this tool. No Textual here; the app
passes in how to ask.

The bash policy reads a line with a token scan, not a shell parser: what the
scan cannot read is asked about.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from nare.tools import Policy

# These change nothing, so a person is never asked about them.
EXEMPT = frozenset({"read", "ask"})

# 2>&1, >&2, 2>&-: a copy of a descriptor, not a file, and its & is not
# backgrounding. A word after >& (`>&out`) is a file, so it stays.
_DUP = re.compile(r"(?<![^\s;&|])\d*[<>]&(?:\d+|-)(?=[\s;&|)]|$)")
# The redirection a token can start with: `2>/dev/null` names /dev/null.
_REDIRECT = re.compile(r"^\d*(?:>>|>|<)")

Ask = Callable[[str, dict[str, Any]], Awaitable[str]]


def _tokens(command: str) -> list[str]:
    """The line's words and operators, or none when shlex cannot split it.
    `&>` and `&>>` are read as `>` and `>>`, so their & is not backgrounding.
    """
    line = _DUP.sub("", command).replace("&>", ">")
    try:
        scan = shlex.shlex(line, posix=True, punctuation_chars=";&|()")
        scan.whitespace_split = True
        return list(scan)
    except ValueError:
        return []


def _outside(token: str, policy: Policy) -> bool:
    path = _REDIRECT.sub("", token)
    if path == os.devnull:
        return False
    if not (path.startswith(("/", "~")) or ".." in path.split("/")):
        return False
    try:
        policy.resolve(os.path.expanduser(path))
    except (ValueError, OSError):
        return True
    return False


def risks(command: str, policy: Policy) -> list[str]:
    """The categories documented by #63, for the prompt's badges and the
    policy alike. A risk always asks, even about an approved program; this
    never allows a call.
    """
    tokens = _tokens(command)
    # Include nested/wrapped invocations conservatively.
    words = {Path(token).name for token in tokens}
    pairs = set(zip(tokens, tokens[1:], strict=False))
    network = bool(words & {"curl", "wget"}) or bool(
        pairs
        & {
            ("pip", "install"),
            ("pip3", "install"),
            ("npm", "i"),
            ("npm", "install"),
            ("git", "clone"),
        }
    )
    options = "".join(token[1:] for token in tokens if token.startswith("-"))
    # Without a root, bash runs in the current directory: that is the boundary.
    bounded = policy if policy.root is not None else Policy(root=Path.cwd())
    flags = {
        "network": network,
        "exec": ("chmod", "+x") in pairs
        or (network and bool(words & {"sh", "bash", "zsh"})),
        "sudo": "sudo" in words,
        "kill": bool(words & {"kill", "pkill", "killall"}),
        "background": bool(words & {"nohup", "pg_ctl", "systemctl"}) or "&" in tokens,
        "rm -rf": "rm" in words and "r" in options and "f" in options,
        "outside root": any(_outside(token, bounded) for token in tokens),
        "git commit": bool(pairs & {("git", "commit"), ("git", "push")}),
    }
    return [badge for badge, present in flags.items() if present]


class Approver:
    """The "always" set lives for this process only and is never written to
    the session: a saved file must not grant a later run anything.
    """

    def __init__(self, ask: Ask) -> None:
        self.ask = ask
        self.always: set[str] = set()

    async def __call__(self, tool: str, args: dict[str, Any]) -> bool:
        if tool in EXEMPT or tool in self.always:
            return True
        key = await self.ask(tool, args)
        if key == "a":
            self.always.add(tool)
        return key in ("y", "a")
