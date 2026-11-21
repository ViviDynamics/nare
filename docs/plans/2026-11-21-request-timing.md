# Per-request timing

Issue #315

## Scope
In: the time to headers, to the first generated chunk and to the last byte of
each model request, on its `cost` event's `detail.timing`; their sums on the
result line as `request_timing`; a `request_timing` contract entry; scripted
values on the fake rail.
Out: wall-clock `sent_at` timestamps; timing of failed attempts; any change to
what is sent or when; saving timing in the session file.

## Assumptions
- Measured in each rail around the HTTP request alone, on `time.monotonic`
  (read through `nare.transport.clock`, so tests replace it). So compaction,
  admission and capacity slot waits, capacity queue waits, retry backoff and
  failed attempts are all outside it, and a retried request reports the attempt
  that answered.
- `first_token_seconds` is the first chunk carrying generated content: text,
  reasoning or a tool call. A role-only chunk, `message_start`, an empty block
  start and a signature are bookkeeping. A tool-only turn streams no delta, and
  its first tool-call chunk is still timed, which is the case the issue names.
- Unstreamed requests report `first_token_seconds` null: the body is one
  response. To time the headers apart from the body, the openai rail sends its
  unstreamed request through `client.stream` and reads it whole, and the
  anthropic rail uses `with_streaming_response` in place of
  `with_raw_response`. Both send the same request bytes.
- A transport that sets no timing (a caller's own) gets `timing: null`. The key
  is always present on a `cost` event.
- The fake rail reports a reply's scripted `timing`, else zeros, with
  `first_token_seconds` null unstreamed, so offline tests are stable.
- The result line's `request_timing` is per invocation and not saved, so the
  session file is unchanged. Additive; contract stays 3.

## Tasks
- [x] 1. Tests first (`tests/test_request_timing.py`): openai unstreamed and
      streamed (a role-only chunk is not a first token, reasoning is), anthropic
      streamed (text, and a tool-only turn), anthropic unstreamed, an openai
      tool-only stream, a retried request that excludes the failed attempt and
      the backoff, a transport with no timing, the fake rail streamed,
      unstreamed and scripted through `main()`, malformed scripted timing, the
      contract entry and the docs.
- [x] 2. `Timing` and `RequestClock` in `nare.transport`; `Reply.timing`.
- [x] 3. openai, anthropic (and so bedrock) and fake rails set it.
- [x] 4. `_cost_event` carries it, for turns and summary requests.
- [x] 5. `RequestTimingTotals` sums it in `nare run`'s event loop for the
      result line.
- [x] 6. `request_timing` in `describe()`; docs/contract.md: a Request timing
      section, the replay key, the library Results row.

## Verification
- [x] `uv run pytest -q tests/test_request_timing.py`
- [x] Updated the four tests that pinned an exact `cost` detail.
- [x] Full build: ruff format, ruff check, mypy, `uv run pytest -q`.
