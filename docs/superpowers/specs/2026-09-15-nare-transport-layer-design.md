# nare — Transport Layer

**Date:** 2026-09-15
**Status:** Approved; amended 2026-09-16 during implementation
**Scope:** Amends the slice-1 spec,
`2026-09-09-nare-walking-skeleton-design.md`. Establishes the bottom layer of
nare's architecture. Slice 1 still ships exactly one transport.

> **Amendment, 2026-09-16.** This document was written against an `anthropic`
> SDK API that no longer exists. Implementation resolved `anthropic` 1.6.0 and
> found three divergences, each verified directly against the installed SDK:
>
> 1. The SDK now depends on **`httpx2`**, not `httpx`. `httpx2` provides the
>    same `MockTransport`, `AsyncClient`, `Request` and `Response`, and
>    `AsyncAnthropic.http_client` is annotated `httpx2.AsyncClient | None`, so
>    section 7's reasoning survives intact — only the module name moved.
> 2. **`temperature` no longer exists on `messages.create` at all.** There is
>    no typed client-level equivalent. Everything in section 4 that reasons
>    about `temperature` is therefore obsolete, and criteria 2, 3 and 4 in
>    section 10 could not be satisfied as written.
> 3. The SDK **refuses non-streaming requests above `max_tokens` 21333**
>    (`_calculate_nonstreaming_timeout` raises when
>    `3600 * max_tokens / 128000 > 600`). Section 4's unclamped
>    `budget + 8192` default made `--effort high` resolve to 24576, which
>    raises before any request — the spec's own default would have shipped a
>    transport that cannot run. Streaming stays deferred to slice 3.
>
> The decision was to adapt rather than pin the SDK backwards: nare is meant to
> be vendored into other projects' containers, and section 6's one-dependency
> posture is worse served by pinning an `anthropic` six major versions behind
> than by amending this document. `--temperature` is **kept** in the CLI and in
> `make_transport`, and the Anthropic transport now raises at construction when
> it is set, naming the vendor limitation. Accepting and silently ignoring it
> was rejected: quietly discarding a sampling parameter is precisely the
> dishonest reporting nare exists to eliminate.
>
> Sections 4, 5, 7 and 10 below carry these changes inline.
>
> One simplification is deliberately NOT taken here and is left for a later
> slice: the vendor now exposes a native `output_config.effort`
> (`low`/`medium`/`high`/`xhigh`/`max`), which would delete the budget table,
> the `max_tokens` interaction, and the clamp entirely. `thinking.budget_tokens`
> still works, so keeping it is the smaller change; revisit when slice 2 next
> touches this file.

## 1. What changes, and why

The slice-1 spec declined a provider abstraction. Its reasoning was sound at
the time: the seam was already justified by `FakeProvider` being a genuine
second implementation, and a neutral message format would have been a
translation layer with nothing on the far side.

The plan has since changed. nare will target Claude, OpenAI, and locally hosted
models, and configuration should select between them. That puts something on
the far side of the seam, so the seam is now defended by three planned
configurations rather than by a test double alone.

Three configurations, two implementations. Ollama, llama.cpp, vLLM, LM Studio
and text-generation-webui all expose an OpenAI-compatible
`/v1/chat/completions`, so "local" is the OpenAI transport pointed at a
different `base_url` with a throwaway key. That is configuration, not a third
class.

This is deliberately the seam and the factory only. The OpenAI transport is not
built here, and the canonical message format question stays open until it is.
The accepted cost is a factory with one product; section 4 is the work done to
ensure that product is not the only shape it can hold.

## 2. The layer boundary

The seam falls at everything vendor-shaped, not merely at the HTTP call.

| The transport owns | Stays above it |
|---|---|
| Client construction, `base_url`, auth | The loop, turn counting, status |
| Request and response wire format | Tool execution (`tools.py`) |
| Tool schema translation | Approval |
| Sampling params: `model`, `temperature`, `max_tokens`, `effort`, `system` | Events and redaction |
| Retry policy, via the vendor SDK | Cost in dollars (slice 2) |
| `stop_reason` and `usage` normalization | |

