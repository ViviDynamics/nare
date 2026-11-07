"""The serializable session: everything a run carries between steps."""

from __future__ import annotations

import base64
import binascii
import copy
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from nare.contract import CONTRACT_VERSION, NARE_VERSION
from nare.events import REDACTED, Event, redact_value

SESSION_VERSION = 1

Status = Literal["working", "blocked", "done", "error"]
Role = Literal["user", "assistant"]


@dataclass
class Message:
    """A turn in the transcript. Anthropic's shape: role plus content blocks."""

    role: Role
    content: list[dict[str, Any]]


def sent_back(block: dict[str, Any]) -> bool:
    """Whether a saved block goes back to the model.

    Thinking with no signature was made by a proxy translating a reasoning
    model. It carries no server state, and a Claude backend ignores it, but
    the proxy hands it back to the model it came from and bills it: on the
    practice run all 44 thinking blocks were unsigned, 38% of the context,
    re-sent every turn. Empty text is dead weight too. Signed and redacted
    thinking go back as they came. The transport sends by this rule and the
    context estimate measures by it, so the two agree.
    """
    kind = block.get("type")
    if kind == "thinking":
        return bool(block.get("signature"))
    if kind == "text":
        return bool(block.get("text"))
    return True


def drop_signed_thinking(s: Session) -> int:
    """Drop the signed thinking from the live history; returns how many blocks.

    A resume that changes the tool set or the system prompt changes the prefix
    signed thinking was made against, so the thinking cannot be sent again.
    Dropping it is valid; the model keeps the visible text of each turn.
    """
    dropped = 0
    for message in s.messages:
        kept = without_sent_thinking(message.content)
        dropped += len(message.content) - len(kept)
        message.content = kept
    return dropped


def without_sent_thinking(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The blocks minus the thinking that goes back to the model.

    On Claude Fable 5.1 and the 5.5 models a signed thinking block stays valid
    only while everything sent before it is unchanged, and a stale one fails
    the request with a 400. Removing thinking from the end of the history is
    valid, and putting a removed block back is not. Unsigned thinking is never
    sent and stays.
    """
    return [
        b
        for b in content
        if b.get("type") not in ("thinking", "redacted_thinking") or not sent_back(b)
    ]


@dataclass(frozen=True)
class Usage:
    """Token counts. `input` EXCLUDES cache reads and writes, following Anthropic."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: float | None = 0.0
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        """Disjoint provider-normalized categories, counted once."""
        return self.input + self.output + self.cache_read + self.cache_write

    def wire(self) -> dict[str, Any]:
        """The usage object of a result line or an event: the counters plus
        `total`, the number --budget-tokens counts. The session file keeps the
        counters alone, so its shape is unchanged.
        """
        return {**asdict(self), "total": self.total_tokens}

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input=self.input + other.input,
            output=self.output + other.output,
            cache_read=self.cache_read + other.cache_read,
            cache_write=self.cache_write + other.cache_write,
            estimated=self.estimated or other.estimated,
            cost=(self.cost + other.cost)
            if self.cost is not None and other.cost is not None
            else None,
        )


