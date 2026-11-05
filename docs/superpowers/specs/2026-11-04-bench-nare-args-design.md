# nare - Benchmark Arguments: Per-Case Flags and Run Variants

**Date:** 2026-11-04
**Status:** Draft
**Issue:** #156
**Scope:** The benchmark runner only. A case can pass `nare run` flags, a run
can pass a variant (a system prompt file, an effort level, a context window,
or any other flag), and results with different arguments do not compare by
default. No change to nare itself; the contract version stays at 2.
**Builds on:** `2026-09-21-nare-benchmark-suite-design.md`,
`2026-10-23-ask-across-models-design.md` (the matrix), and #132's
`--system-file`.
**Unblocks:** #43 (configurable compaction point and the benchmark that finds
it), #42 (summarized compaction), and the experiments #113 proposes.

## 1. The problem

The agent's command line is fixed in `benchmarks/runner/sandbox.py`:

    nare run --yes --jsonl --session /out/session.json \
         --max-turns N --model M [--max-tokens N] -- PROMPT

Nothing a case or a run says can add a flag. The benchmark therefore cannot
measure any of nare's options: not a system prompt variant, not `--effort`,
not a narrower `--tools`, and not anything about compaction.

Compaction is the sharpest case. nare compacts when its estimate reaches 80%
of the context window (`compact.py`), and the window comes from the backend
unless `--context-window` says otherwise. A kept run of `context-overflow`
shows the backend reporting a window of 262144 tokens, so that case, written
to exercise a growing transcript, never compacts on that model. The only way
to make it compact is a smaller `--context-window`, and the runner cannot pass
one. #43's configurable compaction point and #42's summarized compaction will
each add flags of their own, and neither can be measured until the runner
can pass a flag.

## 2. What done looks like

- A case can pass `--tools read,list,search`, and a case that names a flag
  nare does not have, or a tool nare does not list, is refused by `verify`
  and by `run` before anything is spent.
- A run can pass a system prompt file. Every results line records which one,
  by path and by content digest, and `bless` carries it into the baseline.
- Results with different run arguments do not compare against each other by
  default. `--allow-arg-change` overrides that and nothing else.
- Every existing baseline and results file still loads, and reads as the run
  that passed no arguments.
- A flag #43 or #42 adds to `nare run` can be passed by a run the day it
  lands, with no change to the benchmark.

## 3. Two kinds of argument

