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


@dataclass(frozen=True)
class Usage:
    """Token counts. `input` EXCLUDES cache reads and writes, following Anthropic."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: float | None = 0.0

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
    contract: int = CONTRACT_VERSION
    nare: str = NARE_VERSION
    events: list[Event] = field(default_factory=list, compare=False)
    version: int = SESSION_VERSION


def new_session(prompt: str) -> Session:
    s = Session(id=uuid.uuid4().hex)
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


def dumps(s: Session) -> str:
    """Serialize. Events are drained by `run()` each step and are never state.

    The transcript is redacted on the way out, for the same reason events are
    redacted at construction: this file lands in the workdir, which is a git
    checkout, so it cannot be the one surface that keeps secrets in the clear.
    The cost is that a resumed session reads `[redacted]` where a secret was,
    which is the correct trade — the model should not be re-sent one either.
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
    raw["messages"] = redact_value(raw["messages"])
    for mi, bi, data in binary:
        raw["messages"][mi]["content"][bi]["image"]["source"]["data"] = data
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
        return Session(**raw)
    except (KeyError, TypeError) as exc:
        raise ValueError(f"malformed session file: {exc}") from exc


def reopen(s: Session, text: str | None = None) -> None:
    """Make a finished session runnable again, with the caller's text if any.

    Resuming is how conductor's relay_feedback works, and how a person answers
    `ask` or follows up in the TUI. `output` is kept: an exhausted resume still
    reports the partial findings it already holds.
    """
    if text:
        append_user_text(s, text)
    s.status = "working"
    s.questions = []
    # Per-run state, like the budget in run(): a resume that inherited the
    # spent correction round would end on its first imperfect answer.
    s.schema_retried = False
    s.error = None
    s.stop_reason = None


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
