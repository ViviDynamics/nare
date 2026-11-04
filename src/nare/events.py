"""Typed events: nare's own record of what a run did, one JSON object per line.

Four fields (`type`, `text`, `detail`, `timestamp`) and six types. The shape is
nare's, versioned by the contract (docs/contract.md); a caller that wants its
own event type maps these onto it in its adapter.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

EventType = Literal["progress", "tool_use", "thinking", "cost", "error", "output"]

# One version number for the rule set, and one stable name per rule. Both are
# published through `nare contract`, so a caller can tell when the redaction it
# is paying for has changed without diffing patterns.
REDACTION_VERSION = 2
REDACTION_RULES: tuple[str, ...] = (
    "anthropic-key",
    "openai-key",
    "github-token",
    "credential-assignment",
)

REDACTED = "[redacted]"

# Keys and tokens are anchored at their own prefixes, so a failed match attempt
# costs bounded work no matter how long the text is.
_PREFIX_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai-key", re.compile(r"sk-[A-Za-z0-9_\-]{20,}")),
    ("github-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
)

# A credential rule matches a credential NAME and a credential-shaped VALUE,
# never a keyword inside a longer word and never a code expression. The shapes
# it must not touch are in tests/test_events.py (the false-positive fixtures),
# and the ones it must catch are the true-positive fixtures beside them.
_CREDENTIAL_RUN = re.compile(r"[\w.\-]+")

# A name is split into its words at `_`, `-`, `.` and camelCase, and a word
# must BE a credential word. So `authorId` (words: author, Id) does not match,
# while `AUTH_TOKEN`, `apiKey` and `getAuthToken` each do.
_NAME_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
_CREDENTIAL_WORDS = frozenset(
    {
        "auth",
        "authorization",
        "token",
        "secret",
        "password",
        "passwd",
        "credential",
        "credentials",
        "apikey",
    }
)
# An all-caps name with a credential word glued on the end (`PGPASSWORD`) is
# a credential name too. A mixed-case glued word is not matched.
_CREDENTIAL_SUFFIXES = (
    "PASSWORD",
    "PASSWD",
    "SECRET",
    "TOKEN",
    "CREDENTIAL",
    "CREDENTIALS",
    "APIKEY",
)

# What separates a name from its value: `KEY=`, `"key":`, `key: `.
_VALUE_SEPARATOR = re.compile(r"""["']?\s{0,8}[:=]\s{0,8}""")
# A scheme word before the secret: `Bearer <token>`. The scheme is itself the
# signal, so the value after it is redacted at any length. Spaces and tabs only:
# a scheme word at the end of a line is prose, not a header value.
_SCHEME = re.compile(r"(?i)(?:bearer|basic|token)[ \t]{1,8}")
_SCHEME_VALUE = re.compile(r'(?:[^\s"\'\\]|\\.)+')
# A quoted value is one unbroken word, so a match cannot run past its closing
# quote and take the JSON structure around it with it.
_DOUBLE_QUOTED = re.compile(r'((?:[^\s"\'\\]|\\.)+)"')
_SINGLE_QUOTED = re.compile(r"""([^\s"']+)'""")
# A bare value runs to the first space, quote, bracket, separator or `=`. It may
# hold any other symbol, because secrets do (`P@ssw0rd!`). The trailing `=` of
# base64 padding is left outside the value.
_BARE_VALUE = re.compile(r"""[^\s"'`(){}\[\]<>,;:=]+""")
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
# A lower-case snake identifier (`db_secret_v2`) is a name, not a value.
_IDENTIFIER = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)+")
# A dotted member access (`self.token_value1`) is code, not a value.
_MEMBER_PATH = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
# CamelCase words with digits (`Auth0Client`, `Auth0Config2`) are type and class
# names. Random secrets rarely have that shape.
_CAMEL_WORDS = re.compile(r"[A-Z][a-z]+\d*(?:[A-Z][a-z]+\d*)+")
_HAS_LETTER = re.compile(r"[A-Za-z]")
_HAS_DIGIT = re.compile(r"[0-9]")
_NOT_A_VALUE_START = ("$", "/", "~", "./", "../", "-", "+")