| | Case arguments | Run arguments |
| --- | --- | --- |
| Written in | `case.toml`, as `args` | the `bin/bench run` command line, after `--` |
| Say | what the task needs (`--tools`, `--schema`, `--require-reads`) | what the experiment varies (`--system-file`, `--effort`, `--context-window`) |
| Apply to | that case's reps | every selected case's reps |
| Recorded on the results line | yes | yes |
| Part of the baseline key | no: they are part of the case, like its prompt | yes, as the judge model is |
| File paths are | relative to the fixture (the agent's working directory) | on the host, copied into the container |

**Run arguments are a generic flag list, not three named options.** The issue
names a system prompt file, an effort level and a context window. A list
covers all three, and also covers the compaction flags #43 and #42 will add,
without another change here. The three named in the issue need no special
handling except the system prompt files, which live on the host (section 5.2).

## 4. Case arguments

    id = "read-only-question"
    tier = "smoke"
    prompt = "..."
    args = ["--tools", "read,list,search"]

- `args` is optional. Absent, the case passes nothing, as today.
- `load_case` checks only the shape: a list of non-empty strings. Checking the
  flags against nare is section 6, and it needs nare's contract, which keeps
  `case.py` free of it.
- A flag's value that names a file names it relative to the fixture, which is
  the agent's working directory (`/work`). A case that passes
  `--schema answer.schema.json` ships that file in `fixture/`.
- Case arguments are not part of the baseline key. A case's prompt, checks and
  arguments are all part of the case. Editing any of them is a reviewed change
  to a committed file, and the benchmark does not detect edits to a case
  against its baseline today. This does not start doing that (section 13).
- No existing case gains `args` in this issue. Adding them to a case changes
  what its baselines measured, so it belongs with a re-bless, in the issue
  that wants the case changed (for example #158).

## 5. Run arguments

### 5.1 On the command line

    bin/bench run --tier full --case context-overflow -- --context-window 16000
    bin/bench run --tier smoke -- --system-file benchmarks/prompts/terse.md
    bin/bench run --tier smoke -- --effort low --context-window 20000

Everything after the first `--` goes to nare as run arguments, so they read
exactly as they would on `nare run`. Only `run` takes them. `compare` and
`bless` read them from the results lines, as they already read the provider,
judge and output cap (`as_recorded`), and refuse a `--` of their own.

`run --matrix` with run arguments is refused, as it already refuses
`--model`: the matrix takes everything from each baseline, its arguments
included (section 9.4).

### 5.2 System prompt files

`--system-file` and `--append-system-file` are the two run arguments whose
value is a host file. For each one, at the start of the run, before the image
is built:

1. The runner reads the file (exit 2 if it cannot) and copies it into a
   directory the run owns.
2. It takes the SHA-256 of the copy. That digest is what the run measured.
3. Each rep mounts that directory read-only at `/in`, and the flag's value is
   rewritten to the copy's path under `/in`.

The copy is taken once, so a prompt file edited during a long run does not
change the later reps of that run. `/in` is outside `/work`, so the file
never shows up in the diff the judge reads.

The recorded path is the one the operator gave, made relative to the
repository root when it is inside the checkout. A file outside the checkout
is recorded by its absolute path, which `bless` refuses (section 9.3).

No other run argument may name a host file in this issue: a flag that names a
file in the work tree belongs to a case (section 6.2).

## 6. Checking arguments against nare's contract

Arguments are checked before the image is built, so a typo costs nothing.
`run` checks the run arguments and the selected cases' arguments, and
`verify` checks every selected case's arguments.

### 6.1 Against the contract

The runner reads nare's contract in process, from `nare.contract.describe()`.
`bin/bench` runs under `uv run` from the same working tree the image
installs, so the contract it reads is the one the agent will speak. Each
argument list is parsed as `nare run` would parse it:

- Every flag is a long name in the contract's `flags`. `--flag=value` and
  `--flag value` are both accepted.
- A flag with `takes_value` is followed by a value, and a value is in the
  flag's `choices` when the contract lists any (`--effort`).
- `--tools` names only tools the contract's `tools` lists, or is `none`. An
  MCP tool's `<alias>__<tool>` name is accepted when the same list passes
  `--mcp-config`, since nare resolves MCP tools itself (added after the
  final review, 2026-11-04).
- A token that is neither a flag nor a flag's value is refused. That covers a
  bare `--` and a positional prompt, since the prompt is the case's.

Each refusal exits 2 and names the case, or "run arguments", and the token.

### 6.2 Who may pass which flag

**Reserved: refused in both lists.**

| Flag | Owned by |
| --- | --- |
| `--yes`, `--jsonl`, `--session` | the runner, which drives nare and reads its stream and session |
| `--max-turns` | the case's `max_turns` field |
| `--model`, `--provider`, `--base-url` | the environment, as `case.py` already refuses them by name |
| `--max-tokens` | `NARE_BENCH_MAX_TOKENS`, which is already part of the baseline key |
| `--prompt-file` | the case's `prompt` |
| `--resume` | nobody: every rep starts from a clean fixture |
| `--replay` | nobody: the fake provider is never benchmarked |
| `--stream` | `NARE_STREAM`, which changes how a turn arrives, not what is measured |
| `--contract` | the runner, which owns the stream it reads |
| `--bash-credentials` | nobody: it would hand the provider key to model-written commands |

**Run only.** `--system`, `--system-file`, `--append-system` and
`--append-system-file`. The system prompt is the variant under test, and a
case that set one would stack silently on every variant.

**Case only.** Flags that name a path in the work tree: `--schema`,
`--mcp-config`, `--require-read` and `--root`. Their paths are relative to a
fixture, and a run has no fixture of its own.

Everything else may go in either list.

### 6.3 Conflicts

A flag that appears in both the run arguments and a selected case's arguments
is refused, with exit 2, naming the flag and the case. argparse would let the
last one win, so a case's `--context-window` and a sweep's would silently
decide between them. Repeats within one list are left alone: each list was
written by one person on purpose, and `--require-read` repeats by design.

### 6.4 A rep whose nare never started

The contract does not describe value types or nare's own startup checks, so
`--context-window abc` passes section 6.1 and nare refuses it at startup. A
rep whose container exits 2 with no result line, and did not time out, is
nare refusing its invocation (`never_started`). That stops the run: the
runner prints that rep's stderr and exits 2, instead of recording every rep
as an error. This is also right with no arguments at all (a key nare cannot
use, for example).

A stopped run writes no results file, so `compare` and `bless` never read a
half run as the newest one. Why it stopped is still written down, in two
places that outlive the terminal:

- the rep's own evidence directory, kept as for any rep: `stdout.jsonl`,
  `stderr.txt`, and the session if nare wrote one
- `stopped.txt` in the run's evidence directory
  (`benchmarks/results/<stamp>/`, or `<stamp>.<model>/` in a matrix), which
  states the case, the rep, the model and provider, nare's exit code, that no
  result line came and the rep did not time out, the run and case arguments
  nare was given, where the rep's evidence is, and nare's stderr and stdout
  verbatim

The message the runner prints names `stopped.txt`.

Results the run had already produced are kept, never deleted, and never left
where `compare` and `bless` look (added during the build, 2026-11-04):

- a results file already written (an earlier model in a matrix, or the first
  pass before a confirmation re-run) moves into its evidence directory as
  `results.partial.jsonl`
- the graded records of cases the stopped pass had finished are written there
  too, and a stopped confirmation re-run's records go to
  `results.confirm.partial.jsonl`, so neither overwrites the other

## 7. How arguments reach the container

The script keeps its rule that values never reach it as interpolated text.
Run arguments and then case arguments are passed as positional parameters
after the script, and the script expands them with `"$@"`:

    docker run ... nare-bench:local sh -c "$SCRIPT" nare-bench RUN... CASE...

    nare run --yes --jsonl --session /out/session.json \
         --max-turns "$BENCH_MAX_TURNS" --model "$BENCH_MODEL" \
         ${BENCH_MAX_TOKENS:+--max-tokens "$BENCH_MAX_TOKENS"} \
         "$@" -- "$BENCH_PROMPT"

Since section 6.3 refuses conflicts, the order changes nothing. It is fixed
anyway so the command line in a rep's evidence is predictable. Arguments are
visible in the container's argv, which is fine: no argument carries a
credential, and section 6.2 refuses the one flag that would expose one.

## 8. What a results line records

Each new field has a default, so results files written before this load
unchanged:

| Field | Value | Default for older results |
| --- | --- | --- |
| `case_args` | the case's `args` as passed | `[]` |
| `nare_args` | the run arguments, as the operator gave them, with paths as section 5.2 records them | `[]` |
| `nare_files` | `{path: "sha256:<hex>"}` for each run argument that named a file | `{}` |
| `context_window` | `{"tokens": N, "source": "flag" \| "backend" \| "default"}`, from nare's `context_window` progress event | `null` |
| `compactions` | how many `compaction` progress events the rep's stream carried | `null` |

The last two are read from the event stream nare already emits. They are
recorded because they are what makes a compaction experiment readable. A
variant whose reps never compacted did not measure compaction. A run with no
`--context-window` takes its window from the backend, so the window a rep
actually had is the number that matters, and only the stream knows it.

`context_window` and `compactions` are evidence, not key. They describe what
nare did, and a change to nare that changes them (#44 changing where the
window comes from, say) is exactly the kind of change the benchmark exists to
measure, not to refuse.

## 9. The baseline key and comparison

### 9.1 The key

Two sets of results compare when their run arguments are the same variant.
The variant is the run arguments, normalized:

1. Parse into (flag, value) pairs, `--flag=value` and `--flag value` alike.
2. Replace each file-valued flag's value with its `sha256:` digest, so the
   same prompt under two paths is one variant and an edited prompt at one
   path is two.
3. Sort the pairs by flag name, keeping the order of repeats.

An empty list is the default variant. Every existing baseline and results
file is the default variant.

### 9.2 The baseline file

`BaselineMeta` gains the run arguments and their files, written only when
non-empty, so every existing baseline file stays byte for byte as it is:

    [meta]
    blessed     = "..."
    commit      = "..."
    provider    = "openai"
    model       = "gpt-5-nano"
    judge_model = "gpt-6.1-sol"
    nare_args   = ["--effort", "low", "--system-file", "benchmarks/prompts/terse.md"]

    [meta.nare_files]
    "benchmarks/prompts/terse.md" = "sha256:..."

### 9.3 run, compare, bless

- `run` checks the variant against the model's baseline before it builds
  anything, next to the existing provider, model, judge and cap check. A
  different variant exits 2 with both argument lists in the message.
- `compare` reads the variant from the results lines and refuses the same
  way.
- `bless` writes the results' variant into the baseline. It refuses, with
  exit 2:
  - keeping cases from an existing baseline blessed on another variant, as it
    already refuses keeping cases from another judge
  - a run-level file outside the checkout, since nobody else could reproduce
    that baseline and it would publish a local path
- `--allow-arg-change` overrides the variant refusals in all three, and
  nothing else. `--allow-model-change` does not override a variant change,
  and `--allow-arg-change` does not override a model, provider, judge or cap
  change. An experiment that compares a variant against the default must
  still be refused when its judge silently differs.
- When the two sides differ, the report prints both argument lists above the
  table, so a pasted table says it compared across variants.

### 9.4 The matrix

`run --matrix` passes each baseline's own `nare_args` to that model, and
before building anything re-hashes each of its files. A file whose digest no
longer matches the baseline exits 2 and names the file: the baseline measured
a prompt that no longer exists. `compare --matrix` reads variants from the
results as `compare` does. The matrix table's note line names each model's
variant when any is non-empty.

## 10. Effort

`run_agent`'s docstring says `--effort` is deliberately never passed, because
what a level means varies by model. That reasoning still holds for the
runner's own default, and the runner still never passes it unasked. An
operator may now pass it as a run argument. It is part of the variant, and
comparisons never cross models, so a level is only ever compared with the
same level on the same model. The docstring is updated to say so.

## 11. What this enables for compaction

With this alone:

    bin/bench run --tier full --case context-overflow -- --context-window 16000

makes `context-overflow` compact on any model, and `compactions` on each
results line shows whether it did. Sweeping `--context-window` is a crude
proxy for a compaction point, because it lowers the hard stop with the
trigger (`loop.py` ends the run with `context` at the window). Separating the
two is #43's job. When #43 adds its flag, `-- --compact-at 0.3` (or whatever
it is called) works with no change here, and is keyed like any other.

## 12. Testing

Unit tests in `benchmarks/tests`, run by `bin/build`:

- **case.py:** `args` absent, a valid list, and each wrong shape (not a list,
  an empty string, a non-string).
- **Argument checking** (a pure function taking the contract's `flags` and
  `tools` as data): an unknown flag, a missing value, a value outside
  `choices`, an unknown tool, a stray positional, `--flag=value`, each
  reserved flag in each list, the run-only and case-only flags in the wrong
  list, and a cross-list conflict. One test reads the real
  `nare.contract.describe()` so that a renamed nare flag fails here rather
  than in a paid run.
- **sandbox.py:** the docker argv carries the arguments as positional
  parameters after the script and never inside its text, a system file is
  mounted read-only at `/in` with its value rewritten, and no `/in` mount
  appears when no file is passed.
- **report.py:** a baseline round-trips with and without `nare_args`, and an
  existing baseline file renders byte for byte as it did.
- **\_\_main\_\_.py:** the key's normalization (order, `=` form, path versus
  digest); run, compare and bless refusing a variant change and accepting it
  with `--allow-arg-change`; each override not covering the other's change;
  bless refusing a file outside the checkout; the matrix passing a baseline's
  arguments and refusing a changed file; a never-started rep stopping the
  run; `context_window` and `compactions` read from a recorded stream; old
  results lines loading with the defaults.

