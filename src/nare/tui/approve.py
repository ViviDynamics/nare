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
from itertools import pairwise
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
# A token whose brace lists make more words than this counts as outside.
_BRACE_LIMIT = 64
# What the scan cannot read: a second line, a subshell or substitution, a
# heredoc. shlex hands back a quoted or escaped `;` as the same token as a
# real one, so `find . \; python3 -delete` would look like two commands.
_UNREADABLE = re.compile(r"""[\n\r`]|\$\(|<\(|>\(|<<|(['"])[;&|()]+\1|\\[;&|()]""")
_OPERATOR = frozenset(";&|()")
_SEPARATORS = frozenset({"|", "|&", "||", "&&", ";", "&"})

# Run unasked. Widen this only from prompts people answered y.
READ_ONLY = frozenset(
    {"cd", "ls", "cat", "head", "tail", "wc", "find", "grep", "rg", "pwd", "echo"}
    | {"git status", "git diff", "git log", "git show"}
)
# Flags that make a read-only program write or run something.
_WRITES = frozenset(
    {"-exec", "-execdir", "-ok", "-okdir", "-delete"}
    | {"-fprint", "-fprint0", "-fprintf", "-fls", "--pre", "--hostname-bin"}
    | {"--output"}
)
# Programs that run another: approving one would approve whatever it runs.
WRAPPERS = frozenset(
    {"bash", "sh", "zsh", "env", "xargs", "timeout", "nice", "time", "command"}
    | {"builtin", "exec", "eval", "source", "."}
)
# Shell grammar, not programs: `{ rm x; }` would otherwise approve `{`.
_KEYWORDS = frozenset(
    {"!", "{", "}", "[[", "]]", "if", "then", "elif", "else", "fi", "case", "esac"}
    | {"for", "select", "while", "until", "do", "done", "function", "coproc"}
)

# The key a person pressed, and the reason they typed with an n.
Ask = Callable[[str, dict[str, Any]], Awaitable[tuple[str, str]]]


def _tokens(command: str) -> list[str]:
    """The line's words and operators, or none when shlex cannot split it.
    `&>` and `&>>` are read as `>` and `>>`, so their & is not backgrounding,
    and `>|` as `>`, so its | is not a pipe.
    """
    line = _DUP.sub("", command).replace("&>", ">").replace(">|", ">")
    try:
        scan = shlex.shlex(line, posix=True, punctuation_chars=";&|()")
        scan.whitespace_split = True
        # shlex drops all from a `#` on, but bash only starts a comment at
        # the start of a word: `ls#;rm x` runs `ls#`, then `rm x`.
        scan.commenters = ""
        return list(scan)
    except ValueError:
        return []


def _brace_step(word: str) -> list[str] | None:
    """`word` with its first brace list expanded, or None when it has none."""
    depth = start = 0
    commas: list[int] = []
    for i, char in enumerate(word):
        if char == "{":
            if depth == 0:
                start, commas = i, []
            depth += 1
        elif char == "," and depth == 1:
            commas.append(i)
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and commas:
                cuts = [start, *commas, i]
                head, tail = word[:start], word[i + 1 :]
                return [head + word[a + 1 : b] + tail for a, b in pairwise(cuts)]
    return None


def _braces(word: str) -> list[str] | None:
    """The words bash's brace expansion makes of `word`, or None past
    _BRACE_LIMIT of them. Lists only: a sequence like {1..3} makes no `/`,
    `.` or `~`. A quoted or escaped comma splits here too, which only adds
    paths to check.
    """
    done: list[str] = []
    todo = [word]
    while todo:
        if len(done) + len(todo) > _BRACE_LIMIT:
            return None
        expanded = _brace_step(word := todo.pop())
        if expanded is None:
            done.append(word)
        else:
            todo += expanded
    return done


