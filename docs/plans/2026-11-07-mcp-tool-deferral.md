# MCP tool definitions deferred behind a search step

Issue #242. Feature-gap item U4 in docs/plans/2026-11-05-feature-gap-decision.md.

## Scope
In: an opt-in setting (`--mcp-defer`, and `mcp_defer=True` on `nare.loop.run`) that keeps
MCP tool definitions out of the request until the model asks for them. Three fixed
meta tools (`mcp_search`, `mcp_load`, `mcp_call`) stand in for them. The loaded set is
recorded in the session and restored on resume when the definition still matches.
Resume detection of the deferral setting and of changed deferred definitions. Contract
documentation. A live token-saving check on one Anthropic-style and one OpenAI-style model.
Out: any automatic threshold (see Assumptions). Caller tools (`nare.Tool`, `--command-tools`)
are never deferred. Built-in tools are never deferred. Compaction is untouched (Sam, #42-#44).
Out: a provider-native tool-search feature (Anthropic `defer_loading`). It is Anthropic
only and would split the two rails. The TUI does not get a toggle in this change.

## Assumptions
- **Default off.** Deferral costs one extra turn per MCP tool use, and it only pays when
  the definitions are large. Without a measured threshold, switching it on by default
  would change every MCP run's request and cost. Off keeps every existing run byte-identical.
- **Search, not a name list.** A list of names must live in the system prompt or a tool
  description, both of which are sent every turn and change whenever the server set
  changes. A search step keeps the names out of the request entirely: they appear only
  as tool results at the tail of the transcript.
- **The tools array never changes during a run.** Anthropic signed thinking is bound to the
  tools array, and the vendor rejects a request whose tools differ from the ones the
  thinking was made with. Appending a loaded tool to the array mid-run would break that
  and invalidate the cached prefix. So a loaded tool is called through `mcp_call`, and
  the array holds only built-ins, caller tools and the three meta tools, in a fixed order.
- **Policy still governs every call.** `mcp_call` resolves to the real tool name before
  dispatch, so the allowlist (`--tools`), the approval callback and the event text all
  see `alias__tool`, as they do today. A tool outside the allowlist is never searchable,
  loadable or callable.
- **Loaded means callable, not validated.** MCP arguments are not checked against the
  schema today, and this change keeps that. The loaded set only gates whether the model
  has seen the definition in this session.
- **Resume.** The session records `mcp_loaded` as {name: sha256 of the schema when loaded}.
  A resume keeps an entry only if the tool is still deferred and its digest still matches.
  A dropped entry is said in a progress event; the model loads it again.
- **Resume detection.** A record with `mcp_defer` only when deferral is active, so a run
  with deferral off writes the same policy record as before. Turning deferral on or off
  on resume changes the tools array, so it is reported as a prefix change and drops
  signed thinking. A changed deferred definition does not change the array, so it drops
  only the stale loaded entry and keeps signed thinking.
- **Contract stays version 3.** The flag, the session field `mcp_loaded` (omitted when
  empty), the progress detail `mcp_defer`, and the `describe()` block are additive, as the
  cut-off and tool-call-limit fields were. A session written with a non-empty `mcp_loaded`
  is not readable by a nare that predates this change, the same limit those additive
  fields already carry.
- **Bounds.** `mcp_search` returns at most 20 matches, sorted by name. `mcp_load` takes at
  most 8 names per call. Descriptions in search results are cut to 120 characters.

## Tasks
- [x] 1. Off is unchanged: a run without the flag sends the same tools array and writes the
  same policy record and session file as before (existing tests stay green; new test pins
  the recorded keys when off).
- [x] 2. Deferral sends no MCP definitions: with deferral on and a fake server of many
  tools, the offered array holds built-ins, caller tools and the three meta tools, and its
  serialized size drops by at least 80 percent versus off.
- [x] 3. Search: `mcp_search` matches by words in name or description, returns names and
  short descriptions, caps at 20, loads nothing.
- [x] 4. Load and call in one run: `mcp_load` returns full definitions and marks them loaded;
  `mcp_call` on a loaded tool reaches the server and returns its result; the same run's
  next turn still offers the identical array.
- [x] 5. Refusals: `mcp_call` on an unloaded deferred tool, on an unknown name, and on a tool
  the allowlist excludes returns an error result and never reaches the server or approval.
  A direct call to a deferred name by its own name is refused with the way to load it.
- [x] 6. Approval sees the real name: the approver is called with `alias__tool` and the
  call's arguments, and a denial comes back as an error result.
- [x] 7. Cache stability: across every turn of a deferred run the tools array is byte-equal,
  and each turn's message list begins with the previous turn's list unchanged.
- [x] 8. Resume: the loaded set is restored from the session file; a call after resume works
  without a new load; a changed definition drops only its entry and keeps signed thinking;
  turning deferral on or off on resume is a prefix change that drops signed thinking.
- [x] 9. CLI and contract: `--mcp-defer` is accepted, reaches the run, and appears in
  `nare contract`; docs/contract.md documents the flag, the three tools, the session field
  and the event detail, and a test checks the documented contract names them.
- [x] 10. Live check on one Anthropic-style and one OpenAI-style model: record the input
  token saving and that the tool call succeeds. Spend ceiling $2.

## Review focus
Anything that changes the offered array between turns or across a resume; a loaded name
that survives a change it should not; a deferred tool reachable without the allowlist;
approval seeing a name other than the real one; the off path's bytes.

## Results

Offline suite: `tests/test_mcp_defer.py` against the fake many-tool stdio server
(`tests/mcp_server.py --many N`). The off path was also compared with origin/main
on a scripted run: the result lines, events and session file match, apart from
timestamps and random ids.

Live check, one request-size sample per turn (the team's gateway, 40 MCP tools,
one prompt that needs one call). "Prompt" is input plus cache read plus cache write.

| Rail and model | Deferral off, first request | Deferral on, first request | Turns off / on |
| --- | --- | --- | --- |
| Anthropic style, claude-haiku-5-5 | 20,440 tokens | 2,603 tokens | 2 / 4 |
| OpenAI style, gpt-6-luna | 10,892 tokens | 1,262 tokens | 2 / 4 |

The first request falls by about 87 to 88 percent. Deferral adds two turns
(search and load) for a tool that is used once, so the whole run is cheaper only
when the definitions are large. On the Anthropic rail, turns after the first read
the cache: cache reads of 2,599, 2,792 and 3,034 tokens, with writes only for the
new suffix, which shows the tools array stayed fixed. The gateway reported a
cost of $0.0018 for the two OpenAI-style runs; it reports none for the Anthropic
style, and both live runs stayed far under the $2 ceiling.
