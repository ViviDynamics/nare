# nare — Walking Skeleton (Slice 1)

**Date:** 2026-09-09
**Status:** Approved, not yet implemented
**Scope:** The first of five slices. Core engine plus headless CLI.
**Amended:** 2026-09-15 by `2026-09-15-nare-transport-layer-design.md`, which
establishes the transport layer. Sections 4, 5, 6, 8, and 9 below carry those
changes inline.
**Amended again:** 2026-09-16 during implementation. `anthropic` 1.6.0 removed
`temperature` from `messages.create`, moved from `httpx` to `httpx2`, and
refuses non-streaming requests above `max_tokens` 21333. The transport spec's
2026-09-16 amendment note carries the full findings and the reasoning; sections
5, 7 and 8 below carry the consequences inline. `--temperature` remains a flag
and is refused at construction by the anthropic transport rather than dropped
or silently ignored.

## 1. What nare is

nare is a standalone agent harness: an agent loop, a tool set, a provider
layer, and a serializable session, exposed through a CLI today and a TUI,
HTTP daemon, and desktop app later.

It is a real harness, not a wrapper around other harnesses. It calls model
providers directly and implements its own tools. Conductor's performer will
eventually select it with `backend: nare`, alongside the eight adapters that
drive external CLIs today.

nare is a product in its own right. Conductor is its first and most demanding
consumer, and the design is tuned to that consumer, but nothing in the core
depends on Conductor existing.

## 2. Why build it rather than adapt a ninth CLI

Conductor's `agent/performer/src/performer/backends/` holds eight adapters
totalling roughly 5,300 lines. They are large because they reverse-engineer
structure out of CLIs that were never built to emit it. Conductor needs six
things a general-purpose harness does not expose cleanly:

| What Conductor needs | What an adapter does instead |
|---|---|
| `blocked` + `questions` as a terminal state | Parsed out of prose; a guess about intent |
| Typed event stream with secret redaction | Reconstructed from stdout line shapes |
| Mid-session feedback relay | Hand-rolled per CLI, differently each time |
| Structured output as a field | Scraped from stdout |
| Resumable sessions | Rebuilds context by hand across up to 5 feedback cycles |
| Honest `stop_reason` and token accounting | Inferred, often wrong |

A harness that emits these natively turns an 800-line adapter into roughly 80.
That gap is the case for building nare.

## 3. Decisions

Six decisions were settled before this spec. Each gets an ADR under
`docs/adr/`.

1. **Own harness**, not a wrapper or meta-harness. nare implements the loop,
   the tools, and the provider calls.
2. **Python 3.12+**, uv, hatchling. Matches Conductor's floor and toolchain,
   one skill set across both repos. The known cost is distribution: there is no
   true single binary. Accepted; it is a packaging problem, not an
   architectural one.
3. **Subprocess CLI integration.** The performer spawns `nare` exactly as it
   spawns `claude` or `opencode`. This keeps nare's own CLI on the critical
   path so the standalone surface cannot rot, and preserves crash isolation.
   In-process `import nare` remains available later at no extra cost, because
   the core is a library regardless.
4. **Approval seam with explicit `--yes`.** One dispatcher, one
   `approve(tool, args) -> bool` callable. Slice 1 ships one implementation
   (approve everything) and `nare run` refuses to start unattended without
   `--yes`. No rule engine, no policy config, but the seam the TUI needs in
   slice 3 exists from the start.
5. **Serializable state plus async generator.** A plain `Session` advanced by
   an explicit `step()`, exposed as `async for event in run(...)`. Resumable by
   construction, deterministic under test.
6. **Not LangGraph.** Conductor runs on it and should. A harness whose value is
   being a clean, low-dependency engine should not carry a graph framework in
   its public surface.

## 4. Architecture

The core is a library. Every surface is a thin adapter over it. This is the
one structural commitment that makes slices 3 through 5 cheap, and it costs
nothing now.

```
src/nare/
  __init__.py     public API: run(), Session, Event      ~30
  session.py      Session, Message, Usage, Status        ~80
  loop.py         step(), run()                          ~120
  events.py       Event + secret redaction               ~70
  tools.py        read write edit bash ask + dispatch     ~250
  transport/
    __init__.py   Transport, Reply, make_transport()     ~70
    anthropic.py  AnthropicTransport                     ~150
  cli.py          nare run [flags below]                 ~140
tests/
  fake_provider.py
  test_loop.py  test_tools.py  test_cli.py  test_transport.py
docs/
  architecture.md            written after the skeleton runs
  adr/000{1..6}-*.md
pyproject.toml  bin/build  .github/workflows/ci.yml
```