Tool schema translation belongs here and is easy to miss. `TOOL_SCHEMAS` are
Anthropic-shaped, `{name, description, input_schema}`, while OpenAI expects
`{type: "function", function: {..., parameters}}`. If that conversion lands
anywhere but the transport edge, the layer leaks on the day the second
transport arrives.

Cost in dollars stays above the line. Transports report token counts; pricing
is slice 2's problem and is not a vendor protocol concern.

## 3. The contract

```python
StopReason = Literal[
    "end_turn", "tool_use", "max_tokens", "stop_sequence", "refusal"
]

class Transport(Protocol):
    async def turn(self, messages: list[Message], tools: list[dict]) -> Reply: ...

@dataclass(frozen=True)
class Reply:
    content: list[dict]           # canonical blocks
    tool_calls: list[ToolCall]
    usage: Usage
    stop_reason: StopReason

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
) -> Transport
```

One method. Everything else is bound at construction, which keeps the loop free
of vendor parameters entirely: `loop.py` changes by a single identifier,
`provider.turn` becoming `transport.turn`.

`make_transport` dispatches on `kind` with a `match` and raises on anything
unknown. It carries a `ponytail:` comment recording that one transport is the
current state and the `match` arm is the extension point, so the shape reads as
deliberate rather than unfinished.

### Still duck-typed

`typing.Protocol` is structural. `FakeProvider` inherits nothing and imports
nothing, so the slice-1 claim — duck-typed, no ABC, no registry — survives
intact. A Protocol is that same duck typing with mypy able to check it, and
mypy is already a dev dependency. The registry is a separate question, deferred
to slice 4 alongside the rest of the extension surface.

## 4. Normalization, validated against OpenAI before OpenAI exists

A one-product factory is only defensible if the second product is already known
to fit it. Three checks were run against OpenAI's actual Chat Completions
constraints. The third is the one that proves the layer is real.

### `stop_reason` is a total mapping in both directions

| Anthropic | OpenAI `finish_reason` | nare |
|---|---|---|
| `end_turn` | `stop` | `end_turn` |
| `tool_use` | `tool_calls` | `tool_use` |
| `max_tokens` | `length` | `max_tokens` |
| `stop_sequence` | — | `stop_sequence` |
| `refusal` | `content_filter` | `refusal` |

No OpenAI value requires a vocabulary nare does not already have. `max_turns`
is set by the loop and never by a transport: the union is nare's, and each
transport emits a subset.

### `usage` has a genuine mismatch, caught here rather than in slice 2

Anthropic's `input_tokens` **excludes** cache reads. OpenAI's `prompt_tokens`
**includes** them, reporting the cached portion separately under
`prompt_tokens_details.cached_tokens`.

```python
@dataclass(frozen=True)
class Usage:
    input: int          # excludes cache reads
    output: int
    cache_read: int = 0
    cache_write: int = 0
```

nare normalizes to Anthropic's convention, so the OpenAI edge subtracts
`cached_tokens` from `prompt_tokens` to produce `input`. Getting this wrong
would silently inflate token counts on every cached request, which is precisely
the honest accounting nare exists to provide. OpenAI has no cache-write
concept, so `cache_write` is always zero there.

### `effort` needs per-transport validation, not just mapping

OpenAI passes `reasoning_effort` through as one of `low`, `medium`, `high`.

Anthropic maps it to an extended-thinking budget, which carries constraints the
parameter itself does not express: `budget_tokens` must be at least 1024 and
strictly less than `max_tokens`, and the resolved `max_tokens` must stay under
the SDK's non-streaming ceiling of 21333.

| `effort` | Anthropic `thinking.budget_tokens` |
|---|---|
| `low` | 1,024 |
| `medium` | 4,096 |
| `high` | 16,384 |

The Anthropic transport therefore validates at construction:

