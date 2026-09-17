# nare Walking Skeleton (Slice 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build nare's core engine and headless CLI — an agent loop, five tools, a transport layer with one Anthropic implementation, and a serializable session — emitting typed JSONL that Conductor's performer can consume through an ~80-line adapter.

**Architecture:** The core is a library; the CLI is a thin adapter over it. A `Session` dataclass is advanced by an explicit `async step()`, surfaced as `async for event in run(...)`. Everything vendor-shaped lives below a `Transport` protocol built by `make_transport()`; everything above it — the loop, tools, approval, events — is vendor-free. Serialization is `asdict()` plus `json`, which makes `--resume` and Conductor's `relay_feedback` the same feature.

**Tech Stack:** Python 3.12+, uv, hatchling, one runtime dependency (`anthropic`). Dev: pytest, pytest-asyncio, ruff, mypy. No pydantic, no structlog, no click/typer, no LangGraph.

**Spec:**
- `docs/superpowers/specs/2026-09-09-nare-walking-skeleton-design.md` (slice 1)
- `docs/superpowers/specs/2026-09-15-nare-transport-layer-design.md` (amends sections 4, 5, 6, 8, 9 of the above — **read it second, it wins on every conflict**)

## Global Constraints

Every task's requirements implicitly include this section. Values are copied verbatim from the specs.

