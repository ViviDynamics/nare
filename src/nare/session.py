"""The serializable session: everything a run carries between steps."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
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
    policy: dict[str, Any] = field(default_factory=dict)
    output: Any = None
    schema_retried: bool = False
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
    raw["messages"] = redact_value(raw["messages"])
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
        raw["usage"] = Usage(**raw["usage"])
        raw["messages"] = [Message(**m) for m in raw["messages"]]
        return Session(**raw)
    except (KeyError, TypeError) as exc:
        raise ValueError(f"malformed session file: {exc}") from exc
