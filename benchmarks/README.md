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
written before the provider and judge were recorded take them from the flags.

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

`run --matrix` runs each baseline's model with that baseline's provider, judge
and output cap, and compares each with its own baseline under the usual
rules, a drop re-run at seven repetitions included. `--model`, `--provider`
and `--judge-model` are refused, and `NARE_MODEL`, `NARE_PROVIDER`,
`NARE_BENCH_JUDGE_MODEL` and `NARE_BENCH_MAX_TOKENS` are not read. It needs
the key of every provider in the matrix: `ANTHROPIC_API_KEY` and
`OPENAI_API_KEY` both, for a matrix that mixes them.

It takes one base URL, `--base-url` or `NARE_BASE_URL`, with or without a
trailing `/v1`. `anthropic` models get the proxy's root and `openai` models
the root plus `/v1`, for the agent and the judge alike. A proxy that serves
the two routes from different roots does not work with `--matrix`: run its
models one at a time, each with its own `--base-url`.

Before building anything it refuses, with exit 2, a matrix whose baselines
name more than one judge (a table where each model judges itself measures
the judges as much as the agents), a provider whose key is not set, and a
model the endpoint does not list. When the listing fails, or there is no base
URL to ask, it warns and runs anyway.

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

A smoke matrix of seven models is 126 repetitions plus any confirmations, and
about an hour of wall clock.

## What a run leaves behind

    benchmarks/results/<stamp>.jsonl          one line per repetition
    benchmarks/results/<stamp>/<case>-<rep>/
      session.json                            nare's session
      stdout.jsonl                            the event stream
      stderr.txt
      judge.txt                               judged cases: every judge reply
      diff.patch                              the diff, as the judge read it
      closing.txt                             the closing message, as the judge read it

Each results line names its directory in `artifacts`, relative to
`benchmarks/results/`. A confirmation re-run rewrites the same files, so
one run leaves one results file; it also replaces the evidence from that
case's first pass, as it replaces those reps in the results file. Nothing
is pruned: a repetition is a few kilobytes, and the directory is
gitignored.

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
`--results`.

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

Run `bin/bench verify` on any case you add. It proves the checks fail on the
pristine fixture, which is the difference between a case that measures
something and a case that is green no matter what the agent does.

## Containment

The container bounds the filesystem and the process tree. It does not bound
the network - the agent's endpoint has to be reachable, so `--network none` is
not available. Treat a benchmark run like any other unattended execution of
model-generated shell commands.
