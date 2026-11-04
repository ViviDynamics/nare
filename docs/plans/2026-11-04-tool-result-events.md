# Event stream: a call id on tool_use, and one event per built-in tool result

Issue #145

## Scope

In:

- Events gain an optional top-level `tool_use_id` field. It is set on every
  event that belongs to one tool call: `tool_use`, the new result event, and
  the existing read evidence and image `progress` events. Events that belong
  to no call leave it out of the JSONL line, so every other line is byte for
  byte what it was.
- One `progress` event per tool result, for every call a turn made, whose
  `detail` is `{"tool": <name>, "tool_result": {...}}`. For a built-in tool
  the record is `tool_use_id`, `is_error` and `chars` (the result content's
  length in characters), with no content. An MCP result keeps the event it
  has today (content included, redacted and capped) and gains `chars`, so an
  MCP call also has exactly one result event.
- Results nare writes itself count: an `ask` beside other calls, a call from
  a truncated turn, a call left unanswered when dispatch fails, and an `ask`
  rewritten to "not delivered". The event reports the result as it was
  appended to the transcript.
- `nare contract` advertises the record under `tool_results`. Docs:
  docs/contract.md, docs/architecture.md.

Out:

- Call counts by tool in `result` (optional in the issue). A caller counts
  result events; a second copy of the same numbers is a second thing to keep
  in step.
- Content on built-in result events. The transcript holds it; the event says
  how the call ended and how big the answer was.

## Assumptions

- The id is a top-level field, not a `detail` key, because a `tool_use`
  event's `detail` is the call's arguments, and an MCP tool can take an
  argument with any name. A top-level field cannot collide.
- The name is `tool_use_id`, the name the transcript, the read evidence
  record and the MCP result detail already use for the same value.
- Additive under the version policy: a new field on an event, a new
  `progress` detail key, a new discovery key. The contract stays at
  version 2.
- Result events are emitted once the turn's results are final, in call
  order, after the turn's evidence and image events. The `ask` rewrite
  happens before they are emitted, so no result event reports a value the
  transcript does not hold.
- The terminal shows the MCP result notice as before and skips the built-in
  result events, as it skips evidence: the call block already shows the
  outcome.

## Tasks

- [ ] 1. `Event.tool_use_id`, left out of the wire form when unset: a plain
  event serializes to the four fields; one with an id to five.
- [ ] 2. `tool_use` events carry the call id: a scripted `bash` call's
  `tool_use` event has `tool_use_id` equal to the call id.
- [ ] 3. One result event per call: `bash`, `edit`, `write`, `read` calls
  each produce one result event matching their id, with `is_error` and
  `chars`, and no content. A failed `edit` reports `is_error: true`.
- [ ] 4. Synthetic results: `ask` beside other calls, a truncated turn, and a
  lone failed `ask` beside a successful one each yield one result event with
  the transcript's `is_error`.
- [ ] 5. MCP: one result event per MCP call, content kept, `chars` added.
- [ ] 6. Evidence and image events carry `tool_use_id`.
- [ ] 7. CLI JSONL: every `tool_use` line's id matches exactly one result
  line; a plain line has no `tool_use_id` key.
- [ ] 8. `nare contract` `tool_results` key, contract doc, architecture doc,
  terminal skip.