@dataclass
class Session:
    id: str
    messages: list[Message] = field(default_factory=list)
    usage: Usage = Usage()
    status: Status = "working"
    questions: list[str] = field(default_factory=list)
    stop_reason: str | None = None
    error: str | None = None
    turns: int = 0
    # The reasoning settings the last invocation sent, as nare named them:
    # an --effort level and an anthropic --thinking-budget. None sent none.
    effort: str | None = None
    thinking_budget: int | None = None
    # Model requests resent after a transient failure, over the session's
    # life, like turns. A failed attempt is not a turn and adds no usage.
    retries: int = 0
    # Tool calls the session has run over its life, as `turns` counts turns.
    # --max-tool-calls counts from where an invocation started.
    tool_calls: int = 0
    # Cut-off replies (the output cap, or a repetition) over the session's life,
    # as `retries` counts failed requests. A file without it loads with 0.
    cutoffs: int = 0
    last_input_tokens: int = 0
    last_input_messages: int = 0
    last_input_chars: int = 0
    last_input_image_input: bool | None = None
    policy: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    output: Any = None
    schema_retried: bool = False
    schema_stated: bool = False
    # Set once a text reply that asked a question was nudged toward `ask`
    # (loop.py). Kept across reopen(), so a question nudged once is not nudged
    # again on a resume. A file without it loads as False.
    ask_nudged: bool = False
    # The schema correction budget of this run (corrections.py). Charged
    # corrections spent; every correction made, free or charged, with its
    # codes; the distinct errors earlier answers made; and the fewest errors a
    # failing answer has made. Cleared by reopen(), as schema_retried is.
    schema_corrections: int = 0
    schema_correction_log: list[dict[str, Any]] = field(default_factory=list)
    schema_errors_seen: list[str] = field(default_factory=list)
    schema_fewest_errors: int | None = None
    # The SHA-256 of the schema last stated to the model, over its canonical
    # JSON, so a resume with a different --schema can say so: None when no
    # schema was stated, or in a file saved before nare recorded it.
    schema_sha256: str | None = None
    # Set when a person interrupts a run, so a file left `working` reads as
    # stopped, not as running. reopen() clears it.
    interrupted_at: str | None = None
    # Process groups a bash call left running, as {"pgid", "command"}: the TUI
    # offers to kill the live ones at turn end and on quit. A record, so
    # reopen() keeps it.
    process_groups: list[dict[str, Any]] = field(default_factory=list)
    # What each read, list and search returned, as content-free records in
    # call order: the session's copy of the evidence its progress events
    # carried. A record, so reopen() keeps it.
    evidence: list[dict[str, Any]] = field(default_factory=list)
    # The bash calls a policy rule decided, as {tool_use_id, effect, patterns}
    # in call order: the session's copy of the progress events they carried.
    # Never the command text. A record, so reopen() keeps it.
    bash_rules: list[dict[str, Any]] = field(default_factory=list)
    # The MCP tools whose definitions the model loaded with mcp_load, as
    # {name: sha256 of the definition}: a call through mcp_call runs only these
    # (nare.mcp_defer). Kept by reopen() and fork(). Written only when non-empty,
    # so a run without deferral writes the file it always has.
    mcp_loaded: dict[str, str] = field(default_factory=dict)
    # The system prompt the run sent, so a resume sends it again and a reader
    # can see what the model was told: "" when none was sent, None in a file
    # saved before nare recorded it. Redacted on save, like the transcript.
    system: str | None = None
    # Where the run started, as absolute paths: the working directory, and the
    # resolved --root when one was given. The TUI's resume picker lists the
    # saved sessions whose cwd is the current directory. None in a file saved
    # before nare recorded them.
    cwd: str | None = None
    root: str | None = None
    # Set on a session `nare run --resume PATH --fork` wrote: the session and
    # turn it was forked from, and the path it was read from (None for a
    # library fork without one). Redacted on save. None on any other session.
    forked_from: dict[str, Any] | None = None
    # Written by every save, which drops stale thinking. A file without it
    # comes from an older nare and may hold thinking that redaction or
    # compaction made stale, so loads() drops it by the markers they leave.
    stale_thinking_dropped: bool = True
    contract: int = CONTRACT_VERSION
    nare: str = NARE_VERSION
    events: list[Event] = field(default_factory=list, compare=False)
    version: int = SESSION_VERSION


def nare_home() -> Path | None:
    """$NARE_HOME, or ~/.nare: the folder nare keeps its local files in.

    A relative NARE_HOME is refused rather than ignored, because a folder that
    moves with the working directory would put sessions in the project. None
    when there is neither NARE_HOME nor a home directory.
    """
    raw = os.environ.get("NARE_HOME", "")
    if raw:
        if not os.path.isabs(raw):
            raise ValueError(f"NARE_HOME must be an absolute path, not {raw!r}")
        return Path(raw)
    try:
        return Path.home() / ".nare"
    except RuntimeError:  # no HOME and no passwd entry
        return None


