"""The serializable session: everything a run carries between steps."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from nare.events import Event

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
    """Token counts. `input` EXCLUDES cache reads, following Anthropic."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input=self.input + other.input,
            output=self.output + other.output,
            cache_read=self.cache_read + other.cache_read,
            cache_write=self.cache_write + other.cache_write,
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
    """Serialize. Events are drained by `run()` each step and are never state."""
    raw = asdict(s)
    raw.pop("events")
    return json.dumps(raw)


def loads(text: str) -> Session:
    raw: dict[str, Any] = json.loads(text)
    version = raw.pop("version", 0)
    if version != SESSION_VERSION:
        raise ValueError(
            f"session file is version {version}; this nare writes {SESSION_VERSION}"
        )
    raw["usage"] = Usage(**raw["usage"])
    raw["messages"] = [Message(**m) for m in raw["messages"]]
    return Session(**raw)
