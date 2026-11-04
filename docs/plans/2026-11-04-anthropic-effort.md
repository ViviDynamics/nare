# Anthropic transport: --effort sends a thinking budget that current Claude models reject

Issue #135

## Scope

In:

- On the anthropic rail, `--effort LEVEL` sends `output_config: {"effort": LEVEL}`
  with `thinking: {"type": "adaptive"}`, and no `budget_tokens`.
- `--effort` accepts `low`, `medium`, `high`, `xhigh` and `max` on both rails. A
  model that does not take a level answers with its own error.
- Older models that still need a fixed budget (Haiku 4.5, Sonnet 4.5, Opus 4.5)
  are reached by an explicit caller choice, `--thinking-budget TOKENS`
  (anthropic only), which sends `thinking: {"type": "enabled", "budget_tokens":
  N}`. With `--effort` too, both are sent, which is what Opus 4.5 takes. No table
  of model names.
- Default `max_tokens`: with `--effort`, 64000 when streaming and the model's
  non-streaming ceiling (21333, or lower per model) otherwise, because adaptive
  thinking counts toward `max_tokens`. With `--thinking-budget`, budget + 8192 as
  the budget path did, and `max_tokens` must exceed the budget.
- The session records what was sent (`effort`, `thinking_budget`), and
  `nare contract` lists the levels and the per-rail request shape.
- docs/contract.md and the README describe effort per rail; the openai rail
  passes the value through as `reasoning_effort`, and its meaning is the server's.

Out:

- Choosing effort per task or per model (the caller's job, per the issue).
- Benchmark effort sweeps (needs the benchmark changes first, per the issue).
- `display: "summarized"` or other thinking display options.

## Assumptions

- Verified against Anthropic's effort and adaptive thinking pages (read
  2026-11-04) and the installed SDK 1.6.0 types (`OutputConfigParam.effort`
  takes the five levels; `ThinkingConfigAdaptiveParam` is `{"type":
  "adaptive"}`). `budget_tokens` returns a 400 on Opus 4.7 and later, Sonnet 5
  and 5.5, Haiku 5.5 and Fable 5 and 5.1, and is deprecated on Opus 4.6 and
  Sonnet 4.6.
- Adaptive thinking is sent with effort because Opus 4.7 and 4.8 run without
  thinking when `thinking` is omitted; on the models where thinking is always
  on, `adaptive` is the same as omitting it.
- Additive under the version policy: a new flag, new accepted values, new
  session fields with defaults and a new discovery key. The contract stays at
  version 2.

## Tasks

- [x] 1. Anthropic request shape: `--effort` renders `output_config.effort` and
  adaptive thinking, for all five levels, unstreamed and streamed, and never
  `budget_tokens`: request-shape tests with no key.
- [x] 2. `--thinking-budget`: renders the enabled budget, alone or with effort,
  refuses a budget at or above `max_tokens` and a nonpositive value, and is
  refused on the openai rail.
- [x] 3. Default `max_tokens` per path, including a model's lower cap.
- [x] 4. The openai rail passes all five levels through as `reasoning_effort`.
- [x] 5. CLI: `xhigh` and `max` accepted on both rails; `--thinking-budget`
  parsed and validated; the session records what was sent.
- [x] 6. `nare contract` and docs.