- **Python `>=3.12`.** uv for env management, hatchling for build.
- **Exactly one runtime dependency: `anthropic`.** Adding any other runtime dependency fails the plan. A second transport's SDK lands later as an optional extra (`pip install nare[openai]`), never as a base dependency.
- **Dev dependencies are pytest, pytest-asyncio, ruff, mypy.** Nothing else. No mocking framework — `httpx2.MockTransport` (which arrives inside `anthropic`) covers the network, and `FakeProvider` covers the loop.
- **No network access in any test** except the single `@pytest.mark.live` smoke test, which is deselected by default and never runs in CI.
- **License metadata:** `license = "LicenseRef-Elastic-License-2.0"`, `license-files = ["LICENSE", "NOTICE"]`. Console script `nare = "nare.cli:main"`.
- **Defaults, exact:** model `claude-sonnet-5`; `max_tokens` 8192; `--max-turns` 50; effort budgets `low`=1024, `medium`=4096, `high`=16384; effort's default `max_tokens` = `min(budget + 8192, 21333)`.
- **SDK reality, amended 2026-09-16 (both specs carry amendment notes).** `anthropic` 1.6.0: the HTTP library is **`httpx2`**, not `httpx`; **`temperature` does not exist** on `messages.create`, and the anthropic transport raises when it is set rather than dropping it silently; the omission sentinel is **`omit`**, not `NOT_GIVEN`; and `max_tokens` above **21333** requires streaming, which is slice 3's, so the transport refuses it.
- **`max_tokens` defaults to `None`** in both the CLI and `make_transport()` signatures, and resolves to 8192 inside the transport. The transport must be able to distinguish an explicit `--max-tokens 8192` from an unset value.
- **There is no `--api-key` flag.** Argv is world-readable through `ps` and `/proc`. Keys come from the environment. `make_transport(api_key=...)` exists for library callers only.
- **Event types are exactly six, verbatim:** `progress`, `tool_use`, `thinking`, `cost`, `error`, `output`. Four fields, verbatim: `timestamp`, `type`, `text`, `detail`. These match Conductor's `BackendEventType` so its adapter is `BackendEvent(**json.loads(line))`.
- **Statuses are exactly four:** `working`, `blocked`, `done`, `error`.
- **`StopReason` is exactly five:** `end_turn`, `tool_use`, `max_tokens`, `stop_sequence`, `refusal`. `max_turns` is set by the loop, never by a transport.
- **`Usage.input` excludes cache reads** (Anthropic's convention). An OpenAI transport would subtract `cached_tokens` from `prompt_tokens`.
- **Two `ponytail:` comments are mandatory** (both specified): sequential tool dispatch in `loop.py`, and the one-arm `match` in `make_transport()`.
- **Out of scope — do not build:** `grep`/`glob` tools, config file, sessions directory or history browser, MCP/hooks/skills/subagents, compaction, TUI/HTTP daemon/desktop, a second provider, a transport registry, plugin loading, an API reference, a roadmap doc, cost in dollars.
- **nare knows nothing about** git, GitHub, pull requests, branches, cards, roles, review cycles, or lifecycle. If a task seems to need one of those, the boundary is being crossed.
- `.github/workflows/external-contributions.yml` is untouched by every task.

**Commit after every task.** Conventional commit messages (`feat:`, `test:`, `docs:`, `chore:`).

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `pyproject.toml` | Packaging, deps, ruff/mypy/pytest config | 1 |
| `bin/build` | lint, format-check, types, tests | 1 |
| `.github/workflows/ci.yml` | Runs `bin/build` on push and PR | 1 |
| `src/nare/__init__.py` | Public API surface only | 1, 11 |
| `src/nare/events.py` | `Event` + secret redaction at construction | 2 |
| `src/nare/session.py` | `Session`, `Message`, `Usage`, `Status`, serialization | 3 |
| `src/nare/transport/__init__.py` | `Transport`, `Reply`, `ToolCall`, `StopReason`, `make_transport()` | 4, 7 |
| `src/nare/transport/anthropic.py` | `AnthropicTransport` — the only vendor-shaped code | 5, 6 |
| `src/nare/tools.py` | Five tools, their schemas, dispatch, approval | 8, 9 |
| `src/nare/loop.py` | `step()`, `run()` | 10, 11 |
| `src/nare/cli.py` | `nare run` | 12, 13 |
| `tests/test_events.py` | Redaction and the event contract | 2 |
| `tests/test_session.py` | Serialization round-trip and versioning | 3 |
| `tests/fake_provider.py` | Scripted `Transport` double, `Exploding`, reply fixtures | 4 |
| `tests/test_transport.py` | Factory, validation, real outbound request, normalization tables | 4–7 |
| `tests/test_tools.py` | Each tool, dispatch, approval | 8, 9 |
| `tests/test_loop.py` | done / blocked / max-turns / tool-error / resume round-trip | 10, 11 |
| `tests/test_cli.py` | Golden JSONL, exit codes, resume | 12, 13 |
| `docs/adr/000{1..6}-*.md`, `docs/architecture.md` | The six decisions, then the shape that resulted | 14 |
| `docs/superpowers/conductor-adapter-sketch.md` | Evidence the event contract holds | 14 |

`transport/` is the one package; everything else is flat. A directory holding one file is still speculation.

`tests/test_events.py` and `tests/test_session.py` are additions to the spec's tree, which named four test modules. Those two files carry real logic — redaction and versioned serialization — and the spec's own testing section does not assign them anywhere else.

---

### Task 1: Project skeleton and toolchain

**Files:**
- Create: `pyproject.toml`, `bin/build`, `.github/workflows/ci.yml`, `src/nare/__init__.py`, `src/nare/py.typed`, `.gitignore`
- Test: `tests/test_package.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `nare.__version__: str`. A green `bin/build` that every later task re-runs.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "nare"
version = "0.1.0"
description = "A standalone agent harness: a loop, a tool set, a transport layer, and a serializable session."
readme = "README.md"
requires-python = ">=3.12"
license = "LicenseRef-Elastic-License-2.0"
license-files = ["LICENSE", "NOTICE"]
dependencies = ["anthropic>=0.40"]

[project.scripts]
nare = "nare.cli:main"

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "ruff>=0.6", "mypy>=1.11"]

[tool.hatch.build.targets.wheel]
packages = ["src/nare"]

[tool.ruff]
line-length = 88
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.mypy]
python_version = "3.12"
strict = true
mypy_path = "tests"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = ["live: hits the real Anthropic API; needs ANTHROPIC_API_KEY"]
addopts = "-m 'not live'"
```

The `addopts` line is what keeps the live smoke test out of CI for free. `mypy_path = "tests"` is what lets mypy resolve `from fake_provider import FakeProvider`.

- [ ] **Step 2: Create the package**

`src/nare/__init__.py`:

```python
"""nare — a standalone agent harness."""

__version__ = "0.1.0"
```

`src/nare/py.typed` is an empty file.

`.gitignore`:

```
__pycache__/
*.egg-info/
.venv/
.mypy_cache/
.pytest_cache/
.ruff_cache/
dist/
```

- [ ] **Step 3: Write the failing test**

`tests/test_package.py`:

```python
import nare


def test_version_is_exposed() -> None:
    assert nare.__version__ == "0.1.0"
```

- [ ] **Step 4: Write `bin/build`**

```bash
#!/usr/bin/env bash
# Lint, types, tests. Matches conductor's idiom so one habit covers both repos.
set -euo pipefail
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest -q
```

Then `chmod +x bin/build`.

- [ ] **Step 5: Run the build**

Run: `uv sync && bin/build`
Expected: PASS — one test, no lint errors, no type errors.

- [ ] **Step 6: Write the CI workflow**

`.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
  pull_request:

jobs:
  build:
    name: lint, types, tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.12"
          enable-cache: true
      - run: uv sync
      - run: bin/build
```

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml bin/build .github/workflows/ci.yml src/nare tests/test_package.py .gitignore
git commit -m "chore: scaffold the nare package, toolchain, and CI"
```

---

### Task 2: Events and secret redaction

**Files:**
- Create: `src/nare/events.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `EventType = Literal["progress", "tool_use", "thinking", "cost", "error", "output"]`
  - `Event(type: EventType, text: str, detail: dict[str, Any] = {}, timestamp: str = <now>)`, frozen, redacts in `__post_init__`
  - `redact(text: str) -> str`

- [ ] **Step 1: Write the failing test**

`tests/test_events.py`:

```python
from dataclasses import asdict

from nare.events import Event, redact


def test_event_fields_match_conductors_backend_event() -> None:
    e = Event("progress", "working")
    assert set(asdict(e)) == {"timestamp", "type", "text", "detail"}


def test_timestamp_is_iso_utc() -> None:
    assert Event("progress", "x").timestamp.endswith("+00:00")


def test_anthropic_keys_are_redacted_in_text() -> None:
    key = "sk-ant-api03-" + "A" * 40
    assert key not in Event("progress", f"exported {key} to env").text
    assert "[redacted]" in Event("progress", f"exported {key}").text


def test_github_tokens_are_redacted() -> None:
    assert "[redacted]" in redact("ghp_" + "b" * 36)


def test_assignments_are_redacted() -> None:
    assert redact("API_KEY=hunter2") == "[redacted]"
    assert redact("token: abc123def") == "[redacted]"


def test_ordinary_text_survives() -> None:
    assert redact("read src/nare/loop.py") == "read src/nare/loop.py"


def test_detail_is_redacted_recursively() -> None:
    key = "sk-ant-api03-" + "C" * 40
    e = Event("tool_use", "bash", {"command": f"echo {key}", "nested": {"k": [key]}})
    assert key not in str(e.detail)


def test_non_string_detail_values_pass_through() -> None:
    e = Event("cost", "tokens", {"input": 10, "output": 2, "ok": True})
    assert e.detail == {"input": 10, "output": 2, "ok": True}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_events.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nare.events'`

- [ ] **Step 3: Write `src/nare/events.py`**

```python
"""Typed events. Field names and type values match conductor's BackendEvent
verbatim, so its adapter is `BackendEvent(**json.loads(line))` and not a
mapping table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

EventType = Literal["progress", "tool_use", "thinking", "cost", "error", "output"]

_SECRETS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|auth|token|secret|password)\b\s*[=:]\s*\S+"),
]


def redact(text: str) -> str:
    for pattern in _SECRETS:
        text = pattern.sub("[redacted]", text)
    return text


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Event:
    type: EventType
    text: str
    detail: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        # Redaction at construction, not at emission: an unredacted Event must
        # never exist, because every later surface trusts it.
        object.__setattr__(self, "text", redact(self.text))
        object.__setattr__(self, "detail", _redact_value(self.detail))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_events.py -q`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add src/nare/events.py tests/test_events.py
git commit -m "feat: add typed events with redaction at construction"
```

---

### Task 3: Session, Message, Usage, and serialization

**Files:**
- Create: `src/nare/session.py`
- Test: `tests/test_session.py`

**Interfaces:**
- Consumes: `Event` from Task 2.
- Produces:
  - `Status = Literal["working", "blocked", "done", "error"]`
  - `Message(role: Role, content: list[dict[str, Any]])`
  - `Usage(input: int, output: int, cache_read: int, cache_write: int)`, frozen, supports `+`
  - `Session(id, messages, usage, status, questions, stop_reason, error, turns, events, version)`
  - `new_session(prompt: str) -> Session`
  - `append_user_text(s: Session, text: str) -> None`
  - `dumps(s: Session) -> str`, `loads(text: str) -> Session`
  - `SESSION_VERSION: int`

- [ ] **Step 1: Write the failing test**

`tests/test_session.py`:

```python
import pytest

from nare.events import Event
from nare.session import (
    SESSION_VERSION,
    Message,
    Session,
    Usage,
    append_user_text,
    dumps,
    loads,
    new_session,
)


def test_usage_adds_every_field() -> None:
    total = Usage(1, 2, 3, 4) + Usage(10, 20, 30, 40)
    assert total == Usage(11, 22, 33, 44)


def test_new_session_starts_with_one_user_message() -> None:
    s = new_session("fix the bug")
    assert s.status == "working"
    assert s.turns == 0
    assert s.messages == [
        Message(role="user", content=[{"type": "text", "text": "fix the bug"}])
    ]
    assert s.id


def test_append_user_text_merges_into_a_trailing_user_message() -> None:
    s = new_session("first")
    append_user_text(s, "second")
    assert len(s.messages) == 1
    assert s.messages[0].content == [
        {"type": "text", "text": "first"},
        {"type": "text", "text": "second"},
    ]


def test_append_user_text_starts_a_new_turn_after_an_assistant_message() -> None:
    s = new_session("first")
    s.messages.append(Message(role="assistant", content=[{"type": "text", "text": "ok"}]))
    append_user_text(s, "second")
    assert len(s.messages) == 3
    assert s.messages[-1].role == "user"


def test_round_trip_preserves_the_session() -> None:
    s = new_session("go")
    s.usage = Usage(5, 6, 7, 8)
    s.turns = 2
    s.status = "blocked"
    s.questions = ["which file?"]
    s.stop_reason = "tool_use"
    back = loads(dumps(s))
    assert back == s


def test_events_do_not_survive_serialization() -> None:
    s = new_session("go")
    s.events.append(Event("progress", "thinking about it"))
    assert '"events"' not in dumps(s)
    assert loads(dumps(s)).events == []


def test_loads_rejects_a_foreign_version() -> None:
    raw = dumps(new_session("go")).replace(
        f'"version": {SESSION_VERSION}', '"version": 99'
    )
    with pytest.raises(ValueError, match="version 99"):
        loads(raw)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_session.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nare.session'`

- [ ] **Step 3: Write `src/nare/session.py`**

```python
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
    events: list[Event] = field(default_factory=list)
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_session.py -q`
Expected: PASS, 7 tests.

- [ ] **Step 5: Run the full build**

Run: `bin/build`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/nare/session.py tests/test_session.py
git commit -m "feat: add the serializable Session, Message, and Usage"
```

---

### Task 4: The transport contract and FakeProvider

**Files:**
- Create: `src/nare/transport/__init__.py`, `tests/fake_provider.py`, `tests/test_transport.py`
- Test: `tests/test_transport.py`

**Interfaces:**
- Consumes: `Message`, `Usage` from Task 3.
- Produces:
  - `StopReason = Literal["end_turn", "tool_use", "max_tokens", "stop_sequence", "refusal"]`
  - `ToolCall(id: str, name: str, args: dict[str, Any])`, frozen
  - `Reply(content: list[dict[str, Any]], tool_calls: list[ToolCall], usage: Usage, stop_reason: StopReason)`, frozen
  - `Transport` — a `typing.Protocol` with one method, `async def turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> Reply`
  - `FakeProvider(replies: list[Reply])` with `.calls: list[tuple[list[Message], list[dict]]]`
  - `text_reply(...)`, `tool_reply(...)`, `Exploding` — the fixtures every later test reuses

`make_transport()` arrives in Task 7, after the product it builds exists.

- [ ] **Step 1: Write `tests/fake_provider.py`**

```python
"""A scripted Transport. Inherits nothing and imports no vendor code, which is
what keeps the seam honestly duck-typed.
"""

from __future__ import annotations

from typing import Any

from nare.session import Message, Usage
from nare.transport import Reply, StopReason, ToolCall, Transport


class FakeProvider:
    def __init__(self, replies: list[Reply]) -> None:
        self._replies = list(replies)
        self.calls: list[tuple[list[Message], list[dict[str, Any]]]] = []

    async def turn(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> Reply:
        self.calls.append(([Message(m.role, list(m.content)) for m in messages], tools))
        if not self._replies:
            raise AssertionError("FakeProvider ran out of scripted replies")
        return self._replies.pop(0)


# The whole cost of keeping the double and the protocol honest: mypy fails here
# if Transport ever drifts away from FakeProvider.
_check: Transport = FakeProvider([])


def text_reply(text: str, *, stop_reason: StopReason = "end_turn") -> Reply:
    return Reply(
        content=[{"type": "text", "text": text}],
        tool_calls=[],
        usage=Usage(input=10, output=5),
        stop_reason=stop_reason,
    )


class Exploding:
    """A Transport whose turn() always fails, for exercising the error path."""

    async def turn(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> Reply:
        raise RuntimeError("connection reset")


def tool_reply(name: str, args: dict[str, Any], *, call_id: str = "call_1") -> Reply:
    return Reply(
        content=[{"type": "tool_use", "id": call_id, "name": name, "input": args}],
        tool_calls=[ToolCall(id=call_id, name=name, args=args)],
        usage=Usage(input=10, output=5),
        stop_reason="tool_use",
    )
```

- [ ] **Step 2: Write the failing test**

`tests/test_transport.py`:

```python
import dataclasses
from typing import get_args

import pytest
from fake_provider import FakeProvider, text_reply

from nare.session import Message, Usage
from nare.transport import Reply, StopReason, ToolCall


def test_stop_reason_vocabulary_is_exactly_the_five() -> None:
    assert set(get_args(StopReason)) == {
        "end_turn",
        "tool_use",
        "max_tokens",
        "stop_sequence",
        "refusal",
    }


async def test_fake_provider_replays_in_order() -> None:
    fake = FakeProvider([text_reply("one"), text_reply("two")])
    first = await fake.turn([Message("user", [])], [])
    second = await fake.turn([Message("user", [])], [])
    assert first.content[0]["text"] == "one"
    assert second.content[0]["text"] == "two"
    assert len(fake.calls) == 2


async def test_fake_provider_raises_when_exhausted() -> None:
    with pytest.raises(AssertionError, match="ran out"):
        await FakeProvider([]).turn([], [])


def test_reply_is_frozen() -> None:
    reply = Reply(content=[], tool_calls=[], usage=Usage(), stop_reason="end_turn")
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(reply, "stop_reason", "tool_use")


def test_tool_call_carries_id_name_and_args() -> None:
    call = ToolCall(id="c1", name="read", args={"path": "x.py"})
    assert (call.id, call.name, call.args) == ("c1", "read", {"path": "x.py"})
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_transport.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nare.transport'`

- [ ] **Step 4: Write `src/nare/transport/__init__.py`**

```python
"""The transport layer: nare's bottom layer.

Everything vendor-shaped lives below this line — wire format, tool schema
translation, sampling parameters, retry policy, and stop_reason and usage
normalization. Everything above it is the loop, the tools, approval, and events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from nare.session import Message, Usage

StopReason = Literal[
    "end_turn", "tool_use", "max_tokens", "stop_sequence", "refusal"
]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Reply:
    content: list[dict[str, Any]]
    tool_calls: list[ToolCall]
    usage: Usage
    stop_reason: StopReason


class Transport(Protocol):
    """One method. Everything else is bound at construction, which keeps the
    loop free of vendor parameters entirely.

    Structural, not nominal: implementations inherit nothing and import nothing
    from here, and mypy still checks them.
    """

    async def turn(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> Reply: ...


__all__ = ["Reply", "StopReason", "ToolCall", "Transport"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_transport.py -q && uv run mypy src tests`
Expected: PASS, 5 tests, and mypy clean — which is the `_check: Transport = FakeProvider([])` assertion doing its job.

- [ ] **Step 6: Commit**

```bash
git add src/nare/transport tests/fake_provider.py tests/test_transport.py
git commit -m "feat: establish the Transport protocol and its test double"
```

---

### Task 5: AnthropicTransport construction and parameter validation

**Files:**
- Create: `src/nare/transport/anthropic.py`
- Modify: `tests/test_transport.py` (append)

**Interfaces:**
- Consumes: `Reply`, `ToolCall`, `StopReason` (Task 4); `Usage` (Task 3).
- Produces:
  - `EFFORT_BUDGETS: dict[str, int]` — `{"low": 1024, "medium": 4096, "high": 16384}`
  - `DEFAULT_MAX_TOKENS: int = 8192`
  - `NONSTREAMING_MAX_TOKENS: int = 21333`
  - `AnthropicTransport(*, model, base_url=None, api_key=None, temperature=None, max_tokens=None, effort=None, system=None, http_client=None)`
  - Readable attributes for the tests: `.model`, `.max_tokens`, `.thinking`, `.system`. There is deliberately **no `.temperature`** — the vendor removed the parameter, so the constructor refuses it rather than storing a value it could never send.

A shared parameter bag cannot know that Anthropic's thinking budget must sit under `max_tokens`, that this vendor rejects `temperature` outright, or that the SDK refuses non-streaming requests above 21333 tokens. A per-transport edge can, and that is the difference between a seam that varies something and one that varies nothing.

- [ ] **Step 1: Write the failing test** (append to `tests/test_transport.py`)

```python
from nare.transport.anthropic import (
    DEFAULT_MAX_TOKENS,
    EFFORT_BUDGETS,
    NONSTREAMING_MAX_TOKENS,
    AnthropicTransport,
)


def build(**kwargs: Any) -> AnthropicTransport:
    params: dict[str, Any] = {"model": "claude-sonnet-5", "api_key": "test-key"}
    params.update(kwargs)
    return AnthropicTransport(**params)


def test_max_tokens_defaults_to_8192() -> None:
    assert build().max_tokens == DEFAULT_MAX_TOKENS


def test_explicit_max_tokens_is_bound() -> None:
    assert build(max_tokens=512).max_tokens == 512


def test_no_effort_means_no_thinking_block() -> None:
    assert build().thinking is None


@pytest.mark.parametrize("effort,budget", sorted(EFFORT_BUDGETS.items()))
def test_effort_renders_a_thinking_budget(effort: str, budget: int) -> None:
    t = build(effort=effort)
    assert t.thinking == {"type": "enabled", "budget_tokens": budget}
    assert t.max_tokens == min(budget + DEFAULT_MAX_TOKENS, NONSTREAMING_MAX_TOKENS)


def test_effort_high_clamps_to_the_nonstreaming_ceiling() -> None:
    # budget + 8192 would be 24576, which the SDK refuses without streaming.
    t = build(effort="high")
    assert t.thinking == {"type": "enabled", "budget_tokens": 16384}
    assert t.max_tokens == NONSTREAMING_MAX_TOKENS == 21333


def test_temperature_is_refused_outright() -> None:
    # anthropic 1.6.0 removed temperature from messages.create. Refusing beats
    # silently dropping a sampling parameter the caller explicitly asked for.
    with pytest.raises(ValueError, match="temperature"):
        build(temperature=0.2)


def test_temperature_is_refused_with_effort_too() -> None:
    with pytest.raises(ValueError, match="temperature"):
        build(effort="high", temperature=0.2)


def test_effort_with_max_tokens_at_or_below_the_budget_raises() -> None:
    with pytest.raises(ValueError, match="16384"):
        build(effort="high", max_tokens=16384)


def test_effort_with_max_tokens_above_the_budget_is_accepted() -> None:
    assert build(effort="high", max_tokens=20000).max_tokens == 20000


def test_max_tokens_above_the_nonstreaming_ceiling_raises() -> None:
    with pytest.raises(ValueError, match="streaming"):
        build(max_tokens=NONSTREAMING_MAX_TOKENS + 1)


def test_missing_api_key_raises_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        AnthropicTransport(model="claude-sonnet-5")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_transport.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nare.transport.anthropic'`

- [ ] **Step 3: Write `src/nare/transport/anthropic.py`** (construction only — `turn()` lands in Task 6)

```python
"""The Anthropic transport. The only vendor-shaped code in nare."""

from __future__ import annotations

import os
from typing import Any, Literal

import httpx2
from anthropic import AsyncAnthropic

DEFAULT_MAX_TOKENS = 8192

# Above this the SDK's own _calculate_nonstreaming_timeout raises, because
# 3600 * max_tokens / 128000 exceeds its ten-minute non-streaming budget.
# Streaming turn() is slice 3's, so the transport refuses rather than guessing.
NONSTREAMING_MAX_TOKENS = 21333

EFFORT_BUDGETS: dict[str, int] = {"low": 1024, "medium": 4096, "high": 16384}

Effort = Literal["low", "medium", "high"]


class AnthropicTransport:
    def __init__(
        self,
        *,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        effort: Effort | None = None,
        system: str | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        if api_key is None and not os.environ.get("ANTHROPIC_API_KEY"):
            raise ValueError(
                "no Anthropic API key: set ANTHROPIC_API_KEY in the environment "
                "(there is no --api-key flag; argv is world-readable)"
            )
        if temperature is not None:
            raise ValueError(
                "anthropic no longer accepts temperature: the parameter was "
                "removed from messages.create, so nare refuses it rather than "
                "silently dropping it. Omit --temperature for this provider."
            )

        self.thinking: dict[str, Any] | None = None
        budget = EFFORT_BUDGETS[effort] if effort is not None else None

        if budget is None:
            resolved = max_tokens if max_tokens is not None else DEFAULT_MAX_TOKENS
        else:
            resolved = (
                max_tokens
                if max_tokens is not None
                else min(budget + DEFAULT_MAX_TOKENS, NONSTREAMING_MAX_TOKENS)
            )
            if resolved <= budget:
                raise ValueError(
                    f"max_tokens {resolved} must exceed the {effort} thinking "
                    f"budget of {budget}"
                )
            self.thinking = {"type": "enabled", "budget_tokens": budget}

        if resolved > NONSTREAMING_MAX_TOKENS:
            raise ValueError(
                f"max_tokens {resolved} exceeds {NONSTREAMING_MAX_TOKENS}, above "
                "which the SDK requires streaming; streaming turn() is slice 3's"
            )

        self.max_tokens = resolved
        self.model = model
        self.system = system
        self._client = AsyncAnthropic(
            api_key=api_key, base_url=base_url, http_client=http_client
        )
```

Note `max_retries` is not passed: the SDK's own default handles 429s and 5xxs, which is the whole retry policy slice 1 ships.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_transport.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nare/transport/anthropic.py tests/test_transport.py
git commit -m "feat: validate anthropic sampling parameters at construction"
```

---

### Task 6: AnthropicTransport.turn — request shaping and normalization

**Files:**
- Modify: `src/nare/transport/anthropic.py`
- Modify: `tests/test_transport.py` (append)

**Interfaces:**
- Consumes: everything from Task 5.
- Produces:
  - `AnthropicTransport.turn(messages, tools) -> Reply`
  - `stop_reason_from(raw: str | None) -> StopReason`
  - `usage_from(raw: Any) -> Usage`

The tests assert against the **real request the real SDK constructs**, through `httpx2.MockTransport`. That is materially stronger than stubbing `turn()`, and it is the harness that makes the second transport cheap to add. `httpx2` arrives as a dependency of `anthropic`, so this costs zero new dependencies.

- [ ] **Step 1: Write the failing test** (append to `tests/test_transport.py`)

```python
import json
import os
from typing import Any

import httpx2

from nare.session import Message
from nare.transport.anthropic import (
    NONSTREAMING_MAX_TOKENS,
    stop_reason_from,
    usage_from,
)

CANNED_BODY: dict[str, Any] = {
    "id": "msg_01",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-5",
    "content": [
        {"type": "text", "text": "reading it now"},
        {"type": "tool_use", "id": "call_1", "name": "read", "input": {"path": "a.py"}},
    ],
    "stop_reason": "tool_use",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 200,
        "output_tokens": 50,
        "cache_read_input_tokens": 800,
        "cache_creation_input_tokens": 12,
    },
}


def recording_client(captured: list[httpx2.Request]) -> httpx2.AsyncClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        return httpx2.Response(200, json=CANNED_BODY)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


async def outbound(**kwargs: Any) -> dict[str, Any]:
    captured: list[httpx2.Request] = []
    transport = build(http_client=recording_client(captured), **kwargs)
    await transport.turn(
        [Message("user", [{"type": "text", "text": "read a.py"}])],
        [{"name": "read", "description": "read a file", "input_schema": {}}],
    )
    return json.loads(captured[0].content)


async def test_model_and_max_tokens_reach_the_wire() -> None:
    body = await outbound(max_tokens=512)
    assert body["model"] == "claude-sonnet-5"
    assert body["max_tokens"] == 512


async def test_temperature_never_reaches_the_wire() -> None:
    # The constructor refuses --temperature outright (task 5), so there is no
    # path by which this vendor's request body can carry one.
    assert "temperature" not in await outbound()
    with pytest.raises(ValueError, match="temperature"):
        await outbound(temperature=0.2)


async def test_unset_sampling_params_are_omitted_entirely() -> None:
    body = await outbound()
    assert "thinking" not in body
    assert "system" not in body


async def test_system_reaches_the_wire() -> None:
    assert (await outbound(system="You are the architect."))["system"] == (
        "You are the architect."
    )


async def test_effort_renders_thinking_clamped_to_the_ceiling() -> None:
    body = await outbound(effort="high")
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 16384}
    assert "temperature" not in body
    assert body["max_tokens"] == 21333


async def test_tool_schemas_pass_through_anthropic_shaped() -> None:
    assert (await outbound())["tools"] == [
        {"name": "read", "description": "read a file", "input_schema": {}}
    ]


async def test_messages_serialize_as_role_plus_blocks() -> None:
    assert (await outbound())["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "read a.py"}]}
    ]


async def test_turn_normalizes_the_response() -> None:
    transport = build(http_client=recording_client([]))
    reply = await transport.turn([Message("user", [])], [])
    assert reply.stop_reason == "tool_use"
    assert reply.tool_calls == [ToolCall(id="call_1", name="read", args={"path": "a.py"})]
    assert reply.content[0]["text"] == "reading it now"
    assert reply.usage == Usage(input=200, output=50, cache_read=800, cache_write=12)


# --- Normalization tables, section 4 of the transport spec -------------------
# The OpenAI rows are asserted now, before the OpenAI transport exists. If its
# spec later contradicts these rows, that contradiction is worth catching.

ANTHROPIC_STOP_REASONS = [
    ("end_turn", "end_turn"),
    ("tool_use", "tool_use"),
    ("max_tokens", "max_tokens"),
    ("stop_sequence", "stop_sequence"),
    ("refusal", "refusal"),
]

OPENAI_FINISH_REASONS = [
    ("stop", "end_turn"),
    ("tool_calls", "tool_use"),
    ("length", "max_tokens"),
    ("content_filter", "refusal"),
]


@pytest.mark.parametrize("raw,expected", ANTHROPIC_STOP_REASONS)
def test_anthropic_stop_reasons_normalize(raw: str, expected: str) -> None:
    assert stop_reason_from(raw) == expected


@pytest.mark.parametrize("raw,expected", OPENAI_FINISH_REASONS)
def test_openai_finish_reasons_have_a_target_in_the_vocabulary(
    raw: str, expected: str
) -> None:
    assert expected in get_args(StopReason)


def test_an_unknown_stop_reason_degrades_to_end_turn() -> None:
    assert stop_reason_from("pause_turn") == "end_turn"


class _RawUsage:
    input_tokens = 200
    output_tokens = 50
    cache_read_input_tokens = 800
    cache_creation_input_tokens = 12


def test_anthropic_usage_excludes_cache_reads_from_input() -> None:
    assert usage_from(_RawUsage()) == Usage(
        input=200, output=50, cache_read=800, cache_write=12
    )


def test_openai_usage_would_subtract_cached_tokens_to_reach_the_same_numbers() -> None:
    # OpenAI's prompt_tokens INCLUDES cache reads; Anthropic's input_tokens does
    # not. The same logical request must produce the same Usage on both edges.
    prompt_tokens, cached_tokens, completion_tokens = 1000, 800, 50
    openai_side = Usage(
        input=prompt_tokens - cached_tokens,
        output=completion_tokens,
        cache_read=cached_tokens,
        cache_write=0,  # OpenAI has no cache-write concept
    )
    assert openai_side.input == usage_from(_RawUsage()).input
    assert openai_side.cache_read == usage_from(_RawUsage()).cache_read


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"), reason="needs a real API key"
)
async def test_live_smoke() -> None:
    transport = AnthropicTransport(model="claude-sonnet-5", max_tokens=64)
    reply = await transport.turn(
        [Message("user", [{"type": "text", "text": "Reply with the word OK."}])], []
    )
    assert reply.stop_reason in get_args(StopReason)
    assert reply.usage.output > 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_transport.py -q`
Expected: FAIL — `ImportError: cannot import name 'stop_reason_from'`

- [ ] **Step 3: Add `turn()` and the normalizers to `src/nare/transport/anthropic.py`**

Add these imports at the top:

```python
import logging
from dataclasses import asdict
from typing import cast