def _is_credential_name(name: str) -> bool:
    words = _NAME_WORD.findall(name)
    lowered = [word.lower() for word in words]
    if any(word in _CREDENTIAL_WORDS for word in lowered):
        return True
    if any(
        a == "api" and b == "key" for a, b in zip(lowered, lowered[1:], strict=False)
    ):
        return True
    return any(word.isupper() and word.endswith(_CREDENTIAL_SUFFIXES) for word in words)


def _looks_like_secret(value: str) -> bool:
    """Eight or more characters with a letter and a digit, and not code, a path,
    a URL, a number or a reference. A shape test, not entropy scoring: each
    exclusion is a shape that the false-positive fixtures showed up as code."""
    return (
        len(value) >= 8
        and _HAS_LETTER.search(value) is not None
        and _HAS_DIGIT.search(value) is not None
        and not value.startswith(_NOT_A_VALUE_START)
        and "://" not in value
        and _NUMBER.fullmatch(value) is None
        and _IDENTIFIER.fullmatch(value) is None
        and (_MEMBER_PATH.fullmatch(value) is None or value.startswith("eyJ"))
        and _CAMEL_WORDS.fullmatch(value) is None
    )


def _scheme_value_is_secret(value: str) -> bool:
    """After `Bearer` or `basic`, a token has a digit, a symbol or real length.
    Prose has none of those: `basic auth enabled` is not a header value."""
    return not value.startswith("$") and (
        _HAS_DIGIT.search(value) is not None
        or re.search(r"[._\-+/=]", value) is not None
        or len(value) >= 20
    )


def _secret_value(text: str, pos: int) -> tuple[int | None, int] | None:
    """The secret-shaped value starting at `pos`, as (replace_from, end).

    `end` is the last character of the secret, never a closing quote. A quoted
    value replaces only its own text, so `"key": "value"` keeps its quotes and
    its JSON shape. A bare or scheme value is replaced as a whole assignment,
    which is `None` here and means "from the name's start" to the caller.
    """
    quote = text[pos : pos + 1] if text[pos : pos + 1] in ("'", '"') else ""
    start = pos + len(quote)
    scheme = _SCHEME.match(text, start)
    if scheme is not None:
        value = _SCHEME_VALUE.match(text, scheme.end())
        if value is None or value.group().startswith("$"):
            return None
        # `Bearer` is a header in practice, so any value after it is a token.
        # `basic` and `token` also appear in prose, so their value must look like one.
        if not scheme.group().lower().startswith("bearer") and not (
            _scheme_value_is_secret(value.group())
        ):
            return None
        return (start if quote else None, value.end())
    if quote:
        body = (_DOUBLE_QUOTED if quote == '"' else _SINGLE_QUOTED).match(text, start)
        if body is None or not _looks_like_secret(body.group(1)):
            return None
        return (start, body.end(1))
    bare = _BARE_VALUE.match(text, start)
    if bare is None or text[bare.end() : bare.end() + 1] == "(":
        return None
    if not _looks_like_secret(bare.group()):
        return None
    return (None, bare.end())


def _credential_span(text: str, name_end: int) -> tuple[int | None, int] | None:
    """The (replace_from, end) of a credential value after a name, or None."""
    separator = _VALUE_SEPARATOR.match(text, name_end)
    if separator is None:
        return None
    return _secret_value(text, separator.end())


def _redact_credentials(text: str) -> str:
    """The key/value rule, in linear time.

    A match is a credential name, then its separator, then a secret-shaped
    value. Each value scan is bounded by the value's own characters: a bare
    value stops at the first character outside its alphabet, a quoted one at
    its quote, and the scheme form at whitespace. Those scans start after a
    separator, and separators are not value characters, so the scans cover
    disjoint stretches of the text and the whole pass stays linear.
    """
    pieces: list[str] = []
    replaced = 0
    for run in _CREDENTIAL_RUN.finditer(text):
        if run.start() < replaced:
            continue
        if not _is_credential_name(run.group()):
            continue
        span = _credential_span(text, run.end())
        if span is None:
            continue
        replace_from, end = span
        start = run.start() if replace_from is None else replace_from
        pieces.append(text[replaced:start])
        pieces.append(REDACTED)
        replaced = end
    pieces.append(text[replaced:])
    return "".join(pieces)


