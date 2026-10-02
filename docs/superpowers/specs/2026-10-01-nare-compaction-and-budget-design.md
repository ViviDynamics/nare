# nare - Compaction, Cost and Budget (Slice 2 core)

**Date:** 2026-10-01
**Status:** Approved, not yet implemented
**Scope:** The part of slice 2 that lives in nare: context compaction, cost in
dollars, and a session budget. Wiring `backend: nare` into Conductor's
performer is Conductor's work and is not in this spec. Terminal outcomes
(#8) follow this spec and build on the stop reasons it adds.

## 1. Why

The walking skeleton deferred compaction to slice 2 on purpose. Today
`messages` grows until the provider refuses it, and the run ends reporting
`max_tokens`. That happens two ways:

- **A, the larger problem:** a single long run, where `bash` and `read` output
  piles up over many turns.
- **B:** a session resumed across Conductor's feedback cycles, which grows each
  cycle.

Cost is the other slice 2 gap. `Usage` counts tokens only. `architecture.md`
already places cost in dollars above the transport, and nothing computes it.
Without cost there is no budget, so a runaway run has no ceiling but
`--max-turns`.

## 2. What was established before this spec

Probed against the LiteLLM proxy nare is run through (LiteLLM 1.100.1):

- Every response on both `/v1/messages` and `/v1/chat/completions` carries
  `x-litellm-response-cost`, the dollar charge for that call.
- Local models (`ada/qwen3-8b`) omit `x-litellm-response-cost` but send
  `x-litellm-response-cost-original: 0.0`.
- `GET /v1/model/info` returns `model_info.max_input_tokens` for hosted models
  (`claude-haiku` 200000, `gpt-5-nano` 272000, `gemini-3.1-flash-lite`
  1048576) and nothing for the local ones, whose proxy config does not set it.
  Setting `max_input_tokens` there makes them discoverable with no nare change.

## 3. Compaction

### Where it runs

A new module, `compact.py`, holds one pure function:

```python
def compact(s: Session, window: int) -> CompactionReport | None
```

It mutates `s.messages` in place, imports no vendor code, and returns what it
did, or `None` when it did nothing. `step()` calls it immediately before
`transport.turn()`. Because a resumed session goes through `step()` too, the
same call handles A and B: a session that grew across resumes is compacted on
its first turn back.

### When it fires

The context estimate is:

    last_input_tokens + len(json of messages appended since that turn) / 4

`last_input_tokens` is a new session field (default `0`) holding the input the
provider reported for the most recent turn: `input + cache_read + cache_write`.
It is saved with the session, so a resume starts from a measured figure. When
it is `0` (a fresh session, or a file written before this field), the estimate
is `len(json of all messages) / 4`.

Compaction fires when the estimate reaches **80%** of the window and stops
once it is at or below **60%**. The gap keeps compaction rare and batched,
which matters because every compaction invalidates the provider's prompt cache
for the rewritten prefix.

### What it does

Walking from the oldest message forward, it replaces each eligible
`tool_result` block's content with a stub:

    [elided by nare: 14302 chars of output from turn 6. Rerun the tool if you need it.]

### What it never touches

- The first user message, which is the task.
- Any assistant message. Every `tool_use` and its arguments survive, and so do
  `thinking` blocks.
- The last **2 turns**.
- `tool_result` blocks with `is_error` set, and results of `ask`. They are
  small and carry meaning.

Every `tool_use` keeps its paired `tool_result`, so the transcript stays
well-formed for both vendors.

### When it cannot make room

If every eligible block has been shrunk and the estimate is still at or above
**100%** of the window, `step()` does not call the transport. The session ends
with `status=error`, `stop_reason="context"`, and an `error` naming the
estimate and the window. Between 60% and 100%, after shrinking, the step
proceeds.

### What a caller sees

One `progress` event per compaction:

    compacted: elided 7 tool results, ~41000 -> ~24000 tokens

with `detail.compaction` carrying `elided`, `before`, `after` and `window`. No
new event type is added, so Conductor's `BackendEventType` mapping is
unchanged.

The session file keeps the compacted transcript. That is deliberate: it is
what bounds B across resumes.

## 4. Context window

### Resolution

`run()` resolves the window once, in this order:

1. `--context-window N` / `NARE_CONTEXT_WINDOW`.
2. `await transport.context_window()`, called only when step 1 gave nothing.
3. `32000`, so a small local model is safe without configuration.

The first `progress` event of a run names the resolved window and its source
(`flag`, `backend` or `default`), so a caller can see why compaction fired when
it did.

### Discovery in the transport

Transports gain one method:

```python
async def context_window(self) -> int | None
```

What a backend publishes about its models is vendor- and proxy-shaped, so it
stays below the transport line.

- With a `--base-url` set, it sends `GET {base}/v1/model/info` with a 5 second
  timeout, finds the entry whose `model_name` equals its model, and returns
  `model_info.max_input_tokens`. A non-200, a timeout, an unparseable body, a
  missing entry or a missing field returns `None` and logs one line on stderr.
  It never fails the run.
- With no `--base-url` (a direct vendor endpoint) it returns `None` without a
  request. Whether Anthropic's or OpenAI's own model APIs publish a window is
  unverified, so nothing is guessed. Direct use falls to the default unless the
  flag is set; the cost is earlier compaction, never an overflow.

`{base}` is the configured base URL with any trailing `/v1` removed, since the
OpenAI route is configured as `<proxy>/v1`.

## 5. Cost

### Per turn

`Reply` gains `cost: float | None = None`: the dollars the backend reported for
that turn. A shared helper in `transport/__init__.py` reads it:

```python
def reported_cost(headers: Mapping[str, str]) -> float | None
```

It reads `x-litellm-response-cost`, falls back to
`x-litellm-response-cost-original`, and returns `None` when neither is present
or the value is not a finite non-negative number.

- `openai.py` already holds the `httpx2` response and passes its headers.
- `anthropic.py` changes `messages.create(...)` to
  `messages.with_raw_response.create(...)`, reads the headers, and calls
  `.parse()` for the same response object it builds the `Reply` from today.

Cost reported in a response body (OpenRouter's `usage.cost`) is not read until
a caller needs it.

### On `Usage`

`Usage` gains `cost: float | None = 0.0`. Each turn's cost is the first that
applies:

1. `reply.cost`, what the backend charged. It wins over configured prices.
2. Configured prices applied to that turn's tokens, all in dollars per million
   tokens:
   - `NARE_PRICE_IN` and `NARE_PRICE_OUT`, both required to price anything.
   - `NARE_PRICE_CACHE_READ` and `NARE_PRICE_CACHE_WRITE`, optional. When
     unset, cache tokens are priced at `NARE_PRICE_IN`. That overstates cost,
     which is the safe direction for a budget, and the README says so.
3. Otherwise `None`.

Totals follow one rule, in `Usage.__add__`: **an unknown turn makes the total
unknown.** `None` plus anything is `None`. A sum with a gap reads as
underspend, and underspend is the wrong error for a budget. Converting tokens
to dollars happens in `loop.py`, above the transport.

The `cost` event's text becomes `1204 in / 88 out, $0.000088`, or
`1204 in / 88 out, cost unknown`. Its `detail` carries the full `Usage`.
`usage.cost` appears in the `result` line and the session file as an additive
field.

## 6. Budget

Two limits, either or both:

| Flag | Env | Measures, across the whole session |
| --- | --- | --- |
| `--budget-tokens N` | `NARE_BUDGET_TOKENS` | `input + output + cache_read + cache_write` |
| `--budget-usd X` | `NARE_BUDGET_USD` | `usage.cost` |

Named apart from the existing per-turn `--max-tokens` on purpose.

The check runs **after a step completes**, tool calls included, so every
`tool_use` has its `tool_result`. When a limit is reached and the step did not
leave the session `done` or `blocked`, the session ends with `status=error`,
`stop_reason="budget"`, and an `error` naming the limit and the figures. A
finished answer wins over an exhausted budget. A budget stop leaves a valid
session, so `--resume` with a larger budget continues it.

Budgets count the session's total, including turns from before a resume. That
is the semantics Conductor needs across feedback cycles.

`--budget-usd` with no configured prices cannot be validated at startup,
because whether the backend reports cost is only known from the first reply.
The run starts; the first turn whose cost is `None` ends it with
`stop_reason="budget"` and

    --budget-usd cannot be enforced: cost for this backend is unknown; set NARE_PRICE_IN and NARE_PRICE_OUT

The exposure is one turn.

## 7. Contract

This stays contract version 1.

- New `stop_reason` values `context` and `budget`, both with `status=error`
  and exit 1. `nare contract` does not enumerate stop reasons, and
  `docs/contract.md` counts new values and fields as additive.
- New fields `usage.cost` and the session's `last_input_tokens`, both with
  defaults. The session version is unchanged: `loads()` fills defaults for a
  file without them.
- `docs/contract.md` gains a list of the stop reasons a caller can see. #8
  turns that list into a branchable outcome.

The unmerged `feat/ask-only-when-ambiguous` branch moves the contract to 2.
The two changes are independent; whichever merges second rebases.

## 8. Failure handling

| Case | Behaviour |
| --- | --- |
| A price, budget or context-window value that does not parse, or is not greater than 0 (prices may be 0) | exit 2 before starting, naming the value |
| Window discovery fails, times out or finds nothing | `None`, then the default, one line on stderr |
| A cost header missing or malformed | that turn's cost is `None` |
| The provider rejects a request as too long anyway, because the character estimate ran low | unchanged: the transport's existing mapping to `max_tokens`, `status=error` |

The last row is a known limit of a `len / 4` estimate. Conservative defaults
keep it rare; this spec does not add a retry.

## 9. Testing

All deterministic, no network.

- `test_compact.py`: elision order, every protected block, the 80/60
  thresholds, every `tool_use` still paired afterwards, the `context` stop, and
  a session with `last_input_tokens == 0`.
- `test_loop.py` with `FakeProvider`: compaction mid-run, a resumed session
  compacting on its first turn, both budget stops, `done` winning over budget,
  cost precedence (reported, then priced, then unknown), unknown propagating
  into the total, and the unenforceable `--budget-usd` stop.
- `test_transport.py` through `httpx2.MockTransport`: both cost headers and
  malformed values, model-info discovery and each of its failure cases, and
  the anthropic raw-response path.
- `test_cli.py`: flag and environment precedence for every new setting, and
  their exit-2 cases.

`FakeProvider` gains a scripted `context_window()` and scripted `cost` on its
replies.

One benchmark case whose tool output overflows a 32000 token window, showing a
small local model finishing where it previously stopped. Before trusting a run
of it on `ada/qwen3-14b`, probe that model's tool calls; on 2026-09-30 its
JSON mode returned `{}` for every call.

## 10. Files

- New: `src/nare/compact.py`, about 80 lines.
- Changed: `loop.py`, `session.py`, `transport/__init__.py`,
  `transport/anthropic.py`, `transport/openai.py`, `cli.py`.
- Docs: `docs/contract.md` (stop reasons), `docs/architecture.md` (compaction
  under known ceilings), `README.md` (new flags and variables).

Roughly 250 to 300 lines of source, plus tests.

## 11. Out of scope

- Model-written summaries. See section 12.
- #8 terminal outcomes, next.
- Streaming (#27).
- A bundled price table. It would go stale and miss every proxy alias.
- Window discovery on direct vendor endpoints.
- Reading cost from a response body.

## 12. Future: summaries

If benchmarks show runs reaching the `context` stop, a second tier runs inside
`compact()` after elision and before that stop: the oldest turns outside the
protected set are replaced by one model-written summary message. It costs a
call and can lose a detail that mattered, and the models nare currently runs
against are small, which is why it is not the first tier. `compact.py` marks
the hook point with a `ponytail:` comment.