- `--temperature` raises, with or without `--effort`. The vendor removed
  `temperature` from `messages.create` entirely, so there is nothing to bind it
  to. The flag stays in the CLI because the planned OpenAI and locally hosted
  transports do accept it, and because refusing loudly is better than either
  dropping a documented flag or accepting one that does nothing.
- `--effort` without an explicit `--max-tokens` sets `max_tokens` to
  `min(budget + 8192, 21333)`. The `+ 8192` keeps the default clear of the
  `high` budget; the clamp keeps it under the non-streaming ceiling, without
  which `--effort high` would refuse to run at its own default.
- `--effort` with an explicit `--max-tokens` at or below the budget raises.
- An explicit `--max-tokens` above 21333 raises, naming streaming as the
  reason. Streaming `turn()` is slice 3's.

`max_tokens` therefore defaults to `None` in the signature rather than to
8192, so the transport can distinguish an explicit 8192 from an unset value.
It resolves to 8192 with no `effort`, and to `min(budget + 8192, 21333)` with
one.

A shared parameter bag cannot know any of this. A per-transport edge can, and
that is the difference between a strategy that varies something and one that
varies nothing.

## 5. Configuration

Flags with environment fallbacks. No config file: the slice-1 decision stands,
and the primary consumer configures nare by constructing a command line.

| Flag | Env | Default |
|---|---|---|
| `--provider {anthropic}` | `NARE_PROVIDER` | `anthropic` |
| `--model NAME` | `NARE_MODEL` | `claude-sonnet-5` |
| `--base-url URL` | `NARE_BASE_URL` | vendor default |
| `--temperature FLOAT` | — | unset; **rejected by the anthropic transport** |
| `--max-tokens INT` | — | 8192 |
| `--effort {low,medium,high}` | — | unset |
| `--system TEXT` | — | unset |

Precedence is flag, then environment, then default. `cli.py` gathers these into
one `make_transport()` call and passes the resulting object into `run()`. The
library API takes a `Transport`, so injection stays trivial.

`--provider` accepts only `anthropic` in slice 1. A flag value that parses and
then fails at the first request is worse than a short list that grows when
implementations do.

### No `--api-key` flag

Argv is world-readable through `ps` and `/proc`, and Conductor's performer
spawns `nare` as a subprocess, so a key on the command line would reach process
listings and plausibly logs. API keys come from the environment only, read by
the vendor SDK itself. `make_transport(api_key=...)` exists for library callers
that already hold the secret in memory.

### `--system`

Not strictly a transport concern, but one of the two gaps recorded in the
handoff's section 5.4, and it rides in on the same CLI change rather than being
retrofitted during slice 2. Conductor injects per-role persona text and has no
way in without it.

## 6. Error handling

Retries stay at the vendor edge. The Anthropic SDK's own `max_retries` handles
429s and 5xxs, as slice 1 already specified. No shared retry code is
introduced; the second transport brings its own SDK's policy. Retry behaviour
is vendor-shaped, which is the same test every other item in section 2 passed.

Construction errors fail before the loop starts: stderr, non-zero exit, no
JSONL. Unknown `--provider` is handled by argparse `choices` at no cost; the
`effort` conflicts from section 4 and a missing API key raise from
`make_transport`.

The result line implies a session existed, so a run that never started does not
emit one. This also preserves a distinction Conductor wants: a backend that
failed to spawn surfaces through the exit code in `start()`, while a backend
that ran and failed surfaces as `status=error` in the result line.

## 7. Testing

`FakeProvider` is unchanged and still satisfies `Transport` structurally. One
line in the test module, `_check: Transport = FakeProvider([])`, makes mypy fail
if the protocol ever drifts away from the double. That is the whole cost of
keeping them honest.

`test_transport.py` is new. Slice 1 forbids mocking frameworks and network
access, which the Anthropic SDK accommodates directly: it accepts an injected
`http_client`, and `MockTransport` ships inside `httpx2`, which already
arrives as a dependency of `anthropic`. Zero new dependencies.

The handler returns a canned response body and captures the outbound request,
so the tests assert against the real request the real SDK constructs:

