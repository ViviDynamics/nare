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
    export NARE_BENCH_JUDGE_MODEL=...   # the judge, required for judged cases;
                                        # never defaults to NARE_MODEL
    export NARE_BENCH_JUDGE_MAX_TOKENS=4096  # judge output cap; default 4096
    export NARE_BASE_URL=https://...    # only if you use a proxy
    export NARE_STREAM=1                # stream agent and judge, for proxies
    export NARE_BENCH_MAX_TOKENS=32000  # agent output cap; default is nare's
                                        # (anthropic needs NARE_STREAM above 21333)

    bin/bench models                    # free: what can I run?
    bin/bench verify                    # free: do the checks measure, and do the references pass?
    bin/bench verify --judge            # also the judge's claims: one call per judged solution
    bin/bench run --tier smoke          # measure
    bin/bench bless --tier smoke        # promote the numbers to a baseline
    bin/bench run --tier extended       # the opt-in real-work tier, only when named

`run` compares against `baselines/<model>.<tier>.toml` and exits 1 on a
confirmed regression. Only a guard cell can regress: one blessed at 0.9 or
more, which the model should pass. Its first pass runs at the case's
repetitions, and any failure there re-runs the case at ten more; two or more
failures among those ten are a regression. Every other cell is reported with
its interval and never fails on its rate, because a handful of repetitions
cannot tell a drop from sampling. See "How the run decides" below for the
error rates this rule is built for.

`bless` writes the provider, judge and output cap the results were run with,
which each results line records, and not the ones in your shell. Results
written before the provider and judge were recorded take them from the flags,
or else from the shell as `run` would: `NARE_PROVIDER`, which defaults to
`anthropic`, and `NARE_BENCH_JUDGE_MODEL`, which has no default: a run with a
judged case refuses to start without one, because a model that grades its own
reps measures its own self-preference.
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

## Cost, provenance and the token test

Every results line carries what produced it, so an old number can be
compared with a new one or refused. `nare_version` and `nare_commit` name the
nare that ran, `image_id` the container image, `case_hash` the case as the
runner read it (`case.toml` and every fixture file, by path and digest),
`started` the rep's start time, `tool_calls` the calls by tool from the
kept `session.json`, and `error_cause` the reason a rep could not be graded.
`error_cause` is one of `timeout`, `no_result_line`, `agent_http_error`,
`sandbox_error`, `judge_http_error`, `judge_empty`, `judge_unparsed`, or
`judge_unanswered`. Each rep also records `cost`, nare's dollar cost for the
rep, and `cost_estimated`, which is true when nare estimated the cost from
prices rather than reading the backend's figure. A backend that reports no
cost leaves `cost` null, and that rep is counted as unknown, never as zero.

`render` shows, per case, the pass count with its interval, the token median
with its range, dollars per rep and per solved task, and the verdict. A `~`
marks an estimated figure and a `*` marks reps with no reported cost.

**Refusal on a changed case.** A baseline records each case's hash. `run` and
`compare` refuse, with exit 2, a case whose hash differs from the one the
baseline measured, since its old numbers are for a different task. Pass
`--allow-case-change` to compare anyway, or bless again. A baseline blessed
before hashes were recorded cannot say, and is compared as before.

**The token test.** A rise in tokens is a ratio, so it is tested on the log
of the token median against each side's spread (the standard deviation of
the logs of its judged reps). A rise is flagged only when it is significant
at one-sided 5%. A rise under the noise is not flagged however large it looks,
and a drop is never flagged. Both sides need a spread: a baseline blessed
before spreads were recorded, or a run with one rep, has no test, and the
note says so rather than calling the rise. `--strict-tokens` fails the run on
a significant rise. Re-bless to record spreads on an old baseline.

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
with its own baseline under the usual rules, a guard failure re-run at ten
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
side, models across, each cell a pass count with its n and its 95% Wilson
interval, marked `regression` or `inconclusive`. It exits 1 if any model's comparison would exit 1 on its own,
and 2 for a configuration error. `compare --matrix` prints the last matrix
run's table again without running anything, and `--baselines` prints the
tier's baselines as the same table.

