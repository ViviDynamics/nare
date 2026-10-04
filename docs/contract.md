# The machine contract

This is contract version 1.

A caller drives nare as a subprocess and turns three things into decisions: the
JSONL event stream on stdout, the session file, and the exit code. This document
is what a caller may rely on, and what nare promises not to change without
saying so.

Read the contract as data instead of parsing this page:

    nare contract

It prints the version, the exit codes, the statuses, the event types and the
redaction rule set this build speaks.

## Refusing a contract you do not speak

    nare run --contract 1 --yes "..."

When the caller's number and nare's differ, nare exits 2 before starting and
writes nothing to stdout. A caller that pins the version it understands never
mis-reads a stream from a newer nare; it fails on the first run instead, with
both numbers named on stderr.

The session file carries the same number. Resuming a session written under a
different contract is refused for the same reason.

## Prompt input

Exactly one optional prompt source is accepted: positional text, `--prompt-file
PATH`, or positional `-` for stdin. The `nare contract` object advertises these
routes under `prompt_input`; they are additive in contract version 1.

    nare run --yes --jsonl --prompt-file review-prompt.txt --session review.json
    nare run --yes --jsonl - --session review.json < review-prompt.txt

File/stdin content must be UTF-8. Decoding preserves CRLF, trailing newlines and
Unicode exactly; no trimming, compression or truncation occurs. An explicitly
empty source is valid. These prompts enter the same user message as positional
text, and saved sessions apply the same redaction rules to all input routes.
With `--resume PATH`, a nonempty prompt from any route is appended as a follow-up;
no source (or an empty source) resumes the existing transcript without new text.

Combining positional text or stdin with `--prompt-file` exits 2, naming both
sources on stderr before reading them. Unreadable files or invalid UTF-8 in
file/stdin also exit 2 with their source named. These errors produce no stdout,
model call or new session file. stdin is read through EOF; callers close the pipe
when finished. Source routes bypass argv's operating-system size limit and keep
prompt text out of argv. Model context limits still apply: a large prompt needs
an appropriate `--context-window`, rather than disabling or claiming to bypass
the context guard.

## The redaction rule set

Every event's `text`, and whatever text a caller pipes through:

    nare redact < its-own-log.txt > its-redacted-log.txt

is redacted by the same rule set. The rules, in the order they are applied, are
`anthropic-key`, `openai-key`, `github-token` and `credential-assignment`.
Matching is replaced with `[redacted]`.

The rule set has an identity, printed by `nare contract` under `redaction`: a
`version` and the rule names. A caller that also redacts its own published
output runs `nare redact` instead of copying the patterns, and reads the
identity back so a drift between its nare and its assumptions is detected
rather than silently ignored.

The route is a filter: text on stdin, redacted text on stdout, no arguments
and no state. It exits 0 with the redacted text on stdout. When stdin is not
readable UTF-8 text it exits 1, explains on stderr, and writes nothing to
stdout. It is linear in the input size: a megabyte of command output is
redacted in milliseconds, not minutes.

## Exit codes

| Status | Exit | Meaning |
| --- | --- | --- |
| `done` | exit 0 | The run finished. `output` carries the answer when `--schema` was used. |
| `blocked` | exit 0 | The model used `ask`. `questions` carries what it needs. This is an outcome, not a failure. |
| `error` | exit 1 | The run started and failed. `error` says how, `stop_reason` says what ended it. |
| never started | exit 2 | No session existed: a bad invocation, an unreadable session file, or a refused contract. Nothing is written to stdout, so there is no `result` line. |

Every terminal status maps to exactly one code, and a test asserts this table
matches the code.

## The event stream

One JSON object per line on stdout, each with `type`, `text`, `detail` and
`timestamp`. The types are `progress`, `tool_use`, `thinking`, `cost`, `error`
and `output`. They match Conductor's `BackendEventType` so its adapter is a
constructor call rather than a mapping table.

The last line is not an event. It is the `result` object, carrying
`session_id`, `status`, `questions`, `usage`, `stop_reason`, `turns`,
`contract`, `nare`, `output`, `budget` and `error`. A caller that reads only one line should read
that one.

Secrets are redacted when an event is constructed, not when it is written, so
an unredacted event never exists.

## What changes the version

Contract version 1 covers the event types and their fields, the `result`
object's fields, the session file's shape, the exit codes above, and the
redaction rule set.

**These keep the version:** a new event type, a new field on an event or on the
`result` object, a new session field with a default, a new flag, a new status
value's behaviour being described more precisely without changing it.

