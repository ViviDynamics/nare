"""Bash command rules: a policy allows or denies a bash command by pattern.

A pattern is matched against a simple command's words as bash reads them after
quote removal, joined by one space. `*` matches any run of characters, `?` one
character, and every other character itself. The rules and their reasons are
in docs/contract.md, "Bash command rules".

Allow is narrow: a line is allowed only when the lexer reads every simple
command, each has plain words and no redirection, and each matches an allow
pattern as a whole. Deny is broad: it is checked on every tail of every simple
command, so a program reached through a wrapper (`env`, `xargs`, `find -exec`,
`sh -c`) still matches. A line that cannot be read, or whose command word
cannot be read, is refused when any deny pattern is set.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable
from typing import Any

from nare.shell_words import _commands, _plain, _Redir, _Word

# A shell option cluster that names the string of commands it runs: `-c`, `-lc`.
_COMMAND_STRING = re.compile(r"-[A-Za-z]*c[A-Za-z]*")
# The shells: a deny refuses one that is not given a command string, since
# the commands it runs come from a file or stdin that nare cannot read.
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "fish"})
# Builtins that run a file of commands. A deny refuses them: the file is not read.
_FILE_READERS = frozenset({".", "source"})
# Programs that run another program, or a string of commands, or whose
# arguments name one. A deny reads their arguments as commands too.
_RUNNERS = _SHELLS | _FILE_READERS | frozenset(
    {
        "eval", "env", "xargs", "find", "sudo", "doas", "su", "runuser", "nice",
        "nohup", "time", "timeout", "exec", "command", "builtin", "busybox",
        "stdbuf", "ionice", "chroot", "flock", "watch", "parallel", "script",
        "strace", "ltrace", "setsid", "taskset", "unbuffer", "coproc", "sg",
        "caffeinate", "screen", "tmux", "ssh", "git", "arch", "xcrun",
    }
)  # fmt: skip
# How deep a command inside a string is read before the line is refused.
_MAX_DEPTH = 4


def validate(rules: Iterable[str], label: str) -> None:
    for rule in rules:
        if not isinstance(rule, str) or not rule.strip():
            raise ValueError(f"a bash {label} pattern must not be empty")
        if "\n" in rule or "\r" in rule:
            raise ValueError(f"a bash {label} pattern must not contain a newline")


def bash_rule_matches(pattern: str, text: str, ignore_case: bool = False) -> bool:
    """Whether `text` matches `pattern` in full, with the syntax above. A deny
    ignores case: on a case-insensitive filesystem `ECHO` runs `echo`.
    """
    regex = "".join(
        ".*" if char == "*" else "." if char == "?" else re.escape(char)
        for char in pattern
    )
    flags = re.DOTALL | (re.IGNORECASE if ignore_case else 0)
    return re.fullmatch(regex, text, flags) is not None


def _allowed_by(command: str, allow: frozenset[str]) -> set[str] | None:
    """The allow patterns that cover every simple command of a plain line, or
    None when some command is not covered. A redirection, an expansion or an
    unreadable line is never covered.
    """
    commands = _commands(command)
    if not commands:
        return None
    covering: set[str] = set()
    for words in commands:
        if any(isinstance(word, _Redir) for word in words):
            return None
        if not all(isinstance(word, _Word) and _plain(word) for word in words):
            return None
        text = " ".join(str(word) for word in words)
        matched = {rule for rule in allow if bash_rule_matches(rule, text)}
        if not matched:
            return None
        covering |= matched
    return covering


def _hits(candidates: Iterable[str], deny: frozenset[str]) -> set[str]:
    """The deny patterns that a candidate command text matches, with and
    without a trailing space: `xargs rm` takes its arguments from stdin, so a
    bare `rm` is `rm ` with arguments to come, and `rm *` refuses it.
    """
    refused: set[str] = set()
    for candidate in candidates:
        for text in (candidate, candidate + " "):
            refused |= {
                rule for rule in deny if bash_rule_matches(rule, text, ignore_case=True)
            }
    return refused


def _tail_hits(tail: list[str], deny: frozenset[str]) -> set[str]:
    candidates = [" ".join(tail)]
    if "/" in tail[0]:
        # `/usr/bin/rm x` is `rm x`.
        candidates.append(" ".join([posixpath.basename(tail[0]), *tail[1:]]))
    return _hits(candidates, deny)


def _suffix_hits(text: str, deny: frozenset[str]) -> set[str]:
    """Every tail of a word's text, character by character. A runner's
    argument may be a command with options in front of it: `env -Srm x`.
    """
    return _hits((text[index:] for index in range(len(text))), deny)


def _argv(words: list[Any]) -> list[Any]:
    """The words a command runs with: its redirections are dropped together
    with their targets, which are files, not arguments.
    """
    argv: list[Any] = []
    skip = False
    for word in words:
        if skip:
            skip = False
        elif isinstance(word, _Redir):
            skip = True
        else:
            argv.append(word)
    return argv


def _denied_by(command: str, deny: frozenset[str], depth: int = 0) -> set[str]:
    """The deny patterns that refuse this line, or every one of them when the
    line cannot be checked. Under a deny every word must be plain, so bash has
    no expansion whose names this check cannot see; a line that is not plain,
    cannot be read, starts a word with `=` (an assignment), or hands a file or
    stdin to a shell, is refused.
    """
    commands = _commands(command) if depth <= _MAX_DEPTH else None
    if commands is None:
        return set(deny)
    refused: set[str] = set()
    for words in commands:
        argv = _argv(words)
        if not argv:
            continue
        if not all(isinstance(word, _Word) and _plain(word) for word in argv):
            return set(deny)
        texts = [str(word) for word in argv]
        if "=" in texts[0]:
            return set(deny)  # `X=1 rm x`, `X+=1 rm x`: a variable may name the program
        name = posixpath.basename(texts[0]).lower()
        if name in _RUNNERS:
            if name in _FILE_READERS:
                return set(deny)
            if name in _SHELLS and not _reads_command_string(texts[1:]):
                return set(deny)  # the commands come from a file or stdin
            if any("\\" in text for text in texts[1:]):
                # A runner may unescape its string itself (`env -S` reads `\_`
                # as a space), which this check does not model.
                return set(deny)
            for text in texts[1:]:
                refused |= _denied_by(text, deny, depth + 1)
                refused |= _suffix_hits(text, deny)
        for index in range(len(texts)):
            refused |= _tail_hits(texts[index:], deny)
        for text in texts:
            # An option may name a program after `=`: `--to-command=rm x`.
            refused |= _hits(
                (text[index + 1 :] for index, char in enumerate(text) if char == "="),
                deny,
            )
    return refused


def _reads_command_string(args: list[str]) -> bool:
    """Whether a shell's options, before any operand, include `-c`. A long
    option such as `--rcfile FILE` is refused: its operand would be a file
    that runs unseen.
    """
    for text in args:
        if text in ("-", "--") or text.startswith("--") or not text.startswith("-"):
            return False
        if _COMMAND_STRING.fullmatch(text):
            return True
    return False


def decide(
    command: str, allow: frozenset[str], deny: frozenset[str]
) -> tuple[str, list[str]] | None:
    """`("deny", patterns)` or `("allow", patterns)` for a bash command, or None
    when no rule decides it and the approval rules apply. Deny wins.
    """
    if deny:
        refused = _denied_by(command, deny)
        if refused:
            return "deny", sorted(refused)
    if allow:
        covering = _allowed_by(command, allow)
        if covering:
            return "allow", sorted(covering)
    return None
