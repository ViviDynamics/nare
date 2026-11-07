# The machine contract

This is contract version 3.

A caller drives nare as a subprocess and turns three things into decisions: the
JSONL event stream on stdout, the session file, and the exit code. This document
is what a caller may rely on, and what nare promises not to change without
saying so.

Read the contract as data instead of parsing this page:

    nare contract

It prints the version, the exit codes, the statuses, the stop reasons
(`stop_reasons`), the event types, the
built-in tool names (`tools`), the navigation limits (`navigation`), the read
evidence record (`evidence`), the tool result record (`tool_results`), the
system prompt routes (`system_input`), every `nare run` option (`flags`) and
the redaction rule set this build speaks.

## Refusing a contract you do not speak

    nare run --contract 2 --yes "..."

When the caller's number and nare's differ, nare exits 2 before starting and
writes nothing to stdout. A caller that pins the version it understands never
mis-reads a stream from a newer nare; it fails on the first run instead, with
both numbers named on stderr.

The session file carries the same number. Resuming a session written under a
different contract is refused for the same reason.

## Run flags

`nare contract` lists every `nare run` option under `flags`, keyed by name,
so a caller checks that a flag exists before it spawns a run instead of
comparing release numbers:

```json
"flags": {"--stream": {"takes_value": false, "choices": null},
          "--effort": {"takes_value": true,
                       "choices": ["low", "medium", "high", "xhigh", "max"]}}
```

`takes_value` is false for a switch and true for an option that takes an
argument; `choices` is the list of accepted values, or `null` when any value
of the option's type is accepted. The key is read from the argument parser
when `nare contract` runs, so it cannot drift from what `nare run` accepts.
Defaults are not listed: several come from the environment. The positional
prompt is advertised under `prompt_input`. A new flag is a new entry here and
keeps the contract version; removing or renaming one bumps it.

