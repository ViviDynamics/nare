# benchmarks

Measures whether a change to nare made the harness better: whether real tasks
get done, at what quality, for how many tokens.

This is development tooling. It is not installed with nare - the wheel
contains only `src/nare` - so running it means working from a checkout.

## Running it

Docker is required; every repetition runs in a throwaway container.

Before choosing a model, run `bin/bench models`. It lists what the endpoint
serves today, so do not pick a model from a list in a document: proxy aliases
change without notice. See "Which models the endpoint serves" below.

    export ANTHROPIC_API_KEY=...        # OPENAI_API_KEY for NARE_PROVIDER=openai
    export NARE_MODEL=...               # one that `bin/bench models` lists
    export NARE_BENCH_JUDGE_MODEL=...   # the judge its baseline names; unset,
                                        # the judge is NARE_MODEL itself
    export NARE_BASE_URL=https://...    # only if you use a proxy
    export NARE_STREAM=1                # stream agent and judge, for proxies
    export NARE_BENCH_MAX_TOKENS=32000  # agent output cap; default is nare's
                                        # (anthropic needs NARE_STREAM above 21333)

    bin/bench models                    # free: what can I run?
    bin/bench verify                    # free: are the cases well-formed?
    bin/bench run --tier smoke          # measure
    bin/bench bless --tier smoke        # promote the numbers to a baseline

`run` compares against `baselines/<model>.<tier>.toml` and exits 1 on a
confirmed regression. A case whose pass rate drops is re-run at seven
repetitions before it is called one, because sampling cannot be pinned and
three repetitions are not conclusive on their own.

`bless` writes the provider, judge and output cap the results were run with,
which each results line records, and not the ones in your shell. Results
written before the provider and judge were recorded take them from the flags,
or else from the shell as `run` would: `NARE_PROVIDER`, which defaults to
`anthropic`, and `NARE_BENCH_JUDGE_MODEL`, which defaults to the model itself.
A `--provider` or `--judge-model` that contradicts what the results recorded
exits 2, for `compare` too. `bless` merges into the existing baseline, so
cases the results do not cover are kept; when that baseline was blessed on
another provider, judge or output cap, it exits 2 rather than relabel them,
unless `--allow-model-change`.

`run` and `compare` refuse, with exit 2, a baseline blessed on another
provider, model, judge or output cap, since those numbers are not comparable;
`run` does so before it builds anything, and `--allow-model-change` overrides.
Results that never recorded their provider are compared on the others alone,
unless `--provider` names it. Run arguments are part of what a baseline
measured too, with their own override, `--allow-arg-change`: see "Arguments
to nare".

Tokens are `input + output + cache_read + cache_write` from nare's result
line. A proxy serves most input from cache, so counting only `input` would
hide a longer prompt.

## Which models the endpoint serves

    bin/bench models                    # needs NARE_BASE_URL and the key

prints every model the endpoint serves, with its context window and its price
in dollars per million tokens in and out, and the tiers that have a baseline
for it. It reads LiteLLM's `/v1/model/info`, and falls back to names only
from `/v1/models` when that does not answer. It sends the key for
`--provider` or `NARE_PROVIDER`, which is `ANTHROPIC_API_KEY` by default. It
calls no model, so it costs nothing. With no base URL there is no proxy to
ask, and it exits 2.

## The model matrix

The matrix for a tier is its set of baselines: each
`baselines/*.<tier>.toml` names a model, its provider, its judge and its
output cap. Adding a model to the matrix means blessing a baseline for it, and
dropping one means deleting its baseline.

    bin/bench run --matrix                            # every model, in turn
    bin/bench run --matrix --case ambiguous-request   # the cheap loop
    bin/bench compare --matrix                        # the table again, free
    bin/bench compare --matrix --baselines            # the baselines' table

`run --matrix` runs each baseline's model with that baseline's provider, judge,
output cap and run arguments (see "Arguments to nare"), and compares each
with its own baseline under the usual rules, a drop re-run at seven
repetitions included. `--model`, `--provider` and `--judge-model` are
refused, and `NARE_MODEL`, `NARE_PROVIDER`, `NARE_BENCH_JUDGE_MODEL` and
`NARE_BENCH_MAX_TOKENS` are not read. It needs the key of every provider in
the matrix: `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` both, for a matrix that
mixes them.

It takes one base URL, `--base-url` or `NARE_BASE_URL`, with or without a
trailing `/v1`. `anthropic` models get the proxy's root and `openai` models
the root plus `/v1`, for the agent and the judge alike. A proxy that serves
the two routes from different roots does not work with `--matrix`: run its
models one at a time, each with its own `--base-url`.