`bless` stays per model, since blessing the whole matrix in one command would
make it too easy to bless away a regression. After a matrix run,
`bin/bench bless --model M` blesses M's results from that run.

A smoke matrix of five models is 90 repetitions plus any confirmations, and
about an hour of wall clock.

## How the run decides

Every cell shows its pass count, n and a 95% Wilson score interval, so
`2/3 [0.21, 0.94]` reads as "two of three, and the true rate is plausibly
anywhere from 0.21 to 0.94". A baseline stores the count and n, not a rate.

The gate is deliberately narrow:

- **Guard cells** (blessed at 0.9 or more) are gated. A first pass with a
  failure re-runs the case at 10 repetitions, and 2 or more failures in those
  10 are a regression.
- **Capability cells** (blessed between 0.1 and 0.9) and **floor cells**
  (below 0.1) are never gated on their rate. A drop there is a note.
- **Inconclusive** cells (most repetitions errored) still fail the run.

The rule's error rates, from the binomial distribution, for one guard cell
whose true rate is 0.95:

| first pass | P(re-run) | P(2+ of 10 fail) | false alarm per cell | per 28-cell run |
| --- | --- | --- | --- | --- |
| 3 reps (smoke) | 14.3% | 8.6% | 1.2% | 29% |
| 5 reps (full) | 22.6% | 8.6% | 1.9% | 42% |