def sessions_dir() -> Path | None:
    """$NARE_HOME/sessions: where a TUI run without --session is saved."""
    home = nare_home()
    return None if home is None else home / "sessions"


def legacy_sessions_dir() -> Path | None:
    """$XDG_STATE_HOME/nare/sessions, where nare saved sessions before
    NARE_HOME. Read, never written: those files stay where they are and resume
    by path. A relative XDG_STATE_HOME is ignored, as the XDG spec says; None
    when there is no home directory either.
    """
    state = os.environ.get("XDG_STATE_HOME", "")
    if not os.path.isabs(state):
        try:
            state = str(Path.home() / ".local" / "state")
        except RuntimeError:  # no HOME and no passwd entry
            return None
    return Path(state) / "nare" / "sessions"


def resumable_in(cwd: str) -> list[SavedSession]:
    """The sessions the resume picker offers in `cwd`: those in NARE_HOME and
    those saved before it, newest first, each file once.
    """
    found: dict[str, SavedSession] = {}
    for folder in (sessions_dir(), legacy_sessions_dir()):
        for row in saved_in(folder, cwd):
            found.setdefault(os.path.realpath(row.path), row)
    return sorted(found.values(), key=lambda row: row.changed, reverse=True)


@dataclass(frozen=True)
class SavedSession:
    """One saved session, as the resume picker lists it."""

    path: Path
    prompt: str
    changed: float  # the file's modification time, seconds since the epoch
    status: str
    turns: int


def saved_in(folder: Path | None, cwd: str) -> list[SavedSession]:
    """The sessions in `folder` that started in `cwd`, newest first. A file
    that does not load, and a session that predates the recorded directory,
    are left out. Compared by real path, so a symlinked directory matches.
    """
    if folder is None or not folder.is_dir():
        return []
    here = os.path.realpath(cwd)
    rows: list[SavedSession] = []
    for path in folder.glob("*.json"):
        try:
            session = loads(path.read_text(encoding="utf-8"))
            changed = path.stat().st_mtime
        except (OSError, ValueError):
            continue
        if session.cwd is None or os.path.realpath(session.cwd) != here:
            continue
        rows.append(
            SavedSession(
                path=path,
                prompt=_first_prompt(session),
                changed=changed,
                # A file left `working` is a run that died, as the TUI shows it.
                status="interrupted" if session.status == "working" else session.status,
                turns=session.turns,
            )
        )
    return sorted(rows, key=lambda row: row.changed, reverse=True)


def _first_prompt(session: Session) -> str:
    for message in session.messages:
        if message.role != "user":
            continue
        for block in message.content:
            if block.get("type") == "text":
                return " ".join(str(block.get("text", "")).split())
    return ""


def new_session(prompt: str, session_id: str | None = None) -> Session:
    s = Session(id=session_id or uuid.uuid4().hex)
    append_user_text(s, prompt)
    return s


def append_user_text(s: Session, text: str) -> None:
    """Add user text, merging into a trailing user turn rather than following it.

    Resuming a blocked session lands here: the transcript already ends with the
    user message carrying `ask`'s tool_result, and vendors require roles to
    alternate. Merging keeps the transcript well-formed.
    """
    block = {"type": "text", "text": text}
    if s.messages and s.messages[-1].role == "user":
        s.messages[-1].content.append(block)
    else:
        s.messages.append(Message(role="user", content=[block]))


def unanswered(messages: list[Message]) -> list[dict[str, Any]]:
    """The calls of a final assistant turn, which have no results yet.

    Only true mid-dispatch, or in a file an older nare saved there. Vendors
    reject a transcript that ends like this, so it is never saved mid-run,
    and reopen() answers the calls before anything follows them.
    """
    if not messages or messages[-1].role != "assistant":
        return []
    return [b for b in messages[-1].content if b.get("type") == "tool_use"]