Before building anything it refuses, with exit 2, a baseline file that is
not at its model's name, `<model>.<tier>.toml` as `bless` writes it (so one
model never has two), a matrix whose baselines name more than one judge (a
table where each model judges itself measures the judges as much as the
agents), a provider whose key is not set, and a model or judge the endpoint
does not list. Each key lists once, and the judge is checked under every key,
since it answers on each model's provider. When a key's listing fails, it
warns and leaves that key's models unchecked, and with no base URL to ask, it
warns and checks nothing; either way it runs.

The models run one after another, never in parallel: every run tags its image
`nare-bench:local`, so two runs at once replace each other's image. After the
per-model reports it prints the table a pull request pastes: cases down the
side, models across, each cell a pass rate with its n, marked `regression` or
`inconclusive`. It exits 1 if any model's comparison would exit 1 on its own,
and 2 for a configuration error. `compare --matrix` prints the last matrix
run's table again without running anything, and `--baselines` prints the
tier's baselines as the same table.

`bless` stays per model, since blessing the whole matrix in one command would
make it too easy to bless away a regression. After a matrix run,
`bin/bench bless --model M` blesses M's results from that run.

A smoke matrix of five models is 90 repetitions plus any confirmations, and
about an hour of wall clock.

## Arguments to nare

A case can pass flags to `nare run`, and so can a run. A case's flags say
what its task needs; a run's say what the experiment varies.

    # case.toml
    args = ["--tools", "read,list,search"]

    bin/bench run --tier full --case context-overflow -- --context-window 16000
    bin/bench run --tier smoke -- --system-file benchmarks/prompts/terse.md
    bin/bench run --tier smoke -- --effort low --context-window 20000

Everything after the first `--` on `bin/bench run` goes to nare as run
arguments, written as on `nare run`, for every selected case. Only `run`
takes them: `compare` and `bless` read them from the results, and
`run --matrix` runs each baseline's own, so a `--` anywhere else exits 2.

Both lists are checked against `nare contract` before the image is built,
so a typo costs nothing: `verify` checks the cases' args, and `run` checks
both lists. A flag must be one `nare run` lists, by its full name
(`--context` is refused, though nare would expand it), with a value when it
takes one and inside its choices when it has them (`--effort`). `--tools`
may name only the tools nare lists, or `none`. A value that starts with `-`
is written `--flag=value`. Anything else, a stray word or a bare `--`
included, exits 2 naming the case or "run arguments" and the token.

Some flags are refused in both lists, because something else owns them:
`--yes`, `--jsonl`, `--session` and `--contract` (the runner, which reads
nare's stream), `--max-turns` (the case's `max_turns`), `--model`,
`--provider` and `--base-url` (the environment), `--max-tokens`
(`NARE_BENCH_MAX_TOKENS`), `--prompt-file` (the case's prompt), `--stream`
(`NARE_STREAM`), and `--resume`, `--replay` and `--bash-credentials`, which
no rep may use. The system prompt flags (`--system`, `--system-file`,
`--append-system`, `--append-system-file`) are run arguments only, since the
system prompt is the variant under test. Flags that name a path in the work
tree (`--schema`, `--mcp-config`, `--require-read`, `--root`) belong to a
case. A flag in both a case's args and the run arguments exits 2, since nare
would silently take the last one.

`--system-file` and `--append-system-file` name a file on the host, which a
relative path looks for in the directory you run `bin/bench` from. The
runner reads it once when the run starts, copies it to
`benchmarks/results/<stamp>/inputs/` (`<stamp>.<model>/inputs/` for each
model of a matrix run), and mounts the copy read-only at `/in` in every
rep, so editing the file during a run changes nothing, and the file never
shows in the diff the judge reads. Each results line records the path,
relative to the checkout when the file is inside it, and the copy's SHA-256.

A rep whose nare never started (its container exited 2 with no result line,
and did not time out) is nare refusing its invocation, `--context-window abc`
for example. The run stops there with exit 2, prints that rep's stderr, and
writes no results file. Why it stopped is kept in two places: the rep's own
directory (`stdout.jsonl`, `stderr.txt`), as for any rep, and `stopped.txt`
in the run's evidence directory (`benchmarks/results/<stamp>/`, or
`<stamp>.<model>/` in a matrix). It names the case, rep, model and provider,
the exit code, the run and case arguments nare was given (a prompt file as
its path under `/in`), a `run files` line with each prompt file's host path
and digest when the run had any, the rep's evidence path, and nare's stderr
and stdout verbatim. The printed message names the file.