def redact(text: str) -> str:
    for _, pattern in _PREFIX_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return _redact_credentials(text)


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        # Normalized to a list on purpose: all of these serialize as a JSON
        # array anyway, and `detail` has to stay JSON-serializable for the
        # JSONL wire. json.dumps actually RAISES on a set, so this closes a
        # latent emission crash as well as the redaction hole.
        return [redact_value(v) for v in value]
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Event:
    type: EventType
    text: str
    detail: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_now)
    # The provider's id of the tool call this event belongs to, on every event
    # that belongs to one. Top-level rather than in `detail`, because a
    # tool_use event's detail is the call's arguments and a tool may take an
    # argument of any name.
    tool_use_id: str | None = None

    def __post_init__(self) -> None:
        # Redaction at construction, not at emission: an unredacted Event must
        # never exist, because every later surface trusts it.
        object.__setattr__(self, "text", redact(self.text))
        object.__setattr__(self, "detail", redact_value(self.detail))
        if self.tool_use_id is not None:
            object.__setattr__(self, "tool_use_id", redact(self.tool_use_id))

    def wire(self) -> dict[str, Any]:
        """The JSONL object. An event of no call keeps the four fields it
        always had, so only the lines of a tool call change."""
        out = asdict(self)
        if out["tool_use_id"] is None:
            del out["tool_use_id"]
        return out


# A credential name/separator/scheme at the end may acquire its value in the
# next delta. Hold it, and every unfinished word (including key prefixes),
# until the existing redactor has the complete unit to inspect.
_MAYBE_CREDENTIAL = re.compile(
    r"""(?i)["']?\s{0,8}"""
    r"""(?:[:=]\s{0,8}["']?\s{0,8}(?:(?:bearer|basic|token)[ \t]{1,8})?)?\Z"""
)


def _token_start(text: str, pos: int) -> int:
    """The start of the whitespace-delimited token that holds `pos`."""
    while pos > 0 and not text[pos - 1].isspace():
        pos -= 1
    return pos


def _cut_before_open_name(text: str, cut: int) -> int:
    """Back the cut up over every credential name whose value reaches the cut.

    A separator or a scheme word may run across whitespace (`token\\n=value`,
    `token: Bearer \\nvalue`), so a cut can fall inside the span of a name before
    it. That name would lose its value to the next piece, and the value's
    decision depends on the name, so the name moves to the held side with it.
    The cut always lands on a token start, never inside a token, and a name
    whose span stops before the cut is decided and stays out of the hold. The
    spans are read from the whole prefix, so a name is only held when its value
    really is at or past the cut. Moving the cut can bring an earlier name into
    reach, so the scan repeats until nothing moves.
    """
    moved = True
    while moved:
        moved = False
        for run in _CREDENTIAL_RUN.finditer(text, 0, cut):
            if not _is_credential_name(run.group()):
                continue
            span = _credential_span(text, run.end())
            if span is not None and span[1] >= cut:
                cut = _token_start(text, run.start())
                moved = True
                break
    return cut


class LiveText:
    def __init__(self) -> None:
        self.pending = ""

    def feed(self, text: str) -> str:
        self.pending += text
        spaces = list(re.finditer(r"\s+", self.pending))
        if not spaces:
            return ""
        boundary = spaces[-1].end()
        complete_value_end = 0
        prefix = self.pending[:boundary]
        for word in _CREDENTIAL_RUN.finditer(prefix):
            if word.start() < complete_value_end:
                continue
            if not _is_credential_name(word.group()):
                continue
            if _MAYBE_CREDENTIAL.fullmatch(prefix[word.end() :]):
                # Hold from the start of the word's whitespace token, never from
                # the middle of one: a cut between `=` and the name after it
                # would judge the value against a lookahead that is not there.
                boundary = _cut_before_open_name(
                    prefix, _token_start(prefix, word.start())
                )
                break
            span = _credential_span(prefix, word.end())
            if span is not None:
                # A keyword inside a completed value belongs to this assignment,
                # so withholding it would split the value from its redaction rule.
                complete_value_end = span[1]
        output = redact(self.pending[:boundary])
        self.pending = self.pending[boundary:]
        return output

    def finish(self) -> str:
        output = redact(self.pending)
        self.pending = ""
        return output