def dumps(s: Session) -> str:
    """Serialize. Events are drained by `run()` each step and are never state.

    The transcript is redacted on the way out, for the same reason events are
    redacted at construction: this file lands in the workdir, which is a git
    checkout, so it cannot be the one surface that keeps secrets in the clear.
    The cost is that a resumed session reads `[redacted]` where a secret was,
    which is the correct trade: the model should not be re-sent one either.

    A resumed session then sends a history that differs from the one its
    signed thinking was made against, and redaction can rewrite a thinking
    block's own text or signature. So the saved copy drops the thinking sent
    from the first message redaction changes on, as compaction does after an
    edit. The live session keeps its thinking.
    """
    raw = asdict(s)
    raw.pop("events")
    if not raw["mcp_loaded"]:
        raw.pop("mcp_loaded")
    binary: list[tuple[int, int, str]] = []
    for mi, message in enumerate(raw["messages"]):
        for bi, block in enumerate(message["content"]):
            image = block.get("image") if block.get("type") == "tool_result" else None
            source = image.get("source", {}) if isinstance(image, dict) else {}
            data = source.get("data")
            if (
                source.get("type") == "base64"
                and source.get("media_type") in {"image/png", "image/jpeg"}
                and isinstance(data, str)
            ):
                try:
                    base64.b64decode(data, validate=True)
                except (binascii.Error, ValueError):
                    continue
                binary.append((mi, bi, source.pop("data")))
    redacted = redact_value(raw["messages"])
    # Compared without image data, which both copies lack until it goes back.
    first = next(
        (
            i
            for i, (sent, saved) in enumerate(
                zip(raw["messages"], redacted, strict=True)
            )
            if [b for b in sent["content"] if sent_back(b)]
            != [b for b in saved["content"] if sent_back(b)]
        ),
        len(redacted),
    )
    for mi, bi, data in binary:
        redacted[mi]["content"][bi]["image"]["source"]["data"] = data
    for message in redacted[first:]:
        message["content"] = without_sent_thinking(message["content"])
    raw["messages"] = redacted
    raw["system"] = redact_value(raw["system"])
    raw["forked_from"] = redact_value(raw["forked_from"])
    return json.dumps(raw)


def _drop_marked_thinking(messages: list[Message]) -> None:
    """Drop sent thinking from the first marked message on, in an unvetted file.

    A file without `stale_thinking_dropped` may come from a nare that kept
    stale thinking, and it holds no copy of what was sent, so the markers
    redaction and compaction leave stand in for the comparison dumps() makes.
    Text that only quotes a marker loses thinking that was still valid, which
    is safe; stale thinking is a 400.
    """
    from nare.compact import PREFIX  # compact imports this module

    for i, message in enumerate(messages):
        sent = json.dumps([b for b in message.content if sent_back(b)])
        if REDACTED in sent or PREFIX in sent:
            for later in messages[i:]:
                later.content = without_sent_thinking(later.content)
            return


def loads(text: str) -> Session:
    raw: dict[str, Any] = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError(
            f"malformed session file: expected an object, got {type(raw).__name__}"
        )
    version = raw.pop("version", 0)
    if version != SESSION_VERSION:
        raise ValueError(
            f"session file is version {version}; this nare writes {SESSION_VERSION}"
        )
    contract = raw.get("contract", CONTRACT_VERSION)
    if contract != CONTRACT_VERSION:
        raise ValueError(
            f"session file speaks contract {contract}; this nare speaks "
            f"{CONTRACT_VERSION}. Resuming it would mean reading shapes this "
            "nare does not define."
        )
    # Popped like version: once loaded, a session holds only valid thinking.
    vetted = raw.pop("stale_thinking_dropped", False) is True
    try:
        if "cost" not in raw["usage"]:
            raw["usage"]["cost"] = (
                None if raw.get("turns", 0) or any(raw["usage"].values()) else 0.0
            )
        raw["usage"] = Usage(**raw["usage"])
        raw["messages"] = [Message(**m) for m in raw["messages"]]
        # Every reader walks the blocks as objects: the transports, the
        # attach view's renderer and the thinking drop below.
        for message in raw["messages"]:
            if not isinstance(message.content, list) or not all(
                isinstance(b, dict) for b in message.content
            ):
                raise TypeError("message content must be a list of blocks")
        if not vetted:
            _drop_marked_thinking(raw["messages"])
        # The TUI vets each entry and drops junk; a junk container records
        # nothing either, so a bash call can still append to it.
        if not isinstance(raw.get("process_groups", []), list):
            raw["process_groups"] = []
        if not isinstance(raw.get("evidence", []), list):
            raw["evidence"] = []
        if not isinstance(raw.get("bash_rules", []), list):
            raw["bash_rules"] = []
        # Refused, not reset: a resume that quietly fell back to the default
        # prompt is what the record exists to prevent.
        if not isinstance(raw.get("system"), (str, type(None))):
            raise TypeError("system must be a string or null")
        if not isinstance(raw.get("schema_sha256"), (str, type(None))):
            raise TypeError("schema_sha256 must be a string or null")
        if not isinstance(raw.get("forked_from"), (dict, type(None))):
            raise TypeError("forked_from must be an object or null")
        loaded = raw.get("mcp_loaded", {})
        if not isinstance(loaded, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in loaded.items()
        ):
            raise TypeError("mcp_loaded must be an object of strings")
        # A file saved before the budget was counted spent its one correction
        # when schema_retried was set, and counts it as one charged correction.
        if "schema_corrections" not in raw and raw.get("schema_retried") is True:
            raw["schema_corrections"] = 1
        return Session(**raw)
    except (AttributeError, KeyError, TypeError) as exc:
        raise ValueError(f"malformed session file: {exc}") from exc


