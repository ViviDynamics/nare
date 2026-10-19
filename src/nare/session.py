"""The serializable session: everything a run carries between steps."""

from __future__ import annotations

import base64
import binascii
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from nare.contract import CONTRACT_VERSION, NARE_VERSION
from nare.events import Event, redact_value

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
    last_input_tokens: int = 0
    last_input_messages: int = 0
    last_input_chars: int = 0
    last_input_image_input: bool | None = None
    policy: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    output: Any = None
    schema_retried: bool = False
    schema_stated: bool = False
    # Set when a person interrupts a run, so a file left `working` reads as
    # stopped, not as running. reopen() clears it.
    interrupted_at: str | None = None
    # Process groups a bash call left running, as {"pgid", "command"}: the TUI
    # offers to kill the live ones at turn end and on quit. A record, so
    # reopen() keeps it.
    process_groups: list[dict[str, Any]] = field(default_factory=list)
    contract: int = CONTRACT_VERSION
    nare: str = NARE_VERSION
    events: list[Event] = field(default_factory=list, compare=False)
    version: int = SESSION_VERSION


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
    return json.dumps(raw)


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
    try:
        if "cost" not in raw["usage"]:
            raw["usage"]["cost"] = (
                None if raw.get("turns", 0) or any(raw["usage"].values()) else 0.0
            )
        raw["usage"] = Usage(**raw["usage"])
        raw["messages"] = [Message(**m) for m in raw["messages"]]
        # The TUI vets each entry and drops junk; a junk container records
        # nothing either, so a bash call can still append to it.
        if not isinstance(raw.get("process_groups", []), list):
            raw["process_groups"] = []
        return Session(**raw)
    except (KeyError, TypeError) as exc:
        raise ValueError(f"malformed session file: {exc}") from exc


def reopen(s: Session, text: str | None = None) -> None:
    """Make a finished session runnable again, with the caller's text if any.

    Resuming is how conductor's relay_feedback works, and how a person answers
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
    s.error = None
    s.stop_reason = None
    s.interrupted_at = None


def save(s: Session, path: str | Path) -> None:
    """Write the session atomically, readable only by its owner.

    NamedTemporaryFile has mkstemp semantics: mode 0600 and a unique name. The
    mode matters because the transcript holds whatever the tools read, and the
    file lands in the workdir, which is a git checkout. The unique name matters
    because a fixed `.tmp` collides when two runs share one session path.

    The rename is what survives a kill: conductor SIGTERMs a run and then
    resumes the same path, and a plain write truncates before it writes, so a
    signal in that window leaves a partial file and no backup.
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
