# Provider failure record

Issue #314

A run that ends on a failed model request reports `status: error`, a null
`stop_reason` and an `error` text. A caller branches on contract fields, never on
that text, so it cannot tell a refused request shape (400, 422) from a refused
credential (401, 403), a gateway failure after retries (502, 503, 504) or a
connection that never opened. Retry events already say this, but only for a
failure nare is about to resend.

- Name a failed request with the retry rule's own walk of the cause chain
  (`transport._kind`), shared by `transient()` and the new
  `provider_failure()`, so a retry event and the final record never disagree
  and no second classification exists. A transport's own `transient` hook
  (the anthropic and bedrock rails' 500 and `overloaded_error`) is honoured.
- `_with_retries` attaches the record to the exception it gives up on: the
  reason, HTTP status, whether it was transient, whether the request was a
  constrained turn, and the attempts made. The run's terminal handler copies it
  to `session.failure` and the terminal `error` event's `detail.failure`, and
  the `result` line carries it. A failure that did not come from a model
  request (nare's own) has no record, so `failure` is null there.
- Startup discovery (context window, prices) is a provider request too, and is
  classified the same way with one attempt.
- Only an enum, a status code, two booleans and a count. Never a body, a header,
  the error text or a credential. The `error` text is unchanged.
- Advertised in `nare contract` as `provider_failure`, additive: contract 3.
- The session file carries it; an older file loads it as null, and `reopen`
  clears it as it clears `error`.

No new retry rule, status set or stop reason belongs in this change, and no
change to which failures are resent.

## Verification

- [x] RED: the issue's reproduction (a constrained openai turn refused with HTTP
  400, `--request-retries 0`) through the real CLI, asserting the event, result
  and session record.
- [x] 400, 401, 403 and 422 on the openai and anthropic rails, plain and
  streamed, against a loopback provider; one request each, and a credential in
  the response body never reaches the record.
- [x] 502, 503 and 504 after two retries on both rails, plain and streamed:
  `transient` true, three attempts.
- [x] A connection that never opened on both rails, plain and streamed; a
  dropped stream on both rails.
- [x] A 403 on the bedrock rail, plain and streamed, through a mock HTTP client.
- [x] An unnamed provider failure has a null reason; nare's own failure and a
  successful run have no record; a failure after a completed turn and a retry
  counts that request's attempts.
- [x] Session round trip, older file, malformed value refused, resume clears.
- [x] `nare contract` describe test; contract version stays 3.
