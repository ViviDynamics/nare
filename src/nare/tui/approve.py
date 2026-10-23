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
_DUP = re.compile(r"(?<![^ \t;&|])\d*[<>]&(?:\d+|-)(?=[ \t;&|)]|$)")
# A word that names the descriptor a redirection after it redirects: `2`, or
# `{fd}`, for which bash picks one.
_FD = re.compile(r"[0-9]+|\{[A-Za-z_][A-Za-z0-9_]*\}")
# The redirection a token can start with: `2>/dev/null` names /dev/null.
_REDIRECT = re.compile(rf"^(?:{_FD.pattern})?(?:>>|<>|>|<)")
# A token whose brace lists make more words than this counts as outside.
_BRACE_LIMIT = 64
# What the scan cannot read: a second line, a subshell or substitution, a
# heredoc. shlex hands back a quoted or escaped `;` as the same token as a
# real one, so `find . \; python3 -delete` would look like two commands. An
# escaped `>` is a word to bash, but `\>|` would read as `>|`, hiding the pipe.
# A quoted `>` reads as a redirection, so `git checkout '>' a.py` would hide
# a.py. A brace sequence is not expanded, and `--har{d..d}` makes `--hard`.
_UNREADABLE = re.compile(
    r"""[\n\r`]|\$\(|<\(|>\(|<<|(['"])[;&|()]+\1|\\[;&|()<>]"""
    rf"""|['"](?:{_FD.pattern})?[<>]"""
    r"|\{-?\w+\.\.-?\w+(?:\.\.-?\d+)?\}"
)
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
# The subcommands that fetch from the network, with npm's aliases for them.
_PIP_FETCHES = frozenset({"install", "download"})
_FETCHES = {
    "npm": frozenset(
        {"i", "in", "ins", "inst", "insta", "instal", "install", "add"}
        | {"isnt", "isnta", "isntal", "isntall", "it", "install-test"}
        | {"ci", "clean-install", "ic", "install-clean", "isntall-clean"}
        | {"cit", "install-ci-test", "update", "up", "upgrade", "udpate"}
        | {"exec", "x"}
    ),
    "pip": _PIP_FETCHES,
    "pip3": _PIP_FETCHES,
}
# git's short options that take a value, which is the rest of the word or
# the next word: `-bfix` is `-b fix`.
_VALUE_LETTERS = {"checkout": "bB", "switch": "cC", "restore": "s", "branch": "u"}
# restore's long options that take the next word as their value.
_RESTORE_VALUES = frozenset({"--source", "--conflict", "--pathspec-from-file"})
# git's own options that take the next word: `git -C sub status`.
_GIT_GLOBALS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
    | {"--attr-source"}
)
# Past this many places a line's cds may leave bash, any name may be a path.
_DIR_LIMIT = 16
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
    and `>|` as `>`, so its | is not a pipe. The & ends a word, so a space
    takes its place: `1&>x` hands `1` on, and a quoted `'&>'` stays a word.
    """
    line = command.replace("&>", " >").replace(">|", ">")
    line = _DUP.sub("", _split_redirections(line))
    try:
        scan = shlex.shlex(line, posix=True, punctuation_chars=";&|()")
        scan.whitespace_split = True
        # shlex drops all from a `#` on, but bash only starts a comment at
        # the start of a word: `ls#;rm x` runs `ls#`, then `rm x`.
        scan.commenters = ""
        return list(scan)
    except ValueError:
        return []


def _split_redirections(line: str) -> str:
    """`line` with a space before each unquoted `<` or `>` that ends a word,
    since bash ends it there: `git checkout .>/dev/null` hands git `.`, which
    shlex would keep whole. A word that names a descriptor stays with it:
    `2>/dev/null`. Words end at spaces and tabs only, as in bash and shlex.
    """
    out: list[str] = []
    word, quote, escaped = "", "", False
    for char in line:
        if escaped:
            escaped = False
        elif quote:
            if char == quote:
                quote = ""
            elif char == "\\" and quote == '"':
                escaped = True
        elif char == "\\":
            escaped = True
        elif char in "'\"":
            quote = char
        elif char in " \t;&|()":
            word = ""
            out.append(char)
            continue
        elif char in "<>":
            if word and not _FD.fullmatch(word):
                out.append(" ")
            word = ""
            out.append(char)
            continue
        word += char
        out.append(char)
    return "".join(out)


def _brace_list(word: str) -> list[int] | None:
    """Where `word`'s first brace list opens, splits and closes, or None
    when it has none.
    """
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
                return [start, *commas, i]
    return None


def _braces(word: str) -> list[str] | None:
    """The words bash's brace expansion makes of `word`, or None past
    _BRACE_LIMIT of them. Lists only: a line with a sequence like {1..3} is
    not read at all. A quoted or escaped comma splits here too, which only
    adds words to check.
    """
    done: list[str] = []
    todo = [word]
    while todo:
        cuts = _brace_list(word := todo.pop(0))
        if cuts is None:
            done.append(word)
            continue
        # Counted before it is built: one wide list of long words is large.
        if len(done) + len(todo) + len(cuts) - 1 > _BRACE_LIMIT:
            return None
        head, tail = word[: cuts[0]], word[cuts[-1] + 1 :]
        # First, so the words keep bash's order.
        todo[:0] = [head + word[a + 1 : b] + tail for a, b in pairwise(cuts)]
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
        if path.startswith("~") and os.path.expanduser(path) == path:
            return True  # ~- and ~+ are bash's last and current directory
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
    # Brace lists expanded, as bash will: `npm {,i} x` runs `npm i x`, since
    # bash drops the empty word. A token that makes too many words stays
    # whole, and _outside flags it.
    tokens = [
        word for token in _tokens(command) for word in _braces(token) or [token] if word
    ]
    # Include nested/wrapped invocations conservatively.
    words = {Path(token).name for token in tokens}
    pairs = set(zip(tokens, tokens[1:], strict=False))
    network = (
        bool(words & {"curl", "wget"}) or ("git", "clone") in pairs or _fetches(tokens)
    )
    # Short options only: the letters of --force are not -r and -f.
    short = [token for token in tokens if token[:1] == "-" and token[:2] != "--"]
    letters = {letter for token in short for letter in token[1:]}
    recursive = bool(letters & {"r", "R"}) or _spelled(tokens, "--recursive")
    # Without a root, bash runs in the current directory: that is the boundary.
    bounded = policy if policy.root is not None else Policy(root=Path.cwd())
    git = _git(tokens)
    dirs = _dirs(tokens, bounded.root or Path.cwd())
    flags = {
        "network": network,
        "exec": ("chmod", "+x") in pairs
        or (network and bool(words & {"sh", "bash", "zsh"})),
        "sudo": "sudo" in words,
        "kill": bool(words & {"kill", "pkill", "killall"}),
        "background": bool(words & {"nohup", "pg_ctl", "systemctl"}) or "&" in tokens,
        # Without a terminal rm never asks, so -r alone deletes as -rf does.
        "rm -r": "rm" in words and recursive,
        "outside root": _cd_away(tokens)
        or any(_outside(token, bounded) for token in tokens),
        "git commit": any(sub in ("commit", "push") for sub, _ in git),
        "git discard": any(_discards(sub, args, dirs) for sub, args in git),
    }
    return [badge for badge, present in flags.items() if present]


def _spelled(tokens: list[str], option: str) -> bool:
    """Whether a token is `option` or names it by a prefix, with or without a
    value: rm reads `--rec` as `--recursive`, and git `--har` as `--hard`.
    """
    return any(
        len(name := token.split("=")[0]) > 2 and option.startswith(name)
        for token in tokens
    )


def _git(tokens: list[str]) -> list[tuple[str, list[str]]]:
    """Each git command on the line, as its subcommand and every word after
    it, past git's own options and redirections. git is known by name, so
    `bin/git push` is one too.
    """
    found: list[tuple[str, list[str]]] = []
    for segment in _segments(tokens):
        for i, word in enumerate(segment):
            if Path(word).name != "git":
                continue
            sub = i + 1
            while sub < len(segment):
                before = segment[sub]
                if before in _GIT_GLOBALS or _REDIRECT.fullmatch(before):
                    sub += 2  # and the word it takes
                elif before[:1] == "-" or _REDIRECT.match(before):
                    sub += 1
                else:
                    break
            if sub < len(segment):
                found.append((segment[sub], segment[sub + 1 :]))
    return found


def _unredirected(words: list[str]) -> list[str]:
    """`words` without their redirections: `2>/dev/null`, or `>` and the
    word after it.
    """
    kept: list[str] = []
    target = False
    for word in words:
        if target:
            target = False
        elif _REDIRECT.match(word):
            target = bool(_REDIRECT.fullmatch(word))
        else:
            kept.append(word)
    return kept


def _discards(sub: str, args: list[str], dirs: list[Path] | None) -> bool:
    """Whether a git subcommand throws work away: uncommitted changes, a
    stash, or the commits only a branch holds. An option counts wherever it
    is, past `--` too: an option that takes a value can take `--` as it,
    and git reads on. `dirs` are where a relative path is looked up.
    """
    values = _VALUE_LETTERS.get(sub, "")
    letters = _letters(args, values)
    # An option that makes a command safe counts only where git is handed
    # it: `git checkout > -b a.py` writes a file named -b.
    handed = _unredirected(args)

    def spelled(*names: str) -> bool:
        return any(_spelled(args, name) for name in names)

    if sub == "clean":
        return True
    if sub == "reset":
        return spelled("--hard", "--merge")
    if sub == "stash":
        return bool({"drop", "clear"} & set(args))
    if sub == "branch":
        # -D, -M and -C are -d, -m and -c with --force.
        return bool(letters & set("DMCf")) or spelled("--force")
    if sub == "switch":
        return bool(letters & set("fC")) or spelled(
            "--force", "--discard-changes", "--force-create"
        )
    if sub == "restore":
        # It restores the working tree unless told to restore the index
        # alone, and only as git reads it: in `-sStable` the S is part of a
        # source, and in `--pathspec-from-file --staged` it names a file.
        given = _option_words(handed, values, _RESTORE_VALUES)
        staged = "S" in _letters(given, values) or _spelled(given, "--staged")
        return not staged or "W" in letters or spelled("--worktree")
    if sub == "checkout":
        if "--" in args or letters & set("fpB"):
            return True
        if spelled("--force", "--patch", "--pathspec-from-file"):
            return True
        names = [arg for arg in handed if arg[:1] != "-"]
        # A new branch takes a name and a start; a switch takes one branch.
        # Any other word is a path, whose changes checkout throws away.
        orphan = _spelled(handed, "--orphan") and not _spelled(handed, "--no-orphan")
        if "b" in _letters(handed, values) or orphan:
            return len(names) > 2
        return len(names) > 1 or any(_a_path(name, dirs) for name in names)
    return False


def _letters(words: list[str], values: str = "") -> set[str]:
    """The short options in `words`. A bundle ends at its first letter in
    `values`, which takes the rest of the word as its value: `-bfix` is
    `-b fix`, with no -f.
    """
    letters: set[str] = set()
    for word in words:
        if word[:1] == "-" and word[:2] != "--":
            for letter in word[1:]:
                letters.add(letter)
                if letter in values:
                    break
    return letters


def _option_words(
    args: list[str], values: str, long_values: frozenset[str]
) -> list[str]:
    """The words git reads as options, for an option that makes a command
    safe, which a value or a path must not pass for. They end at `--`,
    unless an option takes it as its value. `values` are the short options
    that take one, and `long_values` the long ones.
    """
    words: list[str] = []
    value = False
    for arg in args:
        if value:
            value = False
        elif arg == "--":
            break
        elif arg[:2] == "--":
            words.append(arg)
            name = arg.split("=")[0]
            value = "=" not in arg and any(o.startswith(name) for o in long_values)
        elif arg[:1] == "-":
            words.append(arg)
            for i, letter in enumerate(arg):
                if i and letter in values:
                    # At the end of the word, its value is the next word.
                    value = i == len(arg) - 1
                    break
    return words


def _dirs(tokens: list[str], root: Path) -> list[Path] | None:
    """Where bash may be when a command on the line runs: the root, or
    wherever its cds, in order, may have taken it. None when a cd goes
    somewhere the scan cannot name.
    """
    dirs = [root]
    for target in _cd_targets(tokens):
        if not target or target[:1] == "-" or set(target) & set("$*?["):
            return None
        # A cd can fail, or run in a pipeline's subshell: it may not apply.
        # bash's cd takes `..` off the path as typed, and cd -P off the path
        # the links lead to: either may be where it lands.
        for path in list(dirs):
            moved = path / os.path.expanduser(target)
            dirs += {moved, Path(os.path.normpath(moved))}
        if len(dirs) > _DIR_LIMIT:
            return None
    return dirs


def _cd_targets(tokens: list[str]) -> list[str]:
    """Where each cd or pushd on the line goes, as written, in order: "" for
    none. A redirection is not where it goes: `pushd >/dev/null src`.
    """
    targets: list[str] = []
    for segment in _segments(tokens):
        words = _unredirected(segment)
        for i, word in enumerate(words):
            if Path(word).name in ("cd", "pushd"):
                targets.append(words[i + 1] if i + 1 < len(words) else "")
    return targets


def _a_path(name: str, dirs: list[Path] | None) -> bool:
    """Whether git could read `name` as a path, not a branch: a pathspec no
    branch name can be (`*.py`, `:/`, `x/..`, `a\\.py`), or a file or
    directory in one of `dirs`. Unless `dirs` are known, any name could be
    a file there.
    """
    if dirs is None or ".." in name or set(name) & set("*?[:\\"):
        return True
    path = os.path.expanduser(name)
    return any(os.path.lexists(where / path) for where in dirs)


def _option_glob(command: str) -> bool:
    """Whether an unquoted glob could expand to an option: one that starts a
    word, or a brace alternative, or sits in a word that starts with `-`.
    bash hands `rm *` a file named `-r` as an option; `find . -name '*.py'`
    gives find its own pattern, and `src/*.py` cannot start with `-`.
    """
    quote = ""
    start, dash, escaped = True, False, False
    for char in command:
        if escaped:
            escaped = False
        elif quote:
            if char == quote:
                quote = ""
            elif char == "\\" and quote == '"':
                escaped = True
            continue
        elif char == "\\":
            escaped = True
        elif char in "'\"":
            quote = char
        elif char.isspace() or char in ";&|()<>":
            start, dash = True, False
            continue
        elif char in "{,":
            start = True  # `{*,x}` puts `*` first in a word of its own
            continue
        elif char in "*?[" and (start or dash):
            return True
        elif start:
            dash = char == "-"
        start = False
    return False


def _segments(tokens: list[str]) -> list[list[str]]:
    """The tokens between operators: one command each, roughly."""
    segments: list[list[str]] = [[]]
    for token in tokens:
        if set(token) <= _OPERATOR:
            segments.append([])
        else:
            segments[-1].append(token)
    return segments


def _fetches(tokens: list[str]) -> bool:
    """Whether npm or pip is told to fetch, under any of its names for it and
    after any options: `npm -g add x` installs as `npm i x` does.
    """
    for segment in _segments(tokens):
        names = [Path(token).name for token in segment]
        for program, fetches in _FETCHES.items():
            if program in names:
                if set(segment[names.index(program) + 1 :]) & fetches:
                    return True
    return False


def _cd_away(tokens: list[str]) -> bool:
    """Whether a cd goes somewhere the line does not name: home with no
    directory, the last one with `-`, or a CDPATH entry for a bare name.
    """
    for target in _cd_targets(tokens):
        if not target or target.startswith("-"):
            return True
        if os.environ.get("CDPATH") and not target.startswith(("/", ".", "~")):
            return True
    return False


def _commands(command: str) -> list[list[str]] | None:
    """The line's commands, split on its separators, or None when the scan
    cannot read it.
    """
    tokens = _tokens(command)
    if not tokens or _UNREADABLE.search(command):
        return None
    # The scan expands braces bash leaves quoted, so `'{,>}'` would make a
    # `>` that hides the word after it.
    if any(set(token) & set("<>") and _brace_list(token) for token in tokens):
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
    """What `a` would approve for one command: its first word as written, and
    git's subcommand (`git add`, `bin/git add`). None for a wrapper, a
    keyword, a `VAR=` prefix, a `#`, an expansion or redirection, or git with
    an option before its subcommand.
    """
    program = words[0]
    if Path(program).name == "git" and len(words) > 1:
        if words[1].startswith("-"):
            return None
        program = f"{program} {words[1]}"
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
    read-only. None when it cannot be scoped, and `a` acts as `y`; so it does
    when the set is empty.
    """
    commands = _commands(command)
    if commands is None or risks(command, policy):
        return None
    programs = [_program(words) for words in commands if not _read_only(words)]
    if None in programs:
        return None
    # A read-only program asked about for a redirect, a `$` or a write flag
    # is left out: approving it whole would lift the very check that asked.
    return frozenset(p for p in programs if p is not None and p not in READ_ONLY)


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
        if not commands or risks(command, self.policy) or _option_glob(command):
            return False
        return all(_read_only(words) or self._approved(words) for words in commands)

    def _approved(self, words: list[str]) -> bool:
        # risks() reads words, not what a `$` expands to: `rm ${X:--r}` may be
        # recursive, and `> $HOME/x` outside the root.
        return _program(words) in self.programs and not any("$" in w for w in words)