No live run is needed to prove the acceptance criteria. One cheap live run,
for example `edit-docstring` at smoke reps with a `--system-file`, is worth
doing before merge to see the whole path work, and the PR reports only
aggregates from it.

## 13. Out of scope

- **Comparing two variants with each other.** This issue compares a variant
  against a blessed baseline. A compaction sweep will want
  `compare --results A --against B` with no baseline in between. That is the
  natural next issue, and the variant key here is what it would check.
- **Several baselines for one model.** A baseline is still
  `<model>.<tier>.toml`, so a model has one blessed variant at a time.
- **Detecting edits to a case** (its prompt, checks or `args`) against the
  baseline that measured it.
- **Run arguments with `--matrix` on the command line.** The matrix runs each
  baseline's own variant.
- **Delivery flags outside the key.** `--request-timeout` and the retry flags
  change how a turn arrives more than what is measured, as `NARE_STREAM`
  does, but this issue keys every allowed flag. Exempting them is a later
  decision with evidence behind it.
- **Host files other than system prompts** as run arguments.
- **The compaction work itself** (#42, #43, #44), and any change to nare or
  its contract.

## 14. Acceptance criteria

- [ ] A case with `args = ["--tools", "read,list,search"]` runs nare with
      those flags, and `verify` and `run` refuse a case whose `args` name an
      unknown flag or tool, before building the image.
- [ ] `bin/bench run -- --system-file PATH` runs every rep with that prompt,
      and every results line records its path and SHA-256. `bless` writes
      both into the baseline.
- [ ] `run`, `compare` and `bless` exit 2 on results and a baseline with
      different run arguments, unless `--allow-arg-change`, which overrides
      only that.
- [ ] Reserved flags, flags in the wrong list, and a flag in both lists exit
      2 naming the flag.
- [ ] A rep whose nare never started stops the run with exit 2, writes no
      results file, and leaves `stopped.txt` with the reason, the arguments
      and nare's stderr and stdout, beside the rep's kept evidence.
- [ ] Each results line records the context window the rep used and how many
      times it compacted.
- [ ] Existing baselines and results load unchanged and read as the default
      variant.
- [ ] `benchmarks/README.md` documents case `args`, run arguments, the key
      and `--allow-arg-change`. `bin/build` passes.

## 15. Calls made in this spec

The issue left these open, and the spec settles them; each is easy to revisit
before the plan:

1. Run arguments are a generic list after `--`, not named `bin/bench` options
   (section 3).
2. Case arguments are not keyed (section 4).
3. System prompt flags are run only, and work-tree path flags are case only
   (section 6.2).
4. A flag in both lists is refused rather than one winning (section 6.3).
5. A never-started rep stops the whole run (section 6.4).
6. `context_window` and `compactions` are recorded, but not keyed (section 8).
7. `--allow-arg-change` is a new flag, separate from `--allow-model-change`
   (section 9.3).
8. No existing case gains `args`, and no baseline is re-blessed (section 4).