- tool schemas translated correctly,
- `max_tokens` actually bound,
- `effort` rendered as `thinking.budget_tokens`, with no `temperature` on the
  wire and `max_tokens` clamped to the non-streaming ceiling,
- `--temperature` refused at construction rather than reaching the wire.

This is materially stronger than stubbing `turn()`, and it is the harness that
makes the second transport cheap to add.

The normalization tables are pure functions and get ordinary table tests,
**including the OpenAI rows now**, since section 4 writes the mapping down even
though the transport does not exist. If the OpenAI transport spec later
contradicts these rows, that contradiction is worth catching.

The factory gets its own small set: correct type per kind, unknown kind raises,
and each `effort` conflict raises.

## 8. Changes to the slice-1 spec

**Section 4, file tree.** `provider.py ~150` becomes:

```
  transport/
    __init__.py   Transport, Reply, StopReason, make_transport()   ~70
    anthropic.py  AnthropicTransport                               ~150
```

The budget moves from roughly 800 lines to roughly 900.

**Section 4, "No Provider protocol class"** is rewritten as "The transport
layer", carrying section 3 of this document. The no-registry half of the
original reasoning stands unchanged.

**Section 5** gains a "Flags" subsection ahead of "Wire format", carrying the
table in section 5 here.

**Section 6, infrastructure.** One runtime dependency, `anthropic`, remains
true for slice 1. The intended posture is recorded now: the OpenAI SDK lands as
an optional extra, `pip install nare[openai]`, and never as a base dependency.
nare's claim to being nearly impossible to conflict with when vendored into
another project's container only survives if the default install stays at one
dependency.

**Section 8, acceptance criteria.** One is added: `nare run --provider anthropic
--model X --max-tokens N` honours all three, and an unknown `--provider` fails
at construction with a clear message rather than at the first request.

**Section 9, out of scope.** "No second provider" stays true and explicit. Add
no transport registry and no plugin loading, both slice 4. The config-file
exclusion stands.

## 9. Deferred

Named so that none of it gets built by accident.

| Deferred | To |
|---|---|
| Canonical message format decision | The OpenAI transport spec |
| Streaming `turn()` | Slice 3, if the TUI needs it |
| Transport registry and plugin loading | Slice 4 |
| Cost in dollars | Slice 2 |
| Config file | Decided against, not pending |
| Capability probing | Decided against, not pending |

Capability probing was considered and rejected. Many locally hosted models tool
call badly or not at all, and nare's loop is entirely tool calls, so a weak
local model will spin to `max_turns`. A `supports_tools` flag cannot be
verified by nare, and a live probe costs a billed request per run and
duplicates Conductor's own `capabilities.py`. The honest failure is the
correct one: the loop exits at `max_turns` with a truthful `stop_reason`.

### Where the OpenAI transport lands

Not slice 2. By the handoff's splitting test 2, its questions — canonical
message format, block-to-flat translation, tool-call identifier plumbing —
share nothing with compaction's questions. It gets its own spec, sequenced when
a real consumer needs it, rather than being attached to Conductor parity.

This also answers the handoff's open question 5.5.4, "does slice 2 add a second
provider?" No. Slice 2 builds the adapter, compaction, and cost accounting
against the seam this document establishes.

## 10. Acceptance criteria

1. `make_transport("anthropic", model=..., max_tokens=...)` returns a working
   transport, and `make_transport("openai", ...)` raises a clear error naming
   the supported kinds.
2. `nare run --model X --max-tokens N` binds both into the outbound request,
   verified through `httpx2.MockTransport`, and no `temperature` key is ever
   present on the wire.
3. `--effort high` renders `thinking.budget_tokens=16384` with
   `max_tokens=21333`, the non-streaming ceiling.
4. `--temperature 0.2` exits non-zero before any request, with a message
   naming the vendor limitation — with or without `--effort`.
5. The `stop_reason` and `usage` tables in section 4 pass as table tests for
   both vendors' inputs.
6. `_check: Transport = FakeProvider([])` type-checks under mypy.
7. Slice 1's existing acceptance criteria continue to pass unchanged.
