"""Reading a bash command line the way bash reads its words.

A small lexer tracks quoting, so what bash would expand, glob or split is told
apart from what a quote made literal. It is shared by the TUI's approver and
by the core's bash command rules. What it cannot read is refused by those
callers, never guessed at.
"""

from __future__ import annotations

import re
import string
from itertools import pairwise

# A word that names the descriptor a redirection after it redirects: `2`, or
# `{fd}`, for which bash picks one.
_FD = re.compile(r"[0-9]+|\{[A-Za-z_][A-Za-z0-9_]*\}")


# Brace sequences ({1..3}, {a..z..2}), which are not expanded here at all.
_SEQUENCE = re.compile(r"\{-?\w+\.\.-?\w+(?:\.\.-?\d+)?\}")


# Redirections, longest first where one starts another.
_REDIRECTIONS = ("&>>", "&>", ">>", ">&", ">|", "<&", "<>", ">", "<")


# The operators a line is split into, longest first.
_OPERATORS = ("&&", "||", "|&", ";;", ";", "&", "|", "(", ")")


# The separators a command ends at. `(`, `)` and `;;` are refused.
_SEPARATORS = frozenset({"|", "|&", "||", "&&", ";", "&"})


# A word is plain when its unquoted characters are only these: nothing in it
# expands, globs or splits.
_PLAIN = frozenset(string.ascii_letters + string.digits + "-_./:=@%+,")


# A descriptor duplication, `2>&1` or `>&-`: it writes no file.
_DUPLICATE = re.compile(r"\d+|-")


# A token whose brace lists make more words than this counts as outside.
_BRACE_LIMIT = 64


class _Word(str):
    """A word as bash reads it after quote removal. `bare` is what the word
    leaves unquoted, plus any `$` or backtick a double-quoted string expands:
    the characters an expansion, a glob or a brace can act on.
    """

    bare: str

    def __new__(cls, text: str, bare: str) -> _Word:
        word = super().__new__(cls, text)
        word.bare = bare
        return word


class _Op(str):
    """An operator a line is split into. `_commands` refuses some of them."""


class _Redir(str):
    """A redirection, with the descriptor before it if any: `>`, `2>&`, `{fd}>`.
    Its target is the word after it.
    """


def _tokens(command: str) -> list[str]:
    """The line's words, separators and redirections, with quotes read as bash
    reads them. [] when the line cannot be read: an unclosed quote, a
    substitution, a heredoc, a process substitution, or a newline.
    """
    tokens: list[str] = []
    text: list[str] = []
    bare: list[str] = []
    active = False  # a word is in progress, even an empty quoted one

    def end() -> None:
        nonlocal active
        if active:
            tokens.append(_Word("".join(text), "".join(bare)))
        text.clear()
        bare.clear()
        active = False

    i, n = 0, len(command)
    while i < n:
        char = command[i]
        if char in " \t":
            end()
            i += 1
        elif char in "\r\n":
            return []
        elif char == "'":
            close = command.find("'", i + 1)
            if close < 0:
                return []
            text.append(command[i + 1 : close])
            active = True
            i = close + 1
        elif char == '"':
            active = True
            i += 1
            while True:
                if i >= n:
                    return []
                inner = command[i]
                if inner == '"':
                    i += 1
                    break
                if inner == "\\" and i + 1 < n and command[i + 1] in '$`"\\':
                    text.append(command[i + 1])
                    i += 2
                    continue
                if inner == "\\" and i + 1 < n and command[i + 1] not in "\r\n":
                    text.append(inner)  # kept, as bash keeps it before other text
                    i += 1
                    continue
                if inner in "\\`" or command.startswith("$(", i):
                    return []
                if inner == "$":
                    bare.append(inner)
                text.append(inner)
                i += 1
        elif char == "\\":
            if i + 1 >= n or command[i + 1] in "\r\n":
                return []
            text.append(command[i + 1])
            active = True
            i += 2
        elif char == "`" or command.startswith("$(", i):
            return []
        elif char == "$":
            text.append(char)
            bare.append(char)
            active = True
            i += 1
        elif char in "<>" or command.startswith("&>", i):
            if command.startswith(("<(", ">(", "<<"), i):
                return []
            op = next(o for o in _REDIRECTIONS if command.startswith(o, i))
            prefix = ""
            # A descriptor is a word of its own only when unquoted: `2>`.
            if char in "<>" and active and "".join(text) == "".join(bare):
                if _FD.fullmatch("".join(text)):
                    prefix = "".join(text)
                    text.clear()
                    bare.clear()
                    active = False
            if not prefix:
                end()
            tokens.append(_Redir(prefix + op))
            i += len(op)
        elif char in "|&;()":
            end()
            op = next(o for o in _OPERATORS if command.startswith(o, i))
            tokens.append(_Op(op))
            i += len(op)
        else:
            text.append(char)
            bare.append(char)
            active = True
            i += 1
    end()
    return tokens


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
            # A list opened and never closed, with a comma after its brace,
            # is one bash expands in part: `{x,{x,}` is `{x,x` and `{x,`.
            # Only an unquoted brace is a list, so the test reads what the
            # word leaves unquoted, when it is a lexed word.
            live = getattr(word, "bare", word)
            if "{" in live and "," in live[live.index("{") :]:
                return None
            done.append(word)
            continue
        # Counted before it is built: one wide list of long words is large.
        if len(done) + len(todo) + len(cuts) - 1 > _BRACE_LIMIT:
            return None
        head, tail = word[: cuts[0]], word[cuts[-1] + 1 :]
        # First, so the words keep bash's order.
        todo[:0] = [head + word[a + 1 : b] + tail for a, b in pairwise(cuts)]
    return done


def _segments(tokens: list[str]) -> list[list[str]]:
    """The tokens between operators: one command each, roughly."""
    segments: list[list[str]] = [[]]
    for token in tokens:
        if isinstance(token, _Op):
            segments.append([])
        else:
            segments[-1].append(token)
    return segments


def _commands(command: str) -> list[list[str]] | None:
    """The line's commands, split on its separators, or None when the scan
    cannot read it.
    """
    tokens = _tokens(command)
    if not tokens or _SEQUENCE.search(command):
        return None
    # The scan expands braces bash leaves quoted, so `'{,>}'` would make a
    # `>` that hides the word after it.
    if any(set(token) & set("<>") and _brace_list(token) for token in tokens):
        return None
    commands: list[list[str]] = [[]]
    for token in tokens:
        if isinstance(token, _Op):
            if token not in _SEPARATORS:  # `(`, `)`, or `;;`
                return None
            commands.append([])
        else:
            commands[-1].append(token)
    return [words for words in commands if words]


def _plain(word: str) -> bool:
    """Whether a word is plain: its unquoted characters are letters, digits or
    -_./:=@%+, and none of them expands. Quoted text is literal, so `'1,$p'`
    is plain.
    """
    return isinstance(word, _Word) and set(word.bare) <= _PLAIN