Roughly 900 lines. Flat deliberately, with one exception: `transport/` is a
package because it is the layer every later slice sits on, and a package edge
is what keeps slices 2 through 5 out of its internals. `tools.py` splits when
it hurts. A directory holding one file is still speculation.

### The transport layer

The bottom layer. `step()` accepts anything with
`async def turn(messages, tools) -> Reply`, and `make_transport(kind, ...)`
builds one from configuration. Slice 1 ships exactly one implementation;
Claude, OpenAI, and locally hosted models are the three planned
configurations, which is what justifies the seam.

Still duck-typed: `typing.Protocol` is structural, so `FakeProvider` inherits
nothing and imports nothing, and it remains a genuine second implementation
that keeps the loop deterministic under test. No ABC, and no registry — the
registry is slice 4's, with the rest of the extension surface.

The transport owns everything vendor-shaped: wire format, tool schema
translation, sampling parameters, retry policy, and `stop_reason` and `usage`
normalization. Full contract and rationale in
`2026-09-15-nare-transport-layer-design.md`.

### Hand-written tool schemas

Five tools, five literal dicts beside their functions. A schema generator that
introspects type hints is worth revisiting at roughly fifteen tools.

### `ask` is a tool

Tool calls are the only structured channel a model has. `ask(questions=[...])`
sets `status=blocked` and terminates the loop. Its `tool_result` is still
appended to `messages` before the loop exits, so a resumed session picks up
from a well-formed transcript rather than a dangling `tool_use`. Ten lines
here; deletes most of an adapter in Conductor.

## 5. The loop and event contract

### Session

```python
Status = Literal["working", "blocked", "done", "error"]

@dataclass
class Session:
    id: str
    messages: list[Message]      # role + content blocks
    usage: Usage                 # in/out/cache tokens, cost
    status: Status = "working"
    questions: list[str] = field(default_factory=list)
    stop_reason: str | None = None
    error: str | None = None
    turns: int = 0
```

Serialized with `asdict()` and reconstructed from `json.loads()`. The file
carries a `version` field.

### Canonical message form

Anthropic's shape: role plus `text | tool_use | tool_result` blocks. Adopted,
not invented, and not translated. With one provider, a neutral format would be
a translation layer with nothing on the far side. Provider two converts at its
own edge, in its own file, when it exists.

### step()

```python
async def step(s: Session, transport, approve) -> Session:
    reply = await transport.turn(s.messages, TOOL_SCHEMAS)
    s.usage += reply.usage; s.turns += 1
    s.messages.append(assistant(reply.content))
    if not reply.tool_calls:
        s.status, s.stop_reason = "done", reply.stop_reason
        return s
    results = [await dispatch(c, approve) for c in reply.tool_calls]
    s.messages.append(user(results))
    if any(c.name == "ask" for c in reply.tool_calls):
        s.status, s.questions = "blocked", questions_from(results)
    return s
```

Tool dispatch is sequential. This is a marked ceiling, carrying a `ponytail:`
comment: models do emit parallel tool calls, and `asyncio.gather` is the
upgrade once a run is measurably slow because of it.

### Events

Six types, matching Conductor's `BackendEventType` verbatim: `progress`,
`tool_use`, `thinking`, `cost`, `error`, `output`. Same four fields
(`timestamp`, `type`, `text`, `detail`) and the same redaction at
construction.

This is not lock-in. It is a reasonable event vocabulary, and matching it
exactly means the Conductor adapter is `BackendEvent(**json.loads(line))`
rather than a mapping table.

Events accumulate on the session per step; `run()` drains them.

### Flags

| Flag | Env | Default |
|---|---|---|
| `--provider {anthropic}` | `NARE_PROVIDER` | `anthropic` |
| `--model NAME` | `NARE_MODEL` | `claude-sonnet-5` |
| `--base-url URL` | `NARE_BASE_URL` | vendor default |
| `--temperature FLOAT` | — | unset; **rejected by the anthropic transport** |
| `--max-tokens INT` | — | 8192 |
| `--effort {low,medium,high}` | — | unset |
| `--system TEXT` | — | unset |
| `--jsonl`, `--yes`, `--resume PATH`, `--session PATH`, `--max-turns N` | — | see below |

Precedence is flag, then environment, then default. Everything above the rule
is bound into a `Transport` by `make_transport()` and never reaches the loop.

There is no `--api-key` flag: argv is world-readable through `ps` and `/proc`,
and the performer spawns `nare` as a subprocess. Keys come from the
environment, read by the vendor SDK itself.

`--system` carries Conductor's per-role persona text. Without it the nine roles
have no way in.

### Wire format

Stdout is pure JSONL events, terminated by one `{"type": "result", ...}` line
carrying status, questions, usage, and stop_reason. Stderr is stdlib logging.