from anthropic import omit
from anthropic.types import MessageParam, ToolParam

from nare.session import Message, Usage
from nare.transport import Reply, StopReason, ToolCall

log = logging.getLogger(__name__)

_STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "stop_sequence": "stop_sequence",
    "refusal": "refusal",
}


def stop_reason_from(raw: str | None) -> StopReason:
    """Normalize. An unknown value degrades to end_turn with a warning rather
    than killing a run, because the loop decides when to stop from tool_calls,
    not from this field.
    """
    if raw in _STOP_REASONS:
        return _STOP_REASONS[raw]
    log.warning("unknown anthropic stop_reason %r; reporting end_turn", raw)
    return "end_turn"


def usage_from(raw: Any) -> Usage:
    """input EXCLUDES cache reads, which is Anthropic's own convention and the
    one nare normalizes to.
    """
    return Usage(
        input=raw.input_tokens,
        output=raw.output_tokens,
        cache_read=getattr(raw, "cache_read_input_tokens", 0) or 0,
        cache_write=getattr(raw, "cache_creation_input_tokens", 0) or 0,
    )
```

Add the method to the class:

```python
    async def turn(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> Reply:
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=cast("list[MessageParam]", [asdict(m) for m in messages]),
            tools=cast("list[ToolParam]", tools),
            system=self.system if self.system is not None else omit,
            thinking=cast(Any, self.thinking) if self.thinking else omit,
        )
        content = [block.model_dump(exclude_none=True) for block in response.content]
        return Reply(
            content=content,
            tool_calls=[
                ToolCall(id=b["id"], name=b["name"], args=b.get("input") or {})
                for b in content
                if b.get("type") == "tool_use"
            ],
            usage=usage_from(response.usage),
            stop_reason=stop_reason_from(response.stop_reason),
        )
