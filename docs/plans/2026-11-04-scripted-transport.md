# A scripted transport, so callers can test without a key

Issue #9

## Scope

In:

- `nare run --provider fake --replay FILE` (and `nare tui`, which shares the
  flags) runs the real CLI, loop, tools, persistence and rendering against a
  scripted list of replies. No key, no network, no vendor import.
- The script is JSON: `{"replies": [...]}`. A reply carries `text`,
  `thinking`, `tool_calls` (`name`, `args`, optional `id`), `stop_reason`,
  `usage` and `cost`, or is `{"error": "..."}` alone, which fails that turn as
  a transport error does.
- The script is validated at startup: an unreadable or malformed file exits 2
  before anything runs, naming the reply and the field. A run that asks for
  more replies than the script holds ends `error`, exit 1.
- `ReplayTransport` is public (`nare.ReplayTransport`, and
  `make_transport("fake", replay=...)`), for a caller that imports nare.
- Fixtures for done, blocked, error and truncation ship in `docs/replay/`,
  documented in `docs/contract.md`, and nare's tests run every one of them
  through a real subprocess, so the examples cannot rot.

Out:

- Recording real sessions into fixtures (out of scope in the issue).
- Schema-constrained decoding and image input on the fake rail: the fake
  declares neither, so `--structured-output` is refused exactly as on the
  anthropic rail. `--schema` validation works, because it is the loop's.
- Replacing the in-tree test doubles (`tests/fake_provider.py`,
  `tests/budget_probe.py`): they script things a caller has no use for
  (recording calls, measuring input).

## Assumptions

- Additive under the contract version policy: a new provider value and a new
  flag keep contract version 2. The script format joins the contract under the
  same rules: a new optional field keeps the version, removing or renaming one
  bumps it.
- A scripted error raises `RuntimeError`, so the run ends with
  `error: "RuntimeError: <text>"` and `stop_reason: null`, the same shape the
  real transports' own `RuntimeError`s produce.
- Default `stop_reason` is `tool_use` when the reply has tool calls, else
  `end_turn`. Default usage is zero in every category. Default tool call ids
  are `call_<reply>_<n>`, unique within one script.
- `--provider fake` without `--replay`, or `--replay` with another provider,
  exits 2: one without the other is a mistake, never a choice.
- With `--stream`, each reply's thinking and text arrive as deltas before the
  reply, as a streaming backend sends them.

## Tasks

- [x] 1. Script loading and validation: unit tests that each malformed shape is
  refused with the reply index and field named.
- [x] 2. `ReplayTransport.turn` and `stream_turn`: unit tests for order,
  defaults, the scripted error and an exhausted script.
- [x] 3. CLI wiring: `--provider fake`, `--replay`, the startup refusals, and a
  run with no key in the environment.
- [x] 4. Fixtures in `docs/replay/` run through a subprocess: done, blocked,
  error and truncated, each with its exit code, status and stop reason.
- [x] 5. Parity: the same script through `--replay` and through an injected
  transport produces the same events, result and session.
- [x] 6. Docs: README, `docs/contract.md`, `docs/architecture.md`.
