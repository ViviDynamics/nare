# Transport: retry transient provider failures within a run, and make the request timeout configurable

Issue #134

## Scope

In:

- A model request that fails transiently is resent inside the run, on both
  rails, streamed or not, constrained (schema correction, tool-free run) or
  not. Transient means: a connect error, a read or write timeout, a
  connection reset or dropped mid-response (including a stream that ends
  before its completion marker), and HTTP 429, 502, 503, 504 and 529. The
  anthropic rail also treats HTTP 500 and an `overloaded_error` sent
  mid-stream as transient, as its SDK did before.
- A retry budget the caller sets: a count per model request and a total
  number of seconds of waiting per model request. Defaults 3 and 60. Zero
  retries turns retrying off. Backoff is exponential from 2 seconds,
  doubling, capped at 30 seconds per wait; a `Retry-After` in seconds
  lengthens a wait. A retry whose wait would exceed the remaining seconds is
  not made.
- `--request-timeout SECONDS` (env `NARE_REQUEST_TIMEOUT`) sets the HTTP
  timeout on both rails; default 600 as today. `--request-retries N` (env
  `NARE_REQUEST_RETRIES`) and `--request-retry-seconds SECONDS` (env
  `NARE_REQUEST_RETRY_SECONDS`) set the budget. An invalid value exits 2.
- A failed attempt adds no turn and no usage. A retry resends the same
  messages, so the request bytes are the same.
- A `progress` event per retry with `detail.retry` (`attempt`, `retries`,
  `reason`, `status`, `wait`, `error`), a cumulative `retries` field on the
  session (default 0) and on the `result` line.
- A request that exhausts its budget, or fails permanently (401, 404, a
  refused schema, anything unclassified), ends the run as today:
  `status=error`, `stop_reason` null, the last error.
- The anthropic SDK's own silent retries are turned off (`max_retries=0`), so
  the caller's budget is the only one and zero means zero.
- `nare contract` advertises `request_retries` and `request_timeout`.
  Docs: docs/contract.md, docs/architecture.md, README.

Out:

- Fallback models or endpoints (issue: out of scope).
- Retrying a completed answer that failed the schema (the correction turn).
- Retrying a mid-stream provider error the server sent as data on the openai
  rail, whose meaning nare cannot tell from the frame.
- Making an unstreamed turn's retry events live: an unstreamed step's events
  reach the caller when the step ends, as every other event of that step
  does. Each event's timestamp says when it happened. On a streamed turn they
  are live.

## Assumptions

- Additive under the version policy: new flags, a new progress detail key, a
  new result field and a new session field with a default. A run whose
  provider failed transiently may now complete instead of erroring; the
  meaning of every status is unchanged, so the contract stays at version 2.
- The transport says what is transient (it knows its exceptions); the loop
  owns whether and how often to resend, because a retry is an event and a
  count, which live above the transport. The architecture doc is updated.
- The budget is per model request, not per run: a long run that meets one
  failure every few turns should not run out of retries on turn three. The
  session count is cumulative, like `turns`.
- Streamed text from an abandoned attempt has already gone out as `progress`
  and stays in the stream; the retry event marks where the next attempt's
  text starts. The live buffers are flushed before the retry event.
- The `structured_output` progress event is emitted once per turn, before the
  first attempt.
- No jitter: the wait in the event is the wait nare took, and tests stay
  deterministic.

## Tasks

- [x] 1. Classification: `transient()` in `nare.transport` names timeouts,
  connection failures, dropped streams and retryable statuses, and nothing
  else (401, 404, 400, unrelated errors): unit tests over each exception.
- [ ] 2. Transports raise classifiable errors: the openai rail's HTTP failure
  carries its status and `Retry-After`, a stream without its completion
  marker raises `StreamDropped`, the anthropic rail adds 500 and a mid-stream
  `overloaded_error`, and its SDK retries are off: tests per rail.
- [ ] 3. Loop retries: a fake that times out twice and then answers completes
  with two retry events, the answer's usage only and one turn; 401/404 not
  retried; budget exhaustion ends as today; zero retries; seconds budget;
  `Retry-After`; streamed, unstreamed and constrained turns.
- [ ] 4. Session and result: `retries` saved and loaded, older files load as
  0, the result line carries it.
- [ ] 5. Request timeout: `request_timeout` reaches both rails' HTTP calls; a
  loopback server slower than the timeout is retried and then answers, on
  both rails, through the real CLI.
- [ ] 6. CLI flags and env: valid values reach the run and the transport,
  invalid values exit 2.
- [ ] 7. Contract discovery and docs: `nare contract` keys, docs/contract.md,
  docs/architecture.md, README.
