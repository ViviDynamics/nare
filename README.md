# nare

An internal agent harness for optimizing Coordinare across the environments it targets.

## Install

Every release publishes a wheel and a container image. nare is not on PyPI, so
`pip install nare` does not work and this file will not pretend it does.

Pin a version. `latest` is fine for a look around and wrong for anything that
records what produced a result:

    # wheel, from the release
    V=2026.9.7
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

Two rails. `--provider anthropic` (the default) speaks Anthropic's API and
reads `ANTHROPIC_API_KEY`. `--provider openai` speaks Chat Completions, which
is also what a LiteLLM proxy, vLLM, llama.cpp and Ollama speak, and reads
`OPENAI_API_KEY`; point it anywhere with `--base-url`.

    nare run --yes --provider openai \
      --base-url https://your-proxy/v1 --model some/model "..."

Which rail matters for reasoning models. A model whose reasoning and answer
arrive together can lose the answer in a translation to Anthropic's shape, and
then it looks silent rather than broken. On the openai rail the reasoning
arrives as a `thinking` event and the answer stays the answer.

There is no `--api-key` flag on either: argv is world-readable through `ps`
and `/proc`.

## Use

    nare run --yes --jsonl "add a docstring to foo() in bar.py"

Stdout is typed JSONL, terminated by one `result` line carrying status,
questions, usage, and stop_reason. Stderr is logging. `--session PATH` writes
the session, and `--resume PATH` continues it.

Use `--prompt-file prompt.txt`, or positional `-` with stdin, to send a prompt
without putting its text in argv. Both routes read UTF-8 exactly and work with
`--resume`; choose one source. See [the input contract](docs/contract.md#prompt-input).

`--yes` approves every tool call, including `bash`. nare runs
model-generated shell commands with your privileges and no sandbox of its
own, so treat it like piping a script you have not read: run it in a
container, a VM, or a throwaway checkout.

Two flags narrow a run. `--tools read,bash` allows only the tools you name,
and `--tools none` allows no tool at all, which is what a caller wants when
it needs one answer and no side effects. `--root DIR` confines `read`,
`write` and `edit` to a directory, refusing any path that leaves it,
including through a symlink. Both are recorded in the session, so a run's
permissions can be read afterwards rather than inferred.

`--image-input` declares that the selected model accepts PNG/JPEG input. It lets
`read` return capped images under the same root and permissions; omitted, image
reads return a named tool error. See [the image contract](docs/contract.md#read-only-image-input).

`--root` gives `bash` that directory to start in. It is not a jail: a shell
can still walk upward, so real confinement stays the sandbox's job.

`--schema FILE` makes the answer data rather than prose. The final answer must
satisfy the JSON Schema in that file; a violation is reported back to the model
once, and a second violation ends the run with `stop_reason=schema_violation`
without promoting the invalid object. Any earlier schema-valid output remains
available. A validating answer is parsed onto the session, carried
in the `output` event's detail, and included in the final `result` line.

## Terminal UI

`nare tui` is the same loop for a person. Type a task, watch it stream, and
approve each tool call: **y** allows, **n** or **Esc** denies, **a** allows
that tool for the rest of the session. `edit` and `write` show a diff first.
`read` and `ask` never prompt.

    nare tui "add a docstring to foo() in bar.py" --root . --session s.json

**Esc** or **Ctrl-C** stops the current turn without losing the session; type
a follow-up to keep going. When nare asks a question, the answer is the next
thing you type. **Ctrl-Q** quits, saving to `--session` if given. A `bash`
command already running when you interrupt keeps running until it exits or
times out.

`nare tui` takes the same provider, model, budget, tool, root, MCP, session
and resume flags as `nare run`, and not `--yes`, `--jsonl`, `--contract` or
`--schema`.

To watch a run something else started, point it at the run's session file:

    nare tui --attach path/to/session.json

The view is read-only and updates once per turn. It shows how long ago the
file was written, since `working` in a file can also mean the process died.

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
| Input/output prices | `NARE_PRICE_IN`, `NARE_PRICE_OUT` | Both needed, dollars per million tokens |
| Cache prices | `NARE_PRICE_CACHE_READ`, `NARE_PRICE_CACHE_WRITE` | Optional dollars per million tokens |

Invalid settings exit 2 before transport construction, with no result on stdout.
Prices must be finite and non-negative. The backend's reported dollar cost wins
(`x-litellm-response-cost`, then `x-litellm-response-cost-original`), then
configured prices, then unknown (`usage.cost=null`). Cache prices default to the
input price, which can overstate cache cost. An unknown turn makes cumulative
cost unknown. A USD budget stops after the first unknown-cost nonterminal turn
and names `NARE_PRICE_IN` and `NARE_PRICE_OUT`; done/blocked still win. Legacy
nonempty sessions without dollar accounting load with unknown cost.

Before each model call, old tool output is elided if estimated context reaches
80% of the window, stopping at 60% where possible. The task, assistant messages,
last two turns, error results and ask results are protected. Every tool call
keeps its result. If estimated context still reaches the window, nare stops
with `stop_reason=context` without calling the model. This character-based
estimate can undercount; a provider can still reject context as `max_tokens`.
With a configured base URL, window discovery uses `/v1/model/info` with a
5 second timeout; failures use 32000. Direct endpoints make no discovery request.

nare validates a subset of JSON Schema: `type`, `properties`, `required`,
`items`, `enum`, and `additionalProperties`. A schema using anything else is
refused at startup rather than validated in part, because an answer that was
only partly checked is worse than one that was not checked at all.

`nare contract` prints the machine contract this build speaks: the version,
the exit codes, the statuses, the event types, and the redaction rule set. A
caller pins what it understands with `nare run --contract N`, and nare refuses
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
`2026.09.17` and are maintained here, tuned to nare. To use them in a fresh clone:

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

Use `--stream` for live text and reasoning events while a model turn runs.
It works on both providers and permits Anthropic outputs above the SDK's
nonstreaming ceiling. Streaming can keep an idle proxy alive when the provider
sends traffic; it cannot prevent a timeout before the first byte. See
[streaming semantics](docs/contract.md#streaming-model-turns).