A caller must therefore ignore fields and event types it does not recognise.
That is the price of additive changes not bumping the number.

**These bump it:** removing or renaming a field or an event type, changing what
an existing field means, changing an exit code, and changing when a status is
reported. Changing or removing a redaction rule, or changing what a rule
matches, bumps it too: the rule set is part of what an event's `text` means.

nare speaks one contract version at a time. There is no negotiation and no
compatibility mode: a caller pins a version, and a nare that speaks another one
refuses to run.

## Stop reasons

| stop_reason | Meaning |
| --- | --- |
| `end_turn`, `stop_sequence` | Provider ended the response; status determines completion |
| `tool_use` | Provider requested tools; may be working or blocked on ask |
| `max_tokens` | Provider truncated the response (including rejected context) |
| `refusal` | Provider refused to continue |
| `schema_violation` | Second invalid structured answer |
| `max_turns` | Invocation's turn count limit reached |
| `context` | Estimated context still exceeds the window after eligible elision |
| `budget` | Session token/USD ceiling reached, or USD accounting is unknown |
| null | No completed response for this failure, e.g. provider exception |

New stop reasons and additive usage/result/session fields keep contract 1.

## Budgets and partial results

`usage` contains cumulative `input`, `output`, `cache_read`, `cache_write`, and
`cost` (dollars or null). Token categories are disjoint. For OpenAI,
`input=prompt_tokens-cached_tokens`, `cache_read=cached_tokens`, and cache_write
is zero. Anthropic reports uncached input, cache reads and cache creation
separately. Completion/reasoning tokens reported in output are not counted
again as a separate category. The cumulative total is the sum of all four token
fields, not the last turn and not the size of the stored transcript. Missing
provider counters normalize to zero; this is reported accounting, not proof of
a strict cap when a provider omits usage.

Every result carries `budget`: configured `tokens` and/or `usd` limits (keys
absent when unset), plus `used_tokens` and `used_usd` (null when cost is unknown).
A budget `error` event has `detail.budget` with the same fields and `detail.usage`
with actual cumulative counters. The result is authoritative for terminal
status. In particular, done/blocked crossing a ceiling still exit 0 and retain
actual usage, even above the limit.

A nonterminal crossing step finishes tool dispatch before stopping. Its tool
uses/results, progress, thinking and cost events remain in the emitted JSONL;
the final error and result follow them. `--session` saves the transcript,
usage, limits and output atomically. Events are not stored inside the session;
the caller retains stdout. Context compaction replaces eligible old tool-result
bodies with stubs, preserving pairs and assistant text; prior streamed events
are unaffected.

Budget exhaustion exits 1 with `status=error`, `stop_reason=budget`. Callers
must branch on that pair, not on the free-text error or exit 1 alone. Provider
exceptions also exit 1 but use null stop_reason. After exhaustion there is no
further model call in that invocation.

`--resume` reopens a session for work but does not reset usage. Flag overrides
environment, environment overrides a persisted limit, and omission retains the
persisted limit. There is no flag to clear a persisted budget. If already at or
above a limit, resume emits a budget error/result and calls no model, including
when no flag was supplied or provider construction would fail for lack of credentials. Raising one limit does not remove the other. All
active limits must allow continuation. USD usage with a historical unknown
cannot be repaired by adding prices later. `--max-turns` remains per invocation.
A completed/blocked crossing result is successful; reopening it with resume
still applies the cumulative pre-call guard.

When a schema was supplied, nare retains the latest complete JSON document that
passes that schema in `result.output` and session `output`, including a document
emitted alongside tool calls. This represents available validated data, not a
complete review when status is error. Invalid later text does not discard an
earlier valid document. There is no invented empty findings list, automatic
merging of separate documents, or promotion of incomplete JSON. Without a
schema, output remains unset unless retained from a previously validated run;
callers can recover raw assistant text from the transcript/progress events and
validate it themselves. Pass the schema again on continuation. A caller retains
all documents it wants to aggregate from its own event stream.

For example:

```python
result = json.loads(events[-1])
if result["status"] == "error" and result["stop_reason"] == "budget":
    partial_findings = result["output"]  # may be None; already validated if present
    actual_tokens = result["budget"]["used_tokens"]
    effective_limit = result["budget"].get("tokens")
    # Store available findings as partial and keep the session/JSONL artifacts.
```

The approved design enforces budgets after steps, so a crossing step can
overshoot by its actual token/USD usage. `--max-tokens` bounds only response
output, not input/cache spend. If Scrutare SPEC requires a strict whole-review
ceiling, this remains a mismatch: Scrutare must account for overshoot and
success on crossing turns in its own allocation policy. nare does not allocate
budgets across personas or orchestrate reviews.