`--session PATH` optionally writes the full session, which doubles as the
resume artifact. `--resume PATH` therefore costs nothing extra, and
Conductor's `relay_feedback` becomes "resume with one more user message" — the
whole slice 2 requirement falling out of slice 1 for free.

### Failure handling

- Provider 429s and 5xxs: the Anthropic SDK's own `max_retries`. No code.
- Tool errors: returned as `is_error=True`; the model adapts, which is what it
  is good at.
- `--max-turns` (default 50): `status=error`, `stop_reason="max_turns"`.
- Context overflow: `stop_reason="max_tokens"`, stop honestly. Compaction is
  slice 2's problem and is not guessed at here.

## 6. Infrastructure

**One runtime dependency: `anthropic`.** No pydantic (dataclasses and `json`
cover a serializable session), no structlog (events are the log), no click or
typer (argparse handles the flag set). Conductor reaches for those and should.
A harness that gets vendored into other people's containers should be nearly
impossible to conflict with.

That claim only survives if it stays true, so the posture is recorded now: a
second transport's SDK lands as an optional extra, `pip install nare[openai]`,
and never as a base dependency.

Dev dependencies are pytest, pytest-asyncio, ruff, mypy — Conductor's
toolchain exactly. `bin/build` runs lint, types, and tests, matching
Conductor's idiom. `.github/workflows/ci.yml` runs it on push and pull
request. The existing `external-contributions.yml` workflow is untouched.

Packaging is hatchling with `license = "LicenseRef-Elastic-License-2.0"` and
`license-files = ["LICENSE", "NOTICE"]`, following Conductor's precedent, with
a `nare = "nare.cli:main"` console script.

## 7. Testing

Everything rests on the loop being deterministic given a transport.
`FakeProvider` replays a scripted list of replies, so every path is an
ordinary unit test with no mocking framework and no network.

- `test_loop` — done, blocked via `ask`, max-turns, tool-error recovery, and a
  resume round-trip asserting `Session -> json -> Session` runs identically.
- `test_tools` — each tool, plus `approve` returning False.
- `test_cli` — golden JSONL with timestamps normalized.
- `test_transport` — factory selection and its errors, plus the real outbound
  request asserted through `httpx2.MockTransport`, which arrives with
  `anthropic` and needs no mocking framework.
- One `@pytest.mark.live` smoke test, skipped without `ANTHROPIC_API_KEY`, not
  run in CI.

## 8. Acceptance criteria

In a throwaway git repository:

1. `nare run --yes --jsonl "add a docstring to foo() in bar.py"` emits typed
   JSONL, actually edits the file, and exits 0 with `status=done`.
2. A task with a missing detail exits with `status=blocked` and populated
   `questions`.
3. `--resume` on a written session file continues that session.
4. `nare run` without `--yes` refuses to start.
5. `--model` and `--max-tokens` reach the outbound request; `--temperature`
   exits non-zero at construction with a message naming the vendor limitation,
   since `anthropic` 1.6.0 no longer accepts it; and an unknown `--provider`
   fails at construction with a clear message rather than at the first
   request.

A sketch of the roughly 80-line Conductor adapter that would consume these is
part of the deliverable, as evidence the contract holds. Wiring it into the
performer is slice 2, not slice 1.

## 9. Out of scope for slice 1

Named explicitly so none of it gets built by accident:

- **No `grep` or `glob` tools.** `bash` plus `rg` covers search. Dedicated
  tools get added when transcripts show the model fumbling it.
- No config file. Environment variables and flags only.
- No sessions directory, history browser, or session list.
- No MCP, hooks, skills, or subagents.
- No compaction or context management.
- No TUI, HTTP daemon, or desktop app.
- No second provider. Slice 1 ships the transport seam and one
  implementation; Claude is the only `--provider` value that parses.
- No transport registry and no plugin loading. Both are slice 4.
- No API reference or plugin-authoring guide. There are no plugins, and a
  roadmap document is a promise maintained instead of code.

Distribution stays `uv tool install nare` and `pip install nare` in the
performer's Dockerfile. The single-binary question is real but blocks nothing.

## 10. The remaining slices

Each gets its own spec, plan, and build cycle.

| # | Sub-project | Deliverable |
|---|---|---|
| 1 | Core engine and headless CLI | This spec |
| 2 | Conductor parity | `backend: nare` wired into the performer; compaction; cost accounting |
| 3 | Interactive CLI and TUI | REPL, permission prompts, diff rendering, session browsing |
| 4 | Extension surface | MCP, hooks, custom tools, subagents |
| 5 | Desktop | HTTP daemon and UI |
