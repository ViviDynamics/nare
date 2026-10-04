# Streaming turns through proxies

Issue #27

## Scope
In: opt-in CLI/library streaming on both providers; immediate progress/thinking; completed tool argument assembly; final usage, cost and stop reason; safe stream failure; Anthropic larger output caps while streaming.
Out: reconnecting/retrying a dropped stream, provider/model selection, changing proxy settings.

## Assumptions
- `--stream` selects streaming; the default remains nonstreaming for compatibility.
- The public Transport.turn protocol remains compatible with existing fake/custom transports. Streaming providers additionally expose streaming=True and stream_turn yielding Event or one final Reply.
- The loop emits live deltas without committing a partial assistant turn or dispatching partial tools. A finished turn has the same transcript and usage as nonstreaming.
- Budget checks remain post-step. Streaming failures preserve completed turns and emitted events; provider usage unavailable after a dropped stream cannot be invented.
- Streaming does not prevent idle timeout if the provider emits no bytes. Offline proxy tests establish survival only when traffic/heartbeats arrive within its idle window.

## Tasks
- [x] 1. SSE transport tests for both providers: text/reasoning deltas before final reply, split tools, cached usage exactly once, truncation, HTTP errors and stream resource cleanup. Add streaming transport methods and shared streaming types.
- [x] 2. Loop tests: live events before final chunk, stream/nonstream transcript equality, budget exhaustion and crossing completion, no tool dispatch or invented usage after a dropped stream. Integrate live event queue without changing step's awaitable API.
- [x] 3. Actual CLI subprocess tests through a deterministic loopback SSE/proxy fixture: progress before finalization, a turn spanning the idle timeout with traffic, saved session and usage. Add flag and contract documentation.
- [ ] 4. Full build/preflight, independent review, CI, merge and usable automatic release.

## Review focus
Missing final usage or stop markers; malformed/interleaved tool argument deltas; cancellation while queue consumer waits; secret split across live deltas; streamed done crossing budget with usage visible.