| Flag | Takes a value | Choices | What it does |
| --- | --- | --- | --- |
| `--prompt-file` | yes | | Read the task from a UTF-8 file ([Prompt input](#prompt-input)) |
| `--stream` | no | | Stream text and reasoning while each turn runs ([Streaming model turns](#streaming-model-turns)) |
| `--jsonl` | no | | Emit the JSONL event stream on stdout |
| `--yes` | no | | Approve every tool call; required for unattended runs |
| `--contract` | yes | | The contract version the caller speaks ([Refusing a contract](#refusing-a-contract-you-do-not-speak)) |
| `--schema` | yes | | JSON Schema file the final answer must satisfy ([Answer schemas](#answer-schemas)) |
| `--structured-output` | yes | `json_schema` | Constrain answers at decode time, on both rails ([Schema-constrained answers](#schema-constrained-answers)) |
| `--max-tool-calls` | yes | | Run at most this many tool calls in this invocation ([Budgets and partial results](#budgets-and-partial-results)) |
| `--max-corrections` | yes | | Let a failing `--schema` answer be corrected up to this many times in the run, default 1 ([Schema corrections](#schema-corrections)) |
| `--max-cutoffs` | yes | | Correct up to this many replies cut off at the output cap or by repeating one span, in this invocation ([Cut-off replies](#cut-off-replies)) |
| `--require-reads` | yes | | Hold the final answer until the run has read at least this many distinct files ([Read requirements](#read-requirements)) |
| `--require-read` | yes | | Hold the final answer until the run has read this path, relative to `--root`; repeatable ([Read requirements](#read-requirements)) |
| `--require-cited-reads` | yes | | Hold the final answer until the run has read each path the answer cites, found by a JSON pointer into the `--schema` answer; repeatable ([Read requirements](#read-requirements)) |
| `--timeout` | yes | | Wall clock for this invocation ([Run deadline and stop signals](#run-deadline-and-stop-signals)) |
| `--request-timeout` | yes | | HTTP timeout for each model request ([Provider retries and the request timeout](#provider-retries-and-the-request-timeout)) |
| `--request-retries` | yes | | Resend a model request after a transient failure, up to this many times |
| `--request-retry-seconds` | yes | | Longest wait between one request's retries |
| `--proxy-num-retries` | yes | | Send `num_retries` to an OpenAI-compatible proxy |
| `--cache-breakpoints` | no | | Mark prompt-cache breakpoints on the openai rail ([Prompt-cache breakpoints](#prompt-cache-breakpoints)) |
| `--provider` | yes | `anthropic`, `openai`, `fake` | Model provider |
| `--replay` | yes | | JSON script of replies `--provider fake` replays ([Scripted runs](#scripted-runs)) |
| `--model` | yes | | Model name |
| `--base-url` | yes | | Override the provider endpoint |
| `--temperature` | yes | | Sampling temperature; not accepted by the anthropic provider |
| `--max-tokens` | yes | | Output token cap per response |
| `--context-window` | yes | | Context window used for elision |
| `--budget-tokens` | yes | | Cumulative session token budget ([Budgets and partial results](#budgets-and-partial-results)) |
| `--budget-usd` | yes | | Cumulative session dollar budget |
| `--effort` | yes | `low`, `medium`, `high`, `xhigh`, `max` | Reasoning effort ([Reasoning effort](#reasoning-effort)) |
| `--thinking-budget` | yes | | A fixed thinking budget, anthropic only, for older models |
| `--system` | yes | | Replace the default system prompt ([System prompt input](#system-prompt-input)) |
| `--system-file` | yes | | `--system`, read from a UTF-8 file |
| `--append-system` | yes | | Add text after the system prompt |
| `--append-system-file` | yes | | `--append-system`, read from a UTF-8 file |
| `--tools` | yes | | Comma-separated tools the run may call, or `none` |
| `--root` | yes | | Confine file tools to a directory and run `bash` in it |
| `--bash-credentials` | no | | Let `bash` see the provider credentials nare reads ([The bash environment](#the-bash-environment)) |
| `--bash-allow` | yes | | Run a `bash` command matching PATTERN without asking; repeatable ([Bash command rules](#bash-command-rules)) |
| `--bash-deny` | yes | | Refuse a `bash` command matching PATTERN, even with `--yes`; repeatable ([Bash command rules](#bash-command-rules)) |
| `--bash-sandbox` | no | | Run each `bash` command under the OS sandbox: writes only in `--root` and a private temp dir, credentials unreadable, no network ([Bash sandbox](#bash-sandbox)) |
| `--bash-network` | no | | With `--bash-sandbox`, let `bash` use the network ([Bash sandbox](#bash-sandbox)) |
| `--resume` | yes | | Continue the session at a path. `nare tui --resume` with no path picks one of this directory's saved sessions; `nare run` needs the path |
| `--mcp-config` | yes | | JSON file of MCP servers ([MCP tool sources](#mcp-tool-sources)) |
| `--command-tools` | yes | | JSON file of tools this command line runs ([caller tools](#caller-tools)) |
| `--image-input` | no | | Declare that the model accepts image input ([Read-only image input](#read-only-image-input)) |
| `--session` | yes | | Write the session to a path |
| `--max-turns` | yes | | Stop after this many turns in this invocation |
| `--fork` | no | | With `--resume PATH`, write the continued session to `--session` (a new file) and leave PATH byte-identical ([Forking a saved session](#forking-a-saved-session)). `nare run` only |

A test fails when this table and the parser disagree, so a new flag is
documented here in the change that adds it.

## Prompt input

Exactly one optional prompt source is accepted: positional text, `--prompt-file
PATH`, or positional `-` for stdin. The `nare contract` object advertises these
routes under `prompt_input`; they are additive and keep the contract version.

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

## Scripted runs

A caller's tests can drive nare end to end with no key and no network:

    nare run --yes --jsonl --provider fake --replay script.json "..."

`--provider fake` replays the replies in `script.json`, one per model turn,
and talks to nothing. Everything above the transport runs for real: the
tools, approval, budgets, schema validation, the session file, the event
stream and the exit code. So the paths a real model will not produce on
demand, a blocked run, a truncated turn, a transport error, are reachable in a
test, and they look exactly as they would in production. Library callers get
the same thing as `nare.ReplayTransport`.

The script is a JSON object with a `replies` list. Each reply is an object
with any of these keys:

| Key | Meaning |
| --- | --- |
| `text` | The model's text for the turn |
| `thinking` | Reasoning, emitted as a `thinking` event |
| `tool_calls` | A list of `{"name", "args", "id"}`. `args` defaults to `{}`, `id` to `call_<reply>_<n>`. `ask` is a tool call like any other |
| `stop_reason` | One of `end_turn`, `tool_use`, `max_tokens`, `stop_sequence`, `refusal`, `repetition`. Defaults to `tool_use` with tool calls, else `end_turn`. `max_tokens` is a truncated turn; `repetition` is a turn stopped for repeating one span ([Cut-off replies](#cut-off-replies)) |
| `usage` | `input`, `output`, `cache_read`, `cache_write`, each a nonnegative integer, default 0 |
| `cost` | The dollars a backend reported for the turn, as a nonnegative number |

or is `{"error": "..."}` and nothing else, which fails that turn as a
transport error does: the run ends `error` with `stop_reason: null` and
`error: "RuntimeError: ..."`, exit 1. A run that asks for more turns than the
script holds fails the same way, naming the turn.

The whole script is checked at startup. An unreadable file, a key the table
does not name, or a value of the wrong type exits 2 before anything runs,
naming the reply and the field. `--provider fake` without `--replay`, or
`--replay` with another provider, exits 2 too. With `--stream`, each reply's
thinking and text arrive as deltas before the reply, as a streaming backend
sends them. The fake rail does not constrain decoding, so `--structured-output`
is refused on it; `--schema` validation is the loop's and works as usual.

Four scripts ship in [`docs/replay/`](replay/), one per outcome a caller has to
handle, and nare's own tests run each of them through a real subprocess:

| Script | Status | Exit | `stop_reason` |
| --- | --- | --- | --- |
| [`done.json`](replay/done.json) | `done` | 0 | `end_turn` |
| [`blocked.json`](replay/blocked.json) | `blocked` | 0 | `tool_use` |
| [`error.json`](replay/error.json) | `error` | 1 | null |
| [`truncated.json`](replay/truncated.json) | `error` | 1 | `max_tokens` |

The script format is part of this contract under the same rules as the rest:
a new optional key keeps the version, and removing or renaming a key, or
changing what one means, bumps it.

## System prompt input

`--system TEXT` replaces nare's default system prompt (`--system ''` sends none)
and `--append-system TEXT` adds to whichever prompt is in effect. Each has a file
route that keeps a long prompt out of argv:

    nare run --yes --jsonl --system-file persona.md --prompt-file task.txt \
      --session review.json

`--system-file PATH` and `--append-system-file PATH` are read like
`--prompt-file`: UTF-8, every byte kept, nothing trimmed. Each is exclusive with
its argv form; giving both exits 2, naming both on stderr. An unreadable file or
invalid UTF-8 exits 2 naming the flag and path. None of these errors writes to
stdout, calls a model or writes a session file.

The session file records the system prompt the run sent, after redaction, in
its `system` field: the text, `""` when no system prompt was sent, or `null` in a
file saved before nare recorded it. On `--resume`:

- with no system option (none of the four flags above), the recorded prompt is
  sent again, byte for byte, so the model keeps its instructions and a caching
  backend keeps its prefix. A secret in it reads `[redacted]` in the file and so
  on the resumed turn too, exactly as in the transcript;
- with a system option, the prompt is built from the flags exactly as for a new
  run. When it differs from the record, one `progress` event says so before the
  first turn, with `detail` `{"system_prompt": {"changed": true}}`, and the new
  prompt is recorded;
- with a `null` record, the flags decide as they always did (the default when
  none is given), and that prompt is recorded from then on.

A `system` field that is neither a string nor `null` makes the session file
malformed (exit 2). `nare tui` takes the same flags and honours the record. The
`nare contract` object advertises the routes under `system_input`. The flags,
the session field and the event detail are additive and keep the contract
version.

## The redaction rule set

Every event's `text`, and whatever text a caller pipes through:

    nare redact < its-own-log.txt > its-redacted-log.txt

is redacted by the same rule set. The rules, in the order they are applied, are
`anthropic-key`, `openai-key`, `github-token` and `credential-assignment`.
Matching is replaced with `[redacted]`.

Version 3 narrowed `credential-assignment`, so it removes secrets and leaves
code alone. A credential name is a whole word of a name, split at `_`, `-`, `.`
and camelCase: `token`, `auth`, `authorization`, `password`, `passwd`, `secret`,
`credential`, `credentials` or `api` followed by `key`, and an all-caps name
ending in one of them (`PGPASSWORD`). `authorId` is not one. The name must be
followed by `:` or `=` (with up to eight spaces either side) and a value that looks
like a secret: eight or more characters with a letter and a digit, either as one
bare token (a run up to the next space, quote, bracket, `:`, `,`, `;` or `=`, so
`P@ssw0rd!` is whole) or as one quoted word, whose JSON escapes are part of it.
Not a secret: a `${NAME}` or `$NAME` reference, a value starting with `-`, `+`,
`/`, `~` or `./`, a URL, a number such as `12345678e9`, a dotted member access
such as `self.token_value1` (a JWT is not one), a snake_case or kebab-case name,
or a CamelCase type name such as `Auth0Client`. A `Bearer` scheme
word redacts the value after it at any length, because it is a header in
practice. A `Basic` or `token` scheme word redacts only a token-like value (one
with a digit, a symbol, or 20 or more characters), so prose after it is not. A quoted value
keeps its quotes, so a redacted JSON document still parses.

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
| `blocked` | exit 0 | The model used `ask` on its own, in a session whose policy allows it. `questions` carries what it needs. This is an outcome, not a failure. |
| `error` | exit 1 | The run started and failed. `error` says how, `stop_reason` says what ended it. |
| never started | exit 2 | No session existed: a bad invocation, an unreadable session file, or a refused contract. Nothing is written to stdout, so there is no `result` line. |

Every terminal status maps to exactly one code, and a test asserts this table
matches the code.

## The event stream

One JSON object per line on stdout, each with `type`, `text`, `detail` and
`timestamp`. The types are `progress`, `tool_use`, `thinking`, `cost`, `error`
and `output`. The shape is nare's own and this contract versions it: a caller
that has an event type of its own maps these four fields onto it in its
adapter, and can rely on them not changing without a version bump. An event that belongs to one tool
call also has a top-level `tool_use_id` (see [Tool results](#tool-results));
every other event leaves the key out.

The last line is not an event. It is the `result` object, carrying
`session_id`, `status`, `questions`, `usage`, `stop_reason`, `turns`,
`retries`, `cutoffs`, `contract`, `nare`, `output`, `text`, `budget` and `error`. A caller
that reads only one line should read that one.

`text` is the final reply of a run that ended `done`: the text of its last
assistant turn, redacted as events are, so it is the `output` event's `text`.
It is there with or without `--schema` (with one it is the answer as written,
and `output` is the validated document). Any other status has `text` null.

Secrets are redacted when an event is constructed, not when it is written, so
an unredacted event never exists.

## Tool results

Every tool call the model makes appears twice in the stream: once as it is
asked for, and once as it ends. Both lines carry the provider's call id in a
top-level `tool_use_id`, outside `detail`, because a `tool_use` event's
`detail` is the call's arguments and a tool may take an argument of any name.

    {"type": "tool_use", "text": "edit", "tool_use_id": "c1",
     "detail": {"path": "src/db.py", "old": "6543", "new": "6544"}, ...}
    {"type": "progress", "text": "edit result: error", "tool_use_id": "c1",
     "detail": {"tool": "edit",
                "tool_result": {"tool_use_id": "c1", "is_error": true,
                                "chars": 41}}, ...}

The result event is a `progress` event whose `detail` is
`{"tool": <name>, "tool_result": <record>}`. Each `tool_use` event's id matches
exactly one result event, so a caller can count calls by tool and outcome, and
tell which ones failed, from the stream alone.

- `is_error` is the value the model was sent. Results nare writes itself count
  too: an `ask` beside other calls, a call from a truncated turn (`not run`), a
  call left unanswered when dispatch fails, and an `ask` that was not
  delivered all end in a result event with `is_error: true`.
- `chars` is the length in characters of the result content the model was
  sent, after the output cap.
- A built-in tool's record has no content; the transcript holds it. An MCP
  tool's record keeps the content it has always carried, redacted and capped
  (see [MCP tool sources](#mcp-tool-sources)), and adds `chars`.

A turn's result events come after its evidence and image events, in call
order, once every call in the turn has ended. Read evidence and image
`progress` events carry the call's `tool_use_id` as well; they are not result
events, which are the ones with `detail.tool_result`.

The `tool_use_id` field, the result events and the `tool_results` key of
`nare contract` are additive and keep the contract version.

## What changes the version

Contract version 2 covers the event types and their fields, the `result`
object's fields, the session file's shape, the exit codes above, and the
redaction rule set.

**These keep the version:** a new event type, a new field on an event or on the
`result` object, a new session field with a default, a new flag, a new status
value's behaviour being described more precisely without changing it. The read
evidence record (a new `progress` detail key and a new `evidence` session
field) is one of these, and so are the tool result events and the
`tool_use_id` field.

A caller must therefore ignore fields and event types it does not recognise.
That is the price of additive changes not bumping the number.

**These bump it:** removing or renaming a field or an event type, changing what
an existing field means, changing an exit code, and changing when a status is
reported. Changing or removing a redaction rule, or changing what a rule
matches, bumps it too: the rule set is part of what an event's `text` means.

nare speaks one contract version at a time. There is no negotiation and no
compatibility mode: a caller pins a version, and a nare that speaks another one
refuses to run.

## Releases

The contract number says what a stream means. The release version says which
code produced it, and a caller records it (`nare --version`, the `result`
line's `nare`, the session file's `nare`) so a result can be reproduced. That
only works if a version keeps naming the same code, so:

- **A published release and its tag are never deleted or moved.** A version
  that installed once installs again, from the same commit.
- **A release's assets never change.** The wheel and the sdist are attached in
  the call that publishes the release, and the repository's releases are
  immutable, so GitHub refuses any later change to them. Each release's notes
  carry the SHA-256 of both files, so a caller can pin a hash as well as a
  version.
- **A version's container image is pushed once.** A rerun of the release
  workflow that finds `ghcr.io/vividynamics/nare:<version>` already published
  leaves it, and `latest`, alone.
- **Versions only move forward.** CalVer `YYYY.M.N` is minted from the
  existing tags, which are never deleted, so a later release always compares
  greater than an earlier one.

GitHub still lets a maintainer delete a release, which is why this is written
down rather than left to a setting. If a history rewrite ever leaves no other
way, the withdrawn versions are listed in [docs/releases.md](releases.md) with
when they went, the commit that ships the same code, and the release that
replaces each one. A version not listed there is published or was never minted.

## Changes in version 2

A turn that called `ask` alongside other tools ended `blocked` under version 1.
It now keeps working: the other calls run, each `ask` gets an error result
telling the model to ask on its own, and the loop takes another turn. A turn
of only `ask` calls still ends `blocked`, but only when every one of them
succeeded. An `ask` the policy refuses, or that fails, never blocks.

This changes when `blocked` is reported, so the version moved. No field, event
type or exit code changed.

A session written under version 1 cannot be resumed by this nare. A caller
holding a `blocked` version 1 session has to start that task again.

## Stop reasons

Every result names what ended the run in `stop_reason`, and a caller branches
on the `status` and `stop_reason` pair, never on the `error` text. Each value
has a kind, which says what a caller can do about it:

- `finished`: the model ended the run, and `status` says how.
- `limit`: a limit ended the run, so it says nothing about the task. A larger
  limit, or a resume, may finish it.
- `answer`: the model ended without an answer nare could accept.
- `stopped`: the caller stopped the run with a signal. It says nothing about
  the task and is not a failure; a resume continues it.
- `failure`: something broke, so the task was not checked at all.

| stop_reason | Kind | Status | Meaning |
| --- | --- | --- | --- |
| `end_turn`, `stop_sequence`, `tool_use` | `finished` | `done`, `blocked` | Provider completed the turn. The loop decides `done` or `blocked` from whether the turn called tools, not from this value |
| `max_tokens` | `limit` | `error` | Provider cut the response off at the output cap (`--max-tokens`), or at the context window, and the cut-off budget (`--max-cutoffs`) is spent |
| `repetition` | `limit` | `error` | The reply repeated one span until it was cut off, and the cut-off budget (`--max-cutoffs`) is spent |
| `max_turns` | `limit` | `error` | Invocation's turn count limit reached (`--max-turns`) |
| `context` | `limit` | `error` | Estimated context still exceeds the window after eligible elision |
| `budget` | `limit` | `error` | Session token/USD ceiling reached, or USD accounting is unknown |
| `timeout` | `limit` | `error` | Invocation's wall clock limit reached (`--timeout`; see Run deadline and stop signals) |
| `tool_calls` | `limit` | `error` | Invocation's tool call cap reached (`--max-tool-calls`), and the model called a tool again after being told to answer |
| `refusal` | `answer` | `error` | Provider refused to continue |
| `schema_violation` | `answer` | `error` | A structured answer that fails once the run's correction budget is spent (`--max-corrections`, default one correction) |
| `reads_required` | `answer` | `error` | A final answer came before the reads `--require-reads`, `--require-read` or `--require-cited-reads` demand, and the correction budget is spent |
| `no_answer` | `answer` | `error` | The model sent reasoning and no answer, which is what a proxy that drops one of them produces |
| `interrupted` | `stopped` | `error` | SIGTERM or SIGINT stopped the run (see Run deadline and stop signals) |
| null | `failure` | `error` | No completed response: the provider call raised (a transport failure, after any retries), or nare did |
| `mcp` | `failure` | `error` | An MCP server failed to start, list its tools, or close (see MCP tool sources) |
| `session_write` | `failure` | `error` | The run ended but `--session` could not be written, so it cannot be resumed |

`nare contract` prints the same table under `stop_reasons`, one object per
value with its `stop_reason`, `kind` and `statuses`, so a caller can read the
kinds instead of copying them:

```python
import json, subprocess

kinds = {
    r["stop_reason"]: r["kind"]
    for r in json.loads(subprocess.check_output(["nare", "contract"]))["stop_reasons"]
}
result = json.loads(events[-1])
if kinds.get(result["stop_reason"]) == "limit":
    ...  # ran out of room: retry with a larger limit, or resume
elif kinds.get(result["stop_reason"]) == "failure":
    ...  # could not check: report that, not a task failure
```

A stop reason this table does not list comes from a newer nare; treat it by
its `status`. New stop reasons and additive usage/result/session fields keep
the contract version.

## Run deadline and stop signals

    nare run --yes --jsonl --timeout 600 --session review.json "review the patch"

`--timeout SECONDS` bounds one invocation by wall clock, counted from when the
run starts, as `--max-turns` counts turns per invocation. It bounds the whole
run, retries included; `--request-timeout` bounds each model request. It is a positive
finite number; anything else never starts (exit 2). SIGTERM and SIGINT stop
`nare run` the same way. Either one:

1. stops the model call, tool call or MCP startup in flight, at once rather
   than after the turn. A tool call it interrupts is answered with an error
   result in the transcript, so the session resumes;
2. kills the `bash` commands this invocation started: the one running, and any
   a finished call left running in the background (a `[still running: ...]`
   result). Groups an earlier invocation of a resumed session left running
   were reported then and are left alone;
3. emits an `error` event whose `detail.stop` is `{"reason": "timeout",
   "timeout": <seconds>, "process_groups": [<pgid>, ...]}` or `{"reason":
   "interrupted", "signal": "SIGTERM" | "SIGINT", "process_groups": [...]}`,
   listing the groups it killed;
4. saves the session (with `--session` or `--resume`) at the last completed
   turn, and writes the `result` line with `status` `error` and `stop_reason`
   `timeout` or `interrupted`;
5. exits 1.

A second signal while nare is stopping acts as it would without nare's
handlers (SIGINT raises, SIGTERM kills), so a stop that hangs can still be
forced; that one writes no result. SIGKILL cannot be caught, which is why the
session is also saved after every turn. A SIGINT used to exit 130 with no
result line; it now exits 1 with one. `nare contract` advertises the flag, the
signals and the stop reasons under `deadline`. They are additive and keep the
contract version.

## Budgets and partial results

`usage` contains cumulative `input`, `output`, `cache_read`, `cache_write`,
`total`, `cost` (dollars or null), and `estimated` (true when any cost was
calculated from prices rather than reported by the backend). **`usage.total` is
the token count to use**: the sum of the four token fields, equal to
`budget.used_tokens` on every result, and what `--budget-tokens` counts. Adding
`input` and `output` alone undercounts any run that used the prompt cache. Each
`cost` event's `detail` has the same fields for its one turn, `total` included;
the session file keeps the four counters without `total`. Streamed cost headers are
ignored; configured prices override discovered model-info prices. With neither,
cost is unknown. Token categories are disjoint. For OpenAI,
`input=prompt_tokens-cached_tokens`, `cache_read=cached_tokens`, and cache_write
is zero. Anthropic reports uncached input, cache reads and cache creation
separately. Completion/reasoning tokens reported in output are not counted
again as a separate category. The cumulative total is the sum of all four token
fields, not the last turn and not the size of the stored transcript. Missing
provider counters normalize to zero; this is reported accounting, not proof of
a strict cap when a provider omits usage.

Every result carries `budget`: configured `tokens` and/or `usd` limits (keys
absent when unset), plus `used_tokens` and `used_usd` (null when cost is unknown),
and `used_tool_calls`, the tool calls this invocation ran, with `tool_calls`, the
`--max-tool-calls` cap, when one is set.
A budget `error` event has `detail.budget` with the same fields and `detail.usage`
with actual cumulative counters. The result is authoritative for terminal
status. In particular, done/blocked crossing a ceiling still exit 0 and retain
actual usage, even above the limit.

A nonterminal crossing step finishes tool dispatch before stopping. Its tool
uses/results, progress, thinking and cost events remain in the emitted JSONL;
the final error and result follow them. `--session` saves the transcript,
usage, limits and output atomically. Events are not stored inside the session;
the caller retains stdout. Read evidence records are the one copy kept (see
Read evidence). Context compaction replaces eligible old tool-result
bodies with stubs, then, if that is not enough, long contents of old `write`
calls and long `edit` texts, preserving pairs and assistant text. It removes
signed and redacted thinking from the first message it changes on, and the
progress event's `detail.compaction.thinking` counts the blocks removed; prior
streamed events are unaffected. The saved transcript is redacted, and from the
first message redaction changes on it leaves out signed and redacted thinking,
which the running session keeps. Each save writes
`stale_thinking_dropped: true`. A file without it, from an older nare, has that
thinking removed on load from the first message whose blocks sent to the model
hold `[redacted]` or a compaction stub.

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
With five or fewer turns remaining, the next user message and a progress event
carry a wrap-up warning. A run that offers no tools, built-in or MCP (for
example `--tools none`), gets the progress event only: every turn of it is an
answer, so the warning never reaches the model and the request does not
depend on the turn count. At the cap, one extra accounted request with tools
disabled asks for a summary, provided no token/USD ceiling has stopped the run. The request keeps the run's tool set and sets `tool_choice` to none, so a
call the model makes on it is refused and not run.
The outcome stays `error`/`max_turns` and includes a `continue` hint. The summary
counts in `turns` and usage; it does not grant another tool turn.
A completed/blocked crossing result is successful; reopening it with resume
still applies the cumulative pre-call guard.

`--max-tool-calls N` (`max_tool_calls` in the library) caps the tool calls one
invocation runs, as `--max-turns` caps its turns: one turn can hold many calls,
so a turn limit bounds exploration only loosely. A call counts when nare runs
it, built-in or MCP, whatever its result; a call nare never runs (one refused
at the cap, an `ask` beside other calls, one from a truncated turn) does not.
Past the cap a call gets an `is_error` result telling the model the limit is
reached and to answer now, and does not run; the calls before it in the same
turn ran. The model keeps its tools for one more turn. If it answers, the run
ends as that answer does. If it calls a tool again, those calls are refused the
same way and the run ends `error` with `stop_reason` `tool_calls`. A token or
USD ceiling crossed in the same turn wins, and the stop reason is `budget`. The
cap is not saved: a resume gets the full allowance again. The session's
`tool_calls` field counts the calls it has run over its life, as `turns` counts
turns; a file without it loads with 0. `nare contract` advertises the cap under
`tool_call_limit`. The flag, the stop reason, the budget keys and the session
field are additive and keep the contract version.

## Cut-off replies

A reply the vendor cut off at the output cap (`max_tokens`), or one that
repeated one span until it was stopped (`repetition`), is not the end of the
run while the cut-off budget lasts. `--max-cutoffs N` (`max_cutoffs` in the
library) allows N corrections in one invocation, and defaults to 2 on the
command line. Each correction tells the model to reason briefly, write each
record once and return the complete answer. The reply's tool calls do not run,
as they never do on a cut-off turn. An empty cut-off reply is not kept in the
transcript.

A reply is `repetition` when its last 2048 characters repeat one span of at
most 256 characters (at least eight copies), or when the same test holds with
each run of digits folded to one digit over the last 4096 characters, which
catches a loop that counts upward. A stream is read only until the loop shows,
so the output cap is not paid for. A reply that repeats and then closes is not
flagged, and a long list of records that differ only in their numbers can be
read as a loop (about 4 KB of them).

Each cut-off is a `progress` event with a `cutoff` detail (`kind`, the
`output_tokens` the reply used, `attempt`, `limit`, `corrected`). The result
records `cutoffs`, the session's count of cut-off replies over its life, and
`budget` records `cutoffs` (the cap) and `used_cutoffs` (cut-off replies this
invocation saw, the last one included). With the budget spent, the run ends
`error` with `stop_reason` `max_tokens` or `repetition`, which is a `limit`.
`--max-cutoffs 0` ends at the first cut, as nare did before this option.

**Decision: the command-line default stays at 2, and the contract stays at
version 2.** The flag, the stop reason, the event detail, and the result and
session fields are additive and keep the contract version.

Behavior change: on the command line, a run that used to end `error` at its
first cut-off (`max_tokens`) now gets corrected and continues. `--max-cutoffs 0`
restores the old outcome. The library default is still no budget.

When a schema was supplied, nare retains the latest complete JSON document that
passes that schema in `result.output` and session `output`, including a document
emitted alongside tool calls. This represents available validated data, not a
complete review when status is error. Invalid later text does not discard an
earlier valid document. There is no invented empty findings list, automatic
merging of separate documents, or promotion of incomplete JSON. Without a
schema, output remains unset unless retained from a previously validated run;
callers can recover raw assistant text from the transcript/progress events and
validate it themselves. Pass the schema again on continuation; a different one
is stated to the model (see [Correction by resume](#correction-by-resume)). A caller retains
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
output, not input/cache spend. A caller that needs a strict ceiling across
several runs must account for overshoot and for success on crossing turns in
its own allocation policy. nare does not allocate budgets across runs or
orchestrate them.

## Questions in a text reply

A headless `nare run` nudges a question in prose toward `ask`. When a turn ends in
text with no tool calls and the text asks a question (a sentence ending in `?`,
outside code, a fenced block or a URL), the run is not `done` yet: nare appends one
user message saying a text reply ends the session, and takes one more turn. The
nudge happens once per session (`ask_nudged` in the session file), only before a
`write` or `edit` has succeeded, only when the policy allows `ask`, and never on a
run with `--schema`. A TUI session never nudges. The nudge is a `progress` event
with a `question_nudge` detail. If the model then calls `ask` on its own the run is
`blocked` with the questions; if it replies in text again the run is `done`.
`done` and `blocked` keep their meanings, so the contract version does not change.

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
content, error status and size (see [Tool results](#tool-results)). Text and structured MCP results are retained; other
content blocks are represented as JSON, rather than interpreted as model images.
The standard [tool output cap](#the-tool-output-cap) applies, keeping the head and the tail. Session transcripts preserve results in
the same way as built-ins; raw transcripts can contain secrets and should be
handled as private artifacts.

### Remote servers with OAuth

A remote server that requires OAuth adds an `auth` object. Without it the
server is used as before, with optional `headers`:

```json
{
  "crm": {
    "url": "https://mcp.example.com/mcp",
    "timeout": 30,
    "auth": {"client_id": "nare-prod", "client_secret_env": "CRM_MCP_SECRET", "callback_port": 8765}
  },
  "docs": {"url": "https://docs.example.com/mcp", "auth": {}}
}
```

- `auth: {}` registers a client dynamically, when the server supports it
  (RFC 7591). `client_id` names a pre-registered client instead, and
  `client_secret_env` names the environment variable that holds its secret.
  A secret never goes in the file or on a command line.
- `scope` requests scopes; `callback_port` fixes the loopback port the
  browser returns to (use it when the pre-registered client has a fixed
  redirect URI). Unknown keys exit 2.
- `authorization_server` names the authorization server when it is not on the
  MCP server's origin. It is the only way to send credentials to another host.
- Where credentials may go, the rule is: every request the authorization flow
  makes must be `https` (or a loopback host). A `POST` (registration, the token
  exchange, refresh; these carry the client secret, the code and the refresh
  token) must go to the authorization server's origin. A `GET` (metadata
  discovery) may go to the MCP server's origin or the authorization server's.
  The authorization server is the MCP server's own origin, unless
  `authorization_server` pins another one, in which case that one is used and
  nothing else. A server's metadata cannot name a destination outside this rule.
  `auth` on a stdio server, a `url` over plain `http` elsewhere, or an
  `Authorization` header alongside `auth` exit 2.
- An authorized connection ignores the proxy environment (`HTTP_PROXY`,
  `HTTPS_PROXY` and their lowercase forms): the client secret, the code and the
  tokens never go through a proxy. A connection without `auth` keeps the
  environment's proxy settings, as before.
- The flow is the MCP authorization specification's: protected resource
  metadata (RFC 9728), authorization server metadata (RFC 8414), PKCE S256,
  and the resource indicator (RFC 8707). It is run by the MCP SDK's client.

Authorization is a separate, explicit step, run once by a person on a machine
with a browser (or with the loopback port forwarded to one):

    nare mcp login docs --mcp-config servers.json

It prints the authorization URL on stderr and waits up to 300 seconds
(`--timeout SECONDS`) for the browser to return to `127.0.0.1`. The listener
accepts only a redirect whose `state` is the one this login started; any other
request is refused with 400 and the wait goes on. Nothing goes to
stdout. Success prints `authorized <alias>` on stderr and exits 0; a failure
exits 1 with a message that quotes no token and no server reply.

`nare run` never opens a browser. It refreshes stored tokens when they near
expiry (30 seconds early). A server with no stored authorization, a refresh the
server refuses, or a revoked token stops the run with `status=error`,
`stop_reason=mcp`, exit 1, before any model call, and the message names
`nare mcp login`. A tool call that meets the same failure is an `is_error` tool
result.

**Where tokens are stored.** `$NARE_HOME/mcp-auth/ALIAS.json`, or
`~/.nare/mcp-auth/ALIAS.json` without `NARE_HOME`. The file is written 0600 in
a 0700 folder, atomically. It holds the access and refresh tokens, the expiry
and any dynamic registration, and it is bound to the server URL it was written
for. A file readable by other users is refused until it is fixed. Delete the
file to forget a server's authorization.

The tokens never appear in argv, the session file, the event stream, stderr or
logs. A session records the policy and the root, never the configuration. A
failure from an OAuth server reports its kind (for example "authorization
failed" or the exception type), never the server's or the authorization
server's text: an error can quote a credential, and the SDK's OAuth errors quote
the authorization server's reply. A refresh, in a later process, goes to the
authorization server the login used, which is kept in the token file.

This is additive: the contract version does not change. The redaction rule set
is unchanged. Tokens and server error text are kept out of events rather than
matched, so the rule set does not carry the guarantee.

Connections and child processes are closed on completion, error, cancellation
or explicit generator close. Library consumers that stop iteration early must
`await stream.aclose()` in the same task that began iteration. Configuration,
headers and environment values are not saved in the session. nare passes only
the SDK's minimal OS environment and configured `env` to stdio servers; model API
keys are not inherited. HTTP uses only explicitly configured headers. No model
sampling callback or credential sharing is enabled. Remote servers remain owned
by their hosts; nare terminates its connection, not their server process.

## Caller tools

A caller adds tools the model can call beside the built-ins, in one of two ways.

In-process, pass `nare.Tool` values as `tools=` to `nare.run` or `nare.step`.
`call` receives the model's arguments, checked against `input_schema`. It may be
a plain function or a coroutine function; a plain function runs in a worker
thread. Raising `ToolError` ends that call with its message as an error result.
Any other exception ends it with `Type: message` as an error result. Neither
ends the run. nare cannot stop a function that runs too long, so a caller bounds
its own. A runnable example is in [Calling nare from Python](#calling-nare-from-python).

From the command line, `--command-tools` names a JSON file, an object keyed by
tool name:

```json
{
  "lookup_order": {
    "description": "Fetch one order by id.",
    "input_schema": {"type": "object", "properties": {"id": {"type": "integer"}},
                     "required": ["id"]},
    "command": ["python", "lookup.py"],
    "timeout": 10
  }
}
```

```sh
nare run --yes --jsonl --command-tools tools.json "why did order 42 ship late?"
```

The command is an argv, run without a shell. It receives the checked arguments
as one JSON object on stdin, and its stdout is the result. It runs in `--root`
when there is one, with nare's environment minus the provider credentials, as
`bash` does, unless `--bash-credentials` is set. A non-zero exit is an error
result that names the status and carries stderr. A command still running after
`timeout` seconds (default 30) is killed with its process group, and the result
reads `timed out after N seconds`. An unknown field, a missing or empty
`command`, or a non-positive `timeout` exits 2 before the run starts. A run
cancelled while a command is running kills it, with its process group, too.
An `input_schema` that nare cannot enforce is refused at registration.

Names are 1 to 64 letters, digits, underscores or hyphens. A name may not be a
built-in name, and may not contain `__`, the separator MCP uses for `alias__tool`,
so the two sources cannot collide. A name registered twice is refused.

Arguments are checked before approval, as built-in arguments are. A call with
an unknown or missing parameter, or a value of the wrong type, is an error
result, and nobody is asked to approve it. A call that passes goes through the
approval seam: `--yes` on the command line, or the `approve` callable in the
library. A denial is an error result, as for built-ins.

Caller tools follow the same allowlist as built-ins. With no `--tools` on the
command line, every registered tool is offered, with every built-in tool. In the
library, a call with no policy offers the registered tools and the read-only
built-ins, as [run and step](#run-and-step) says. An explicit allowlist offers
only the tools it names, and `--tools none` offers none. A name that is allowlisted but
not registered is declared in the policy's `pending` set, as with MCP names:

```python
Policy(tools=frozenset({"read", "lookup_order"}), pending=frozenset({"lookup_order"}))
```

`step` has no implicit allow-all: the policy it receives names what the session
offers.

Each call writes one `tool_use` event and one `tool_result` progress event with
the same `tool_use_id`, as built-in calls do. The progress text is
`caller result: <name>`. Its content is kept, redacted and capped at the
[tool output cap](#the-tool-output-cap), as MCP content is.

The model chooses the arguments, and a caller tool runs with the caller's
privileges. The check on arguments is a schema check, not a policy: a tool that
writes or spends should validate what it does.

## Calling nare from Python

A Python caller can run the loop in-process instead of as a subprocess. The
events, the session and the stop reasons are the ones the command line gives.
Names come from `nare`, except `nare.reads` and `nare.schema`, which the examples
import from where they are defined. Every example runs as written: each one uses
`ReplayTransport`, a scripted transport that needs no key and no
network. To call a model, build the transport with `make_transport` instead
([Choosing a transport](#choosing-a-transport)).

### run and step

`run` is the loop. It is an async generator of `Event` values. It advances the
session to a terminal status, `done`, `blocked` or `error`, and yields each
event as it happens:

```python
import asyncio
from nare import Policy, ReplayTransport, new_session, run

async def main() -> None:
    transport = ReplayTransport({"replies": [{"text": "The fix is a missing await on line 12."}]})
    session = new_session("Why does fetch() return None in bar.py?")
    async for event in run(session, transport=transport,
                           policy=Policy(tools=frozenset())):
        if event.type == "output":
            print(event.text)
    assert session.status == "done"

asyncio.run(main())
```

`step` runs one turn: one model request, and the tool calls that turn makes.
It returns the session, and the events it produced wait in `session.events`
for the caller to take. `step` takes `schema`, `check`, `tools` and
`max_corrections`. It has no `max_turns`, `max_tool_calls`, `max_cutoffs`,
`require_reads` or `require_read`: those are `run`'s, and `run` builds the
objects `step` takes for them. A caller that needs them uses `run`, which calls
`step` once per turn.

```python
import asyncio
from nare import Policy, ReplayTransport, approve_all, new_session, step

async def main() -> None:
    transport = ReplayTransport({"replies": [{"text": "Nothing to change."}]})
    session = new_session("Is bar.py formatted?")
    await step(session, transport, approve_all, Policy(tools=frozenset()))
    assert session.status == "done"
    outputs = [event.text for event in session.events if event.type == "output"]
    assert outputs == ["Nothing to change."]
    session.events.clear()

asyncio.run(main())
```

**Pass `policy`.** With no policy, `run` offers only `read`, `list`, `search`
and `ask`, whether or not an `approve` callable is passed. It cannot write, edit
or run `bash`: an approver gates the calls a policy allows, and does not widen
the default. To offer `write`, `edit` or `bash`, pass a policy that names them,
such as `Policy()` for the built-in set. A call that needs an answer and no side
effects passes `Policy(tools=frozenset())`, as the examples do. A policy that
names a caller tool must name it in `tools` and in `pending`, as the
[caller tools](#caller-tools) section shows. MCP servers and caller tools are
offered when the call registers them, whichever default applies.

**Changed for library callers.** An earlier library default offered every
built-in tool, `bash` included, to a call that passed no policy, and passing
`approve` alone kept that set. Now a call with no policy offers only the
read-only tools, and passing `approve` alone no longer restores the built-in
set. A caller that relied on the earlier behaviour passes `policy=Policy()` to
keep it, or names the tools it needs. A caller that passed `approve` alone to
get `write`, `edit` or `bash` passes a policy as well. This is a change to the
Python API only. The command line still passes its policy explicitly, so
`nare run`, its stream, its session file and its exit codes are unchanged, and
the contract version does not move.

With no `root`, the read tools reach any path the process can read. A caller
that gives the model a tree passes `Policy(root=...)`.

### Choosing a transport

`make_transport(kind, *, model, ...)` builds a transport for `"anthropic"` or
`"openai"`. It reads the key from `api_key=` or the environment, and sends no
request until a run does. `system=` is the system prompt. The library adds no
prompt of its own, unlike the command line, which adds nare's default prompt
whenever tools are on.

```python
from nare import make_transport

INSTRUCTIONS = "Classify support tickets. Reply with the JSON object only."
transport = make_transport("anthropic", model="claude-sonnet-5",
                           system=INSTRUCTIONS)
assert transport.system == INSTRUCTIONS
assert transport.supports_structured_output is True
```

`ReplayTransport({"replies": [...]})` replays its replies, one per turn. A reply
is a dict with any of `text`, `thinking`, `tool_calls` (each `{"id", "name",
"args"}`), `stop_reason` and `usage` (`input`, `output`, `cache_read`,
`cache_write`). It does not declare structured output, so `run` refuses
`structured_output=` with it. The examples here do not pass that option.

### Typed answers: schema and check

`schema` takes the JSON Schema as a dict. The keywords it may use are listed in
[Answer schemas](#answer-schemas). The command line refuses an unsupported
schema before the run starts, by calling `nare.schema.check_supported`. `run`
does not make that call. An unsupported keyword is found on the first answer
instead, after one model request, and the run ends with status `error`. Make
the call first, as the worked example below does.

`check` takes a function that receives the parsed answer and returns its
errors: an empty result passes, and each error is a mapping with `code`,
`location`, `message` and `guidance`, or a plain string. A check with two
positional parameters also receives the run's reads, as a second argument: the
records of the result's `reads` key, empty when nothing was read. A check with
one parameter is called with the answer alone, as before. `check` runs only on
a final answer that passes the schema, and its errors go into the
same correction as schema errors. It needs `schema`. A check that raises ends
the run with `status` `error` and an error event, and the exception does not
reach the caller. See [Structured errors and checks](#structured-errors-and-checks).

### Caller tools in-process

`tools=` takes `nare.Tool` values. Raising `ToolError` ends that call with its
message as the error result the model sees, and the run goes on. The policy
names the tool in `tools` and in `pending`, because the policy is not built
from the tools. [Caller tools](#caller-tools) has the rules.

```python
import asyncio
from nare import Policy, ReplayTransport, Tool, ToolError, new_session, run

ORDERS = {42: {"status": "shipped", "shipped_on": "Tuesday"}}

def lookup_order(args):
    row = ORDERS.get(args["id"])
    if row is None:
        raise ToolError(f"no order {args['id']}")
    return row  # a str is sent as is; any other value as JSON

lookup = Tool(
    name="lookup_order",
    description="Fetch one order by id.",
    input_schema={"type": "object", "properties": {"id": {"type": "integer"}},
                  "required": ["id"]},
    call=lookup_order,
)

async def main() -> None:
    transport = ReplayTransport({"replies": [
        {"tool_calls": [{"id": "c1", "name": "lookup_order", "args": {"id": 7}}]},
        {"tool_calls": [{"id": "c2", "name": "lookup_order", "args": {"id": 42}}]},
        {"text": "Order 42 shipped on Tuesday."},
    ]})
    session = new_session("When did order 42 ship?")
    policy = Policy(tools=frozenset({"lookup_order"}),
                    pending=frozenset({"lookup_order"}))
    async for _ in run(session, transport=transport, policy=policy, tools=[lookup]):
        pass
    assert session.status == "done"
    results = [block for message in session.messages
               for block in message.content if block.get("type") == "tool_result"]
    assert results[0]["is_error"] and "no order 7" in results[0]["content"]
    assert not results[1]["is_error"]

asyncio.run(main())
```

### Budgets, and where the library and the command line differ

| Option | Library default | Command line default | What it allows |
| --- | --- | --- | --- |
| `max_turns` | 50 | 50 (`--max-turns`) | Turns in this invocation |
| `max_corrections` | 1 | 1 (`--max-corrections`) | Corrections of a failing answer or an unmet read requirement, per run. A free correction does not count |
| `max_cutoffs` | `None`: no correction, the run ends at the first cut-off reply | 2 (`--max-cutoffs`) | Corrections of replies cut off at the output cap or by repetition, per invocation |
| `require_reads`, `require_read` and `require_cited_reads` | 0, `()` and `()` | 0 and none (`--require-reads`, `--require-read`, `--require-cited-reads`) | Hold the final answer until N distinct files, each named path, or each path the answer cites, has been read |

`max_cutoffs` is the one default that differs in effect. A library call that
passes nothing ends at the first cut-off reply, as nare did before the option
existed. Pass `max_cutoffs=2` to match the command line. The read requirements
need the `read` tool in the policy, and a path is relative to the policy's
`root`.

The command line also takes its provider, model, base URL, context window,
budgets and request settings from `NARE_*` variables when no flag is given
(`NARE_MODEL`, `NARE_PROVIDER`, `NARE_CONTEXT_WINDOW`, `NARE_BUDGET_TOKENS` and
others). The library's arguments read none of them, so a library caller passes
these values in code.

A call that does not pass `max_cutoffs` ends at the first cut-off reply:

```python
import asyncio
from nare import Policy, ReplayTransport, new_session, run

CUT_OFF = {"text": "The order shipped on", "stop_reason": "max_tokens"}
COMPLETE = {"text": "The order shipped on Tuesday."}

async def answer(**options):
    transport = ReplayTransport({"replies": [CUT_OFF, COMPLETE]})
    session = new_session("When did order 42 ship?")
    async for _ in run(session, transport=transport,
                       policy=Policy(tools=frozenset()), **options):
        pass
    return session

async def main() -> None:
    session = await answer()
    assert session.status == "error" and session.stop_reason == "max_tokens"
    session = await answer(max_cutoffs=2)
    assert session.status == "done" and session.cutoffs == 1

asyncio.run(main())
```

### Read requirements in-process

`require_read` holds the final answer until each named path has been read, and
each held answer is a correction charged to `max_corrections`. `require_cited_reads`
holds it until each path the answer cites has been read, and needs `schema`; see
[Read requirements](#read-requirements). The answer
here is held once, the model reads the file, and the next answer is accepted.

```python
import asyncio
import tempfile
from pathlib import Path
from nare import Policy, ReplayTransport, new_session, run
from nare.reads import read_record

async def main() -> None:
    with tempfile.TemporaryDirectory() as root:
        Path(root, "spec.md").write_text("Orders ship within 2 days.\n",
                                         encoding="utf-8")
        transport = ReplayTransport({"replies": [
            {"text": "Orders ship within 2 days."},  # answered before any read
            {"tool_calls": [{"id": "r1", "name": "read",
                             "args": {"path": "spec.md"}}]},
            {"text": "Orders ship within 2 days, per spec.md."},
        ]})
        session = new_session("What does the spec say about shipping?")
        policy = Policy(tools=frozenset({"read"}), root=Path(root))
        async for _ in run(session, transport=transport, policy=policy,
                           require_read=["spec.md"]):
            pass
    assert session.status == "done"
    assert [record["path"] for record in read_record(session)] == ["spec.md"]
    assert session.schema_correction_log[0]["kind"] == "reads"

asyncio.run(main())
```

### Results

The `result` line of `--jsonl` has a Python counterpart on the session, which
`run` fills in as it goes:

| `result` line | Python |
| --- | --- |
| `status`, `stop_reason`, `error` | `session.status`, `session.stop_reason`, `session.error` |
| `output` | `session.output`: the parsed value, once a schema answer validates |
| `text` | the `text` of the `output` event, the answer as the model wrote it |
| `turns` | `session.turns` |
| `usage.total` | `session.usage.total_tokens`: cache included, what `--budget-tokens` counts |
| `corrections` | `session.schema_correction_log`: an empty list when no correction happened |
| `cutoffs` | `session.cutoffs`: over the session's life, as on the command line |
| `reads` | `nare.reads.read_record(session)`: present only when a read requirement was set |

The `budget` object of the result line is computed inside the loop and is not
exported. The session fields above carry the same counts.

### The typed-function recipe

A script that wants one typed answer from a model, with no side effects, sets
the same few options on every call:

1. Tools off: `policy=Policy(tools=frozenset())`. The command line is `--tools none`.
2. The schema: `schema=`, or `--schema FILE`.
3. Turns: `max_turns=2` or more, so a failing answer still has its correction turn.
   With 1, a failing answer ends the run with status `error` and `stop_reason`
   `max_turns`, after one more request.
4. Constrained decoding: `structured_output="json_schema"`, on a transport that
   declares it. Both `make_transport` rails do. The command line is
   `--structured-output json_schema`. Constrained turns are sent without tools,
   which is what this recipe wants.
5. Constant instructions in the system prompt (`system=`, or `--append-system`
   on the command line), and the varying item in the prompt. Constant text
   comes first and the varying item last, so the request prefix stays
   cacheable across calls.
6. Rules the schema cannot express go in `check=`.

The worked example classifies a support ticket. It uses the replay transport,
so the structured-output option is left out, as the previous section explains.

```python
import asyncio
from nare import Policy, ReplayTransport, new_session, run
from nare.schema import check_supported

SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"enum": ["bug", "feature", "question"]},
        "order_id": {"type": "integer"},
    },
    "required": ["label", "order_id"],
    "additionalProperties": False,
}
KNOWN_ORDERS = {42, 77}

def check(answer):
    if answer["order_id"] not in KNOWN_ORDERS:
        return [{
            "code": "unknown_order",
            "location": ["order_id"],
            "message": f"order {answer['order_id']} does not exist",
            "guidance": "use an order id that the ticket names",
        }]
    return []

async def classify(transport, ticket: str):
    check_supported(SCHEMA)
    session = new_session(ticket)
    async for _ in run(session, transport=transport,
                       policy=Policy(tools=frozenset()),
                       schema=SCHEMA, check=check, max_turns=2):
        pass
    return session

async def main() -> None:
    transport = ReplayTransport({"replies": [
        {"text": '{"label": "bug", "order_id": 9}'},   # fails the check
        {"text": '{"label": "bug", "order_id": 42}'},  # the correction passes
    ]})
    session = await classify(transport,
                             "Order 42 arrived broken. Please send a new one.")
    assert session.status == "done"
    assert session.output == {"label": "bug", "order_id": 42}
    assert len(session.schema_correction_log) == 1

asyncio.run(main())
```

### What to put in --append-system

`--append-system` has no library argument. The library takes one `system=`
string on `make_transport`, so join the parts yourself. The command line
builds its prompt the same way, by joining its default and this text with a
blank line. Put in the text what the model cannot read off the repository:

- Commands to run, exactly, such as `uv run pytest -q tests/test_x.py`.
- Conventions that are not standard for the stack, such as "migrations are
  append-only; never edit one that has shipped".
- A role, when the caller needs one.

Leave out a repository overview, a directory tree, and a generated AGENTS.md.
Gloaguen et al. ([arXiv:2602.11988](https://arxiv.org/abs/2602.11988)) found
that repository context files did not raise task success on average, raised
inference cost by over 20%, found that the instructions in them are well
followed, and found that repository overviews are not helpful. Lulla et al.
([arXiv:2601.20404](https://arxiv.org/abs/2601.20404)) found AGENTS.md files
associated with a lower median runtime and fewer output tokens, at comparable
task completion. The second is a correlation across pull requests, not a
controlled comparison, so the guidance here rests on the first.

Keep the text short and the same on every call. It is part of the request
prefix, so changing it between calls misses the prompt cache. A resume without
a system option sends the prompt the session recorded.

Related fixes: [#142](https://github.com/ViviDynamics/nare/issues/142) imports
the MCP client only when a run configures MCP servers, which shortens startup.
[#139](https://github.com/ViviDynamics/nare/issues/139) keeps the wrap-up text
out of a run with no tools, so a tool-free request does not depend on the turn
count. [#144](https://github.com/ViviDynamics/nare/issues/144) sends the schema
at decode time on the anthropic rail.

## The bash environment

`bash` runs with nare's environment minus the provider credentials nare reads
for its own requests: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` and
`OPENAI_API_KEY`. A command the model writes, `env` included, does not see
them, so neither the transcript nor an instruction planted in a file the model
reads can reach them. This is the same rule as for MCP stdio servers above:
no child process nare starts inherits the model API keys. Every other variable
is passed through unchanged. A credential a later transport reads joins the
list, and `nare contract` prints it under `bash_env.withheld`.

A task that needs a key in the shell opts in:

    nare run --yes --jsonl --bash-credentials "publish the package"

The opt-in is recorded in the session's policy as `"bash_credentials": true`,
so a run that handed the shell its keys says so afterwards. Library callers
pass `Policy(bash_credentials=True)`. Without the opt-in the policy has no
`bash_credentials` key. Redaction is unchanged: it still applies to every
event, and this removes the keys at the source instead of relying on it.

Withholding is not a sandbox. A command can still read files, the network and
anything else the environment gives it; confining those stays the caller's job.

## Bash command rules

A policy can allow or deny a `bash` command by pattern, so a caller can allow
`git diff *` without allowing every command. `--bash-allow PATTERN` and
`--bash-deny PATTERN` set them on the command line, repeatable; the library
takes `Policy(bash_allow=..., bash_deny=...)` as frozensets of patterns. A
pattern is matched as follows.

- `*` matches any run of characters, including none and spaces. `?` matches one
  character. Every other character matches itself, so `.` is a dot.
- A command is matched as bash reads its words after quote removal, joined by
  one space: `'git' diff` is `git diff`, and `"echo" ok` is `echo ok`.

A line is split into simple commands at `&&`, `||`, `|`, `|&`, `;` and `&`. A
line is **unreadable** when the lexer cannot read it: it contains `(` or `)`
outside quotes, `$(`, a backtick, a process substitution, a heredoc or here
string, a newline, an unterminated quote, or a brace sequence such as `{1..3}`.

**Deny wins, and it holds under `--yes`.** A deny is checked before any
approval, so a matching call is refused without the approver being asked. Deny
patterns ignore case, since a case-insensitive filesystem runs `ECHO` as
`echo`. A deny is checked on every tail of every simple command: each run of
its words from any word to the end, the same with a first word's directory
removed (`/usr/bin/rm x` is `rm x`), and the same with a trailing space, since
`xargs rm` takes its arguments from stdin and a bare `rm` is `rm` with arguments
to come. So `xargs rm`, `find . -exec rm {} \;` and `time rm x` all match
`rm *`.

**Under a deny, every word must be plain.** A word is plain when bash would
read its unquoted characters as themselves: no `$`, glob (`*`, `?`, `[`), brace,
tilde or `!`. A line with any word that is not plain is refused, since bash
turns it into names this check cannot see. A line is also refused when it
cannot be read (see the lexer rules above), when its first word holds an `=`
(a variable assignment, which may name the program), or when it reads a file of
commands: `.`, `source`, or a shell given no command string (`sh script.sh`,
`echo 'rm x' | sh`). Redirections are not commands, so `> file rm x` is checked
as `rm x`.

A **runner** is a program that runs another program, or a string of commands,
or whose arguments name one: the shells, `eval`, `env`, `xargs`, `find`,
`sudo`, `nice`, `timeout`, `exec`, `command`, `busybox`, `git` and a few more
(the list is `nare.bash_rules`). For a runner a deny also reads its arguments:
each argument is read again as a command line, and each character run of it is
checked, so `sh -c "rm x"`, `env -S'rm x'` and `bash -e -c 'rm x'` match. A
shell's `-c` must come before any operand, and a long option such as
`--rcfile` is refused, because its operand would be a file that runs unseen.

A runner's argument that contains a backslash is refused, since a runner such as
`env -S` unescapes its string itself. A word's text after any `=` is also read
as a command, so an option that names a program, such as `--to-command=rm x`,
is checked.

A deny refuses, rather than guesses, every line it cannot check, and says which
patterns refused it.

**Allow is narrow.** A call is allowed without asking only when the line is
readable, every simple command has plain words (no `$`, glob, brace, tilde or
other expansion) and no redirection, and every simple command matches an allow
pattern as a whole. `git diff --stat && touch x` is therefore allowed only if
`touch x` also matches an allow pattern. An unreadable line, a redirection, or
an expansion is never allowed by a rule and goes to the approver as before.
An allowed call runs without the approver, and the approval rules that ask
before destructive git commands or writes outside the root do not apply to it:
the pattern is the caller's grant. The root and credential rules still apply.

A call that no rule decides follows the approval rules as before, including
`--yes`, which approves it.

**What a pattern does not do.** A pattern matches the words as spelled. It does
not know a program's options: `git push *` does not match `git -C dir push`, so
a deny meant to stop pushes is written `git * push *`. A deny sees only what the
line spells out. It does not read environment variables that name a program,
set in the line or inherited, such as `GIT_EXTERNAL_DIFF`, `PAGER`, `EDITOR` or
`BASH_ENV`; an assignment in front of a command is refused, but `export` and
the environment the run started with are not. A program that runs commands from
its own code, or from a file it was given, is not read: `python -c`, `perl -e`,
`make`, `npm run`, `docker exec`, and any script run by a program that is not a
shell or a listed runner. Rules are not a sandbox: confining what a command can
reach stays the caller's job.

**The session record.** Each call a rule decides adds a `progress` event with
`bash_rule: {"tool_use_id", "effect", "patterns"}` and the same record in the
session's `bash_rules` list, in call order. `effect` is `allow` or `deny`, and
`patterns` lists the rules that decided it (one for a deny; for an allow, the
rules that covered each command). The command text is never recorded. A session
that set rules records them under its policy as `"bash_allow"` and
`"bash_deny"`, sorted lists, and a policy with no rules has neither key.

The rules add flags, a session field with a default and keys under the recorded
policy, so the contract version stays the same ([What changes the version](#what-changes-the-version)).

## Bash sandbox

`--bash-sandbox` (library: `Policy(bash_sandbox=True)`) runs each `bash`
command under the operating system's own sandbox. It is off by default. Without
it, `bash` is confined by nothing but the container around nare, and the
pattern rules above are not a sandbox.

    nare run --yes --jsonl --root ./work --bash-sandbox "fix the failing test"

What a confined command may do:

- **Write** only inside `--root` and a private temporary directory made for
  that call. `TMPDIR` points at it, and it is deleted when the call returns.
- **Read** anything the user can read, except the credential locations below.
- **Use the network** not at all, unless `--bash-network` is also given.
- **See** only the environment variables a shell needs: `PATH`, `HOME`, `USER`,
  `LOGNAME`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TERM`, `TZ` and `SHELL`, plus
  `TMPDIR`. A variable such as `GH_TOKEN` or a cloud key is not passed on.
  `--bash-credentials` restores the full environment, as before.

The credential locations are denied for reads and writes: `~/.ssh`, `~/.aws`,
`~/.gnupg`, `~/.kube`, `~/.docker`, `~/.azure`, `~/.terraform.d`,
`~/.password-store`, `~/.config/gh`, `~/.config/gcloud`, `~/.config/op`,
`~/.netrc`, `~/.npmrc`, `~/.pypirc`, `~/.s3cfg`, `~/.vault-token`, `~/.pgpass`,
`~/.my.cnf`, `~/.git-credentials`, `~/.config/git/credentials`,
`~/.cargo/credentials` and `~/.cargo/credentials.toml`, `~/.gem/credentials`,
`~/.m2/settings.xml`, and all of `NARE_HOME` (MCP tokens and saved sessions).
On macOS also `~/Library/Keychains` and `/Library/Keychains`, and the
keychain's mach services are refused, so `security find-generic-password`
returns nothing.

**The root must not hold a credential.** `--root` is refused when a credential
location lies inside it or is it. Otherwise bash could rename a folder that
contains a credential, and read the credential at its new, undenied path.

**Platforms.** On macOS nare uses `sandbox-exec` (Seatbelt). On Linux it uses
`bwrap` (bubblewrap), which needs the `bubblewrap` package and permission to
create user namespaces. Other platforms have no sandbox here.

**Refusal, never a fallback.** `--bash-sandbox` needs `--root`, and it refuses
a root of `/`. When the platform has no sandbox, or it fails to start (no
`bwrap`, or user namespaces are blocked), `nare run` exits 2 before any turn
and says why. A call made while a sandboxed session runs is refused the same
way and its command never starts unconfined. `--bash-network` without
`--bash-sandbox` is refused too.

**Recording.** A sandboxed session records the profile under its policy as
`bash_sandbox`: `mechanism` (`seatbelt` or `bubblewrap`), `profile` (`confined-1`),
`network`, `writable` (the root), `private_temp: true`, and `denied` (the
credential locations on this machine). A session without the flag has no
`bash_sandbox` key. `nare contract` reports under `bash_sandbox` whether this
host can confine bash, and why not when it cannot.

**Limits.** These are stated, not hidden:

- Credential folders are masked when they exist at the start of a call. On
  Linux a credential created inside the root during the same call is not
  masked. On macOS the path rule covers it.
- Caller tools (`--command-tools`) and MCP stdio servers are not confined. They
  are programs the caller chose to run, not the model's shell.
- The root is writable in full. Put the work in a directory you can throw away.
- Non-file mach services stay reachable on macOS, apart from the keychain
  services above. Anything a process can ask of a system service is still open.
- On Linux the sandbox has no seccomp filter. Kernel attack surface is shared
  with the host.
- Landlock is not used. Where bubblewrap is missing, nare refuses rather than
  switching to a weaker mechanism.
- `/tmp` and `/run` are not the host's. On Linux each is an empty tmpfs, so a
  Unix socket there (an agent socket, a container runtime socket) cannot be
  reached. On macOS writes to `/tmp` are refused. A tool that hard-codes `/tmp`
  fails on macOS. Tools that honour `TMPDIR` work on both.
- Linux masks credential folders by path. A credential folder bind-mounted at a
  second path, outside the masks, stays readable there.

## Tool arguments

Before a built-in tool runs, and before the approval callback is asked about
it, nare checks the call's arguments against that tool's own input schema,
the one the model was shown. A call that fails gets an `is_error` tool result
naming the tool, the parameter and the problem, every problem at once, and
nothing runs:

    bash: timeout: expected integer, got string
    edit: missing required parameter new
    read: unknown parameter lines; read takes path, offset, limit

- An unknown parameter is refused. That includes the keywords nare sets on a
  call itself (the working directory, the evidence base, the bash
  environment), which a model cannot reach.
- An optional parameter sent as `null` is read as absent, since some models
  send every parameter. A required one sent as `null` is a type error.
- `ask` keeps its own reading of `questions` (a bare string is one question),
  so the check never changes when `blocked` is reported; missing and unknown
  parameters are still refused.
- MCP tools are left to their servers, which validate their own input.

A call to a tool nare does not have is an `is_error` result listing only the
tools this session allows (`none` when it allows none), never every tool nare
has, so the model is not offered one it would then be refused.

A failed `edit` says why `old` did not match, and changes nothing. When `old`
matches with all whitespace ignored, the error says so and shows those lines
of the file, numbered. Otherwise it shows the lines closest to `old`'s first
line, or says no line resembles it. When `old` appears more than once, it
lists the line each occurrence starts on. Excerpts are bounded (10 lines, 200
characters a line, 20 line numbers), and an inexact match is never applied.

Tool arguments that are not a complete JSON object are an `is_error` result
too, on every path that receives them: a streamed turn on either provider, and
an unstreamed openai or anthropic turn. The openai path used to pass `{}` and
run the tool with no arguments. The tool is not run.

Arguments that repeat an object key are refused the same way, with the message
`tool arguments are invalid: duplicate key: <key>; tool not run`, with the path to
the object before the key when the repeat is nested (`scope: duplicate key: file`). An
unstreamed anthropic reply is checked from its raw body, because the SDK
would otherwise keep the last of a repeated key silently. A repeat outside
any tool's input refuses every tool call in that reply.

`nare contract` describes the check under `tool_arguments`, including
`repeated_keys`.

## The tool output cap

Every tool result's text is held to 30000 characters (`nare contract` prints
it as `tool_output.max_chars`, and as `navigation.max_output`). How a long
result is cut depends on the tool, so the model keeps the part it needs:

- `bash` caps stdout and stderr apart, within that total together. A stream
  that fits in half of it is kept whole, and the other gets the rest, so a
  flood of stdout cannot push stderr out. A stream over its share keeps its
  head and its tail, because test runners and compilers print their verdict
  last. A marker in the middle says how much was cut and how to see the rest:

      [... 12,345 characters of stdout cut here; to see all of it, redirect the output to a file and read that with offset and limit ...]

  The status line (`exit N`) and any `[still running: ...]` line sit outside
  the cap.
- An MCP result keeps its head and its tail in the same way, with a marker
  naming `the result`.
- `read` keeps its head-first cut, since paging covers the rest. A read that
  stops before the end of the file, at `limit` lines or at the cap, ends with
  a notice after the returned text:

      [lines 1 to 2000 of 3000; continue with offset=2000]
      [truncated at 30000 chars, partway through line 7: lines 1 to 7 of 90; continue with offset=7]

  A read that reaches the end of the file has no notice. The notice is not
  file content: [read evidence](#read-evidence) still describes exactly the
  lines and bytes returned before it.
- `list` and `search` pages never reach the cap; a page ends early instead,
  with a cursor.

`nare contract` names each rule under `tool_output`: `bash` is
`head_and_tail_per_stream`, `mcp` is `head_and_tail` and `read` is
`head_then_continue_notice`.

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
The additive flag and streaming event timing keep the contract version.

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
provider/model output limits and require max output to exceed a
`--thinking-budget`.

Budget enforcement remains **after each complete step**, including tool
results. Streaming does not make the cumulative ceiling strict: the crossing
turn can overshoot, and done/blocked wins exactly as in nonstreaming. Actual
usage remains visible on completion. After a budget stop there is no subsequent
model call.

A dropped stream is never resumed mid-turn. Within the retry budget the whole
request is resent (see
[provider retries](#provider-retries-and-the-request-timeout)); the abandoned
attempt's text has already gone out as `progress`, and the `retry` event marks
where the next attempt's text starts. When the budget is spent, already
emitted progress and completed transcript turns remain available; incomplete
tool arguments are never dispatched, and an incomplete assistant turn is not
saved as a completed turn. Usage that the provider did not report cannot be recovered or
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

## Provider retries and the request timeout

```sh
nare run --yes --jsonl --request-timeout 900 --request-retries 5 \
  --request-retry-seconds 120 "..."
```

A model request that fails transiently is resent inside the run, on both
rails, streamed or not, and on schema-constrained turns (the correction turn
and every turn of a tool-free `--structured-output` run), which are resent
constrained. Transient means:

| Failure | `reason` |
| --- | --- |
| Read, write, connect or pool timeout | `timeout` |
| Connect error, connection reset or closed mid-response | `connection` |
| A stream that ended before its completion marker | `stream` |
| HTTP 429, 502, 503, 504, 529 | `status` |
| anthropic rail: HTTP 500, which is Anthropic's `api_error` | `status` |
| anthropic rail: an `overloaded_error` sent mid-stream | `overloaded` |

Everything else is permanent and ends the run on the first attempt: a 400,
401, 403, 404 or 422, any other status, a stream that finished but lacks its
usage or stop reason, and an error frame inside an openai-rail stream, whose
meaning nare cannot tell. On the openai rail a 500 is permanent, because
behind a proxy it is as often a translation failure that resending repeats.

The budget is per model request, not per run, so a long run that meets a
failure every few turns does not run out early.

| Flag | Environment | Default | Meaning |
| --- | --- | --- | --- |
| `--request-retries N` | `NARE_REQUEST_RETRIES` | 3 | Most resends of one request; 0 never resends |
| `--request-retry-seconds S` | `NARE_REQUEST_RETRY_SECONDS` | 60 | Most seconds one request waits between its attempts |
| `--request-timeout S` | `NARE_REQUEST_TIMEOUT` | 600 | HTTP timeout: the longest wait to connect or for the next bytes |

Waits are 2, 4, 8 and 16 seconds, then 30 each, with no jitter. A
`Retry-After` given in seconds lengthens a wait to it. A retry whose wait would
take the request past its seconds budget is not made. The seconds bound the
waiting, not the attempts: one attempt that times out takes up to the request
timeout by itself. The request timeout bounds each wait for bytes, not a whole
response, so a streamed turn that keeps sending is never cut off by it. An
invalid value (a negative count, a timeout that is not a positive finite
number) exits 2 before starting. Without the flag, the anthropic rail keeps its
SDK's 600 seconds with 5 to connect. The SDK's own retries are off: the budget
here is the only one.

A failed attempt adds no turn and no usage, and the resent request carries the
same messages, so the request bytes are the same and a caching backend's
prefix still hits. Each retry emits a `progress` event:

```json
{"type": "progress", "text": "model request failed (timeout); retry 1 of 3 in 2s: ReadTimeout: ...",
 "detail": {"retry": {"attempt": 1, "retries": 3, "reason": "timeout",
                      "status": null, "wait": 2.0, "error": "ReadTimeout: ..."}}}
```

On a streamed turn it arrives live, after the abandoned attempt's text. On an
unstreamed turn it arrives with the turn's other events, when the request ends;
its `timestamp` says when it happened. The `result` line and the session file
carry `retries`, the count over the session's life, as `turns` is. A session
file without it loads as 0. The same is true of `cutoffs`, the count of cut-off
replies, which the session file and the `result` line carry.

A request that fails permanently, or still fails when its budget is spent,
ends the run as before: `status=error`, `stop_reason` null, and `error` is the
last failure. `--proxy-num-retries` is separate: it asks a proxy to retry
upstream, inside one of nare's attempts.

`nare contract` advertises this under `request_retries` and `request_timeout`.
The flags, event detail, result field and session field are additive and keep
the contract version.

## Reasoning effort

    nare run --yes --jsonl --effort xhigh "..."

`--effort` takes `low`, `medium`, `high`, `xhigh` or `max` on both rails. nare
does not keep a table of which model takes which level: a model that does not
take a level answers with its own error, which ends the run as any provider
refusal does (`status=error`, `stop_reason` null, not retried).

| Rail | `--effort LEVEL` sends | Notes |
| --- | --- | --- |
| anthropic | `output_config: {"effort": LEVEL}` and `thinking: {"type": "adaptive"}` | No `budget_tokens`. Adaptive thinking is sent because Opus 4.7 and 4.8 do not think without it; where thinking is always on it is the same as omitting it |
| openai | `reasoning_effort: LEVEL` | Passed through. What a level means belongs to the server behind `--base-url`: one server may map an unknown level to its most expensive setting |

Current Claude models refuse a fixed thinking budget with a 400. The older
models that still need one (for example Haiku 4.5) are reached with
`--thinking-budget TOKENS`, anthropic only, which sends `thinking: {"type":
"enabled", "budget_tokens": TOKENS}`. With `--effort` as well, both are sent,
which is what Opus 4.5 takes. The openai rail refuses `--thinking-budget`, and a
nonpositive value exits 2.

Thinking counts toward `max_tokens`, so the default leaves room for it when
`--max-tokens` is not given:

| Settings | Default `max_tokens` |
| --- | --- |
| neither | 8192 |
| `--effort`, streamed | 64000 |
| `--effort`, unstreamed | the model's nonstreaming ceiling (21333, lower for some models) |
| `--thinking-budget N` | N + 8192, capped at that ceiling when unstreamed; `max_tokens` must exceed N |

The session file records what the invocation sent as `effort` and
`thinking_budget` (null when not sent); a resume without the flags records
null. A file without them loads as null. `nare contract` lists the levels and
each rail's request shape under `effort`. The new levels, flag, session fields
and discovery key are additive and keep the contract version.

## Read-only navigation

    nare run --yes --jsonl --contract 2 --tools read,list,search --root "$PWD" \
      --session survey.json "Where is the database port configured?"

`list` and `search` let a session find its way around a tree without `bash`, so
a caller can grant exploration and reading while granting no execution and no
writes. Both are pure Python and start no process. Like every tool, each is
offered to the model only when the allowlist names it (or when `--tools` is
omitted). `nare contract` advertises them under `tools` and their limits under
`navigation`; both keys are additive and keep the contract version.

Both resolve their `path` (default: the root) exactly as `read` does, so a
path outside `--root`, including through a symlink, is an `is_error` result.
Neither follows a symlink while walking: `list` reports a symlink as a symlink,
and `search` never enters or reads through one. `search` skips `.git/`
directories. Paths in results are relative to `--root` (relative to the working
directory without one), which is what `read` accepts.

`list` returns one directory, sorted by name, under a header:

    src: entries 1-4 of 4
    app.py  (file, 812 bytes)
    logo.png  (file, 20412 bytes, binary)
    current  (symlink)
    lib/  (dir)

A kind is `dir`, `file`, `symlink` or `other`. A file is `binary` when its first
8 KiB hold a NUL byte.

`search` takes an optional `glob` and an optional `pattern`. A glob without `/`
matches a file name at any depth; one with `/` matches the path below `path`,
where `**` spans directories. Without a pattern the result is the matching file
paths and no file is opened. With one, each matching line of each text file is
`path:line: text`; binary files are not searched, line numbers start at 1, and
`text` is cut to 200 characters (then `...`). The pattern is a Python regular
expression; `(?i)` makes it case-insensitive.

    src: 2 matching lines in 1 file
    src/db.py:14: PORT = 6543
    src/db.py:31:     connect(port=PORT)

Results are paged. A page holds at most `limit` records (default 200 for `list`
and 100 for `search`, at most 1000) and never more than the 30000 character
tool output cap, so a page is never truncated. A page with more after it ends
with:

    [next page: cursor "<opaque>"]

Pass that cursor with the same `path`, `glob` and `pattern` to get the next
page; a cursor from a different call is an `is_error` result. A cursor marks a
position, not an index, and order is by name, so the same tree yields the same
pages, byte for byte, on every call.

## Read evidence

Every successful `read`, `list` and `search` call adds one `progress` event
whose `detail` is `{"tool": <name>, "evidence": <record>}`, and appends the
same record to the session file's `evidence` list, in call order. A caller can
therefore list every file version and range the model was shown from the JSONL
stream alone, or from the session file after the run. A call that fails
(`is_error`) records nothing, because it returned nothing.

A record holds no file content: paths, digests, sizes, ranges and names. It
passes through the same redaction as every event detail, which leaves it
unchanged unless a path itself looks like a credential; the session stores the
redacted copy, so the two always agree. `tool_use_id` ties a record to its
call in the transcript; the event carries the same id at its top level, and
the call's result event follows it.

A `read`:

```json
{"tool": "read", "tool_use_id": "c1", "path": "src/db.py",
 "sha256": "<64 hex>", "size": 4210,
 "lines": {"first": 11, "last": 40}, "bytes": {"start": 310, "end": 1187},
 "truncated": false}
```

- `path` is relative to `--root` (to the working directory without one),
  which is what `read` accepts.
- `sha256` and `size` are of the whole file, computed from the same bytes the
  text was decoded from, so the digest names the version the model saw. A file
  that changes between two reads yields two records with two digests; nothing
  reconciles them.
- `lines` are the lines returned, counted from 1 and inclusive: line
  `offset + 1` first. `bytes` are offsets into the file, end exclusive,
  covering each returned line with its line ending.
- After the 30000 character output cap, the range is what was returned, not
  what was asked for: `truncated` is true, and `lines.last` and `bytes.end`
  stop at the last character returned, which can be inside a line.
- A read that returns no lines (an `offset` past the end, an empty file) has
  `null` for `lines` and `bytes`. An image read has `null` lines and the whole
  file as its bytes.

A `list` records the directory and the entries on the page, and whether more
pages follow. It opens no file but to sniff for binary, so it has no digest:

```json
{"tool": "list", "tool_use_id": "c2", "path": "src",
 "entries": [{"name": "db.py", "kind": "file", "size": 4210, "binary": false},
             {"name": "lib", "kind": "dir", "size": null, "binary": false}],
 "more": false}
```

A `search` records what was asked, and each file on the page that contributed
results, in page order. With a `pattern`, a file carries its whole-file
`sha256` and `size` and the line numbers returned from it. Without one no file
is opened, so a file carries only its `path`. Search counts lines at `\n`
endings, as its results do; `read` also ends a line at a lone `\r` and at the
other Unicode line boundaries, so for a file holding those the two numberings
can differ:

```json
{"tool": "search", "tool_use_id": "c3", "path": "src", "glob": "*.py",
 "pattern": "PORT", "more": false,
 "files": [{"path": "src/db.py", "sha256": "<64 hex>", "size": 4210,
            "lines": [14, 31]}]}
```

The event detail key, the session field (which defaults to an empty list, so
an older session loads) and the `evidence` key of `nare contract` are additive
and keep the contract version.

## Read-only image input

    nare run --yes --jsonl --contract 2 --provider openai --model VISION_MODEL \
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
keep the contract version; existing text, event, result and exit-code meanings remain
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

## Answer schemas

`--schema FILE` names a JSON Schema the final answer must satisfy. nare
validates it with its own validator, which implements the keywords below and
**refuses** a schema using any other keyword, or a supported keyword in a shape
it does not implement, before any model call (exit 2, nothing on stdout). A
keyword is enforced whole or the schema is refused; nothing is checked in part.
A schema generated by pydantic's `model_json_schema()` and similar generators
validates without edits.

| Kind | Keywords |
| --- | --- |
| Types and shape | `type` (a single type name), `properties`, `required`, `items` (one schema), `additionalProperties` (`true`, `false`, or a schema every unnamed property's value must satisfy), `enum`, `const` |
| Strings | `minLength`, `maxLength` (counted in characters), `pattern` |
| Arrays | `minItems`, `maxItems`, `uniqueItems` |
| Numbers | `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum` (numbers, not the draft 4 booleans) |
| Composition | `anyOf`, `oneOf`, `allOf` (each a non-empty list of schemas) |
| References | `$ref`, `$defs`, `definitions` |
| Annotations, never checked or applied | `title`, `description`, `default`, `$schema` |

`$ref` resolves local JSON pointers only: `#`, `#/$defs/Name`,
`#/definitions/Name`, or any other pointer into the same document. Recursive
references are supported. A remote reference (anything not starting with `#`),
a reference that points at no schema, and a reference cycle that returns to
itself without descending into a property or an item are refused. Keywords
beside a `$ref` apply as well. `pattern` is matched with Python's `re.search`,
so it is unanchored as JSON Schema specifies; a pattern Python cannot compile is
refused. `const`, `enum` and `uniqueItems` compare by JSON equality: `1` equals
`1.0`, and `true` is not `1`. `default` is never written into the answer.
A schema-valued `additionalProperties` is how generators describe a map (a
pydantic `dict[str, X]`, a zod `record`, an OpenAPI map): every property not
named in `properties` is checked against it, and it may be or contain a `$ref`.

Not supported, so refused: `format`, `not`, `if`/`then`/`else`,
`patternProperties`, `propertyNames`, `minProperties`, `maxProperties`,
`dependentSchemas`, a list-valued `type`, and remote references.

Every validation error names the JSON path of the failing value, for example
`findings[0].title: expected at most 80 characters, got 81` or, for a map value,
`env.HOME: expected string, got integer`, and all errors go
back in the one correction turn. A failed `anyOf` or `oneOf` lists each
alternative's errors.

### Schema corrections

`--max-corrections N` sets how many times a failing answer may be corrected in
one run. The default, 1, is one correction and then `schema_violation`. A
correction is free, and does not count against N, when the answer has fewer
distinct errors than every earlier failing answer in the run. Free corrections
need N above 1, and `--max-corrections` requires `--schema`.

Each correction names the errors an earlier answer made that this one no longer
makes, and asks the model to keep those fixes. The result's `budget` gains
`schema_corrections` (the limit) and `used_schema_corrections` (the charged
corrections), and a `corrections` list gives each correction's `charged` flag
and `codes`, the distinct rules the answer broke, each named without the value
it rejected. The list is present only when a correction happened. Each correction is also a `progress` event whose
`schema_correction` detail carries the same fields. The budget is per run: a
resume starts it again, as the correction round is per run.

### Read requirements

`--require-reads N` holds a final answer until the run has read at least N
distinct files, and `--require-read PATH` (repeatable) holds it until each named
path has been read.

`--require-cited-reads POINTER` (repeatable, and `require_cited_reads=[...]` in
Python) holds it until each path the answer cites has been read. It needs
`--schema`. POINTER is a JSON pointer (RFC 6901: `/` separates tokens, `~1` is
`/`, `~0` is `~`) into the answer, and `*` matches any array index or object
key. The string found at each match is a cited path, compared with evidence the
same way `--require-read` compares its paths. Quote the pointer in the shell,
since `*` is a glob:

    nare run --schema manifest.json --tools read --require-cited-reads '/checks/*/path' "..."

Only string values are paths; a value that is not a string is skipped, and the
schema is what types it. A pointer that matches nothing holds nothing, so check
it against the schema: an optional field may be absent from an answer. A cited
path must have been read with `read`. A path that appeared in a `search_files` or
`list_dir` result was never read, and does not count. A path outside the root
cannot be read, so the correction asks the model to remove it. A malformed
pointer (no leading `/`, an empty token, an unknown `~` escape) is refused at
startup, as is a cited requirement without `--schema`. Paths are relative to `--root`. Only a read that succeeded
and stayed inside the root counts; a failed read, or one refused as outside the
root, does not. A read with `offset` or `limit` counts as a read of its file. A
link inside the root counts as its target, and a link that points outside it is
refused at startup. Without `--root` a path may be any path the read tool
accepts. A requirement needs the `read` tool.

A held answer is a correction, charged against the same budget as schema
corrections (`--max-corrections`), and the model is told what is missing. A
read correction is never free, and it leaves the tools offered, even under
`--structured-output`. When the budget is spent the run ends `error` with
`reads_required`, and any `output` the answer had already validated stays on the
session, as it does for `schema_violation`. `--max-corrections` sets this budget
too, so it is accepted with a read requirement and no `--schema`. A count of
reads is over the session's life, so a resumed run keeps the files it already
read.

Each held answer names the unread paths it cites, in the same correction as any
other read hold, so the model reads them before it answers again. The hold is
charged once per answer that still cites an unread path, against the same budget.

With a requirement set, the result gains `reads`: each distinct file read, once,
with its `path`, `sha256` and `size` (the latest read's digest), so a caller can
check what its answer cites against what was read. A correction entry carries
`kind`, `reads` for a read correction and `schema` for a schema one. Without a
requirement the result has no `reads` key.
### Structured errors and checks

Every complaint about an answer is a structured error, in the schema correction
and in the event stream. Each has the same four keys:

| Key | Meaning |
| --- | --- |
| `code` | The rule broken: `missing`, `unexpected_property`, `type`, `enum`, `const`, `min_length`, `max_length`, `pattern`, `min_items`, `max_items`, `unique_items`, `minimum`, `maximum`, `exclusive_minimum`, `exclusive_maximum`, `any_of`, `one_of`; from the answer's text, `invalid_json` or `duplicate_key`; from a check, whatever the check gives |
| `location` | The keys and indexes from the answer's root, as a list (`["findings", 0, "title"]`); `[]` for the root |
| `message` | The message as it has always been stated, `findings[0].title: expected string, got integer` |
| `guidance` | What the model should do about it, or `null` |

An `unexpected_property` error carries removal guidance: it names the key, says
to remove it rather than rename it, and lists the keys the schema does name. The
correction states that guidance; every other correction states the message only,
as before. The `errors` list is on each `error` event for a schema violation and
on each `progress` event for a schema correction.

A caller can add checks the schema cannot express, such as ids that exist or
cross-field rules, with the Python entry point `nare.run(..., check=fn)`. `fn`
receives the parsed answer and returns its errors: `SchemaError` values from
`nare.schema`, mappings with `code`, `location`, `message` and `guidance`, or
plain strings. An empty result passes. A `fn` with two positional parameters is
also given the run's reads as a second argument, the records of the `reads` key.
A check runs only on a final answer that passes the schema, and its errors go into the same correction as schema
errors, under the same budget. The text of a turn that calls tools is never
checked, and while a check is set such a turn keeps no output. A check that raises ends the run with
`status` `error`, `stop_reason` null, and an error event reading
`check raised <Type>: <message>`; nare does not hand the exception to the
caller. `check` requires `schema`. The command line takes no check: it needs
code, which `nare run` does not load.

### Finding the document in an answer

The answer is the final turn's text. nare takes the JSON value from it, then
validates that value:

1. the whole text, if it parses as JSON;
2. otherwise the first fenced code block, with or without a language tag,
   that parses;
3. otherwise the one document that starts at the first `{` or `[`, with any
   prose before it and after it tolerated. `Here it is: {"a": 1}` and
   `{"a": 1}` followed by `Note: fixed the env.` both yield `{"a": 1}`.

nare never repairs or merges JSON (a trailing comma or single quotes stay
invalid), and never picks between documents. These are refused, and the
refusal says which it was, so the correction turn tells the model what to fix:

| Answer | Message |
| --- | --- |
| No `{` or `[` at all | `no JSON document found in the answer` |
| The first `{` or `[` starts invalid JSON | `no JSON document found in the answer: the text at character N is not valid JSON (...)` |
| The answer ends inside the document | `the JSON document starting at character N is incomplete: ...` |
| A second document after the first | `the answer has two JSON documents, at characters N and M; send exactly one` |
| Text after the document that starts a document the answer ends inside | `the answer has text after the JSON document that starts an incomplete JSON document at character N` |
| A document with an object that repeats a key, at any depth, on any of the routes above | `findings[0].scope: duplicate key: file`, one clause per repeat, naming the key and the path to its object (`duplicate key: a` at the root). The answer is not read with the last value of the repeated key |

Character positions count from 0 in the final turn's text. Prose after the
document may hold braces or brackets that are not JSON (`{braces}`); one that
does decode, such as a citation `[1]`, counts as a second document.

## Schema-constrained answers

    nare run --yes --jsonl --contract 2 --provider openai --model MODEL \
      --base-url "$PROXY" --tools read --root "$PWD" \
      --schema answer.schema.json --structured-output json_schema "..."

`--structured-output json_schema` asks the backend to enforce the `--schema` at
decode time on the turns where that is possible, on both rails. A constrained
turn sends **no tools** and the rail's own format. On the openai rail, the
OpenAI-compatible response format:

    "response_format": {"type": "json_schema",
                        "json_schema": {"name": "answer", "schema": {...}, "strict": true}}

On the anthropic rail, Anthropic's structured outputs, beside any
`output_config.effort`:

    "output_config": {"format": {"type": "json_schema", "schema": {...}}}

Two kinds of turn are constrained:

- the one schema correction turn, after a no-tool answer failed the schema. It
  replaces the free-text reprompt with tools offered; the reprompt text is the
  same;
- every turn of a run whose policy offers no tools.

Tool-using turns are never constrained. Measured on vLLM behind LiteLLM, a
request carrying both tools and a json_schema response format cannot call a
tool: the model returns an invented schema-valid answer instead. A first
answer is known to be final only after it is generated, so a valid
unconstrained answer is accepted as before. `strict` is true so a backend that
cannot honour the schema refuses the request instead of answering freely.

nare's validator stays authoritative. A constrained answer is extracted and
validated like any other, and a correction that still fails ends the run with
`stop_reason` `schema_violation`. Each constrained turn records one
`progress` event whose `detail` is `{"structured_output": {"mode":
"json_schema", "tools_withheld": true|false}}`. It is recorded before the
request and emitted with that turn's events, so under `--stream` the turn's
live deltas can reach stdout first; count markers, do not use them to label
deltas. A resumed run offers tools again, because the correction round is
per-run state. A backend that returned tool calls on the tool-free correction
anyway would have them run, and the rest of the run would stay constrained
and tool-free; a backend following the OpenAI specification cannot.

On the openai rail the schema is forwarded exactly as written, with its
references, composition and bounds; nare does not flatten or trim it to suit a
backend. A backend that cannot enforce some keyword refuses or ignores it under
its own rules, and nare's validator still checks the answer.

### Keyword limits on the anthropic rail

Anthropic refuses a keyword it cannot enforce with a 400 instead of ignoring
it, so the anthropic rail sends the schema through the Anthropic SDK's own
`transform_schema`. What the decode-time constraint then enforces, and what only
nare's validator checks after decoding:

| Keyword | At decode time | Checked by nare after decoding |
| --- | --- | --- |
| `type`, `properties`, `required`, `items`, `enum`, `anyOf`, `allOf`, local `$ref` and `$defs`, `minItems` of 0 or 1 | enforced | yes |
| `oneOf` | sent as `anyOf`, so exclusivity is not enforced | yes |
| `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `minLength`, `maxLength`, `pattern`, `maxItems`, `minItems` above 1, `uniqueItems`, `const`, `default` | moved into the description, not enforced | yes |
| `additionalProperties` | every object is sent closed (`false`), so a declared property is the only one decoded | yes |

`nare contract` names the keywords in the third row under
`structured_output.anthropic_not_enforced_at_decode`. A constrained answer that
breaks one of them fails validation as any answer does: the correction turn
follows, and a second failure ends the run with `schema_violation`.

These schemas are refused at startup on the anthropic rail (exit 2, nothing on
stdout, the reason on stderr), instead of with a 400 on the correction turn
after the first answer was spent:

- a subschema with no `type`, `anyOf`, `oneOf`, `allOf` or `$ref`, which the
  transform cannot convert;
- a map: a schema-valued `additionalProperties`, or `true` on an object that
  declares no properties, which a closed object would decode with no entries;
- `definitions` instead of `$defs`, which the transform does not carry;
- `allOf` with a `$ref`, which Anthropic does not support;
- an `enum` holding objects or arrays;
- more than 24 optional properties, or more than 16 properties with a union
  type (`anyOf` or a type list), Anthropic's documented limits for one
  request.

Anthropic can still refuse a schema past its internal grammar limits ("Schema
is too complex for compilation"); that is a provider failure (`stop_reason`
null), as a strict-mode refusal is on the openai rail.

`strict: true` follows OpenAI's strict mode, which refuses (HTTP 400) a schema
that does not mark every property required with `additionalProperties` false.
Such a refusal ends the run as a provider failure (`stop_reason` null), not as
`schema_violation`. vLLM enforces the schema without that restriction.

`--structured-output` without `--schema`, or with a schema the anthropic rail
refuses, never starts (exit 2). The one exception is a resume whose budget is already
spent: it reports that result without building a transport, so no model is
called. In the library, `run(structured_output="json_schema")`
raises `ValueError` before any turn when there is no schema, the transport
does not set `supports_structured_output = True`, or the transport's
`check_response_schema(schema)` refuses the schema; a supporting transport's
`turn` and `stream_turn` accept a keyword-only `response_schema`. Without the
flag every request, event and session field is unchanged, and transports are
called with `(messages, tools)` only. `nare contract` advertises the capability
under `structured_output`. The flag, event detail and discovery field are
additive and keep the contract version.

## Correction by resume

A caller whose own checks reject an answer can correct it without starting
over. The transcript is already in the session file, so a resume sends only
the correction, not the original prompt again:

    nare run --yes --jsonl --contract 2 --resume review.json \
      --tools none --max-turns 2 --schema stricter.schema.json \
      "Finding 2 cites the wrong file. Answer again with the file that holds it."

- The prompt is appended to the transcript as a follow-up, so the model
  answers with the whole earlier run in view.
- `--tools none` offers no tools: every turn is an answer. With
  `--structured-output json_schema` on the openai provider, every turn of
  such a run is constrained (see
  [Schema-constrained answers](#schema-constrained-answers)).
- `--max-turns 2` allows the answer and nare's one schema correction turn.
  The correction round is per run, so a resume has its own. A second invalid
  answer ends the run `error`/`schema_violation`, exit 1.
- The system prompt the session recorded is sent again unless a system option
  is given (see [System prompt input](#system-prompt-input)).
- Budgets carry over: usage is cumulative across resumes, and persisted token
  and dollar limits still apply unless a flag or the environment overrides
  them. A session already at a limit refuses before any model call (see
  [Budgets and partial results](#budgets-and-partial-results)).
  `--max-turns` counts this invocation only.

### A changed schema

The session records the SHA-256 of the schema it stated to the model, in
`schema_sha256`, computed over the schema's JSON with sorted keys and no
whitespace, so reordering keys or reformatting the file is not a change.

- The same schema on a resume adds nothing to the transcript.
- A different schema adds one user message before the first turn, after the
  follow-up prompt: `The required JSON Schema has changed.` and then the new
  schema, stated exactly as the first one was. One `progress` event says so,
  with `detail` `{"schema": {"changed": true}}`, and the new digest is
  recorded. Answers are validated against the new schema.
- A retained `output` that does not satisfy the new schema is dropped, so
  `result.output` is always a document that passes the schema this run was
  given. One that does satisfy it is kept.
- A resume without `--schema` states nothing and validates nothing.
- A file saved before nare recorded the digest has `schema_sha256` absent. Its
  first resume with a schema states nothing new, as before, and records the
  digest from then on.

A `schema_sha256` that is neither a string nor `null` makes the session file
malformed (exit 2). The session field and the event detail are additive and
keep the contract version.

## Forking a saved session

`--fork` branches a saved session. It resumes the file at `--resume PATH`, but
writes the continued session to `--session`, a new file, and never to PATH:

    nare run --yes --jsonl --resume review.json --fork \
      --session review-alt.json "Try the other approach."

- `--fork` needs `--resume PATH` and `--session PATH`. Exit 2, with nothing on
  stdout and no model call, when either is missing, when `--session` names the
  resumed file, or when `--session` already exists. A fork never overwrites.
- PATH is only read. Its bytes are the same after the run, whatever the run
  does, including a failure or an interruption.
- The new file has a new session `id`, reported as `session_id` on the result
  line. Its transcript is the one PATH held, then the prompt, as on a resume.
- Redaction, the recorded system prompt and budgets apply exactly as on a
  resume. So does the changed-prefix rule: a fork that changes the tool set or
  the system prompt drops the signed thinking from the new file, says so in a
  `progress` event, and leaves the thinking in PATH.
- A `progress` event with `detail` `{"fork": {...}}` says the fork happened,
  before the first turn.
- Process groups a bash call left running in PATH are not carried over: the new
  file does not offer to kill them.
- `nare tui` has no `--fork`; argparse refuses it with exit 2.

The new file records where it came from in its `forked_from` field: an object
or `null` (`null` in every file that is not a fork). It is redacted on save,
like the system prompt.

| Key | Meaning |
| --- | --- |
| `session_id` | The `id` of the session forked from |
| `turn` | Its `turns` count when the fork was made |
| `path` | The absolute path it was read from, or `null` for a library fork given none |

A caller that reads `forked_from` before it trusts a file's history knows which
run the branch came from. The field, the event detail and the flag are additive
and keep the contract version.

The same operation in Python is `nare.fork(session, source=path)`. It returns a
copy with a new `id` and the `forked_from` record, and leaves `session` as it
was. Call `reopen` on the copy with the follow-up, as the command line does,
then `run` it, and `save` it to a new path. `transport` is one built with
`make_transport`, as in [Choosing a transport](#choosing-a-transport):

    from pathlib import Path

    from nare import fork, loads, run
    from nare.session import reopen, save

    original = loads(Path("review.json").read_text(encoding="utf-8"))
    branch = fork(original, source="review.json")
    reopen(branch, "Try the other approach.")
    async for event in run(branch, transport=transport):
        ...  # as in "run and step"
    save(branch, "review-alt.json")

## Prompt-cache breakpoints

    nare run --yes --jsonl --provider openai --model MODEL \
      --base-url "$PROXY" --cache-breakpoints "..."

The anthropic rail always marks two prompt-cache breakpoints: the newest
block of the transcript and the last tool. `--cache-breakpoints`, or
`NARE_CACHE_BREAKPOINTS` set to `1`, `true` or `yes`, marks the same two
positions on the openai rail with `cache_control: {"type": "ephemeral"}`.
Chat Completions has no breakpoint of its own; the marker is for a proxy that
forwards it to a backend that caches, such as LiteLLM in front of Anthropic or
Bedrock. Where the marker goes:

- the last message's last content part. A string content becomes one text
  part carrying the marker; a tool message's content becomes a list of one
  text part. A tool result's image travels in a user message after its tool
  message, so the image part is the end of that block and carries the marker.
- the last tool call, when the last message is an assistant tool call.
- the last tool's `function` object, when the request offers tools. A
  schema-constrained turn offers none, so it marks none.

The system prompt is not marked, matching the anthropic rail. A caching
backend orders the prefix as tools, system prompt, messages, so the tool marker
caches the tools alone and the newest-block marker caches all three. Thinking is never sent
back, so a transcript that ends on thinking marks the block before it, as the
anthropic rail does. Only the marked message changes shape. Every earlier
message is the same bytes as an unmarked request, so last turn's prefix is
still this turn's prefix.

The option is off by default, and without it every request is byte-identical
to a request from before the option existed. When it is on and the server
answers with a 4xx, the error keeps the server's reason and adds that the
request carried breakpoints and that `--cache-breakpoints`
(`NARE_CACHE_BREAKPOINTS`) is the option to turn off. nare does not detect
whether a proxy's backend caches. A cache hit is reported as `cache_read`
(`prompt_tokens_details.cached_tokens`), and a backend that ignores the marker
reports zero. The anthropic provider accepts the option and changes nothing,
since it always marks. `nare contract` advertises it under
`cache_breakpoints`; the flag and discovery field are additive and keep the
contract version.

Servers with automatic prefix caching (OpenAI, vLLM with prefix caching on,
and others) need no marker, but they hit only on a stable prefix. nare keeps
its own part stable: the system prompt is fixed for a run, and tools are
offered in a fixed order (built-in tools first, then MCP tools in the order
their servers list them). A caller keeps the rest stable: the same `--tools` and
the same MCP configuration across turns and resumes. A resume with no system
option sends the system prompt the session recorded (see
[System prompt input](#system-prompt-input)), so the system prompt stays stable
without being resent. Context elision rewrites old tool results, so the
turn that elides reads back only the prefix in front of the first rewritten
message, and the turns after it hit again.