def _outside(token: str, policy: Policy) -> bool:
    paths = _braces(_REDIRECT.sub("", token))
    if paths is None:
        return True
    for path in paths:
        if path == os.devnull:
            continue
        if not (path.startswith(("/", "~")) or ".." in path.split("/")):
            continue
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
    # Short options only: the letters of --force are not -r and -f.
    short = [token for token in tokens if token[:1] == "-" and token[:2] != "--"]
    letters = {letter for token in short for letter in token[1:]}
    recursive = bool(letters & {"r", "R"}) or "--recursive" in tokens
    # Without a root, bash runs in the current directory: that is the boundary.
    bounded = policy if policy.root is not None else Policy(root=Path.cwd())
    flags = {
        "network": network,
        "exec": ("chmod", "+x") in pairs
        or (network and bool(words & {"sh", "bash", "zsh"})),
        "sudo": "sudo" in words,
        "kill": bool(words & {"kill", "pkill", "killall"}),
        "background": bool(words & {"nohup", "pg_ctl", "systemctl"}) or "&" in tokens,
        # Without a terminal rm never asks, so -r alone deletes as -rf does.
        "rm -r": "rm" in words and recursive,
        "outside root": any(_outside(token, bounded) for token in tokens),
        "git commit": bool(pairs & {("git", "commit"), ("git", "push")}),
        "git discard": ("git", "clean") in pairs
        or (("git", "reset") in pairs and "--hard" in tokens),
    }
    return [badge for badge, present in flags.items() if present]


def _commands(command: str) -> list[list[str]] | None:
    """The line's commands, split on its separators, or None when the scan
    cannot read it.
    """
    tokens = _tokens(command)
    if not tokens or _UNREADABLE.search(command):
        return None
    commands: list[list[str]] = [[]]
    for token in tokens:
        if token and set(token) <= _OPERATOR:
            if token not in _SEPARATORS:  # `(`, `)`, or `;;`
                return None
            commands.append([])
        else:
            commands[-1].append(token)
    return [words for words in commands if words]


def _program(words: list[str]) -> str | None:
    """What `a` would approve for one command: its first word as written, or
    `git <subcommand>`. None for a wrapper, a keyword, a `VAR=` prefix, a `#`,
    an expansion or redirection, or git with an option before its subcommand.
    """
    program = words[0]
    if program == "git" and len(words) > 1:
        if words[1].startswith("-"):
            return None
        program = f"git {words[1]}"
    if words[0] in WRAPPERS or words[0] in _KEYWORDS or program.startswith("-"):
        return None
    if not program or set(program) & set("=<>$*?[]{}~#"):
        return None
    return program


def _read_only(words: list[str]) -> bool:
    return (
        _program(words) in READ_ONLY
        and not any(set(word) & set("<>$") for word in words)
        and not any(word.split("=")[0] in _WRITES for word in words)
    )


def scope(command: str, policy: Policy) -> frozenset[str] | None:
    """The programs `a` approves on this line: each one that is not
    read-only. None when it cannot be scoped, and `a` acts as `y`.
    """
    commands = _commands(command)
    if commands is None or risks(command, policy):
        return None
    programs = [_program(words) for words in commands if not _read_only(words)]
    # A read-only program asked about for a redirect or a write flag:
    # approving it whole would lift the very check that asked.
    if None in programs or set(programs) & READ_ONLY:
        return None
    return frozenset(program for program in programs if program is not None)


class Approver:
    """What `a` grants lives for this process only and is never written to
    the session: a saved file must not grant a later run anything. A tool is
    approved whole; bash is approved one program at a time, in `programs`.
    """

    def __init__(self, ask: Ask, policy: Policy | None = None) -> None:
        self.ask = ask
        self.policy = policy or Policy()
        self.always: set[str] = set()
        self.programs: set[str] = set()

    async def __call__(self, tool: str, args: dict[str, Any]) -> bool | str:
        """True to run the call; a denial is False, or the reason given."""
        if tool in EXEMPT or tool in self.always:
            return True
        command = str(args.get("command", ""))
        if tool == "bash" and self._unasked(command):
            return True
        key, reason = await self.ask(tool, args)
        if key == "a" and tool == "bash":
            self.programs |= scope(command, self.policy) or set()
        elif key == "a":
            self.always.add(tool)
        if key in ("y", "a"):
            return True
        return reason.strip() or False

    def _unasked(self, command: str) -> bool:
        commands = _commands(command)
        if not commands or risks(command, self.policy):
            return False
        return all(
            _read_only(words) or _program(words) in self.programs for words in commands
        )