```

`omit` is the SDK's own sentinel for "leave this parameter out", which is why unset sampling params never reach the wire — the parameters are annotated `| Omit` in anthropic 1.6.0. There is no `temperature` argument at all: the vendor removed it, and task 5's constructor already refuses the flag. `exclude_none=True` keeps null fields out of the blocks that get replayed on the next request.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_transport.py -q`
Expected: PASS. The live test shows as deselected — `addopts = "-m 'not live'"` keeps it out of every default run, and the `skipif` keeps it honest for anyone who opts in with `-m live` but has no key.

- [ ] **Step 5: Verify the live test is genuinely excluded**

Run: `uv run pytest tests/test_transport.py -q 2>&1 | grep -i deselect`
Expected: a line reporting 1 deselected.

- [ ] **Step 6: Run the full build**

Run: `bin/build`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/nare/transport/anthropic.py tests/test_transport.py
git commit -m "feat: implement AnthropicTransport.turn with normalized stop_reason and usage"
```

---

### Task 7: make_transport

**Files:**
- Modify: `src/nare/transport/__init__.py`
- Modify: `tests/test_transport.py` (append)

**Interfaces:**
- Consumes: `AnthropicTransport` (Tasks 5–6).
- Produces: `make_transport(kind: str, *, model: str, base_url=None, api_key=None, temperature=None, max_tokens=None, effort=None, system=None) -> Transport`

A flag value that parses and then fails at the first request is worse than a short list that grows when implementations do.

- [ ] **Step 1: Write the failing test** (append to `tests/test_transport.py`)

```python
from nare.transport import make_transport


def test_anthropic_kind_builds_an_anthropic_transport() -> None:
    t = make_transport("anthropic", model="claude-sonnet-5", api_key="test-key")
    assert isinstance(t, AnthropicTransport)


def test_parameters_reach_the_transport() -> None:
    t = make_transport(
        "anthropic", model="m", api_key="k", max_tokens=99, system="persona"
    )
    assert (t.model, t.max_tokens, t.system) == ("m", 99, "persona")


def test_an_unknown_kind_names_the_supported_kinds() -> None:
    with pytest.raises(ValueError, match="anthropic"):
        make_transport("openai", model="gpt-4o", api_key="k")


def test_temperature_refusal_surfaces_from_the_factory() -> None:
    with pytest.raises(ValueError, match="temperature"):
        make_transport("anthropic", model="m", api_key="k", temperature=0.2)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_transport.py -q`
Expected: FAIL — `ImportError: cannot import name 'make_transport'`

- [ ] **Step 3: Add the factory to `src/nare/transport/__init__.py`**

```python
def make_transport(
    kind: str,
    *,
    model: str,
    base_url: str | None = None,
    api_key: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    effort: Literal["low", "medium", "high"] | None = None,
    system: str | None = None,
) -> Transport:
    """Build a transport from configuration.

    ponytail: one arm today. The match is the extension point — the OpenAI
    transport (and with it, locally hosted models via base_url) lands as a
    second arm. A registry is slice 4, with the rest of the extension surface.
    """
    match kind:
        case "anthropic":
            from nare.transport.anthropic import AnthropicTransport

            return AnthropicTransport(
                model=model,
                base_url=base_url,
                api_key=api_key,
                temperature=temperature,
                max_tokens=max_tokens,
                effort=effort,
                system=system,
            )
        case _:
            raise ValueError(
                f"unknown provider {kind!r}; nare supports: anthropic"
            )
```

Add `"make_transport"` to `__all__`. The import is inside the arm so that `nare.transport` stays importable without paying for the vendor SDK.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_transport.py -q`
Expected: PASS — this closes transport acceptance criteria 1, 3, 4, 5, and 6.

- [ ] **Step 5: Commit**

```bash
git add src/nare/transport/__init__.py tests/test_transport.py
git commit -m "feat: add make_transport, the configuration-to-transport factory"
```

---

### Task 8: The five tools and their schemas

**Files:**
- Create: `src/nare/tools.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `read_file(path, offset=0, limit=2000) -> str`
  - `write_file(path, content) -> str`
  - `edit_file(path, old, new) -> str`
  - `run_bash(command, timeout=120) -> str`
  - `ask(questions: list[str]) -> str`
  - `TOOL_SCHEMAS: list[dict[str, Any]]` — five literal Anthropic-shaped dicts
  - `TOOLS: dict[str, Callable[..., str]]` mapping schema name to function

Tool functions raise on failure; Task 9's dispatcher turns exceptions into `is_error` results. No `grep` or `glob` tool: `bash` plus `rg` covers search, and dedicated tools get added when transcripts show the model fumbling it.

- [ ] **Step 1: Write the failing test**

`tests/test_tools.py`:

```python
from pathlib import Path

import pytest

from nare.tools import (
    TOOL_SCHEMAS,
    TOOLS,
    ask,
    edit_file,
    read_file,
    run_bash,
    write_file,
)


def test_exactly_five_tools_with_matching_schemas() -> None:
    assert {s["name"] for s in TOOL_SCHEMAS} == {
        "read",
        "write",
        "edit",
        "bash",
        "ask",
    }
    assert set(TOOLS) == {s["name"] for s in TOOL_SCHEMAS}


@pytest.mark.parametrize("schema", TOOL_SCHEMAS, ids=lambda s: str(s["name"]))
def test_every_schema_is_anthropic_shaped(schema: dict[str, object]) -> None:
    assert set(schema) == {"name", "description", "input_schema"}
    assert schema["description"]
    assert isinstance(schema["input_schema"], dict)