## MCP tool sources

`nare run --mcp-config servers.json --yes --jsonl "inspect the application"`
connects explicit MCP sources for the duration of the invocation. The JSON file
is an object keyed by server alias:

```json
{
  "browser": {"url": "http://sandbox:8080/mcp", "timeout": 30},
  "local": {"command": "python", "args": ["server.py"], "env": {"APP_MODE": "read-only"}}
}
```

Remote sources use Streamable HTTP; stdio sources are child processes. Each
source specifies exactly one of `command` or `url`. Optional `headers` apply
only to HTTP; `args` and `env` apply only to stdio. Unknown fields and invalid
values exit 2 before starting. `timeout` is a positive finite number of seconds,
default 30, bounding the complete initialization/discovery phase per server and each
complete tool call, including blocked transport writes. Cancellation may add up
to 0.1 seconds of courtesy-cancel grace; connection/process cleanup follows.

Model-facing tool names are `alias__tool`, not `alias.tool`. Names must fit the
providers' 64-character ASCII letters/digits/underscore/hyphen restriction.
Aliases start with a letter and cannot contain `__`. Discovery fails on duplicate
or incompatible names. Paginated `tools/list` is supported.

Omitting `--tools` permits built-ins and all configured MCP tools. An explicit
allowlist permits only its named tools:

```sh
nare run --yes --jsonl --mcp-config servers.json \
  --tools browser__observe,browser__navigate "inspect the application"
```

`--tools none` excludes everything. All external calls pass through the same
approval callback as built-ins. `--root` confines built-in file operations; an
MCP server enforces its own filesystem/application boundaries. A CLI caller
approves calls through `--yes`; library callers supply `approve` to `run`.

Library callers pass `mcp_servers={"browser": Server(url="http://sandbox:8080/mcp")}`
to `nare.loop.run`, importing `Server` from `nare.mcp`. A supplied `Policy` keeps
its explicit allowlist; `Policy(pending=frozenset({"browser__observe"}),
tools=frozenset({"browser__observe"}))` allows a name awaiting discovery.
Without a policy all discovered tools are allowed. Each invocation rediscovers
schemas; supply configuration and policy again on resume.

Startup/discovery/connection cleanup failure emits an error and final result
with `status=error`, `stop_reason=mcp`, exit 1. No model call occurs on startup
failure. A failing tool call returns an ordinary `is_error` tool result so the
model can adapt. `tool_use` events identify calls and redacted arguments;
`progress` events carry redacted `detail.tool` and `detail.tool_result`, including
content and error status. Text and structured MCP results are retained; other
content blocks are represented as JSON, rather than interpreted as model images.
The standard tool output cap applies. Session transcripts preserve results in
the same way as built-ins; raw transcripts can contain secrets and should be
handled as private artifacts.

Connections and child processes are closed on completion, error, cancellation
or explicit generator close. Library consumers that stop iteration early must
`await stream.aclose()` in the same task that began iteration. Configuration,
headers and environment values are not saved in the session. nare passes only
the SDK's minimal OS environment and configured `env` to stdio servers; model API
keys are not inherited. HTTP uses only explicitly configured headers. No model
sampling callback or credential sharing is enabled. Remote servers remain owned
by their hosts; nare terminates its connection, not their server process.

## Streaming model turns

```sh
nare run --yes --jsonl --stream --provider openai \
  --model YOUR_MODEL --base-url https://YOUR_PROXY/v1 \
  --budget-tokens 50000 --session review.json "review the patch"
```

`--stream` is opt-in on both OpenAI-compatible and Anthropic transports. Library
callers construct a transport with `streaming=True`; `run()` emits live
`progress` and `thinking` events while the model turn is still running. Direct
`transport.turn()` still returns the same completed `Reply` as nonstreaming.
The additive flag and streaming event timing keep contract version 1.

Live text is buffered at word boundaries so a secret split across provider
chunks reaches the same redactor as a whole secret. An unfinished word or
credential assignment can wait for another chunk or the end of the stream.
Events carry sanitized text; raw provider deltas are not JSONL events.

Tool calls are assembled from deltas and dispatched only after the reply is
complete, using the existing allowlist and approval. Incomplete or nonobject
JSON arguments return a named tool error without invoking the tool, even if
a provider marks its stream complete; reported usage is retained. Usage is normalized once
from final provider usage, including disjoint cache counts; repeated usage
snapshots are not added together. Cost uses the same response headers or price
configuration as nonstreaming. Session transcripts and final outcomes are the
same for equivalent streamed and nonstreamed replies.

