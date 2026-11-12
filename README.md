# nare

A standalone agent harness: a headless CLI and a library that run a model
against a project's tools and report what happened as typed events, a session
file and an exit code.

## Install

Every release publishes a wheel and a container image. nare is not on PyPI, so
`pip install nare` does not work and this file will not pretend it does.

Pin a version. `latest` is fine for a look around and wrong for anything that
records what produced a result. A published release is never deleted or moved,
so a pin keeps working; see [the release policy](docs/contract.md#releases).
The newest release is on the [releases page](https://github.com/ViviDynamics/nare/releases/latest),
or `gh release view --repo ViviDynamics/nare --json tagName --jq .tagName`.

    # wheel, from the release
    V=2026.11.9
    uv tool install "https://github.com/ViviDynamics/nare/releases/download/$V/nare-$V-py3-none-any.whl"

    # container, same argv as the CLI
    docker run --rm -e ANTHROPIC_API_KEY -v "$PWD:/work" \
      ghcr.io/vividynamics/nare:$V run --yes --jsonl "..."

    # from source, at a tag
    pip install "git+https://github.com/ViviDynamics/nare@$V"

`nare --version` prints the installed release, the `result` line carries it as
`nare`, and so does the session file. A caller records the version that
produced a result rather than whatever main was that day.

For the terminal UI, install the extra:

    uv tool install 'nare[tui]'

## Providers

Two model rails, and a third for Claude on AWS. `--provider anthropic` (the
default) speaks Anthropic's API and reads `ANTHROPIC_API_KEY`. `--provider
openai` speaks Chat Completions, which is also what a LiteLLM proxy, vLLM,
llama.cpp and Ollama speak, and reads `OPENAI_API_KEY`; point it anywhere with
`--base-url`. `--provider bedrock` is the Anthropic rail reached through AWS.

### Claude on Amazon Bedrock

`--provider bedrock` calls Claude on Amazon Bedrock directly, with no API key
and no proxy. The request, streaming, tools, thinking, usage and retries are
the anthropic rail's. Credentials come from the standard AWS chain: the
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` pair, `AWS_PROFILE` (SSO
profiles included), IRSA web identity in EKS, or an instance role. An
`AWS_BEARER_TOKEN_BEDROCK` bearer token works too. The region comes from
`--region`, then `NARE_AWS_REGION`, `AWS_REGION`, `AWS_DEFAULT_REGION`, and the
active profile's region in the AWS config file. A missing region or credential
exits 2 and names both.

    nare run --yes --provider bedrock --region us-east-1 \
      --model us.anthropic.claude-sonnet-4-5-20250929-v1:0 "..."

`--model` takes a Bedrock model or inference-profile ID. The base install does
not carry the AWS signer: `pip install 'nare[bedrock]'` adds it, and the
container image includes it. `--base-url` sets the Bedrock runtime endpoint,
for a VPC interface endpoint. Context-window discovery is skipped, so pass
`--context-window` when the model's window matters. `--structured-output` sends
the anthropic rail's `output_config.format`, and `--temperature` is refused, as
on the anthropic rail. Bash commands do not see the AWS credential variables, as they do not see
the provider keys. The IAM actions the rail needs are `bedrock:InvokeModel` and
`bedrock:InvokeModelWithResponseStream` on the model or inference profile.

The rail is tested offline, with the real SDK client, real SigV4 signing and an
AWS event stream. Against a self-hosted Bedrock-compatible endpoint, the wire path
is verified: SigV4 signing, the InvokeModel request bodies, tool calls and their
results, structured output, effort, thinking, the turn-limit summary request and
usage ([evidence](docs/evidence/269/README.md)). Streaming did not complete on that
endpoint, because its stream framing differs from AWS's.

Against AWS Bedrock itself, no turn has completed yet, so none of these features is
verified there. Still open on AWS: streaming, prompt-cache counters, cut-off stop
reasons, a fixed thinking budget on Haiku 4.5, and bash credential withholding
([#301](https://github.com/ViviDynamics/nare/issues/301), tracking
[#269](https://github.com/ViviDynamics/nare/issues/269)).

For a LiteLLM proxy, `--proxy-num-retries 0` sends `num_retries: 0` on every
OpenAI-compatible request, including streamed and schema-constrained turns.
The option accepts a nonnegative integer and is omitted by default, preserving
the existing request. It controls proxy retries only; verify fallbacks and the
proxy's support separately. Other providers refuse the option.

    nare run --yes --provider openai \
      --base-url https://your-proxy/v1 --model some/model "..."

nare resends a model request that fails transiently, on both rails: a
timeout, a dropped connection, a stream that ends early, or HTTP 429, 502,
503, 504 or 529. `--request-retries N` (default 3, 0 turns it off) and
`--request-retry-seconds S` (default 60) bound it per request, and
`--request-timeout S` (default 600) sets the HTTP timeout. Each has a
`NARE_REQUEST_*` environment variable. A 401 or 404 is never resent. Each
retry is a `progress` event, and the `result` line counts them in `retries`.
See [provider retries](docs/contract.md#provider-retries-and-the-request-timeout).

A provider or proxy that refuses a request for capacity before it dispatches
anything (HTTP 429 with a capacity marker) can be waited out with
`--capacity-queue`, which resends the same request. It is off by default and is
not a retry: it has its own limits (`--capacity-wait-seconds`,
`--capacity-refusals`) and its own counts in the `result` line. See
[capacity queueing](docs/contract.md#capacity-queueing).

`--capacity-adaptive` (with `--capacity-queue`) goes further: it keeps a limit on
the requests in flight to each provider route and cuts it on refusals, then
raises it again after a quiet period. `--capacity-shared-dir` shares that limit
across the nare processes on one host. See
[adaptive capacity](docs/contract.md#adaptive-capacity).

`--effort` takes `low`, `medium`, `high`, `xhigh` or `max`. On the anthropic
rail it sends `output_config.effort` with adaptive thinking; on the openai rail
it is passed through as `reasoning_effort`, and what a level means is the
server's. For an older Claude model that still needs a fixed thinking budget,
`--thinking-budget TOKENS` (anthropic only) sends one instead. See
[reasoning effort](docs/contract.md#reasoning-effort).

Which rail matters for reasoning models. A model whose reasoning and answer
arrive together can lose words or whole answers when a proxy translates to
Anthropic's shape. LiteLLM can drop the start of an answer where reasoning
switches to text, making replies start mid-sentence or appear silent. nare
warns once per transport when a reply contains unsigned thinking, a sign of
proxy translation. Use `--provider openai` for these models, or configure the
proxy to route them through its OpenAI adapter. The routes use different API
keys, so nare does not switch automatically. On the openai rail the reasoning
arrives as a `thinking` event and the answer stays the answer.

On the anthropic rail nare marks the newest message and the last tool for
prompt caching, so a backend that caches reads each turn's history back at its
cache price. Thinking that arrives without a signature, which is what a proxy
makes when it translates a reasoning model, is kept in the session but not sent
back to the model, and neither is empty text.

The openai rail marks nothing by default, because some OpenAI-compatible
servers reject a field they do not know. `--cache-breakpoints` (env:
`NARE_CACHE_BREAKPOINTS=1`) marks the same two positions with
`cache_control: {"type": "ephemeral"}`, for a proxy such as LiteLLM that
forwards it to a backend that caches. If a server refuses the request, the
error names the option to turn off. See
[prompt-cache breakpoints](docs/contract.md#prompt-cache-breakpoints).

There is no `--api-key` flag on either: argv is world-readable through `ps`
and `/proc`.

The fake rail is for tests. `--provider fake --replay script.json` replays a
scripted list of replies, with no key and no network, through the real tools,
session file, events and exit codes, so a caller's CI can cover a blocked run,
a truncated turn and a transport error. See
[scripted runs](docs/contract.md#scripted-runs) for the format and the four
example scripts.

## Use

    nare run --yes --jsonl "add a docstring to foo() in bar.py"

Stdout is typed JSONL, terminated by one `result` line carrying status,
questions, usage, and stop_reason. `usage.total` is the token count to use
(cache included, what `--budget-tokens` counts), and a `done` run carries its
final reply in `text`. Stderr is logging. `--session PATH` writes
the session, and `--resume PATH` continues it. The file is redacted, and from
the first message redaction changes on it leaves out signed and redacted
thinking, because newer Claude models reject thinking whose earlier context
changed. A file an older nare saved has that thinking removed when it loads.

Use `--prompt-file prompt.txt`, or positional `-` with stdin, to send a prompt
without putting its text in argv. Both routes read UTF-8 exactly and work with
`--resume`; choose one source. See [the input contract](docs/contract.md#prompt-input).

`--yes` approves every tool call, including `bash`. nare runs
model-generated shell commands with your privileges. Unless you pass
`--bash-sandbox`, it has no sandbox of its own, so treat it like piping a
script you have not read: run it in a container, a VM, or a throwaway checkout.

nare sends a short default system prompt that keeps the agent in scope: no
installs, long-lived services or commits unless asked, claims limited to what
tool output showed, and a list of anything it left running. `--system TEXT`
replaces that prompt, and `--system ''` sends none. `--append-system TEXT`
adds to whichever prompt is in effect, which is how a caller adds a persona
and keeps the rules. A run with `--tools none` gets no default, because every
rule in it is about tools. Some OpenAI-compatible servers refuse a system
message, such as o1-mini or a model whose chat template has no system role;
run those with `--system ''`. `--system-file PATH` and
`--append-system-file PATH` read the same text from a UTF-8 file, which keeps a
long prompt out of argv. The session records the system prompt it sent, and a
`--resume` without a system option sends that one again.

Two flags narrow a run. `--tools read,bash` allows only the tools you name,
and `--tools none` allows no tool at all, which is what a caller wants when
it needs one answer and no side effects. `--root DIR` confines `read`,
`list`, `search`, `write` and `edit` to a directory, refusing any path that
leaves it, including through a symlink. Both are recorded in the session, so a
run's permissions can be read afterwards rather than inferred.

`--tools read,list,search` is a read-only session that can still find its
way around: `list` pages through a directory and `search` finds files by glob
and lines by regular expression, both in pure Python, never following a
symlink, so exploring a tree needs no `bash`. See
[the navigation contract](docs/contract.md#read-only-navigation).

Every `read`, `list` and `search` also leaves an evidence record, in a
`progress` event and in the session file: the path, the SHA-256 and size of
the whole file, and the lines and bytes actually returned, so a caller can
check an answer against exactly what the model was shown. The records hold no
file content. See [the evidence contract](docs/contract.md#read-evidence).

`--image-input` declares that the selected model accepts PNG/JPEG input. It lets
`read` return capped images under the same root and permissions; omitted, image
reads return a named tool error. See [the image contract](docs/contract.md#read-only-image-input).

`--root` gives `bash` that directory to start in. It is not a jail: a shell
can still walk upward. `--bash-sandbox` (with `--root`) runs each `bash`
command under the OS sandbox instead: sandbox-exec on macOS, bubblewrap on
Linux. Writes stay in the root and a private temp dir, credential folders are
unreadable, and the network is off unless `--bash-network` allows it. It is
off by default, and where the sandbox cannot start nare refuses to run rather
than run unconfined. See [the bash sandbox](docs/contract.md#bash-sandbox).

`bash` runs without the provider keys nare reads (`ANTHROPIC_API_KEY`,
`ANTHROPIC_AUTH_TOKEN`, `OPENAI_API_KEY`), so a command cannot print them.
`--bash-credentials` passes them through for a task that needs one, and the
session's policy records it. See [the bash environment](docs/contract.md#the-bash-environment).

`--schema FILE` makes the answer data rather than prose. The final answer must
satisfy the JSON Schema in that file. One JSON document is taken from the
answer (the whole reply, a fenced block, or one document with prose around it;
never a repaired or merged one); a violation is reported back to the model
once, and a second violation ends the run with `stop_reason=schema_violation`
without promoting the invalid object. Any earlier schema-valid output remains
available. A validating answer is parsed onto the session, carried
in the `output` event's detail, and included in the final `result` line.

`--structured-output json_schema` (anthropic, bedrock and openai) also asks the backend to
enforce that schema at decode time on the correction turn, which is then sent
without tools, and on every turn of a run that offers no tools. Tool-using
turns stay unconstrained, because a constrained request cannot call a tool, and
nare still validates every answer. On the anthropic rail some keywords (bounds,
lengths, patterns) are checked only by nare after decoding, and a schema
Anthropic cannot take is refused at startup. See
[docs/contract.md](docs/contract.md#schema-constrained-answers).

Python callers can run the same loop in-process, with `nare.run` and
`nare.step`, typed answers through `schema=` and `check=`, and caller tools.
Its budget arguments default to the command line's except `max_cutoffs`, the
command line reads its defaults from `NARE_*` variables, and the library adds no
default system prompt. With no `policy`, `nare.run` offers only `read`, `list`,
`search` and `ask`, whether or not it gets an `approve` callable. Pass a policy
such as `Policy()` to offer `write`, `edit` and `bash`; `approve` then gates each
call the policy allows.
See [calling nare from Python](docs/contract.md#calling-nare-from-python).

## Terminal UI

`nare tui` is the same loop for a person. Type a task, watch it stream, and
approve each tool call: **y** allows, **n** or **Esc** denies, **a** allows
that tool for as long as this `nare tui` runs (it is not saved to the
session). `edit` and `write` show a diff first. `read`, `list`, `search` and `ask` never prompt.

    nare tui "add a docstring to foo() in bar.py" --root .

**Esc** or **Ctrl-C** stops the current turn without losing the session; type
a follow-up to keep going. When nare asks a question, the answer is the next
thing you type. **Ctrl-Q** quits and leaves the last answer and a
`nare tui --resume` command on the terminal. An interrupt or a quit kills a
`bash` command that is still running. If a `bash` command leaves a process
running in the background, nare lists it when it finishes the turn and again
when you quit: **k** kills it, **l** leaves it running. A daemon that detaches
into a session of its own, as `pg_ctl` does, is not caught. **PageUp** and
**PageDown** scroll the transcript while you type; it stops following new
output until you scroll back to the bottom or send a prompt.

**Copy**: drag over text in the transcript with the mouse, then press Ctrl-C.
The selection copies during a turn too. A copy clears the selection, so the
next Ctrl-C interrupts. nare copies with the terminal's OSC 52 clipboard escape,
so the copy reaches your clipboard only if the terminal accepts that escape
(tmux may need `set-clipboard on`). The notice counts the characters nare
sent. It cannot tell whether the terminal kept them. To select with the
terminal instead, hold Shift while you drag (in most terminals). A folded
thinking box does not take a selection.
**Paste**: use the terminal's paste key (often Ctrl-Shift-V, Cmd-V on macOS, or
right-click). Ctrl-V inside nare does not read the system clipboard, and it
says so.

`nare tui --resume` with no path lists the saved sessions that started in the
current directory, newest first, with the first prompt, when each last changed,
its status and its turns. It lists sessions from `~/.nare/sessions` and from the
folder used before it (see below). Enter resumes one and saves back to its file;
Esc starts nothing. `nare run --resume` needs the path and lists the candidates
when it is missing.

Every TUI run is saved. Without `--session`, the file goes to
`$NARE_HOME/sessions/<id>.json`, outside the project. A `--session` path inside
`--root` or the current directory gets a warning, since `git add -A` would
commit it, unless the path is inside `NARE_HOME`.

### What lives in ~/.nare

`NARE_HOME` is the folder nare keeps its local files in. It defaults to
`~/.nare`, and it must be an absolute path. nare creates it with mode 0700 when
a TUI run first needs it, and session files are 0600. An existing folder keeps
its permissions. A relative value, or a sessions folder that cannot be written,
makes a TUI run that saves to the default location exit 2, with a message that
names `NARE_HOME` and `--session`. An explicit `--session PATH` does not read
`NARE_HOME` at all, so it still runs.

`NARE_HOME` is exempt from the inside-the-project warning. If you point it at a
folder you work in, sessions there are not warned about, so keep it outside
your projects.

- `sessions/` holds the TUI's saved sessions. Nothing removes them: delete the
  ones you no longer need.
- Nothing else is written there yet. Model and provider settings stay in
  environment variables or a project `.env`, and no API key is read from this
  folder.
- `nare run` saves a session only with `--session`, so a headless run writes
  nothing to `~/.nare`.
- Sessions saved before this change, under `$XDG_STATE_HOME/nare/sessions`
  (`~/.local/state/nare/sessions` when `XDG_STATE_HOME` is unset), are not
  moved. `nare tui --resume` lists them beside the new ones, and a resume by
  path works on either. A resumed session saves back to the file it came from.

`nare tui` takes the same provider, model, budget, tool, root, MCP, session
and resume flags as `nare run`, and not `--yes`, `--jsonl`, `--contract`,
`--schema`, `--prompt-file` or `--stream`. An MCP server that writes to stderr
can leave text on the screen until it is redrawn.

To watch a run something else started, point it at the run's session file:

    nare tui --attach path/to/session.json

The view is read-only and updates once per turn. It shows how long ago the
file was written, since `working` in a file can also mean the process died.
A run that was interrupted shows as `interrupted`, with how long ago.

## Session budgets, cost and context

    nare run --yes --jsonl --contract 2 --provider openai --model YOUR_MODEL \
      --max-tokens 8192 --budget-tokens 50000 --tools read --root "$PWD" \
      --schema findings.schema.json --session persona.session.json \
      "Review this checkout" > persona.events.jsonl

`--max-tokens` caps output for each response. `--budget-tokens` counts the whole
session: `input + output + cache_read + cache_write`, including turns before a
resume. Categories are disjoint: Anthropic input excludes cache reads and writes;
OpenAI cached prompt tokens are subtracted from input and counted in cache_read.
Repeated context sent on later turns consumes tokens again. Counts come from
provider usage, not a local estimate; absent usage cannot be reconstructed.

Budgets are checked after a complete step, including its tool results. The
crossing turn can overshoot the limit; there is no strict no-overspending
guarantee. `done` and `blocked` win on that turn, with actual usage still visible.
Otherwise exhaustion exits 1 with `status=error`, `stop_reason=budget`, and the
actual counters and effective limits in `result.usage` and `result.budget`.
No subsequent model call is made. A provider failure has a different stop reason
(or null), not `budget`.

The session saves cumulative usage and effective limits. On resume, explicit
flags override environment settings, which override saved limits. Omitted
limits retain the saved ceiling; they do not reset spending. An exhausted
session refuses another model call. Raise the total ceiling to continue:

    nare run --yes --jsonl --resume persona.session.json \
      --budget-tokens 100000 --schema findings.schema.json > persona.resume.jsonl

For partial findings, read `result.output` or the saved session's `output`: with
`--schema`, nare retains the latest schema-valid JSON document even from a tool
turn. It can be partial and is not a claim of completion. Invalid or incomplete
JSON is never promoted. Keep the JSONL files for completed events and the session
for the redacted transcript and matched tool results. Resumes preserve existing
valid output; pass the schema again for continued validation. See the
[termination and recovery contract](docs/contract.md#budgets-and-partial-results)
and [offline CLI evidence](docs/evidence/41/README.md).

| Setting | Environment | Meaning |
| --- | --- | --- |
| `--budget-tokens N` | `NARE_BUDGET_TOKENS` | Positive integer cumulative token limit |
| `--budget-usd X` | `NARE_BUDGET_USD` | Finite positive cumulative dollar limit |
| `--context-window N` | `NARE_CONTEXT_WINDOW` | Positive integer context window; otherwise backend, otherwise 32000 |
| `--compaction MODE` | `NARE_COMPACTION` | `nare run` only: `elide` (default) or `summary`, what happens first at the compaction point (below). `nare tui` has no such flag and ignores the variable: it always elides |
| `--compaction-trigger SHARE` | `NARE_COMPACTION_TRIGGER` | `nare run` only: the share of the window where compaction starts, default `0.8`. Must satisfy `0 < target < trigger <= 1` |
| `--compaction-target SHARE` | `NARE_COMPACTION_TARGET` | `nare run` only: the share that elision stops at, default `0.6`. Must be below the trigger |
| Input/output prices | `NARE_PRICE_IN`, `NARE_PRICE_OUT` | Both needed, dollars per million tokens |
| Cache prices | `NARE_PRICE_CACHE_READ`, `NARE_PRICE_CACHE_WRITE` | Optional dollars per million tokens |

Invalid settings exit 2 before transport construction, with no result on stdout.
Prices must be finite and non-negative. Nonstreaming replies prefer the backend's
reported dollar cost (`x-litellm-response-cost`, then
`x-litellm-response-cost-original`). Streamed replies ignore these headers because
they arrive before final usage; a streamed openai-rail reply from a LiteLLM proxy
takes `usage.cost` from the final usage chunk instead. Without reported cost, explicit prices take
precedence over prices discovered from `/v1/model/info`, then cost is unknown
(`usage.cost=null`). Calculated costs are labelled as estimates and carry
`usage.estimated=true`. Cache prices default to the
input price, which can overstate cache cost. An unknown turn makes cumulative
cost unknown. A USD budget stops after the first unknown-cost nonterminal turn
and names `NARE_PRICE_IN` and `NARE_PRICE_OUT`; done/blocked still win. Legacy
nonempty sessions without dollar accounting load with unknown cost.

With five or fewer turns left, nare tells the model and the person to wrap up.
A run with no tools (`--tools none` and no MCP tools) tells only the person,
as a `progress` event: every turn of it is an answer, with nothing to wrap up.
At `--max-turns` (50 by default), one extra, accounted turn with tools disabled
requests a summary. The run still exits with `error` and `stop_reason=max_turns`,
and explains that `continue` starts another run with the same turn allowance.
Token and USD ceilings still apply before a summary request.

`--max-tool-calls N` caps the tool calls one `nare run` runs, built-in and MCP.
Past the cap a call is refused with an error telling the model to answer; a
model that calls again ends the run with `stop_reason=tool_calls`.
`result.budget.used_tool_calls` reports the calls the invocation ran.

`--max-cutoffs N` (default 2) corrects a reply the model cut off, at the output cap
or by repeating one span. The correction says to reason briefly and answer completely,
and the reply's tool calls do not run. Past N the run ends `error` with
`stop_reason=max_tokens` or `repetition`. `result.cutoffs` counts the cut-off replies
over the session's life, and `result.budget.used_cutoffs` the ones this invocation saw.

`--timeout SECONDS` bounds one `nare run` by wall clock. At the deadline, or on
SIGTERM or SIGINT, nare stops the call in flight, kills the bash commands the
run started, saves the session and writes the result line with
`stop_reason=timeout` or `stop_reason=interrupted`, then exits 1. The session
resumes from its last completed turn.

Before each model call, old tool output is elided if estimated context reaches
80% of the window, stopping at 60% where possible. `--compaction-trigger` and
`--compaction-target` (or their environment variables) move those two shares,
so a model that degrades before its window is full can be compacted earlier.
The defaults are the ones above. If that is not enough, long
contents of old `write` calls and long `edit` texts are elided next. The task,
assistant text, the last two turns, error results, ask results and the inputs
of failed calls are protected. Every tool call keeps its result. Signed and
redacted thinking from the first elided message on is removed from the
session, because newer Claude models reject thinking whose earlier context
changed; the compaction event counts the blocks removed. If estimated context
still reaches the window, nare stops with `stop_reason=context` without
calling the model. This character-based estimate can undercount; a provider
can still reject context as `max_tokens`.
With a configured base URL, window discovery uses `/v1/model/info` with a
5 second timeout; failures use 32000. The lookup runs once per process for each
gateway and model, failures included. Direct endpoints make no discovery request.

`--compaction summary` (or `NARE_COMPACTION=summary`) has the model summarize
first. At the same trigger (80% by default), one extra request, accounted like a turn but not
counted as one, asks the model for a summary of its turns before the last two,
but only when those turns hold more than the summary's length bound plus a
quarter of the gap between the trigger and the target (6,400 tokens at a
48,000-token window by default), with the run's tools offered and `tool_choice` none. The summary replaces those
turns in the first message, labelled as the model's own, after the task and
what a caller or a person said in them (feedback on a resume, answers to
`ask`), which is kept word for word. nare's own notices in those turns, such as
the turns-left countdown and corrections, are left to the summary. When the
summary request fails or its reply is discarded, nare elides as above and does
not ask again in that run. When no summary is worth asking for, nare elides and
may ask at a later step. The session file keeps the replaced turns in
`summaries`. The mode is not saved: a resume without the flag elides. See
[the contract](docs/contract.md#compaction-modes).

nare validates the subset of JSON Schema that generators emit (types,
properties and maps, enums and `const`, bounds, composition and local `$ref`),
listed in [the contract](docs/contract.md#answer-schemas). A schema using
anything else is refused at startup rather than validated in part, because an
answer that was only partly checked is worse than one that was not checked at
all.

`nare contract` prints the machine contract this build speaks: the version,
the exit codes, the statuses, the stop reasons and the kind of ending each one
is, the event types, every `nare run` flag, and the redaction rule set. A
caller checks that a flag exists there instead of comparing release numbers,
and pins what it understands with `nare run --contract N`, and nare refuses
to start when the numbers differ rather than emitting a stream the caller
would mis-read.

`nare redact` applies the same rules nare applies to its events to whatever a
caller pipes through it, so a caller redacts its own logs with the rules it
already trusts instead of copying them. It is linear in the input size, and it
refuses to guess: stdin that is not readable UTF-8 text exits nonzero with
nothing on stdout. The rules are named in the contract, so a caller can tell
when they change.

See [docs/contract.md](docs/contract.md) for what a caller may rely on and what
changes the version, [docs/architecture.md](docs/architecture.md) for the
shape, and [docs/adr/](docs/adr/) for the decisions behind it.

## Working on nare

`bin/build` runs everything CI runs: formatting, lint, strict types, tests.

Agent workflow skills for this repo live in [`.agents/skills/`](.agents/skills/)
(`.claude/skills` and `.opencode/skill` point at the same copies): `conventions`,
`ci-safety`, `watch-ci`, `merge-pr`, `ship-issue`, `rebase-main`, `copilot-review`,
and `watch-ci-main`. They were adapted from the org's internal skill set at tag
`2026.11.1` (Copilot review policy from `2026.10.4`) and are maintained here, tuned to nare. To use them in a fresh clone:

    cp repo.env.example repo.env
    .agents/skills/ci-safety/scripts/check-wiring

The scripts need `gh`, `jq`, and `git`.

## Licensing

nare is source-available under the
[Elastic License 2.0](LICENSE). You may run, modify, and self-host it,
including commercially. You may not offer it to third parties as a hosted or
managed service.

## Contributing

Issues yes, pull requests no. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
policy and the reasoning behind it, and [SECURITY.md](SECURITY.md) for how to
report a vulnerability.

MCP tool sources are configured explicitly with `--mcp-config servers.json`.
Both stdio child servers and remote Streamable HTTP servers use the normal tool
allowlist and approval seam. For configuration, names (`server__tool`), errors,
credential isolation and resume semantics, see [the MCP contract](docs/contract.md#mcp-tool-sources).

With many MCP tools, `--mcp-defer` keeps their definitions out of the request: the model
finds a tool with `mcp_search`, loads it with `mcp_load` and calls it with `mcp_call`.
It is off by default, and [the contract](docs/contract.md#mcp-tool-deferral) says how it
behaves on resume.

A caller can also register its own tools. The Python API takes `nare.Tool` values as
`tools=` to `nare.run` or `nare.step`, and `nare run --command-tools tools.json` runs a
command for each call. Both follow the tool allowlist and the approval seam. See
[caller tools](docs/contract.md#caller-tools).

Use `--stream` for live text and reasoning events while a model turn runs.
It works on both providers and permits Anthropic outputs above the SDK's
nonstreaming ceiling. Streaming can keep an idle proxy alive when the provider
sends traffic; it cannot prevent a timeout before the first byte. See
[streaming semantics](docs/contract.md#streaming-model-turns).