The per-run column is the chance that at least one of 28 guard cells, all
truly at 0.95, reports a regression with no change at all. It is the figure
to weigh before adding guard cells: a matrix of many cells blessed near 0.95
will fail on noise about a third of the time. Cells blessed at 0.99 or more
are far quieter (0.1% per cell at 0.98). The no-change simulation in
`benchmarks/tests/test_report.py` reproduces the 3-rep row. A real drop is
caught only sometimes: a guard cell falling from 0.95 to 0.80 is confirmed
about 30% of the time, so pooled comparisons (`bin/bench ab`, #153) are the
tool for effects that size.

## Comparing two commits: `bin/bench ab`

The question a harness change raises is whether it helped, with the model
fixed. `bin/bench ab` answers it by running two nare commits on the same
cases, rep by rep, and pooling the result across cases.

    bin/bench ab --base origin/main --head my-branch --reps 10
    bin/bench ab --base <sha> --head <sha> --case read-only-question --reps 2

Each commit is checked out to a temporary worktree and built as its own image
(`nare-bench:ab-base`, `nare-bench:ab-head`), so the arms differ in nare and in
nothing else. The reps interleave, base then head, within each case, so drift
in the endpoint hits both arms alike. The model, provider, judge and endpoint
come from the same flags and environment as `run`.

By default the cases are the ones the model's smoke baseline blessed between
0.1 and 0.9: the cases a harness change can move. Guards (blessed above 0.9)
and floor cases (below 0.1) say little about a change, so they are left out.
`--case` adds a case whatever its baseline says, and `--all-cases` runs the
whole tier.

The output is one table of per-case counts, then the pooled figures:

- **Pass rate, head minus base**, with each case weighted equally, and a
  bootstrap interval over cases.
- **Mantel-Haenszel odds ratio** of head over base, with a Robins-Breslow-
  Greenland interval. A stratified estimate: each case contributes its own
  2x2 table, so one case's noise does not swamp the others.
- **Cochran-Mantel-Haenszel test** of no difference, with continuity
  correction, two-sided, at 5%.
- **Tokens and dollars per rep**, head over base, paired by case: a geometric
  mean of the per-case ratios, with a bootstrap interval over cases. Dollars
  pair only where every rep in both arms reported a cost.

With few cases the bootstrap intervals are coarse. The test is the authority
on the pass rate.

Results are written to `benchmarks/results/ab/<stamp>/`: `base.jsonl`,
`head.jsonl`, each arm's evidence, and `summary.txt`. They are not
`results/*.jsonl`, so `compare` and `bless` never read an A/B run as the
newest run. Each line records its arm's commit in `nare_commit`.

**Power.** The test pools cases, so it sees effects no single case can. The
table gives the chance the one-sided 5% test flags a head arm worse than base,
for a uniform drop in pass rate. The setup is six cases at base rates 0.3,
0.4, 0.5, 0.6, 0.7 and 0.8, the matrix's mid-range, and reps per arm per case
as the rows give. It is reproduced by `power()` in `benchmarks/runner/ab.py`
(seeded, 2000 to 4000 trials per cell, so each figure is good to about two
points):

| reps per arm | drop 0.10 | drop 0.15 | drop 0.20 | drop 0.30 |
| --- | --- | --- | --- | --- |
| 5 | 0.18 | 0.32 | 0.49 | 0.83 |
| 10 | 0.36 | 0.57 | 0.79 | 0.99 |
| 15 | 0.40 | 0.69 | 0.89 | 1.00 |
| 20 | 0.52 | 0.81 | 0.96 | 1.00 |
| 30 | 0.64 | 0.93 | 0.99 | 1.00 |

With no drop, the false-alarm rate is about 5% (5.6% at 10 reps, from the
same simulation). Ten reps per arm is 120 reps across six cases: about 80
minutes and a few dollars at the matrix models' prices. Pick the reps for the
smallest effect you care about; a drop of 0.10 needs about 30 per arm.

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
may name only the tools nare lists, or `none`, and in a case whose args
also pass `--mcp-config`, an MCP tool as `<alias>__<tool>`, which nare
resolves from that config when it starts, or, in a case whose args also
pass `--command-tools`, a name that file could define (letters, digits, `_`
or `-`, no `__`), which nare resolves from it and stops the run on when the
file lacks it. A value that starts with `-`
is written `--flag=value`. Anything else, a stray word or a bare `--`
included, exits 2 naming the case or "run arguments" and the token, and so
does an argument that is not valid UTF-8, since results and baselines record
every argument as text.

Some flags are refused in both lists, because something else owns them:
`--yes`, `--jsonl`, `--session` and `--contract` (the runner, which reads
nare's stream), `--max-turns` (the case's `max_turns`), `--model`,
`--provider` and `--base-url` (the environment), `--max-tokens`
(`NARE_BENCH_MAX_TOKENS`), `--prompt-file` (the case's prompt), `--stream`
(`NARE_STREAM`), and `--resume`, `--replay` and `--bash-credentials`, which
no rep may use. The system prompt flags (`--system`, `--system-file`,
`--append-system`, `--append-system-file`) are run arguments only, since the
system prompt is the variant under test. Flags that name a path in the work
tree (`--schema`, `--mcp-config`, `--command-tools`, `--require-read`,
`--root`) belong to a
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
A file that cannot be read, or is not UTF-8 (nare reads it as UTF-8),
exits 2 before anything is built.

A rep whose nare never started (its container exited 2 with no result line,
and did not time out) is nare refusing its invocation, `--context-window abc`
for example. The run stops there with exit 2, prints that rep's stderr, and
leaves no results file. Why it stopped is kept in two places: the rep's own
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

The pass that stopped keeps the graded records of every case it finished,
judge verdicts included, in the same line format: in `results.partial.jsonl`
in its evidence directory, or in `results.confirm.partial.jsonl` when the
confirmation re-run stopped, beside its first pass. The runner prints each
path. A stop never leaves a results file in `benchmarks/results/` itself.
The confirmation re-run reuses the first pass's `<case>-<rep>` evidence
directories and clears each before its rep, so for a case the stopped
re-run reached, the first-pass records in `results.partial.jsonl` can point
at evidence the re-run has overwritten.

Each results line also records the case's args (`case_args`), the run
arguments (`nare_args`), each run file's digest (`nare_files`), the context
window the rep had (`context_window`: its size, and whether it came from the
flag, the backend or nare's default) and how many times nare compacted
(`compactions`). A window from the backend is the number that matters for a
compaction experiment, and only the stream knows it. nare reports its
window before its first turn, so a rep whose stream has no window and no
compaction (a Docker fault with empty stdout, for example) never reached a
turn, and records `compactions` as `null`, unknown, rather than 0.

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
the message. A last line says how to match the baseline: the arguments to
pass after `--`, or, for a baseline with none, to drop the `--` and what
follows (for `compare --matrix`, which a `--` cannot change, to run the
matrix again). `--allow-arg-change` overrides that, and only that: a model,
provider, judge or cap change still needs `--allow-model-change`, which does
not cover a variant change. When both apply, both refusals print, and
`compare --matrix` prints every model's, each naming its baseline file, so
one try shows every override needed. When the override lets two variants
meet, both lists print above the table. For `compare --matrix` that is each
differing model's name followed by both lists, indented.

`bless` writes the results' run arguments and file digests into the
baseline's `[meta]`. Cases the results do not cover are kept, as always, and
it exits 2 rather than keep them from a baseline blessed on another variant,
unless `--allow-arg-change`. A baseline must name run files every checkout
has, unchanged, since `run --matrix` reads each again and refuses that model
otherwise, which stops the whole matrix. So `bless` always exits 2, naming
the file, for a run file that is outside the checkout, that cannot be read
or no longer has the digest the run recorded (restore it, or run again),
or that git does not track (commit it first). When git is not installed, or
the benchmark runs outside a git checkout, it exits 2 for every run file,
since then nobody can say. These come before anything is written, and neither
`--allow-` flag lifts them. A model has one blessed variant at a time.

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

should make `context-overflow` compact; `compactions` on each results line
says whether it did.

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
`benchmarks/results/<stamp>/`, the records of the cases it finished beside
it, and no results file: see "Arguments to nare".

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

## Tool-call health

Every run counts, per model, how often the serving stack or the model made a
call nare could not use. The counts come from each rep's kept `session.json`
and event stream, so a model that mangles its calls reads differently from a
harness that does:

- **argument errors**: a call refused before it ran, because its arguments were
  not a JSON object or failed the tool's argument checks;
- **unknown tool names**: a name no tool in nare has;
- **empty arguments**: `{}` to a tool whose required parameters were then
  missing (a subset of the argument errors);
- **reasoning with no answer and no call**: a turn that reasoned and then did
  neither;
- **markup in reply text**: `<tool_call>` tags, or JSON shaped like a call, in
  the text instead of a call nare could run.

`run` prints them under the table, one block per model. `bin/bench tool-health`
counts the same from an existing results file's evidence, with no model
called, which is how older runs are scored. A rep whose evidence is missing is
counted as unread, not as clean.

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
    export NARE_BENCH_JUDGE_MODEL=...   # optional: defaults to the judge the run used
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
counted as wrong: it drops out of its own figures, and so does a claim it
marked `unknown`. A reference that could not answer drops the repetition from
every judge's. Jev gives no reason for a verdict, and that is not counted
against it.

## Calibrating the judge

The LLM judge's error rates are measured against a small set of claims that
people have labelled, kept in `benchmarks/calibration/labels.jsonl`. Nothing
in the harness labels them: two people do, blind to what the judge said.

    bin/bench calibrate export --results benchmarks/results/<stamp>.jsonl \
        --sample 80 --seed 1 --out <a path outside the repo>.jsonl
    bin/bench calibrate check      # the labels file: format, claims, duplicates
    bin/bench calibrate score      # the judge's FP and FN rates, per judge model

`export` writes one row per claim instance, with the claim, the diff's digest
and where its evidence is kept. It leaves out the judge's verdict and reason,
so a labeller reads the diff and not the judge. Each labeller fills in their
own `labels` entry (`"a"` and `"b"`) and the row is added to the labels file:

    {"case": "fix-failing-test", "claim": 1, "claim_text": "...",
     "diff_sha256": "<64 hex>", "labels": {"a": true, "b": true},
     "label": true, "adjudicated": false}

When the two labellers agree, `label` is their answer. When they disagree,
`adjudicated` is `true` and `label` is the agreed answer after discussion.
`calibrate check` refuses a row that breaks that rule, a claim that no longer
matches its case, or the same claim on the same diff twice.

### Reviewing and importing the labels

Reviewers record a verdict per claim on a web page and export it. Import
loads that export into the labels file:

    bin/bench calibrate export --results benchmarks/results/<stamp>.jsonl \
        --out <a path outside the repo>/candidates.jsonl
    # reviewers fill in the page, then export it as CSV or JSON
    bin/bench calibrate import <reviewed file>.csv \
        --export <a path outside the repo>/candidates.jsonl \
        --reviewer-a "Jason Berry" --reviewer-b "Sam Madinya"
    bin/bench calibrate check
    bin/bench calibrate score

`--export` is the file `export --out` wrote. Every row is matched to it on
`(case, claim, diff_sha256)`, so the file must be the export the reviewers
read. `--reviewer-a` and `--reviewer-b` are the names the page records; the
first is labeller `a` and the second labeller `b`. Each row keeps both
names, so a label can be traced to the people behind it.

A CSV takes the `review.csv` columns. Only these are read:

- `case`, `claim`, `diff_sha256`: the match on the export. `claim_text` is
  optional, and if given it must equal the export's text.
- `human_a`, `human_b`: `true`, `false` or `unknown`. A row with both blank is
  not reviewed yet and is skipped.
- `label`: the adjudicator's `true` or `false`, for a claim where the two
  verdicts disagree. It needs `--adjudicate`, and is refused on any other row.
- `note_a`, `note_b`: optional notes, kept on the row.

Every other column is ignored, including `draft_label`, `draft_reason` and
`confidence`. A draft is never a label.

A JSON file is an array of verdicts, one per reviewer per claim:

    [{"case": "fix-failing-test", "claim": 1, "diff_sha256": "<64 hex>",
      "reviewer": "Jason Berry", "verdict": "true", "note": "optional"}]

`reviewer` must be one of the two names, and `verdict` is `true`, `false` or
`unknown`. A JSON file cannot carry a decision; adjudicate in a CSV.

What the import does with each claim:

- **Both reviewers agree** (`true` and `true`, or `false` and `false`): the
  label is their answer, status `agreed`.
- **They disagree**: status `disagree`, with no label. It stays out of the
  rates until an adjudicator's `label` is imported with `--adjudicate`, which
  makes the row `adjudicated`.
- **Either says `unknown`**: status `unknown`, with no label, whatever the
  other reviewer said. The row is kept, with `unknown_from` naming who said
  it, and it stays out of the rates.
- **Only one reviewer has answered**: status `pending`, with no label, until
  the other one imports.

A file is refused as a whole, with nothing written, when any row is wrong:
its case, claim or diff SHA-256 is not in the export (the diff or the claim
has changed since the export, so export again and review the new rows), its
claim text no longer matches, a verdict or reviewer name is not valid, or a
labeller's name differs from the one already recorded on that claim. The
refusal names each bad row.

Import is idempotent. A reviewer's verdict replaces their earlier one on the
same claim, and a labeller not in the file keeps theirs, so one reviewer's
file can be imported now and the other's later, with the same result as
importing both at once. Importing the same file twice leaves the labels file
byte-for-byte as it was.

The evidence paths in the export point into a results directory, which is not
in the clone. Open the diff from the machine that ran the rep, or from the
evidence copy your reviewers were given.

`score` reads the labelled claims against the judged reps on the same diff. A
false positive is a claim labelled false and judged true; a false negative is
one labelled true and judged false. `run` prints the same figures after each
run. A claim with no judged rep on its diff is counted as unmatched, and the
rates are kept per judge model, so a change of judge is measured again rather
than assumed. The set is small, so the rates are a rough check, not a
precision measurement.

It exits 0 when the comparison finished, even if some judges failed on some
repetitions, since those failures are in the report; 1 when there is nothing
to compare; and 2 for a configuration error, before any model is called.

## Cases and their tiers

Every case, with the tier it runs in. `smoke` cases are the cheap loop; `full`
is every `smoke` and `full` case. A smoke result predicts a full result (see the
tests), so a case is tagged with the smallest tier it belongs to.

**The extended tier (#97)** is opt-in. It holds larger cases that look like real
work: a feature spread across storage, an API, a page and tests; a bug whose
symptom and cause sit in different modules; a refactor held to the original's
output; a format migration that must keep old files loading. `extended` runs
only when named, `bin/bench run --tier extended`, and `full` never includes it,
so `full` results, baselines and CI are what they were before the tier existed.
It has no baselines yet: a proof run shows the cases work. The judge is now
chosen (#46: `claude-haiku-5-5`, #239), so blessing is a separate decision
that no baseline has made yet. Its defaults are three repetitions per case
and a 30-minute timeout, set in `TIER_DEFAULTS`.

The matrix judge is `claude-haiku-5-5` (#239). Each baseline records its judge
in `[meta]`, and `run --matrix` refuses a matrix whose baselines name more than
one judge, so re-bless every matrix model together when the judge changes.

`bin/bench verify` with no `--tier` checks every case, extended included. CI
runs `bin/bench verify --tier full`, which does not reach the extended tier, so
run `bin/bench verify --tier extended` after changing one of its cases.

| Case | Tier | What it measures |
| --- | --- | --- |
| `ambiguous-after-reading` | smoke | asks which of two unused helpers, after reading both |
| `ambiguous-request` | smoke | asks about the value it must change, and names it |
| `bash-timeout-recovery` | full | runs a hanging command under a timeout, and reports it |
| `bootstrap-without-database` | full | bootstraps a service without a database, and says what it did not test |
| `context-overflow` | full | eight logs read in separate calls, on the default window |
| `context-overflow-small-window` | full | the same task on a 48,000-token window, one read at a time, so it compacts |
| `edit-docstring` | smoke | adds a docstring that says what the function does |
| `expense-tracker-cli` | full | builds a small CLI with tests, and stops |
| `fix-failing-test` | smoke | fixes the code, not the test |
| `impossible-test` | smoke | a docstring and a test that contradict: stops, or names the conflict |
| `invoice-discount-bug` | extended | a discounted-invoice total wrong by a cent: the cause is in pricing, the failing test in invoices |
| `long-test-output` | full | a failing test behind 36,000 characters of output |
| `mcp-fanout` | full | forty small MCP lookups, summed |
| `mcp-large-result` | full | one MCP call with a 60,000-character report, one line of which matters |
| `multi-file-rename` | smoke | renames a function across three files |
| `notes-tags-feature` | extended | tags across a stdlib notes app: store, JSON API, the page, and tests |
| `prefs-migration` | extended | a v1 to v2 settings format: old files load, new ones are written, the CLI is unchanged |
| `report-refactor` | extended | splits one report function into modules, checked against the original's output |
| `read-only-question` | smoke | answers a question about the code and changes nothing |
| `stale-backup-cleanup` | smoke | fixes a bug and leaves an unrelated backup file alone |
| `typed-classify` | smoke | a tool-free schema answer over twelve items |

Three cases use what the others do not:

- **MCP** (`mcp-fanout`, `mcp-large-result`): the case's args pass `--mcp-config`
  with a stdio server in its fixture (`fanout_server.py`, built on the `mcp`
  package the image installs). The tool is named `fanout__<tool>`.
- **Typed, tool-free** (`typed-classify`): `--tools none --schema` makes the
  answer nare's typed output, which the check reads from `session.output`.
- **Window** (`context-overflow-small-window`): `--context-window 48000` makes
  the hex logs compact from the fourth read, so the check reads nare's elision
  stubs. Its logs are hex, not dots, because a tokenizer squeezes dots.

## Writing a case

A case is a directory: `case.toml` plus a `fixture/` tree that becomes the
agent's working directory.

**Never name a model, a provider, or a base URL in a case.** Those come from
the environment, which is what lets the same case run against a proxy alias
and a first-party model unchanged. `case.py` rejects them.

Checks are `bash` (must exit 0) or `result` (asserts on nare's result line).
A claim that a command can decide belongs in a `bash` check, not the judge:
whether a file was changed is a `git diff`, and whether the answer names a
value is a pattern on the closing message. The optional `[judge]` block keeps
the prose and semantic claims about the diff, as binary checkable statements;
`min_met` turns it into a gate. A judge can only ever fail a repetition that
passed its checks - it can never rescue one that failed.

The judge answers each claim `true`, `false` or `"unknown"`, with one short
reason per claim. An `unknown` is never a `false`: it cannot turn a rep into a
fail unless even counting every unknown as true would miss `min_met`, and it
cannot make a pass unless the true claims already reach `min_met`. Any other
rep the judge cannot settle is a judge failure: it is an error, and it stays in
the pass rate's denominator as a rep that did not pass. (An infrastructure
error, such as a dead container, stays out of the denominator.) The summary
shows how many reps the judge could not settle, so a judge that fails on the
hard reps cannot quietly raise the rate.

Each judged rep's per-claim verdicts and reasons are in its results line
(`judge_claims`, `judge_reasons`), with the digest of the diff the judge read
(`diff_sha256`), which is what the calibration set is matched against.

A bash check runs in a fresh container over the agent's working directory,
with the run's artifacts mounted read-only at `/out`, so a check can see what
the agent did outside the tree it left. `/out/session.json` is nare's
session, and `/out/closing.txt` is the closing message, as the judge reads it.
A container writes that file after the agent exits, from the closing message
the host holds, so the agent cannot change what a check reads. A check that reads the transcript should accept a result
nare has elided for context (`[elided by nare: N chars ...]`), since a long
run compacts. Changes are measured from the fixture commit, which is the root
of the repository: `git diff --quiet "$(git rev-list --max-parents=0 HEAD)" -- path`.
`/out/after.json` is the agent's container as nare exited, before it
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

### Reference solutions and exploits

A case has two more directories, and `verify` checks both. Neither is a
model's output; both are hand-written, and both are in git.

- `solution/` is a correct attempt. It must pass every check, and with
  `--judge`, every judge claim. A case with no `solution/` fails `verify`.
- `exploit/` is a cheap wrong attempt: a test deleted, a special case for the
  one input the check names, a fake result, a fake database. It must be
  caught. By default that means a bash or result check fails. With
  `caught_by = "judge"` in `exploit/exploit.toml`, it must be caught by the
  judge, and without `--judge` it is reported as not run. Use that only when
  no command can see the cheat; the expense-tracker case is one.

Each directory holds any of these, all optional:

    patch.diff     applied to the fixture with git apply
    solve.sh       run with sh in the working directory, after the patch
    session.json   the transcript a blocked or transcript-reading case reads
    closing.txt    the closing message the judge and the checks read
    result.toml    status and turns, for the result checks (default done, 1)

A reference's `session.json` is scripted, not recorded from a model: it is the
transcript a correct run would leave, written to the shape nare writes. The
checks read it as they read a real one, and the reference's `solve.sh` does the
work it describes.

Nothing is run against a model. `verify` applies each attempt in a fresh
container, the way an agent's work arrives, and then runs the case's checks on
the result. `--judge` sends the diff and the closing message to the judge: one call
per judged solution, and one per exploit the judge decides. So it needs a key
and a judge model. Without it,
`verify` makes no model call.

A reference that fails is a case that does not measure what it says: fix the
case, or the reference if the reference is wrong, and say which in the pull
request.

## Containment

The container bounds the filesystem and the process tree. It does not bound
the network - the agent's endpoint has to be reachable, so `--network none` is
not available. Treat a benchmark run like any other unattended execution of
model-generated shell commands.

## Against Claude Code (#114)

The question is how nare compares with Claude Code on the same tasks, with the
same model, the same judge and the same grading, in tokens and dollars per
solved task. It is run by hand, never in CI: it needs a key and costs money.
It is its own runner, `python -m benchmarks.runner.claude_arm`. The extended
tier (#97) is its first user: `--tier extended` runs the comparable cases of
that tier. A run without `--tier` runs the nine `full` cases, as it always has.

What is compared, and how:

- **Cases.** `COMPARABLE_CASES` in `benchmarks/runner/claude_code.py` are the
  thirteen cases both arms can run on the same fixture, prompt and checks: nine
  smoke and full cases, and the four extended ones. The
  other eight are listed with a reason by `--list`. A case added to
  `benchmarks/cases` must land in one of the two sets, and the tests hold that.
- **One rep is one rep.** The Claude arm copies the fixture, runs the checks
  in the same image, judges with the same judge, and grades with the same
  functions (`grade_result_check`, `grade_bash_check`, `rep_outcome`) as
  nare's runner. Only the agent differs: `claude -p` in
  `nare-bench-claude:local`, built from `benchmarks/Dockerfile.claude-code`,
  with the Claude Code version set by its `CLAUDE_CODE_VERSION` build
  argument. The nare arm is `bin/bench run`, unchanged.
- **Same model, same judge.** Both arms use `claude-haiku-5-5` through the same
  gateway root. The judge is named by `NARE_BENCH_JUDGE_MODEL` and is never the
  model under test.
- **Tools and prompts are each harness's own.** nare has its default tools.
  Claude Code runs in print mode with its own system prompt, tool definitions
  and built-in skills, and with subagents and web tools disallowed. That
  harness overhead is part of what is measured, and the note says so.
- **Cost is recomputed.** Each arm's dollars are its usage times the gateway's
  list prices, read from `/v1/model/info` at run time and saved in the
  manifest. Neither harness's own cost figure is compared. Claude Code's
  reported `total_cost_usd` matched this figure on every result in the #114
  run that reported one (`docs/evidence/114/claude-code-reported-cost.csv`).
- **Usage is the result's total.** Claude Code's streamed messages report a
  provisional output count, so the arm takes input, output and cache tokens
  from the final result object, which is the billing total.

Running it. Build from a checkout of the nare commit under test, and set the
gateway root (without `/v1`) and the judge:

    export ANTHROPIC_API_KEY=...              # the key, never in a file
    export NARE_BASE_URL=https://...          # the root, without /v1
    export NARE_BENCH_JUDGE_MODEL=...         # not the model under test
    python -m benchmarks.runner.claude_arm --list
    python -m benchmarks.runner.claude_arm --tier extended --out extended --reps 3
    python -m benchmarks.runner.claude_arm --out smoke --reps 12 \
        --case read-only-question --case edit-docstring    # and the rest
    python -m benchmarks.runner.claude_arm report \
        --nare benchmarks/results/<stamp>.jsonl ... \
        --claude benchmarks/results/claude-code/smoke/results.jsonl ... \
        --manifest benchmarks/results/claude-code/smoke/manifest.json ...

Without `--reps`, each case runs at its tier's count, three for smoke and five
for full, as `bin/bench run` does. `bin/bench run` has no `--reps` flag, so to
match a count, run the nare arm once per block of reps and give the Claude
arm the same total per case. Results land in `benchmarks/results/claude-code/`,
which `compare` and `bless` never read as the newest run.