OpenAI-compatible streaming requests ask for `stream_options.include_usage`.
Missing/incomplete final usage, stop reason or stream completion marker is a
provider failure, rather than a successful turn with invented zero usage.
Anthropic streaming requires `message_stop` and final output usage. Streaming
permits Anthropic output limits above the SDK's nonstreaming ceiling; retain
provider/model output limits and require max output to exceed a thinking budget.

Budget enforcement remains **after each complete step**, including tool
results. Streaming does not make the cumulative ceiling strict: the crossing
turn can overshoot, and done/blocked wins exactly as in nonstreaming. Actual
usage remains visible on completion. After a budget stop there is no subsequent
model call.

A dropped stream is not retried or resumed mid-turn. Already emitted progress
and completed transcript turns remain available; incomplete tool arguments
are never dispatched, and an incomplete assistant turn is not saved as a
completed turn. Usage that the provider did not report cannot be recovered or
invented; the saved accounting includes completed replies only. Callers capture
JSONL to retain live progress and can resume the completed transcript explicitly.
Schema-valid output from previous completed replies remains available.

Streaming helps an idle proxy only while the provider sends bytes frequently
enough, including SSE heartbeats. A provider that sends nothing before the
proxy's deadline can still time out. Offline actual-CLI tests use a loopback
proxy that enforces a 0.3-second idle window: nonstreaming gets HTTP 524, while
both streaming rails emit progress before finalization and survive a 0.75-second
withheld completion with heartbeats. This is conditional traffic evidence, not
an assurance about every model/proxy combination.

## Read-only image input

    nare run --yes --jsonl --contract 1 --provider openai --model VISION_MODEL \
      --image-input --tools read --root "$PWD/images" --session image.json \
      "Read chart.png and describe it."

`read` recognizes PNG/JPEG image bytes (including extensionless files) and image
file extensions. Text reads behave as before. Image reads return the whole image;
text `offset`/`limit` do not slice images. The same allowlist, approval and root
resolution checks run before opening the image. No write, edit, bash or screenshot
permission is added by `--image-input`.

The flag declares model capability; nare does not guess from model names or make
a live capability probe. Both built-in transports implement the image wire format.
Omit the flag for a text-only model. A disabled read returns `is_error=true`, naming
the selected transport/model and image input, so the model can continue. A caller
must select a capable model before enabling the flag; declaring a text-only model
capable can still produce a provider error. Custom transports opt in with
`supports_image_input=True` and `image_input=True`, implement native serialization,
and expose `model` for diagnostics.

Pillow validates PNG/JPEG decoding. Images are capped at 4,194,304 compressed bytes,
20,000,000 pixels, and 8,000 pixels on either edge. Unsupported formats, invalid
images and exceeded limits are ordinary named tool errors. Bytes are never
truncated or resized. `nare contract` advertises the flag, formats and caps under
`image_input`.

The saved `tool_result.content` remains a text string. An image adds an optional
`image` field with `type=image`, `width`, `height`, and `source` containing
`type=base64`, `media_type`, `data`. This new optional field, flag and discovery field
keep contract version 1; existing text, event, result and exit-code meanings remain
unchanged. Image progress includes dimensions and MIME type, never the binary
payload. Image data is opaque base64 preserved exactly in saved sessions; text
fields still use the same redaction rules. Text redaction does not inspect image
pixels or metadata embedded in the binary file.

Resume with `--image-input` to resend saved images to a capable model. Without it,
the loop and native serializers withhold historical image bytes and replace them with a named
explanation; the saved originals remain available. No image file needs to remain
on disk for those historical results. When images are enabled, an older image can be elided by context
compaction, preserving the tool pairing and instructing the model to rerun `read`.

Withheld images remain stored even if compaction elides accompanying text.

The context estimate excludes base64 string length and adds four estimated tokens
per 32×32 image patch, rounded up on each edge. This is a heuristic, not a provider
tokenizer or a universal upper bound. Later estimates calibrate to actual completed
provider input usage. Context estimation and compaction ignore withheld images;
a change in image capability starts fresh context calibration. The optional
`last_input_image_input` session field records that calibration capability.
Cumulative budgets account for actual provider input/output
and disjoint cache tokens, including image input, with the existing post-step
crossing-turn overshoot and done-wins precedence.

Anthropic sends the image inside native tool-result content; OpenAI-compatible
Chat Completions keeps text tool messages and attaches image URLs in a subsequent
user message after all tool results. See the provider protocols:
[Anthropic tool results](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls),
[Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).