def test_read_returns_file_contents(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("one\ntwo\nthree\n")
    assert read_file(str(target)) == "one\ntwo\nthree"


def test_read_honours_offset_and_limit(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("one\ntwo\nthree\n")
    assert read_file(str(target), offset=1, limit=1) == "two"


def test_read_raises_on_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_file(str(tmp_path / "nope.py"))


def test_write_creates_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "b.py"
    write_file(str(target), "hello")
    assert target.read_text() == "hello"


def test_edit_replaces_a_unique_string(tmp_path: Path) -> None:
    target = tmp_path / "c.py"
    target.write_text("def foo():\n    pass\n")
    edit_file(str(target), "pass", "return 1")
    assert target.read_text() == "def foo():\n    return 1\n"


def test_edit_refuses_an_ambiguous_string(tmp_path: Path) -> None:
    target = tmp_path / "c.py"
    target.write_text("x\nx\n")
    with pytest.raises(ValueError, match="2 times"):
        edit_file(str(target), "x", "y")


def test_edit_refuses_a_missing_string(tmp_path: Path) -> None:
    target = tmp_path / "c.py"
    target.write_text("x\n")
    with pytest.raises(ValueError, match="not found"):
        edit_file(str(target), "zzz", "y")


def test_bash_reports_stdout_and_exit_code() -> None:
    assert run_bash("echo hi") == "exit 0\nhi"


def test_bash_reports_a_failure_without_raising() -> None:
    assert run_bash("exit 3").startswith("exit 3")


def test_bash_captures_stderr() -> None:
    assert "boom" in run_bash("echo boom >&2")


def test_bash_times_out() -> None:
    import subprocess

    with pytest.raises(subprocess.TimeoutExpired):
        run_bash("sleep 5", timeout=1)


def test_ask_returns_an_acknowledgement() -> None:
    assert "blocked" in ask(["which file?"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nare.tools'`

- [ ] **Step 3: Write `src/nare/tools.py`**

```python
"""Five tools, five hand-written schemas beside them.

A schema generator that introspects type hints is worth revisiting at roughly
fifteen tools. At five it is a dependency on cleverness for no gain.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

MAX_TOOL_OUTPUT = 30_000


def read_file(path: str, offset: int = 0, limit: int = 2000) -> str:
    lines = Path(path).read_text().splitlines()
    return "\n".join(lines[offset : offset + limit])


def write_file(path: str, content: str) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return f"wrote {len(content)} characters to {path}"


def edit_file(path: str, old: str, new: str) -> str:
    target = Path(path)
    text = target.read_text()
    count = text.count(old)
    if count == 0:
        raise ValueError(f"old_string not found in {path}")
    if count > 1:
        raise ValueError(
            f"old_string appears {count} times in {path}; include enough "
            "surrounding context to make it unique"
        )
    target.write_text(text.replace(old, new))
    return f"edited {path}"


def run_bash(command: str, timeout: int = 120) -> str:
    done = subprocess.run(
        command, shell=True, capture_output=True, text=True, timeout=timeout
    )
    output = (done.stdout + done.stderr).strip()
    if len(output) > MAX_TOOL_OUTPUT:
        output = output[:MAX_TOOL_OUTPUT] + f"\n[truncated at {MAX_TOOL_OUTPUT} chars]"
    return f"exit {done.returncode}\n{output}".rstrip()


def ask(questions: list[str]) -> str:
    """Terminate the run with questions for the caller.

    Tool calls are the only structured channel a model has, so asking is a tool.
    The loop reads the questions off the call and sets status=blocked; this
    result exists so the transcript stays well-formed for a resume.
    """
    return "Questions recorded. The session is blocked pending answers."


TOOLS: dict[str, Callable[..., str]] = {
    "read": read_file,
    "write": write_file,
    "edit": edit_file,
    "bash": run_bash,
    "ask": ask,
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "read",
        "description": "Read a UTF-8 text file and return its contents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file."},
                "offset": {"type": "integer", "description": "First line to return."},
                "limit": {"type": "integer", "description": "How many lines."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write",
        "description": (
            "Write a file, creating parent directories and overwriting any "
            "existing contents."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file."},
                "content": {"type": "string", "description": "Full file contents."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit",
        "description": (
            "Replace an exact string in a file. The old string must appear "
            "exactly once; include surrounding context to make it unique."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file."},
                "old": {"type": "string", "description": "Exact text to replace."},
                "new": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old", "new"],
        },
    },
    {
        "name": "bash",
        "description": (
            "Run a shell command and return its exit code and combined output. "
            "Use this with rg and find for search."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command to run."},
                "timeout": {"type": "integer", "description": "Seconds before kill."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "ask",
        "description": (
            "Stop and ask the caller for missing information. Use this when the "
            "task cannot be completed without a decision only the caller can "
            "make. This ends the session."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "One question per item.",
                }
            },
            "required": ["questions"],
        },
    },
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tools.py -q`
Expected: PASS, 16 tests.

- [ ] **Step 5: Commit**

```bash
git add src/nare/tools.py tests/test_tools.py
git commit -m "feat: add the five tools and their hand-written schemas"
```

---

### Task 9: Dispatch and the approval seam

**Files:**
- Modify: `src/nare/tools.py`
- Modify: `tests/test_tools.py` (append)

**Interfaces:**
- Consumes: `ToolCall` (Task 4), `TOOLS` (Task 8).
- Produces:
  - `Approve = Callable[[str, dict[str, Any]], bool]`
  - `approve_all(tool: str, args: dict[str, Any]) -> bool`
  - `tool_result(call_id: str, text: str, *, is_error: bool = False) -> dict[str, Any]`
  - `async dispatch(call: ToolCall, approve: Approve) -> dict[str, Any]`
  - `questions_from(calls: Iterable[ToolCall]) -> list[str]`

One dispatcher, one `approve(tool, args) -> bool` callable, applied uniformly to every tool. Slice 1 ships one implementation. No rule engine and no policy config — but the seam the TUI needs in slice 3 exists from the start.

- [ ] **Step 1: Write the failing test** (append to `tests/test_tools.py`)

```python
from nare.tools import approve_all, dispatch, questions_from
from nare.transport import ToolCall


async def test_dispatch_returns_a_tool_result_block(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("hello")
    result = await dispatch(
        ToolCall(id="c1", name="read", args={"path": str(target)}), approve_all
    )
    assert result == {
        "type": "tool_result",
        "tool_use_id": "c1",
        "content": "hello",
        "is_error": False,
    }


async def test_a_tool_exception_becomes_an_error_result(tmp_path: Path) -> None:
    result = await dispatch(
        ToolCall(id="c1", name="read", args={"path": str(tmp_path / "nope")}),
        approve_all,
    )
    assert result["is_error"] is True
    assert "FileNotFoundError" in result["content"]


async def test_an_unknown_tool_becomes_an_error_result() -> None:
    result = await dispatch(ToolCall(id="c1", name="grep", args={}), approve_all)
    assert result["is_error"] is True
    assert "grep" in result["content"]


async def test_a_bad_argument_becomes_an_error_result_not_a_crash() -> None:
    result = await dispatch(
        ToolCall(id="c1", name="read", args={"wrong": 1}), approve_all
    )
    assert result["is_error"] is True


async def test_denied_approval_blocks_the_tool(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"

    def deny(tool: str, args: dict[str, object]) -> bool:
        return False

    result = await dispatch(
        ToolCall(id="c1", name="write", args={"path": str(target), "content": "x"}),
        deny,
    )
    assert result["is_error"] is True
    assert "not approved" in result["content"]
    assert not target.exists()


async def test_approve_sees_the_tool_name_and_args() -> None:
    seen: list[tuple[str, dict[str, object]]] = []

    def record(tool: str, args: dict[str, object]) -> bool:
        seen.append((tool, args))
        return False

    await dispatch(ToolCall(id="c1", name="bash", args={"command": "ls"}), record)
    assert seen == [("bash", {"command": "ls"})]


def test_questions_from_collects_across_calls() -> None:
    calls = [
        ToolCall(id="c1", name="read", args={"path": "a"}),
        ToolCall(id="c2", name="ask", args={"questions": ["which file?", "why?"]}),
    ]
    assert questions_from(calls) == ["which file?", "why?"]


def test_questions_from_is_empty_without_an_ask() -> None:
    assert questions_from([ToolCall(id="c1", name="read", args={})]) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -q`
Expected: FAIL — `ImportError: cannot import name 'dispatch'`

- [ ] **Step 3: Add the dispatcher to `src/nare/tools.py`**

Add to the imports:

```python
import asyncio
from collections.abc import Iterable

from nare.transport import ToolCall

Approve = Callable[[str, dict[str, Any]], bool]
```

Add below `TOOL_SCHEMAS`:

```python
def approve_all(tool: str, args: dict[str, Any]) -> bool:
    """Slice 1's only approval policy. `nare run` refuses to start without
    --yes, which is what makes one implementation honest rather than lax.
    """
    return True


def tool_result(call_id: str, text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": call_id,
        "content": text,
        "is_error": is_error,
    }


async def dispatch(call: ToolCall, approve: Approve) -> dict[str, Any]:
    """Run one tool call. Every failure comes back as an is_error result rather
    than an exception: the model adapts, which is what it is good at.
    """
    function = TOOLS.get(call.name)
    if function is None:
        return tool_result(
            call.id,
            f"unknown tool {call.name!r}; available: {', '.join(sorted(TOOLS))}",
            is_error=True,
        )
    if not approve(call.name, call.args):
        return tool_result(call.id, f"{call.name} was not approved", is_error=True)
    try:
        return tool_result(call.id, await asyncio.to_thread(function, **call.args))
    except Exception as exc:
        return tool_result(call.id, f"{type(exc).__name__}: {exc}", is_error=True)


def questions_from(calls: Iterable[ToolCall]) -> list[str]:
    return [
        question
        for call in calls
        if call.name == "ask"
        for question in call.args.get("questions", [])
    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tools.py -q && bin/build`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nare/tools.py tests/test_tools.py
git commit -m "feat: add tool dispatch and the approval seam"
```

---

### Task 10: step()

**Files:**
- Create: `src/nare/loop.py`
- Test: `tests/test_loop.py`

**Interfaces:**
- Consumes: `Event` (Task 2); `Session`, `Message` (Task 3); `Transport`, `ToolCall` (Task 4); `dispatch`, `questions_from`, `Approve`, `TOOL_SCHEMAS` (Tasks 8–9).
- Produces: `async step(s: Session, transport: Transport, approve: Approve) -> Session`

`step()` is the whole engine. It is synchronous in shape, deterministic given a transport, and appends events to `s.events` rather than yielding — draining is `run()`'s job in Task 11.

- [ ] **Step 1: Write the failing test**

`tests/test_loop.py`:

```python
from pathlib import Path

from fake_provider import Exploding, FakeProvider, text_reply, tool_reply

from nare.loop import step
from nare.session import Usage, new_session
from nare.tools import approve_all


async def test_a_reply_without_tool_calls_finishes_the_session() -> None:
    session = new_session("say hi")
    await step(session, FakeProvider([text_reply("all done")]), approve_all)
    assert session.status == "done"
    assert session.stop_reason == "end_turn"
    assert session.turns == 1


async def test_usage_accumulates_across_steps() -> None:
    session = new_session("go")
    fake = FakeProvider([text_reply("one"), text_reply("two")])
    await step(session, fake, approve_all)
    session.status = "working"
    await step(session, fake, approve_all)
    assert session.usage == Usage(input=20, output=10)
    assert session.turns == 2


async def test_the_assistant_reply_lands_in_the_transcript() -> None:
    session = new_session("go")
    await step(session, FakeProvider([text_reply("all done")]), approve_all)
    assert session.messages[-1].role == "assistant"
    assert session.messages[-1].content == [{"type": "text", "text": "all done"}]


async def test_a_tool_call_runs_and_appends_a_user_result(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    session = new_session("write a file")
    fake = FakeProvider([tool_reply("write", {"path": str(target), "content": "hi"})])
    await step(session, fake, approve_all)
    assert target.read_text() == "hi"
    assert session.status == "working"
    assert session.messages[-1].role == "user"
    assert session.messages[-1].content[0]["type"] == "tool_result"


async def test_ask_blocks_the_session_with_questions() -> None:
    session = new_session("do the thing")
    fake = FakeProvider([tool_reply("ask", {"questions": ["which file?"]})])
    await step(session, fake, approve_all)
    assert session.status == "blocked"
    assert session.questions == ["which file?"]
    # The tool_result is still appended, so a resume picks up from a
    # well-formed transcript rather than a dangling tool_use.
    assert session.messages[-1].content[0]["type"] == "tool_result"


async def test_a_failing_tool_keeps_the_session_working() -> None:
    session = new_session("read a missing file")
    fake = FakeProvider([tool_reply("read", {"path": "/nope/nope.py"})])
    await step(session, fake, approve_all)
    assert session.status == "working"
    assert session.messages[-1].content[0]["is_error"] is True


async def test_step_emits_progress_and_cost_events() -> None:
    session = new_session("go")
    await step(session, FakeProvider([text_reply("all done")]), approve_all)
    kinds = [e.type for e in session.events]
    assert kinds == ["progress", "cost", "output"]
    assert session.events[0].text == "all done"
    assert session.events[1].detail == {
        "input": 10,
        "output": 5,
        "cache_read": 0,
        "cache_write": 0,
    }
    assert session.events[2].text == "all done"


async def test_step_emits_a_tool_use_event(tmp_path: Path) -> None:
    session = new_session("go")
    fake = FakeProvider([tool_reply("bash", {"command": "echo hi"})])
    await step(session, fake, approve_all)
    tool_events = [e for e in session.events if e.type == "tool_use"]
    assert len(tool_events) == 1
    assert tool_events[0].text == "bash"
    assert tool_events[0].detail == {"command": "echo hi"}


async def test_thinking_blocks_become_thinking_events() -> None:
    from nare.transport import Reply

    session = new_session("go")
    reply = Reply(
        content=[
            {"type": "thinking", "thinking": "let me consider"},
            {"type": "text", "text": "done"},
        ],
        tool_calls=[],
        usage=Usage(input=1, output=1),
        stop_reason="end_turn",
    )
    await step(session, FakeProvider([reply]), approve_all)
    assert [e.type for e in session.events][:2] == ["thinking", "progress"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_loop.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nare.loop'`

- [ ] **Step 3: Write `src/nare/loop.py`**

```python
"""The agent loop. A plain Session advanced by an explicit step(), surfaced as
an async generator: resumable by construction, deterministic under test.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from nare.events import Event
from nare.session import Message, Session
from nare.tools import TOOL_SCHEMAS, Approve, dispatch, questions_from
from nare.transport import Transport

MAX_TURNS_DEFAULT = 50


def _final_text(content: list[dict[str, Any]]) -> str:
    return "\n".join(b.get("text", "") for b in content if b.get("type") == "text")


async def step(s: Session, transport: Transport, approve: Approve) -> Session:
    reply = await transport.turn(s.messages, TOOL_SCHEMAS)
    s.usage += reply.usage
    s.turns += 1
    s.messages.append(Message(role="assistant", content=reply.content))

    for block in reply.content:
        if block.get("type") == "text":
            s.events.append(Event("progress", block.get("text", "")))
        elif block.get("type") == "thinking":
            s.events.append(Event("thinking", block.get("thinking", "")))
    s.events.append(
        Event(
            "cost",
            f"{reply.usage.input} in / {reply.usage.output} out",
            asdict(reply.usage),
        )
    )

    if not reply.tool_calls:
        s.status = "done"
        s.stop_reason = reply.stop_reason
        s.events.append(Event("output", _final_text(reply.content)))
        return s

    for call in reply.tool_calls:
        s.events.append(Event("tool_use", call.name, dict(call.args)))

    # ponytail: tool calls dispatch sequentially. Models do emit parallel tool
    # calls; asyncio.gather is the upgrade once a run is measurably slow
    # because of it, and not before.
    results = [await dispatch(call, approve) for call in reply.tool_calls]
    s.messages.append(Message(role="user", content=results))

    if any(call.name == "ask" for call in reply.tool_calls):
        s.status = "blocked"
        s.questions = questions_from(reply.tool_calls)
        s.stop_reason = reply.stop_reason
    return s
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_loop.py -q`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add src/nare/loop.py tests/test_loop.py
git commit -m "feat: add step(), the agent loop's single advance"
```

---

### Task 11: run() and the public API

**Files:**
- Modify: `src/nare/loop.py`, `src/nare/__init__.py`
- Modify: `tests/test_loop.py` (append)

**Interfaces:**
- Consumes: `step` (Task 10), `dumps`/`loads` (Task 3).
- Produces:
  - `run(session: Session, *, transport: Transport, approve: Approve = approve_all, max_turns: int = MAX_TURNS_DEFAULT) -> AsyncIterator[Event]`
  - `nare.__all__` — the public surface every later slice imports

- [ ] **Step 1: Write the failing test** (append to `tests/test_loop.py`)

```python
import nare
from nare.loop import MAX_TURNS_DEFAULT, run
from nare.session import Session, dumps, loads
from nare.transport import Transport


async def drain(
    session: Session, transport: Transport, *, max_turns: int = MAX_TURNS_DEFAULT
) -> list[str]:
    return [
        e.type
        async for e in run(session, transport=transport, max_turns=max_turns)
    ]


async def test_run_drives_to_done_and_yields_every_event(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    session = new_session("write then finish")
    fake = FakeProvider(
        [
            tool_reply("write", {"path": str(target), "content": "hi"}),
            text_reply("finished"),
        ]
    )
    kinds = await drain(session, fake)
    assert kinds == ["cost", "tool_use", "progress", "cost", "output"]
    assert session.status == "done"
    assert session.turns == 2
    assert target.read_text() == "hi"


async def test_run_stops_at_blocked() -> None:
    session = new_session("go")
    fake = FakeProvider([tool_reply("ask", {"questions": ["which one?"]})])
    await drain(session, fake)
    assert session.status == "blocked"
    assert session.questions == ["which one?"]


async def test_max_turns_ends_the_run_honestly(tmp_path: Path) -> None:
    session = new_session("loop forever")
    fake = FakeProvider([tool_reply("bash", {"command": "true"}) for _ in range(5)])
    kinds = await drain(session, fake, max_turns=2)
    assert session.status == "error"
    assert session.stop_reason == "max_turns"
    assert session.turns == 2
    assert "error" in kinds


async def test_a_transport_failure_becomes_status_error() -> None:
    session = new_session("go")
    kinds = await drain(session, Exploding())
    assert session.status == "error"
    assert session.error is not None
    assert "connection reset" in session.error
    assert kinds == ["error"]


async def test_events_are_drained_not_hoarded() -> None:
    session = new_session("go")
    await drain(session, FakeProvider([text_reply("done")]))
    assert session.events == []


async def test_a_resumed_session_runs_identically(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    first = new_session("write then finish")
    await drain(
        first, FakeProvider([tool_reply("ask", {"questions": ["which path?"]})])
    )
    assert first.status == "blocked"

    revived = loads(dumps(first))
    assert revived == first

    from nare.session import append_user_text

    append_user_text(revived, f"use {target}")
    revived.status = "working"
    fake = FakeProvider(
        [
            tool_reply("write", {"path": str(target), "content": "hi"}),
            text_reply("finished"),
        ]
    )
    await drain(revived, fake)
    assert revived.status == "done"
    assert revived.turns == 3
    assert target.read_text() == "hi"
    # The revived transcript is what the transport actually saw.
    assert fake.calls[0][0][0].role == "user"


def test_the_public_api_is_the_documented_surface() -> None:
    assert set(nare.__all__) == {
        "Event",
        "Message",
        "Reply",
        "Session",
        "ToolCall",
        "Transport",
        "Usage",
        "approve_all",
        "dumps",
        "loads",
        "make_transport",
        "new_session",
        "run",
        "step",
    }
    for name in nare.__all__:
        assert hasattr(nare, name), name
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_loop.py -q`
Expected: FAIL — `ImportError: cannot import name 'run'`

- [ ] **Step 3: Add `run()` to `src/nare/loop.py`**

Add to the imports:

```python
from collections.abc import AsyncIterator

from nare.tools import approve_all
```

Add at the end of the module:

```python
async def run(
    session: Session,
    *,
    transport: Transport,
    approve: Approve = approve_all,
    max_turns: int = MAX_TURNS_DEFAULT,
) -> AsyncIterator[Event]:
    """Advance the session to a terminal status, yielding events as they occur.

    The only place that catches broadly: a harness reports failures as events
    and a status, it does not hand a traceback to its caller.
    """
    while session.status == "working":
        if session.turns >= max_turns:
            session.status = "error"
            session.stop_reason = "max_turns"
            session.error = f"stopped after {max_turns} turns"
            session.events.append(Event("error", session.error))
        else:
            try:
                await step(session, transport, approve)
            except Exception as exc:
                session.status = "error"
                session.error = f"{type(exc).__name__}: {exc}"
                session.events.append(Event("error", session.error))
        while session.events:
            yield session.events.pop(0)
```

- [ ] **Step 4: Write `src/nare/__init__.py`**

```python
"""nare — a standalone agent harness.

    import asyncio
    from nare import make_transport, new_session, run

    async def main() -> None:
        transport = make_transport("anthropic", model="claude-sonnet-5")
        session = new_session("add a docstring to foo() in bar.py")
        async for event in run(session, transport=transport):
            print(event.type, event.text)

    asyncio.run(main())
"""

from nare.events import Event
from nare.loop import run, step
from nare.session import Message, Session, Usage, dumps, loads, new_session
from nare.tools import approve_all
from nare.transport import Reply, ToolCall, Transport, make_transport

__version__ = "0.1.0"

__all__ = [
    "Event",
    "Message",
    "Reply",
    "Session",
    "ToolCall",
    "Transport",
    "Usage",
    "approve_all",
    "dumps",
    "loads",
    "make_transport",
    "new_session",
    "run",
    "step",
]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -q && bin/build`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/nare/loop.py src/nare/__init__.py tests/test_loop.py
git commit -m "feat: add run() and settle the public API surface"
```

---

### Task 12: CLI argument parsing and transport construction

**Files:**
- Create: `src/nare/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `make_transport` (Task 7), `MAX_TURNS_DEFAULT` (Task 10).
- Produces:
  - `build_parser() -> argparse.ArgumentParser`
  - `transport_from_args(args: argparse.Namespace) -> Transport`

Precedence is flag, then environment, then default. `--max-tokens` has **no argparse default**: the transport must be able to tell an explicit `8192` from an unset value, which is what lets `--effort` resolve it to `min(budget + 8192, 21333)`.

`--temperature` is still parsed and still passed to `make_transport`, because the planned OpenAI and locally hosted transports accept it. On `anthropic` it raises at construction — see task 5.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:

```python
import argparse

import pytest

from nare.cli import build_parser, transport_from_args
from nare.transport.anthropic import AnthropicTransport


def parse(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(["run", *argv])


def test_defaults_match_the_spec() -> None:
    args = parse("do a thing")
    assert args.prompt == "do a thing"
    assert args.provider == "anthropic"
    assert args.model == "claude-sonnet-5"
    assert args.base_url is None
    assert args.temperature is None
    assert args.max_tokens is None  # resolved to 8192 inside the transport
    assert args.effort is None
    assert args.system is None
    assert args.max_turns == 50
    assert args.jsonl is False
    assert args.yes is False


def test_environment_supplies_the_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARE_MODEL", "claude-opus-5")
    monkeypatch.setenv("NARE_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("NARE_PROVIDER", "anthropic")
    args = parse("go")
    assert args.model == "claude-opus-5"
    assert args.base_url == "http://localhost:11434"


def test_a_flag_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARE_MODEL", "claude-opus-5")
    assert parse("go", "--model", "claude-sonnet-5").model == "claude-sonnet-5"


def test_there_is_no_api_key_flag() -> None:
    with pytest.raises(SystemExit):
        parse("go", "--api-key", "sk-ant-nope")


def test_an_unknown_provider_flag_is_rejected_by_argparse() -> None:
    with pytest.raises(SystemExit):
        parse("go", "--provider", "openai")


def test_transport_from_args_binds_every_knob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    args = parse("go", "--model", "m", "--max-tokens", "512", "--system", "persona")
    transport = transport_from_args(args)
    assert isinstance(transport, AnthropicTransport)
    assert (transport.model, transport.max_tokens) == ("m", 512)
    assert transport.system == "persona"


def test_temperature_is_refused_by_the_anthropic_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with pytest.raises(ValueError, match="temperature"):
        transport_from_args(parse("go", "--temperature", "0.2"))


def test_an_unknown_provider_from_the_environment_fails_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NARE_PROVIDER", "openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with pytest.raises(ValueError, match="anthropic"):
        transport_from_args(parse("go"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nare.cli'`

- [ ] **Step 3: Write `src/nare/cli.py`** (parsing only — execution lands in Task 13)

```python
"""`nare run` — the headless CLI.

A thin adapter over the library. Conductor's performer spawns this exactly as
it spawns claude or opencode, which keeps nare's own surface on the critical
path so it cannot rot.
"""

from __future__ import annotations

import argparse
import os

from nare.loop import MAX_TURNS_DEFAULT
from nare.transport import Transport, make_transport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nare", description="A standalone agent harness."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="run a task headlessly")

    run_parser.add_argument("prompt", nargs="?", help="the task, as plain text")
    run_parser.add_argument(
        "--provider",
        choices=["anthropic"],
        default=os.environ.get("NARE_PROVIDER", "anthropic"),
        help="model provider (env: NARE_PROVIDER)",
    )
    run_parser.add_argument(
        "--model",
        default=os.environ.get("NARE_MODEL", "claude-sonnet-5"),
        help="model name (env: NARE_MODEL)",
    )
    run_parser.add_argument(
        "--base-url",
        default=os.environ.get("NARE_BASE_URL"),
        help="override the vendor endpoint (env: NARE_BASE_URL)",
    )
    run_parser.add_argument(
        "--temperature",
        type=float,
        help="sampling temperature; not accepted by the anthropic provider",
    )
    run_parser.add_argument(
        "--max-tokens", type=int, help="output token cap (default 8192)"
    )
    run_parser.add_argument(
        "--effort",
        choices=["low", "medium", "high"],
        help="reasoning effort",
    )
    run_parser.add_argument("--system", help="system prompt / persona text")
    run_parser.add_argument(
        "--jsonl", action="store_true", help="emit typed JSONL on stdout"
    )
    run_parser.add_argument(
        "--yes", action="store_true", help="approve every tool call; required"
    )
    run_parser.add_argument("--resume", help="continue the session at this path")
    run_parser.add_argument("--session", help="write the session to this path")
    run_parser.add_argument(
        "--max-turns",
        type=int,
        default=MAX_TURNS_DEFAULT,
        help=f"stop after this many turns (default {MAX_TURNS_DEFAULT})",
    )
    return parser


def transport_from_args(args: argparse.Namespace) -> Transport:
    """Everything vendor-shaped is bound here and never reaches the loop."""
    return make_transport(
        args.provider,
        model=args.model,
        base_url=args.base_url,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        effort=args.effort,
        system=args.system,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add src/nare/cli.py tests/test_cli.py
git commit -m "feat: add nare run argument parsing and transport construction"
```

---

### Task 13: CLI execution, JSONL, the result line, and resume

**Files:**
- Modify: `src/nare/cli.py`
- Modify: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: everything above.
- Produces: `main(argv: list[str] | None = None, *, transport: Transport | None = None) -> int`

The `transport` keyword is the injection point the library API already implies — tests and in-process callers pass a `Transport` they already hold, and it is never reachable from argv.

**Wire format.** Stdout is pure JSONL events under `--jsonl`, terminated by exactly one `{"type": "result", ...}` line carrying status, questions, usage, stop_reason, and turns. Stderr is stdlib logging. Conductor's adapter skips the `result` line when building `BackendEvent`s and reads it for `BackendStatus`.

**Exit codes.** `0` the run completed (`done` or `blocked`); `1` the run started and failed (`status=error`); `2` the run never started. A run that never started emits no result line at all, because the result line implies a session existed — which is exactly the distinction Conductor wants between a backend that failed to spawn and a backend that ran and failed.

- [ ] **Step 1: Write the failing test** (append to `tests/test_cli.py`)

```python
import json
import re
from pathlib import Path

from fake_provider import Exploding, FakeProvider, text_reply, tool_reply

from nare.cli import main

TIMESTAMP = re.compile(r'"timestamp": "[^"]+"')


def lines(captured: str) -> list[dict[str, object]]:
    return [
        json.loads(TIMESTAMP.sub('"timestamp": "T"', line))
        for line in captured.strip().splitlines()
    ]


def test_run_refuses_to_start_without_yes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "go"], transport=FakeProvider([])) == 2
    captured = capsys.readouterr()
    assert "--yes" in captured.err
    # A run that never started emits no result line, because the result line
    # implies a session existed.
    assert captured.out == ""


def test_run_needs_a_prompt_or_a_resume(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--yes"], transport=FakeProvider([])) == 2
    assert "prompt" in capsys.readouterr().err


def test_golden_jsonl(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "out.txt"
    fake = FakeProvider(
        [
            tool_reply("write", {"path": str(target), "content": "hi"}),
            text_reply("finished"),
        ]
    )
    code = main(["run", "--yes", "--jsonl", "write a file"], transport=fake)
    assert code == 0
    assert target.read_text() == "hi"

    emitted = lines(capsys.readouterr().out)
    usage = {"input": 10, "output": 5, "cache_read": 0, "cache_write": 0}
    assert emitted[:5] == [
        {"timestamp": "T", "type": "cost", "text": "10 in / 5 out", "detail": usage},
        {
            "timestamp": "T",
            "type": "tool_use",
            "text": "write",
            "detail": {"path": str(target), "content": "hi"},
        },
        {"timestamp": "T", "type": "progress", "text": "finished", "detail": {}},
        {"timestamp": "T", "type": "cost", "text": "10 in / 5 out", "detail": usage},
        {"timestamp": "T", "type": "output", "text": "finished", "detail": {}},
    ]
    result = emitted[5]
    assert result["type"] == "result"
    assert result["status"] == "done"
    assert result["stop_reason"] == "end_turn"
    assert result["questions"] == []
    assert result["turns"] == 2
    assert result["usage"] == {
        "input": 20,
        "output": 10,
        "cache_read": 0,
        "cache_write": 0,
    }
    assert len(emitted) == 6


def test_every_line_is_json_and_the_result_is_last(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["run", "--yes", "--jsonl", "go"], transport=FakeProvider([text_reply("ok")]))
    emitted = lines(capsys.readouterr().out)
    assert [e["type"] for e in emitted].count("result") == 1
    assert emitted[-1]["type"] == "result"


def test_blocked_exits_zero_with_questions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeProvider([tool_reply("ask", {"questions": ["which file?"]})])
    assert main(["run", "--yes", "--jsonl", "go"], transport=fake) == 0
    result = lines(capsys.readouterr().out)[-1]
    assert result["status"] == "blocked"
    assert result["questions"] == ["which file?"]


def test_an_errored_run_exits_one(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["run", "--yes", "--jsonl", "go"], transport=Exploding())
    assert code == 1
    assert lines(capsys.readouterr().out)[-1]["status"] == "error"


def test_max_turns_is_honoured(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeProvider([tool_reply("bash", {"command": "true"}) for _ in range(5)])
    code = main(
        ["run", "--yes", "--jsonl", "--max-turns", "2", "loop"], transport=fake
    )
    assert code == 1
    result = lines(capsys.readouterr().out)[-1]
    assert result["stop_reason"] == "max_turns"
    assert result["turns"] == 2


def test_without_jsonl_the_output_is_plain_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["run", "--yes", "go"], transport=FakeProvider([text_reply("ok")]))
    out = capsys.readouterr().out
    assert "[progress] ok" in out
    assert "{" not in out


def test_session_file_is_written_and_resumable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "s.json"
    fake = FakeProvider([tool_reply("ask", {"questions": ["which file?"]})])
    main(
        ["run", "--yes", "--jsonl", "--session", str(path), "go"], transport=fake
    )
    assert json.loads(path.read_text())["status"] == "blocked"

    resumed = FakeProvider([text_reply("finished")])
    code = main(
        ["run", "--yes", "--jsonl", "--resume", str(path), "use bar.py"],
        transport=resumed,
    )
    assert code == 0
    result = lines(capsys.readouterr().out)[-1]
    assert result["status"] == "done"
    assert result["turns"] == 2
    # The feedback merged into the trailing user turn rather than following it.
    sent = resumed.calls[0][0]
    assert sent[-1].role == "user"
    assert sent[-1].content[-1] == {"type": "text", "text": "use bar.py"}


def test_a_session_file_is_written_even_when_the_run_fails(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    main(["run", "--yes", "--session", str(path), "go"], transport=Exploding())
    assert json.loads(path.read_text())["status"] == "error"


def test_an_unreadable_resume_path_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["run", "--yes", "--resume", str(tmp_path / "missing.json")],
        transport=FakeProvider([]),
    )
    assert code == 2
    assert capsys.readouterr().out == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -q`
Expected: FAIL — `ImportError: cannot import name 'main'`

- [ ] **Step 3: Add execution to `src/nare/cli.py`**

Add to the imports:

```python
import asyncio
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

from nare.events import Event
from nare.loop import run
from nare.session import Session, append_user_text, dumps, loads, new_session
from nare.tools import approve_all
```

Add at the end of the module:

```python
def _load_or_new(args: argparse.Namespace) -> Session:
    if not args.resume:
        return new_session(args.prompt)
    session = loads(Path(args.resume).read_text())
    if args.prompt:
        append_user_text(session, args.prompt)
    # Resuming is how conductor's relay_feedback works: reopen and keep going.
    session.status = "working"
    session.questions = []
    session.error = None
    return session


def _emit(event: Event, jsonl: bool) -> None:
    if jsonl:
        print(json.dumps(asdict(event)), flush=True)
    else:
        print(f"[{event.type}] {event.text}", flush=True)


def _emit_result(session: Session, jsonl: bool) -> None:
    if jsonl:
        print(
            json.dumps(
                {
                    "type": "result",
                    "session_id": session.id,
                    "status": session.status,
                    "questions": session.questions,
                    "usage": asdict(session.usage),
                    "stop_reason": session.stop_reason,
                    "turns": session.turns,
                    "error": session.error,
                }
            ),
            flush=True,
        )
    else:
        print(
            f"{session.status} after {session.turns} turns "
            f"({session.usage.input} in / {session.usage.output} out)",
            flush=True,
        )


async def _execute(
    session: Session, transport: Transport, args: argparse.Namespace
) -> int:
    try:
        async for event in run(
            session,
            transport=transport,
            approve=approve_all,
            max_turns=args.max_turns,
        ):
            _emit(event, args.jsonl)
    finally:
        if args.session:
            Path(args.session).write_text(dumps(session))
    _emit_result(session, args.jsonl)
    return 1 if session.status == "error" else 0


def main(
    argv: list[str] | None = None, *, transport: Transport | None = None
) -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)

    # Everything below exits 2 and emits nothing on stdout: the result line
    # implies a session existed, so a run that never started does not emit one.
    if not args.yes:
        print(
            "nare run refuses to start unattended without --yes", file=sys.stderr
        )
        return 2
    if not args.prompt and not args.resume:
        print("nare run needs a prompt, or --resume PATH", file=sys.stderr)
        return 2

    try:
        session = _load_or_new(args)
        if transport is None:
            transport = transport_from_args(args)
    except (ValueError, OSError) as exc:
        print(f"nare: {exc}", file=sys.stderr)
        return 2

    return asyncio.run(_execute(session, transport, args))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Run the whole build**

Run: `bin/build`
Expected: PASS, every test green, mypy clean.

- [ ] **Step 6: Verify the acceptance criteria by hand, in a throwaway repository**

```bash
uv tool install --force --editable .
cd "$(mktemp -d)" && git init -q .
printf 'def foo():\n    return 1\n' > bar.py

# 1. edits a file, emits typed JSONL, exits 0 with status=done
nare run --yes --jsonl "add a docstring to foo() in bar.py"; echo "exit=$?"
cat bar.py

# 2. a missing detail blocks with questions
nare run --yes --jsonl "rename the function in that file"; echo "exit=$?"

# 3. resume continues the session
nare run --yes --jsonl --session s.json "add a docstring to foo() in bar.py"
nare run --yes --jsonl --resume s.json "also add a type hint"

# 4. refuses to start unattended
nare run "do something"; echo "exit=$?"

# 5. knobs reach the request; an unknown provider fails at construction
nare run --yes --jsonl --model claude-sonnet-5 --max-tokens 256 "say hi"
NARE_PROVIDER=openai nare run --yes "say hi"; echo "exit=$?"
```

Expected: criterion 1 exits 0 and `bar.py` gains a docstring; 2 exits 0 with `"status": "blocked"` and a populated `questions`; 3 continues the same `session_id` with a higher `turns`; 4 exits 2 with no stdout; 5 succeeds, and the `NARE_PROVIDER=openai` run exits 2 naming `anthropic` with no JSONL.

- [ ] **Step 7: Commit**

```bash
git add src/nare/cli.py tests/test_cli.py
git commit -m "feat: add nare run execution, JSONL output, and resume"
```

---

### Task 14: ADRs, architecture, and the Conductor adapter sketch

**Files:**
- Create: `docs/adr/0001-own-harness.md` … `docs/adr/0006-not-langgraph.md`
- Create: `docs/architecture.md`
- Create: `docs/superpowers/conductor-adapter-sketch.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: the whole working skeleton. Written last, deliberately: `architecture.md` describes what was built, not what was planned.

The adapter sketch lives under `docs/superpowers/` with the specs and the handoff, not in `docs/`. It is evidence that the event contract holds, addressed to whoever builds slice 2 — it is not product documentation, and slice 1 ships no API reference.

- [ ] **Step 1: Write the six ADRs**

`docs/adr/0001-own-harness.md`:

```markdown
# 1. Build an own harness, not a wrapper

Status: Accepted (2026-09-09)

## Context

Conductor drives eight external CLIs through adapters totalling ~5,300 lines.
They are large because they reverse-engineer structure out of tools that were
never built to emit it: blocked states parsed from prose, events reconstructed
from stdout line shapes, stop reasons inferred and often wrong.

## Decision

nare implements the loop, the tools, and the provider calls itself. It is not a
wrapper around other harnesses and not a meta-harness.

## Consequences

A harness that emits blocked + questions, typed events, honest stop_reason, and
resumable sessions natively turns an 800-line adapter into roughly 80. The cost
is that nare owns provider compatibility forever, which the transport layer
(ADR 0007 territory, see docs/superpowers/specs) is designed to absorb.
```

`docs/adr/0002-python.md`:

```markdown
# 2. Python 3.12+, uv, hatchling

Status: Accepted (2026-09-09)

## Context

nare ships alongside Conductor, which is Python on uv and hatchling. A second
language means a second toolchain, a second CI idiom, and a second skill set
for the same two people.

## Decision

Python 3.12+, uv for environments, hatchling for packaging.

## Consequences

One skill set across both repos, and `pip install nare` in the performer's
Dockerfile. The known cost is distribution: there is no true single binary.
Accepted — it is a packaging problem, not an architectural one, and it blocks
nothing today.
```

`docs/adr/0003-subprocess-integration.md`:

```markdown
# 3. Conductor integrates by subprocess, not by import

Status: Accepted (2026-09-09)

## Context

Conductor's performer could import nare directly, since both are Python. It
spawns every other backend as a subprocess.

## Decision

The performer spawns `nare` exactly as it spawns `claude` or `opencode`.

## Consequences

nare's own CLI stays on the critical path, so the standalone product surface
cannot rot while the integration keeps working. Crash isolation is preserved: a
backend that dies takes nothing with it. In-process `import nare` remains
available later at no extra cost, because the core is a library regardless.
```

`docs/adr/0004-approval-seam.md`:

```markdown
# 4. One approval seam, one implementation, explicit --yes

Status: Accepted (2026-09-09)

## Context

The TUI in slice 3 needs per-tool permission prompts. Slice 1 runs headless and
needs none. Building a rule engine now would be speculation; building nothing
would mean retrofitting the dispatcher later.

## Decision

One dispatcher and one `approve(tool, args) -> bool` callable. Slice 1 ships
`approve_all`, and `nare run` refuses to start unattended without `--yes`.

## Consequences

The seam slice 3 needs exists from the start at a cost of one parameter. No
rule engine, no policy config, no allowlist file. `--yes` is what keeps a
single permissive implementation honest rather than lax.
```

`docs/adr/0005-serializable-session.md`:

```markdown
# 5. A serializable Session advanced by an explicit step()

Status: Accepted (2026-09-09)

## Context

Conductor relays feedback mid-session up to five times per card, and today
rebuilds context by hand across those cycles in every adapter.

## Decision

A plain dataclass `Session`, advanced by `async def step()`, exposed as
`async for event in run(...)`. Serialized with `asdict()` and reconstructed
from `json.loads()`, with a version field on the file.

## Consequences

Resumable by construction, so `--resume` costs nothing extra and
`relay_feedback` becomes "resume with one more user message" — a whole slice-2
requirement falling out of slice 1 for free. Deterministic under test: the loop
is a pure function of a session and a transport.
```

`docs/adr/0006-not-langgraph.md`:

```markdown
# 6. No graph framework in the core

Status: Accepted (2026-09-09)

## Context

Conductor runs on LangGraph and should: it orchestrates a nine-role lifecycle
with real branching. nare's loop is a while loop over one model call and a list
of tool calls.

## Decision

No LangGraph, and no graph framework, in nare's public surface.

## Consequences

The runtime dependency set stays at one package, which is the whole claim
behind nare being nearly impossible to conflict with when vendored into another
project's container. If nare ever needs branching orchestration, that belongs
above it, in the consumer that already has a graph.
```

- [ ] **Step 2: Write `docs/architecture.md`**

```markdown
# nare architecture

Written after the skeleton ran, describing what is there.

## The shape

The core is a library. Every surface is a thin adapter over it.

    cli.py            argparse, JSONL out, session file in and out
      |
      v
    loop.py           step() and run() — turn counting, status, events
      |          \
      |           v
      |         tools.py     five tools, their schemas, dispatch, approval
      v
    transport/        everything vendor-shaped, behind one method

`session.py` and `events.py` sit underneath all of it and depend on nothing.

## The layer boundary

The transport owns client construction, base_url and auth, the request and
response wire format, tool schema translation, sampling parameters, retry
policy, and `stop_reason` and `usage` normalization.

Everything else stays above it: the loop, turn counting, status, tool
execution, approval, events and redaction, and cost in dollars.

Tool schema translation is the easy one to misplace. `TOOL_SCHEMAS` are
Anthropic-shaped; a future OpenAI transport converts them at its own edge. If
that conversion lands anywhere but the transport, the layer leaks on the day
the second transport arrives.

## What nare knows nothing about

Git, GitHub, pull requests, branches, cards, roles, review cycles, lifecycle.
Conductor derives all of that from four statuses plus work it does itself. A
change that teaches nare any of those words is crossing the boundary.

## Determinism

`step()` is a function of a session and a transport. `FakeProvider` replays a
scripted list of replies, inherits nothing, and imports no vendor code, so
every path is an ordinary unit test with no mocking framework and no network.
The one live test is deselected by default.

## Known ceilings

Both are marked in the source with `ponytail:` comments.

- Tool calls dispatch sequentially in `loop.py`. `asyncio.gather` is the
  upgrade once a run is measurably slow because of it.
- `make_transport()` has one `match` arm. That arm is the extension point; a
  registry is slice 4.
```

- [ ] **Step 3: Write `docs/superpowers/conductor-adapter-sketch.md`**

````markdown
# The Conductor adapter, sketched

Evidence that slice 1's contract holds — not shipped code, and not slice 1's
job to wire in. Written against `performer/backends/base.py`. The point is the
line count: the eight existing adapters average 668 lines.

```python
"""nare backend. The harness emits what the performer needs, so this adapter
transports bytes rather than reconstructing meaning."""

import asyncio
import json
from pathlib import Path

from performer.backends.base import BackendAdapter, BackendStatus
from performer.models import BackendEvent

_STATE = {"done": "completed", "blocked": "blocked", "error": "failed",
          "working": "running"}


class NareAdapter(BackendAdapter):
    def __init__(self, workdir: Path) -> None:
        self._workdir = workdir
        self._session = workdir / ".nare-session.json"
        self._proc: asyncio.subprocess.Process | None = None
        self._events: list[BackendEvent] = []
        self._result: dict | None = None
        self._pump: asyncio.Task | None = None

    async def start(self, stand, score, *, model=None, effort=None,
                    temperature=None, max_tokens=None) -> None:
        argv = ["nare", "run", "--yes", "--jsonl",
                "--session", str(self._session)]
        if self._session.exists():
            argv += ["--resume", str(self._session)]
        if score.persona:
            argv += ["--system", score.persona]
        for flag, value in (("--model", model), ("--effort", effort),
                            ("--temperature", temperature),
                            ("--max-tokens", max_tokens)):
            if value is not None:
                argv += [flag, str(value)]
        argv.append(score.prompt)

        self._proc = await asyncio.create_subprocess_exec(
            *argv, cwd=self._workdir, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True,
        )
        self._pump = asyncio.create_task(self._read())

    async def _read(self) -> None:
        assert self._proc and self._proc.stdout
        async for raw in self._proc.stdout:
            payload = json.loads(raw)
            # The one branch in the whole adapter: the terminal line is state,
            # every other line is an event.
            if payload.get("type") == "result":
                self._result = payload
            else:
                self._events.append(BackendEvent(**payload))

    def drain_events(self) -> list[BackendEvent]:
        drained, self._events = self._events, []
        return drained

    def get_status(self) -> BackendStatus:
        r = self._result
        if r is None:
            return BackendStatus(state="running")
        usage = r["usage"]
        return BackendStatus(
            state=_STATE[r["status"]],
            questions=r["questions"],
            error_reason=r["error"],
            stop_reason=r["stop_reason"],
            tokens_processed=usage["input"] + usage["output"],
        )

    async def relay_feedback(self, feedback: str) -> None:
        # --resume is picked up by start(); the session file is already there.
        await self.stop()
        self._result = None
        await self.start(self._stand, self._score.with_prompt(feedback))

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            await self._proc.wait()
        if self._pump:
            self._pump.cancel()
```

## What slice 1 supplies, and what slice 2 still owes

Free from slice 1: `state`, `questions`, `stop_reason`, token counts,
`drain_events()` as a constructor call, `relay_feedback()` as resume, and
`stop()` as a process kill.

Still owed by slice 2: `output` (slice 1 says "the final assistant text";
decide then whether a `submit` tool earns its place), cost in dollars,
compaction, the `SUPPORTED_BACKENDS` entry, `"nare": "nare"` in
`capabilities.py:_BACKEND_BINARIES`, and which Dockerfile variants carry it.

Two contract details this sketch depends on: the `result` line is not a
`BackendEvent` and must be filtered, and a run that never starts emits no
result line at all — a failure to spawn surfaces through the exit code, not
through `status=error`.
````

- [ ] **Step 4: Update `README.md`**

Insert between the title paragraph and `## Licensing`:

```markdown
## Install

    uv tool install nare        # or: pip install nare

Set `ANTHROPIC_API_KEY` in the environment. There is no `--api-key` flag —
argv is world-readable through `ps` and `/proc`.

## Use

    nare run --yes --jsonl "add a docstring to foo() in bar.py"

Stdout is typed JSONL, terminated by one `result` line carrying status,
questions, usage, and stop_reason. Stderr is logging. `--session PATH` writes
the session, and `--resume PATH` continues it.

See [docs/architecture.md](docs/architecture.md) for the shape, and
[docs/adr/](docs/adr/) for the decisions behind it.
```

- [ ] **Step 5: Verify the docs are accurate**

Run: `bin/build && uv run nare run --help`
Expected: PASS, and the help output lists every flag the README and specs name.

- [ ] **Step 6: Commit**

```bash
git add docs README.md
git commit -m "docs: record the six decisions, the architecture, and the adapter sketch"
```

---

## Acceptance criteria, mapped

Slice 1, section 8:

| # | Criterion | Verified by |
|---|---|---|
| 1 | `--yes --jsonl` emits typed JSONL, edits the file, exits 0 with `status=done` | Task 13 step 4 (`test_golden_jsonl`) and step 6 |
| 2 | A missing detail exits with `status=blocked` and questions | Task 13 (`test_blocked_exits_zero_with_questions`), step 6 |
| 3 | `--resume` continues a written session | Task 13 (`test_session_file_is_written_and_resumable`), step 6 |
| 4 | `nare run` without `--yes` refuses to start | Task 13 (`test_run_refuses_to_start_without_yes`), step 6 |
| 5 | Knobs reach the request; unknown provider fails at construction | Tasks 6, 7, 12; Task 13 step 6 |
| — | The ~80-line adapter sketch | Task 14 |

Transport layer, section 10:

| # | Criterion | Verified by |
|---|---|---|
| 1 | Factory builds anthropic; unknown kind names the supported kinds | Task 7 |
| 2 | `--model --max-tokens` bind into the outbound request, and no `temperature` key is ever present | Task 6, through `httpx2.MockTransport` |
| 3 | `--effort high` → `budget_tokens=16384`, `max_tokens=21333` (the non-streaming ceiling) | Tasks 5 and 6 |
| 4 | `--temperature` exits non-zero before any request, naming the vendor limitation | Tasks 5, 7, 12 |
| 5 | `stop_reason` and `usage` tables pass for both vendors' inputs | Task 6 |
| 6 | `_check: Transport = FakeProvider([])` type-checks under mypy | Task 4, enforced by `bin/build` |
| 7 | Slice 1's criteria continue to pass | The table above |

## After the plan

Use `superpowers:finishing-a-development-branch`. Then the handoff's flow
resumes at `/superpowers:brainstorming` for slice 2 (Conductor parity), whose
integration surface is in `vividynamics/conductor` at
`agent/performer/src/performer/backends/base.py`. `docs/superpowers/HANDOFF.md`
needs updating first — its section 1 still says "Code: none written", and its
section 5.4 gaps are closed by this plan.