def fork(s: Session, source: str | Path | None = None) -> Session:
    """A new session that starts from `s`'s transcript, and leaves `s` alone.

    The copy has a new id and a `forked_from` record of `s`'s id, its turn count
    and `source`, the path it was read from. Usage, evidence, the bash rule
    records and the policy carry over, as they do on a resume. Process groups
    do not: a bash process left running belongs to `s`'s run, and the fork must
    not offer to kill it.
    Like a resume, the fork is not reopened here; call `reopen` before `run`.
    """
    child = copy.deepcopy(s)
    child.id = uuid.uuid4().hex
    child.events = []
    child.process_groups = []
    child.forked_from = {
        "session_id": s.id,
        "turn": s.turns,
        "path": None if source is None else os.path.realpath(source),
    }
    return child


def reopen(s: Session, text: str | None = None) -> None:
    """Make a finished session runnable again, with the caller's text if any.

    Resuming is how a caller relays feedback, and how a person answers
    `ask` or follows up in the TUI. `output` is kept: an exhausted resume still
    reports the partial findings it already holds.
    """
    calls = unanswered(s.messages)
    if calls:
        # Whether the call ran is unknown: the model is told so, and checks.
        stopped = "no result: the run stopped before this call returned"
        s.messages.append(
            Message(
                role="user",
                content=[
                    {
                        "type": "tool_result",
                        "tool_use_id": call.get("id"),
                        "content": stopped,
                        "is_error": True,
                    }
                    for call in calls
                ],
            )
        )
    if text:
        append_user_text(s, text)
    s.status = "working"
    s.questions = []
    # Per-run state, like the budget in run(): a resume that inherited the
    # spent correction round would end on its first imperfect answer.
    s.schema_retried = False
    s.schema_corrections = 0
    s.schema_correction_log = []
    s.schema_errors_seen = []
    s.schema_fewest_errors = None
    s.error = None
    s.stop_reason = None
    s.interrupted_at = None


def save(s: Session, path: str | Path) -> None:
    """Write the session atomically, readable only by its owner.

    NamedTemporaryFile has mkstemp semantics: mode 0600 and a unique name. The
    mode matters because the transcript holds whatever the tools read, and the
    file lands in the workdir, which is a git checkout. The unique name matters
    because a fixed `.tmp` collides when two runs share one session path.

    The rename is what survives a kill: a supervising caller may SIGTERM a
    run and then resume the same path, and a plain write truncates before it
    writes, so a signal in that window leaves a partial file and no backup.
    """
    directory = Path(path).parent
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, delete=False
    ) as handle:
        handle.write(dumps(s))
    try:
        os.replace(handle.name, path)
    except OSError:
        os.unlink(handle.name)
        raise