A stopped run may have written results files already, for an earlier model
of a matrix or for the first pass before a confirmation re-run. Those are
moved, never deleted, to `results.partial.jsonl` in the evidence directory
of the same name, and the runner prints each path, so that `compare` and
`bless` never read half a run as the newest one.

Each results line also records the case's args (`case_args`), the run
arguments (`nare_args`), each run file's digest (`nare_files`), the context
window the rep had (`context_window`: its size, and whether it came from the
flag, the backend or nare's default) and how many times nare compacted
(`compactions`). A window from the backend is the number that matters for a
compaction experiment, and only the stream knows it.

### The variant

Results compare with a baseline only when their run arguments are the same
variant: the same flags and values in any order (repeats of one flag keep
theirs), `--flag=value` and `--flag value` alike, each run file counted by
its digest, so the same prompt under two paths is one variant and an edited
prompt at one path is two. No run arguments is the default variant, which
every baseline blessed before this is. A case's args are part of the case,
like its prompt, and not part of the variant.

`run` refuses a baseline blessed on another variant before it builds
anything, and `compare` refuses the same way, with exit 2 and both lists in
the message. `--allow-arg-change` overrides that, and only that: a model,
provider, judge or cap change still needs `--allow-model-change`, which does
not cover a variant change. When the override lets two variants meet, both
lists print above the table. For `compare --matrix` that is each differing
model's name followed by both lists, indented.

`bless` writes the results' run arguments and file digests into the
baseline's `[meta]`. Cases the results do not cover are kept, as always, and
it exits 2 rather than keep them from a baseline blessed on another variant,
unless `--allow-arg-change`. It always exits 2 for a run file outside the
checkout, since nobody else could reproduce that baseline, and neither
`--allow-` flag lifts that. A model has one blessed variant at a time.

`run --matrix` takes no `--`. It runs each model with its baseline's run
arguments, after checking them as `run` does and reading each run file
again: one that cannot be read, or whose digest no longer matches the
baseline's, exits 2 before anything is built, and no flag overrides that. An
edited prompt is a new experiment, which a single `run` with `--` measures.
The matrix table's note names each model's variant when any has one.

The runner never passes `--effort` unasked, since what a level means varies
by model. Passed as a run argument, it is part of the variant, so a level is
only ever compared with the same level on the same model.

    bin/bench run --tier full --case context-overflow -- --context-window 16000

makes `context-overflow` compact on any model, and `compactions` on each
results line says whether it did.

## What a run leaves behind

    benchmarks/results/<stamp>.jsonl          one line per repetition
    benchmarks/results/<stamp>/<case>-<rep>/
      session.json                            nare's session
      stdout.jsonl                            the event stream
      stderr.txt
      judge.txt                               judged cases: every judge reply
      diff.patch                              the diff, as the judge read it
      closing.txt                             the closing message, as the judge read it
    benchmarks/results/<stamp>/inputs/        the run's prompt files, as every rep read them

Each results line names its directory in `artifacts`, relative to
`benchmarks/results/`. A confirmation re-run rewrites the same files, so
one run leaves one results file; it also replaces the evidence from that
case's first pass, as it replaces those reps in the results file. Nothing
is pruned: a repetition is a few kilobytes, and the directory is
gitignored.

A run that stops because nare never started leaves `stopped.txt` in
`benchmarks/results/<stamp>/` and no results file: see "Arguments to nare".

`diff.patch` and `closing.txt` are kept for every repetition that produced a
result line and did not break underneath the agent, judged or not. They are
what lets `compare-judges`, or any other judge, be re-run against a past
repetition.

The stamp is taken when a run starts, and `compare` and `bless` read the
newest stamp. If two runs overlap, that is the one that started last, not
the one that finished last, so let one run finish before starting another.

A matrix run is one run with one stamp, and writes each model's results the
same way under `<stamp>.<model>`, the model's name as its baseline file
spells it:

    benchmarks/results/<stamp>.<model>.jsonl
    benchmarks/results/<stamp>.<model>/<case>-<rep>/

After a matrix run, `compare --model M` and `bless --model M` read M's file
from it. `compare-judges` reads one model's run, so pass it that file with
`--results`; without it, a matrix run as the newest run exits 2.

## Comparing judges

`compare-judges` measures how TypeSafe's Jev and the benchmark's own LLM judge
each agree with a stronger reference model, on the evidence a past run kept.
It changes no grade, gate or baseline. It produces the numbers for deciding
whether Jev should judge benchmark runs (#46).

**Agreement is with the reference model, not ground truth.** Nobody labels
anything by hand, so every figure says how close a judge gets to the
reference, never whether either is right.

Jev is reached only through the LiteLLM proxy's `/jev/decisions` route, so
`NARE_BASE_URL` must name the proxy. The proxy holds the TypeSafe key; the
benchmark sends the same proxy key it uses for `run`.

    export ANTHROPIC_API_KEY=...        # OPENAI_API_KEY for NARE_PROVIDER=openai
    export NARE_BASE_URL=https://...    # the proxy; required
    export NARE_BENCH_REFERENCE_MODEL=claude-opus-5-5   # the proxy's alias; required
    export NARE_BENCH_JUDGE_MODEL=...   # optional: defaults to the model under test
    export NARE_BENCH_JEV_MODEL=...     # optional: defaults to jev-latest

    bin/bench compare-judges                       # the newest results
    bin/bench compare-judges --results benchmarks/results/<stamp>.jsonl \
        --case edit-docstring

The model under test is read from the results file, so `NARE_MODEL` is not
needed. Only runs that kept `diff.patch` and `closing.txt` can be compared.
The reference has no default because its alias depends on the proxy.

Two jobs are measured:

- **Claims.** Each judged repetition's claims, judged by Jev (one yes or no
  question per claim, answered as P(yes)), by the LLM judge, and by the
  reference.
- **Triage.** Why each failed repetition failed. `ran_out` and `judge_gate`
  are decided by code; every other failure is labelled by Jev and by the
  reference, from the same seven labels.

It writes two files into the compared run's evidence directory, under the
comparison's own stamp, so a second comparison never overwrites a first:

    benchmarks/results/<stamp>/
      compare-<cstamp>.jsonl      one line per repetition, task and judge
      compare-<cstamp>.txt        the summary, as printed

Reading the summary:

- **Claim agreement.** The LLM judge's, and Jev's at each cutoff, where
  P(yes) at or above the cutoff reads as true. Each band leaves claims
  between `1 - t` and `t` unanswered and reports coverage, the share
  answered, and agreement on the rest: the numbers a confidence gate needs.
  Per-claim lines show a claim Jev handles badly instead of averaging it away.
- **Outcome flips.** Gated repetitions whose pass or fail would change if
  that judge replaced the reference. This is the number that changes what
  the benchmark reports.
- **Triage.** The code-labelled counts, a confusion table of Jev against the
  reference in counts, and Jev's median confidence where it agreed and where
  it did not.
- **Cost and speed.** Calls, tokens, dollars, and median and p95 latency per
  judge. `~` marks a cost estimated from Jev's input tokens because neither
  the proxy's header nor Jev's reply reported one.

Every percentage carries its n. A judge that could not answer is never
counted as wrong: it drops out of its own figures, and a reference that could
not answer drops the repetition from every judge's. Jev gives no reason for a
verdict, and that is not counted against it.

It exits 0 when the comparison finished, even if some judges failed on some
repetitions, since those failures are in the report; 1 when there is nothing
to compare; and 2 for a configuration error, before any model is called.

## Writing a case

A case is a directory: `case.toml` plus a `fixture/` tree that becomes the
agent's working directory.

**Never name a model, a provider, or a base URL in a case.** Those come from
the environment, which is what lets the same case run against a proxy alias
and a first-party model unchanged. `case.py` rejects them.

Checks are `bash` (must exit 0) or `result` (asserts on nare's result line).
The optional `[judge]` block lists binary, objectively checkable claims about
the diff; `min_met` turns it into a gate. A judge can only ever fail a
repetition that passed its checks - it can never rescue one that failed.

A bash check runs in a fresh container over the agent's working directory,
with the run's artifacts mounted read-only at `/out`, so a check can see what
the agent did outside the tree it left. `/out/session.json` is nare's
session. `/out/after.json` is the agent's container as nare exited, before it
was thrown away: `processes` lists everything still running, a server that
escaped with `setsid` included, and `database_files` lists any PostgreSQL
server binary or data directory anywhere in it. `verify` gives these checks
the artifacts of a run in which the agent did nothing.

`args` is an optional list of flags for `nare run`, for example
`["--tools", "read,list,search"]`. A path in a flag's value is relative to
the fixture, so a case that passes `--schema answer.schema.json` ships that
file in `fixture/`. Which flags a case may pass is under "Arguments to
nare". Adding `args` to an existing case changes what its baselines
measured, so bless it again.

Run `bin/bench verify` on any case you add. It proves the checks fail on the
pristine fixture, which is the difference between a case that measures
something and a case that is green no matter what the agent does.

## Containment

The container bounds the filesystem and the process tree. It does not bound
the network - the agent's endpoint has to be reachable, so `--network none` is
not available. Treat a benchmark run like any other unattended execution of
model-generated shell commands.
