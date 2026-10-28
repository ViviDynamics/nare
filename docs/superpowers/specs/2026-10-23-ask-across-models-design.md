# nare - Ask Reliably Across Models

**Date:** 2026-10-23
**Status:** Reviewed against code 2026-10-26. Section 6 revised in 6.1 after
its first measurement, 2026-10-27. 6.1's leak test and its readings of a drop
revised after review of the plan, 2026-10-27. Section 6.2 added after the
second measurement, 2026-10-27. Section 6.2 picked D, 2026-10-28; section 7
is not built in #45 (#117).
**Issue:** #45
**Scope:** A model matrix for the benchmark, two new cases, and a change to
the `ask` description, measured on the matrix. A loop change (section 7) is
specified here but built only if the measurement calls for it. The contract
version stays at 2.
**Builds on:** `2026-09-24-ask-only-when-ambiguous-design.md` (shipped in
#38) and the default system prompt (#83).

## 1. The problem

From the 2026-10-02 team call: models do not call `ask` consistently. Gemini
3.1 and GPT-5 nano called it on an ambiguous request and Haiku did not. The
issue guesses that Haiku could not tell the request was ambiguous.

What was measured that week on ambiguous-request ("Increase the timeout.",
against a fixture holding `TIMEOUT = 30`) shows the models failing in
different ways, and Haiku's failure was not the one the issue guesses:

| Model | Result | How it failed |
| --- | --- | --- |
| `gemini-3.1-flash-lite` | 2 of 3, bench | The miss set `TIMEOUT = 60`. The passes read `config.py` before asking. |
| `gpt-5-nano` | called `ask`, ad hoc | Before reading `config.py`. |
| `claude-haiku-4-5` | 0 of 3, bench; 2 of 14, ad hoc | **Asked in prose.** The question was in its final text reply, so the session ended `done`. |
| `spark/glm-5.3-flash` | 1 of 3, bench (PR #38) | Not broken down per rep. |
| `ada/qwen3-8b`, `ada/qwen3-14b` | 0, ad hoc | Ended `done` without asking. |

That is three different failures, and each needs a different fix:

1. **Asking in prose.** The model saw the ambiguity and asked the wrong way.
   Nothing tells it that a question in a text reply ends the session: nare
   reports `done`, and a headless caller moves on.
2. **Guessing, or doing nothing.** The model picked a value and made the
   change, or ended without a question.
3. **Asking blind.** The description says "only after reading the files
   involved", and the model asked first anyway. On this case it can still
   pass, because "by how much" is ambiguous before reading too.

This evidence is weak, which is why measuring comes first (section 5):

- **It is stale.** It predates the default system prompt (#83) and the
  current `questions` description. `claude-haiku-4-5` is gone from the proxy,
  and its successor `claude-haiku-5-5` has never been run on this case.
- **It is mostly ad hoc, and each model judged itself.** The judge defaults
  to the model under test, so even the bench numbers are not comparable across
  models. The only committed baseline, `ada/qwen3-14b`, was blessed before
  contract 2, and that model's tool calls were broken on the proxy by
  2026-09-30.
- **The case leaks its answer.** The `questions` description's example is
  "Raise TIMEOUT from 30 to what: 60, 120, or another value? Unanswered, I
  will use 60.", which is ambiguous-request's fixture and the answer its
  judge looks for. A model can pass our only ambiguous case by copying the
  example, which says nothing about whether it recognises ambiguity.

## 2. What done looks like

The issue asks for three things:

| Issue item | Where |
| --- | --- |
| Iterate on the prompt and handling so smaller models call `ask` reliably | Section 6 (the description), section 7 (the loop, if needed) |
| Add more capable models to the test matrix | Sections 3 and 4: a matrix the benchmark runs, with two newer OpenAI models in it, one of them capable tier |
| Expose new models through LiteLLM, and make sure agents check for the latest options | Section 3.3: `bin/bench models`, a preflight, and a pointer to `models` in `benchmarks/README.md`. Adding models to the proxy is outside this repo (section 9). |

**Reliably** means two things, one a target and one a gate:

- **Target:** on every matrix model, both ambiguous cases pass at least 2 of
  3 reps. A model that misses it is recorded in #45 with its failure labels.
- **Gate:** no matrix model has a confirmed regression on edit-docstring,
  multi-file-rename or read-only-question. An `ask` that fires when it should
  not is the failure #38 fixed, and it must not come back.

## 3. The model matrix

### 3.1 The matrix is the set of baselines

A baseline already records `provider`, `model`, `judge_model` and the output
cap in `[meta]` (the cap only when one was set), and `run` already refuses a
baseline blessed on a different model, judge or output cap (`model_changed`
in `benchmarks/runner/__main__.py`). Before PR 1 it checked only after the
reps had run, and did not compare the provider. PR 1 checks before the image
is built, so a mismatch costs nothing, and adds the provider, since the same
model on the other route is a different measurement. So the matrix needs no
new configuration:
**the matrix for a tier is the set of `benchmarks/baselines/*.<tier>.toml`
files.** Adding a model means blessing a baseline for it. Dropping one means
deleting its baseline. Both are reviewed like any committed file, which is
the right amount of ceremony for changing what we measure.

Cases stay model-free, and `case.py` still rejects model fields. Baselines
already name models, because a baseline is a measurement of one model.

### 3.2 `bin/bench run --matrix`

- Runs each baseline's model in turn, with that baseline's provider and
  output cap, and compares each against its own baseline. The existing rules
  apply per model: a drop is re-run at seven reps before it is called a
  regression.
- The cap is the baseline's `max_tokens`, absent meaning nare's default. A
  single run reads it from `NARE_BENCH_MAX_TOKENS` instead
  (`_max_tokens_from` in `config.py`), and the matrix keeps that function's
  non-streaming ceiling check.
- `--matrix` needs no `--model` or `NARE_MODEL`, which `resolve_config`
  demands today. It resolves one config per baseline before building
  anything, so a baseline whose provider's key is not set exits 2 before any
  model call. The initial matrix mixes providers (section 3.4), so it needs
  `OPENAI_API_KEY` as well as `ANTHROPIC_API_KEY`. On this proxy both hold
  the same key.
- **One base URL, two routes.** The openai transport posts to
  `<base URL>/chat/completions` (`transport/openai.py`), so its base URL
  ends in `/v1`, as the README's `--base-url https://your-proxy/v1` does.
  The Anthropic SDK adds `/v1/messages` itself, so its base URL does not.
  The matrix takes one base URL (`--base-url` or `NARE_BASE_URL`), strips a
  trailing `/v1` as `ModelMetadata.load` does, and gives `anthropic`
  baselines that root and `openai` baselines the root plus `/v1`, for the
  agent and the judge alike. With no base URL, each provider uses its own
  default endpoint. A proxy that serves the two routes from different roots
  is not supported by `--matrix`: run its models one at a time, each with
  its own `--base-url`.
- **Sequential, never parallel.** `sandbox.py` tags every image
  `nare-bench:local`, so two concurrent runs replace each other's image
  mid-run. Build the image once, then run each model.
- **One judge for the whole matrix.** Every baseline in the tier must name the
  same `judge_model`, or the run exits 2 before any model call and names the
  baselines that differ. When each model judges itself, a cross-model
  comparison measures the judges as much as the agents. The judge is reached
  on each baseline's provider (`judge_transport` in `judge.py`), so on an
  `openai` baseline the same judge model answers over Chat Completions.
- `--case` works as it does today, so `bin/bench run --matrix --case
  ambiguous-request` is the cheap loop while iterating on wording.
- After the per-model reports, it prints one table: cases down the side,
  models across, each cell a pass rate with its n, regressions marked. That
  table is what a PR pastes.
- `bin/bench compare --matrix` prints the same table from the last matrix
  run's results without running anything. Whether a matrix run writes one
  results file or one per model is the plan's call; `compare --matrix` must
  read exactly what the last matrix run wrote. Today `compare` and `bless`
  read only the newest results file and exit 2 if it names a model other
  than `--model`, and `compare-judges` exits 2 on a file that names more than
  one model. Whatever the layout, those three must still work on one model's
  results from a matrix run.
- Exit codes keep their meaning, taken over the matrix: 1 if any model's
  comparison would exit 1 on its own (a confirmed regression, an
  inconclusive case, or a token rise under `--strict-tokens`), 2 for a
  configuration error.

`bless` stays per model (`bless --model X`). Blessing the whole matrix in one
command would make it too easy to bless away a regression. Before PR 1 a
results line carried the model and the output cap but not the provider or the
judge, so `bless` wrote the provider and judge it resolved from its own flags
and environment, and a bless after a matrix run could not know the
baseline's provider or the matrix judge. PR 1 records both in every results
line, and `bless` writes what the results recorded, as it already did the
cap. A `--provider` or `--judge-model` must agree with the results, or the
bless exits 2. The flags, or else the environment, fill in only for results
written before PR 1.

### 3.3 Checking what the proxy serves

Aliases on the proxy change without notice: `claude-haiku-4-5` was listed on
2026-10-05 and gone by 2026-10-14, replaced by `claude-haiku-5-5`. A list in
a document goes stale, so the tooling reads the live one.

- **`bin/bench models`** prints what the endpoint serves. For each model it
  shows the context window and the price per million tokens from LiteLLM's
  `/v1/model/info` when that answers, and names only from `/v1/models` when it
  does not. It reads the `model_info` keys `ModelMetadata` reads
  (`max_input_tokens`, `input_cost_per_token`, `output_cost_per_token`), and
  prints nothing from an entry's `litellm_params`, which names upstream
  endpoints. It also shows which tiers have a baseline for that model,
  matched on each baseline's `[meta]` model, since file names are slugs. It
  costs nothing, and it is the command a person or an agent runs before
  choosing a model. `benchmarks/README.md` says so, and that is the only
  place the pointer goes: `.agents/skills/` is synced from upstream (PR
  #75).
- **No base URL.** There is no proxy to ask. `bin/bench models` exits 2
  with a one-line message saying so, and the matrix preflight warns and
  continues, as it does when the listing call fails.
- **Preflight.** `run --matrix` makes the same call before building
  anything. If the endpoint does not list a matrix model, or the judge, the
  run exits 2 before any model call, naming the model and its baseline file.
  It lists once per key, and checks the judge under every key, since the
  judge answers on each baseline's provider. If a key's listing call itself
  fails, the run warns and leaves only that key's models unchecked, because
  an outage of the listing should not block a measurement. This is a new
  check that shares `models`' listing code. It is not `sandbox.preflight`,
  which only checks that docker is on PATH.
- Both call the endpoint with httpx2, as `ModelMetadata.load` in
  `src/nare/transport/__init__.py` and `benchmarks/runner/jev.py` do, and
  never with urllib: Cloudflare in front of the proxy refuses urllib's
  default User-Agent (error 1010), and httpx2's default passes. Like
  `ModelMetadata.load`, they strip a trailing `/v1` from the base URL. They
  send the run's key as a Bearer token, which the proxy accepted on both
  endpoints on 2026-10-26. `ModelMetadata` itself cannot be reused: a
  transport builds it with that transport's client and auth header, and it
  keeps only its own model's entry.

### 3.4 The initial matrix

These come from the endpoint's listing on 2026-10-23, re-read on 2026-10-26
with no change. Prices are what the proxy publishes in `/v1/model/info`.
The endpoint also serves eight local `ada/*` models.

| Model | Provider | Why it is in | $ per M in, out |
| --- | --- | --- | --- |
| `gpt-5-nano` | `openai` | Called `ask` in the call's test | 0.05, 0.40 |
| `gpt-6-luna` | `openai` | A newer OpenAI model at about nano's price, and the likely replacement if we move off nano | 0.10, 0.50 |
| `gemini-3.1-flash-lite` | `openai` | Called `ask` in the call's test | 0.25, 1.50 |
| `spark/glm-5.3-flash` | `openai` | The default for live runs, and a reasoning model | 0.15, 0.50 |
| `gpt-6.1-sol` | `openai` | Capable tier, the newer OpenAI model | 2, 10 |

**Provider:** `openai` for every model in the matrix. A Claude model would
use `anthropic`. When a proxy translates a reasoning model's thinking onto
the Anthropic route, nare warns that words or whole answers can be dropped
and suggests `--provider openai` (`_warn_proxy_reasoning` in
`transport/anthropic.py`). The proxy reports reasoning support for the four
models besides spark, and spark is a reasoning model too.

**Judge:** `gpt-6.1-sol` for every baseline. It is a capable model on the
`openai` route, and the judge's claims are binary and checkable, so judging
its own reps is a small bias. The matrix table notes it. It was chosen on
2026-10-27, in place of `claude-sonnet-5-5`, because the Anthropic account
behind the proxy was out of quota until 2026-11-01. The same day, #45 went ahead
without the two Claude models (below).

**The two Claude models are left out.** The first matrix also had
`claude-haiku-5-5`, the model the issue is about, and `claude-sonnet-5-5`
for the capable tier. With the Anthropic account out of quota until
2026-11-01, #45 went ahead on 2026-10-27 with the five `openai` models rather
than wait, so `gpt-6.1-sol` is the capable tier. Either Claude model rejoins
with one bless on the same judge, with `--provider anthropic` and a base URL
without `/v1`. `gpt-6.1-sol` as judge on that route was probed on
2026-10-27 and answered as it does on the `openai` route.

**`ada/qwen3-14b` leaves the matrix.** Its baseline predates contract 2 and
the system prompt, so nothing can be compared against it. The `ada/*` models
do tool calls through LiteLLM's JSON mode, which answered `{}` to every call
on 2026-09-30. Delete the baseline. The local models can still be run by hand
with `--model`, and one rejoins the matrix when someone blesses a baseline
for it that works.

**Cost.** A smoke matrix run is 6 cases × 3 reps × 5 models: 90 reps, plus
any seven-rep confirmations. Estimated from the smoke tier's token medians
(about 1k to 5k per rep) and the prices above, that is a few dollars,
most of it the judge and the capable model. It is about an hour of
wall clock: the reps run one after another, and each starts a container,
takes a few model turns, often starts a second container for its bash
checks, and makes a judge call. The first run replaces this estimate with a
measured one.

## 4. Two new cases

Both ids join `SEEDS` in `benchmarks/tests/test_cases.py`, whose first test
fails on a case it does not list.

### 4.1 ambiguous-after-reading (smoke)

The ambiguity shows only in the files, so a model cannot pass by asking
blind, and nothing in the `ask` description resembles it.

- **Prompt:** "Delete the unused helper."
- **Fixture:** `utils.py` with four helpers, two of which nothing calls
  (`format_bytes` and `slugify`), and `main.py`, which calls the other two.
  Its `.gitignore` is expense-tracker-cli's (`__pycache__/` and
  `.pytest_cache/`), so a model that runs `main.py` leaves no bytecode in
  the diff the judge reads.
- **Checks:** `result` with status `blocked`. A `bash` check reads
  `/out/session.json` and passes only if the recorded questions name both
  `format_bytes` and `slugify`. It fails on the pristine fixture, where the
  session has no questions, so `bin/bench verify` accepts the case.
- **Judge,** `min_met = 1`: "utils.py was not changed."

The readings are: delete `format_bytes`, delete `slugify`, or delete both.
Each is a different change, which makes the request ambiguous under the rule
in the 09-24 spec. "The unused helper" is singular, so the request itself
says the caller expects one.

Both ambiguous cases have `max_turns = 12`: nare adds "N turns left: wrap up
and summarise." to every turn once 5 or fewer remain, and the limit leaves
room for a normal read-then-ask before that countdown starts. ambiguous-request
had 6 and this case first had 8, which started it after turn 1 and turn 3, and
the "before" baselines were first blessed with those limits.

The questions are the session's top-level `questions`, which only an `ask`
that blocks fills in (`step` in `loop.py`). The `case.toml`:

```toml
id = "ambiguous-after-reading"
tier = "smoke"
# The ambiguity is in the files, not the prompt: two helpers are unused, and
# "the unused helper" is singular. A correct run reads, then asks which.
prompt = "Delete the unused helper."
# Room to read both files and search for callers before asking.
max_turns = 12

[[check]]
kind = "result"
status = "blocked"

# The questions name both readings. Only an ask records them in the session.
[[check]]
kind = "bash"
cmd = '''
python3 -I - <<'EOF'
import json, sys
asked = " ".join(json.load(open("/out/session.json"))["questions"])
missing = [name for name in ("format_bytes", "slugify") if name not in asked]
sys.exit(f"the questions do not name {missing}" if missing else 0)
EOF
'''

[judge]
assertions = ["utils.py was not changed."]
min_met = 1
```

A test in `benchmarks/tests/test_cases.py` runs this check with `run_here`,
as the bootstrap case's checks are tested, against a `session.json` whose
questions name both helpers, one of them, and neither.

### 4.2 read-only-question (smoke)

This is a guard. The request is a question that changes nothing, and a
natural answer ends with an offer ("Want me to add a test for it?"). It must
end `done`. It catches an `ask` change that makes models ask whenever they
end on a question. Section 6 carries that risk, and section 7 carries more.

- **Prompt:** "What does parse_duration return for '90s'?"
- **Fixture:** `duration.py`, whose `parse_duration` turns `"90s"`, `"5m"`
  and `"2h"` into a `timedelta`, so the answer takes reading the code.
  Its `.gitignore` is expense-tracker-cli's (`__pycache__/` and
  `.pytest_cache/`): a model that runs `parse_duration` to check its answer
  leaves a `.pyc`, which the judge would otherwise count as a changed file.
- **Checks:** `result` with status `done`. With no `bash` check, `verify`
  exempts it, as it did ambiguous-request until section 6.2 gave that case
  a check.
- **Judge,** `min_met = 2`: "No file was changed." / "The answer says
  parse_duration returns a timedelta of 90 seconds."

A `bash` check that nothing changed would pass on the pristine fixture, and
`verify` would call the case broken, so the judge's first claim does that
job. The `case.toml`:

```toml
id = "read-only-question"
tier = "smoke"
# A guard: a question that changes nothing, whose natural answer may end on
# an offer. Asking here is the failure #38 fixed. A correct run answers.
prompt = "What does parse_duration return for '90s'?"
max_turns = 6

[[check]]
kind = "result"
status = "done"

[judge]
assertions = [
  "No file was changed.",
  "The answer says parse_duration returns a timedelta of 90 seconds.",
]
min_met = 2
```

## 5. Measure first

Before nare changes, bless all five baselines on the matrix PR's head,
whose `src/nare` is unchanged from `main`, with the one judge.

The matrix is empty until they exist, so each is made one model at a time.
`bless` writes the provider and judge the `run` recorded, and exits 2 on a
flag that disagrees, so the flags on `bless` are a check that the right run
is being blessed:

    bin/bench run   --tier smoke --model M --provider P --judge-model gpt-6.1-sol
    bin/bench bless --tier smoke --model M --provider P --judge-model gpt-6.1-sol

`P` is the row's provider from section 3.4, `openai` for all five. An
`openai` row's `run` needs `OPENAI_API_KEY`, which on this proxy is the same
key as `ANTHROPIC_API_KEY`, and a `--base-url` that ends in `/v1` (section
3.2).
`bless` needs neither: it calls no model. The output cap, if any, comes
from `NARE_BENCH_MAX_TOKENS` on the `run`, and `bless` reads it from the
results.

Then label every failing rep of the two ambiguous cases from its artifacts
(`closing.txt`, `diff.patch`, `session.json`):

| Label | Means |
| --- | --- |
| asked in prose | Ended `done` with no change, and the closing text asks the caller something |
| guessed | Ended `done` with a change to the fixture |
| asked blind | Called `ask` before any `read` or `bash` call |
| other | Anything else, described in a sentence |

The labelling is by hand, not code. It is needed twice, before and after
section 6, and a heuristic would need its own validation. The table goes in
`docs/evidence/45/README.md`, next to the matrix table it explains.

## 6. The change: the `ask` description

Measured on 2026-10-27, the wording in this section failed. Section 6.1 says
why and replaces it, and section 6.2 replaces 6.1's candidates in turn. This
section stays as the record of what was tried.

Two edits, both in `src/nare/tools.py`.

**Say where a question in prose goes.** One sentence is added after "only
after reading the files involved". It is aimed at failure 1. It goes in the
description, not the system prompt, for the reason the 09-24 spec gave: the
description reaches every caller, including one that replaces the prompt
with `--system`.

```python
"description": (
    "Stop and ask the caller to resolve an ambiguous request. Use this "
    "only when the request can reasonably be read more than one way and "
    "the readings lead to different changes, and only after reading the "
    "files involved. Ask only with this tool: a text reply ends the "
    "session as finished, so a question written there may never be "
    "answered. Do not use it for anything you can find out by reading "
    "files or running commands, for details you can decide yourself such "
    "as wording, style or names, to ask permission, or to offer "
    "follow-up work. Call ask on its own, before making any change. This "
    "ends the session."
),
```

"May never be answered" is deliberate. In `nare tui` a person does read a
text reply and can answer it, so "never answered" would be false there.

**Replace the leaked example.** The `questions` description keeps its shape
and swaps the example for one that no case uses:

```python
"description": (
    "One question per item, naming the readings you are choosing between "
    "and the one you will use if it goes unanswered, e.g. 'Set the log "
    "level to INFO or to WARNING? Unanswered, I will use WARNING.'"
),
```

The two edits are measured together, so ambiguous-request alone cannot tell
them apart. ambiguous-after-reading can: if ambiguous-request drops while
ambiguous-after-reading holds or rises, the old example was doing the work.
That result is worth having, and it is not a reason to keep the leak.

The description grows by about 30 tokens, sent on every turn, so token-band
notes on the short cases are expected. No test in `tests/` pins either text
in full. `test_the_ask_question_states_its_default` in `tests/test_tools.py`
pins "the one you will use if it goes unanswered", which the new `questions`
text keeps. As in the 09-24 spec, no test judges whether the wording works;
that is the matrix's job.

**Measuring it:** `bin/bench run --matrix` against the section 5 baselines.
The PR states the matrix table before and after, and the failure labels of
any ambiguous-case rep that still fails. The same PR then re-blesses all
five baselines, one model at a time, so the baselines on `main` measure
the description on `main`.

### 6.1 After the first measurement

Measured on 2026-10-27, both candidates below failed. Section 6.2 says why
and replaces them.

**What was measured.** The section 6 text (commit `54f175f`) was run as one
matrix, stamp `2026-10-27T05-56-45Z`, against the section 5 baselines
(`docs/evidence/45/README.md`). The ambiguous cases, and the guard case that
moved, before then after:

| model | ambiguous-request | ambiguous-after-reading | read-only-question |
| --- | --- | --- | --- |
| `gemini-3.1-flash-lite` | 0/3, 0/3 | 0/3, 0/3 | 3/3, 3/3 |
| `gpt-5-nano` | 1/3, 1/3 | 1/3, 2/3 | 3/3, 5/7 |
| `gpt-6.1-sol` | 3/3, 0/7 | 3/3, 3/3 | 3/3, 3/3 |
| `gpt-6-luna` | 2/3, 1/7 | 1/3, 1/2 | 3/3, 3/3 |
| `spark/glm-5.3-flash` | 0/3, 0/3 | 1/3, 1/3 | 3/3, 3/3 |

luna's 1/2 lost a rep to an HTTP 403 from the proxy. With section 5's
labels, 26 of the 28 failing ambiguous reps after guessed, one asked in
prose and one asked blind, against 13 guessed and 5 asked in prose of 18
before. All 19 guesses on ambiguous-request set `TIMEOUT` to 60. No rep of a
non-ambiguous case called `ask`, before (0 of 60) or after (0 of 64).

**Why it failed.** Only spark keeps its reasoning. For the other models the
evidence is what they did.

- **The old example did the recognising on ambiguous-request.** sol's three
  passes before put the example's frame in their own question: "Raise
  TIMEOUT in config.py from 30 to what: 60, 120, or another value?
  Unanswered, I will use 60." (rep 2, `2026-10-27T05-46-57Z`). Without it,
  sol set 60 in all seven reps and closed "Increased `TIMEOUT` from `30` to
  `60` in `config.py`." On ambiguous-after-reading, which no example has
  resembled, sol asked 3 of 3 both times. luna's two passes before both
  offered 60 as the default, one in the example's frame ("from 30 to what
  value"), and 6 of its 7 reps after set 60. This is the result section 6
  said would mean the old example was doing the work.
- **What the example did was overrule an exclusion.** The description
  excludes "details you can decide yourself such as wording, style or
  names". Over the two 12-turn runs, spark weighs the new value against that
  exclusion in 5 of its 6 ambiguous-request reps and files it there in 4:
  "Here, the value to increase to is a detail I could decide myself, like
  wording/style/names." (before, rep 1), "That's a wording/style/detail
  decision." (after, rep 1). The fifth used the example to overrule it: "The
  guidance says don't use it for details you can decide yourself such as
  wording/style. Timeout value is a functional value that could matter. The
  tool description example even says: [the example] That's exactly this
  scenario. So asking is appropriate." (before, rep 0). The new example, a
  choice between two named log levels, has ambiguous-after-reading's shape
  (which of two), not an open amount, and says nothing against the
  exclusion.
- **"This ends the session" reads as the price of asking.** "But asking is
  expensive (ends session)." and "the ask tool ends the session and the user
  may not answer" (spark, after, ambiguous-after-reading rep 0, which then
  deleted both helpers). The sentence weighs against asking in 6 of spark's
  27 reps of the two cases on 2026-10-27, 4 of them in the 6- and 8-turn
  runs where the countdown also said to wrap up. Once it is why spark wrote its
  question in prose: "But since ask ends the session, I can't proceed
  after." (before, ambiguous-request rep 0). The sentence dates from slice 1
  (#4), not #38. The evidence is one model's.
- **Small, reversible changes are discounted.** "the difference is minor and
  easily reversible; deleting all unused helpers is a superset of any
  reading" (spark, after, ambiguous-after-reading rep 0), and "It leads to
  different changes (60 vs 120), but it's trivially reversible." (before,
  ambiguous-request rep 1). Neither candidate below answers this: a clause
  such as "even small ones" fits a docstring's wording too, which is the
  failure #38 fixed.
- **The sentence about where a question goes did its job.** `gpt-5-nano`
  asked in prose in 4 of its 6 ambiguous reps before and 1 after, and called
  `ask` in 2 before and 4 after. spark's one prose question before was the
  one the example drove, and it became a guess. With no `ask` call on a
  non-ambiguous case either side, it cost nothing that shows. It stays.
- **`gpt-5-nano`'s read-only-question drop does not look like the
  description.** Neither failing rep (1 and 3) called `ask`. Each made one
  search whose output did not show `parse_duration`'s body, then got nare's
  "5 turns left: wrap up and summarise.", which this case's `max_turns = 6`
  sends after turn 1 of every rep, and answered that it could not tell. Rep
  5 did the same and passed on the docstring its search printed. Before, all
  three reps read `duration.py` after the same message. It looks like the
  countdown effect section 4.1 removed from the ambiguous cases, and step 1
  below checks it.

`gemini-3.1-flash-lite` keeps no reasoning and guessed in all 12 of its
ambiguous reps, before and after, so nothing here says which words move it.

**The wording.** Two candidates. Both keep section 6's `questions`
description (the log-level example) and its sentence about where a
question goes. Candidate A:

```python
"description": (
    "Stop and ask the caller to resolve an ambiguous request. Use this "
    "only when the request can reasonably be read more than one way and "
    "the readings lead to different changes, and only after reading the "
    "files involved. Ask only with this tool: a text reply ends the "
    "session as finished, so a question written there may never be "
    "answered. Do not use it for anything you can find out by reading "
    "files or running commands, to ask permission, to offer follow-up "
    "work, or for details you can decide yourself such as wording, "
    "style or names. A new value the request leaves open is not such a "
    "detail. Call ask on its own, before making any change. This ends "
    "the session."
),
```

Candidate B is A with its last sentence, "This ends the session.", replaced
by "This stops the session until the caller answers."

What each change does:

- **"A new value the request leaves open is not such a detail."** It does
  what the old example did, for any value rather than one case: a value the
  request asks for but does not give is no longer a detail the model may
  pick, which is the line spark drew itself ("a functional value that could
  matter") when the example backed it. It names no case's file, setting,
  value or verb. Wording, style and names stay the model's call, so
  edit-docstring's wording stays excluded. A test of behaviour ("a value
  that changes what the code does") was shorter and was dropped: deleting
  unused code changes nothing the code does, so the same test would invite
  a model to file ambiguous-after-reading's choice as a detail.
- **The exclusion list is reordered** so that "details you can decide
  yourself" comes last and "such a detail" follows it directly. No
  exclusion is added or dropped, and #38's four keep their words.
- **B only: "This stops the session until the caller answers."** It states
  the same fact from the caller's side: the session is `blocked`, and the
  caller answers by resuming it, as `ask`'s own result already says ("The
  session is blocked pending answers."). It also sets the tool against the
  text reply four sentences earlier, which "may never be answered". It is
  aimed at spark's reading of the old sentence as a cost.

**Why two.** The evidence supports A's sentence on the regression itself:
sol and luna, through an exclusion the example had overruled. It supports
B's sentence only from spark, and B's is the one change that could make
asking look cheap enough to fire on a guard case, which only the full matrix
measures. The cheap loop decides whether B earns that risk.

**No example of ambiguous-request's shape.** No candidate has an example of
an amount left open ("raise X from N to what"), under any names. The data
show the shape, not the names, is what sol used: its three passes before
filled the example's frame with the case's own names and values, and with
the frame gone it asked in 0 of 7 though nothing else about the case
changed. A frame with other names would let a model match "Increase the
timeout" to it and fill it in from `config.py`, so a pass would again show
template-matching, and a transcript could not tell it from recognition,
because the question holds the case's own names either way. A's sentence
names the category in words and gives no question to fill in, so a pass
shows a model applying a stated rule to a request it had to read. That is
less than finding the ambiguity unaided, and it is what nare wants the
description to do: the 09-24 spec's rule table opens with this request.
ambiguous-after-reading, which nothing in either candidate resembles,
stays the measure of recognition without a hint. The log-level example is
another shape, a choice between two named settings, and sol's 0 of 7 with
it shows it does not carry ambiguous-request.

**Tokens.** Counted as words plus punctuation marks, with a
characters-divided-by-four count after it, the `ask` and `questions`
descriptions together are 151 (172) on `main`, 171 (199) in section 6, 184
(214) in A and 188 (220) in B. So A adds about 33 to 41 tokens to every
turn over `main`, 13 to 15 over section 6, and B about 37 to 48 over
`main`. No tokenizer was at hand, so these are estimates, made the same way
for every text.

**Tests,** in `tests/test_tools.py`. Keep the tests that pin the prose
sentence and the log-level example. Add one that pins "A new value the
request leaves open is not such a detail.", and, if B is picked, one that
pins its last sentence. Widen
`test_the_ask_example_is_no_benchmark_cases_answer` to the whole `ask`
schema: every string in it, the description and `questions` among them, is
checked on its own, without regard to case. None of the ambiguous cases'
names appears, even inside a longer name (`HTTP_TIMEOUT`, `MAX_RETRIES`):
`TIMEOUT`, `RETRIES`, `config.py`, `utils.py`, `main.py`, `format_bytes`,
`slugify`, `parse_csv_line`. None of their values appears, even with a unit
(`60s`): `30`, `60`, `120`. And neither case's shape appears under any
names: an amount left open ("from 30 to", "from 45 seconds to", "to 8 or
16", or "to what" asked as a question, "to what:" or "to what value") and
an unused helper to choose ("unused", or a dead, uncalled or unreferenced
helper or function). The prompts' verbs are not checked, so "Do not delete
files to make room." stays possible, and neither is "to what" in ordinary
prose, such as "Keep the change to what the request asks for." The test
checks itself: it must catch near copies under other names, such as "Raise
MAX_WORKERS to 8 or 16? Unanswered, I will use 8." and "Remove the unused
function, format_size or make_slug?", and must pass those harmless
sentences. It does not chase every paraphrase. As before, no test judges
whether the wording works.

**Measuring it.** The setup is section 5's: the five `openai` rows, the
judge `gpt-6.1-sol`, both keys, and `NARE_STREAM=1`, as the baselines were
run. One run at a time, since every run builds the same image tag
(section 3.2). The two Claude rows join each step once their baselines
exist (section 3.4).

1. **Is a drop noise?** Before step 2 for `gpt-5-nano` on
   read-only-question, and in steps 3 and 4 for any model with a confirmed
   regression on a guard case or on ambiguous-after-reading, run that case
   for that model on PR 1's code, in a separate worktree of
   `45-ask-across-models` (whose `src/nare` is `main`'s):

       bin/bench run --case read-only-question --model gpt-5-nano \
         --provider openai --judge-model gpt-6.1-sol --base-url <proxy>/v1

   Repeat until at least 14 reps are judged. A run is 3 reps, or 7 when it
   re-confirms a drop. The candidate's side of the same model and case is
   made the same way, to at least 14 (for `gpt-5-nano` on
   read-only-question, in step 4). Label each failing rep of a guard case,
   first match wins: `asked` (called `ask`), `stopped short` (ended `done`
   never having seen `parse_duration`'s body, whatever its closing says),
   `asked in prose` (had seen it, and ended on a question to the caller
   instead of the answer) or `other`.
2. **Cheap loop.** For A, then B, each from a commit so its stamp names one:

       bin/bench run --matrix \
         --case ambiguous-request --case ambiguous-after-reading

   That is five models, two cases and 3 reps, about 30 reps per candidate,
   plus any re-confirmations at 7, each against the section 5 baselines.
3. **Pick.** A cell, one model on one case, is at target when its pass rate
   over the reps the run kept is at least 2/3. A candidate scores its cells
   at target, out of 10. The section 6 text scored 2 (nano and sol on
   ambiguous-after-reading) and the section 5 baselines 3. A candidate is
   out if a confirmed regression on ambiguous-after-reading in its cheap
   loop holds up when pooled (below). Of the rest, B is picked only if it
   scores higher than A; otherwise A, the smaller change.
4. **Gate.** On the picked candidate, `bin/bench run --matrix`, then step 1's
   command on this code until `gpt-5-nano` has at least 14 judged reps of
   read-only-question, counting the gate run's, and step 1's pools for any
   other confirmed regression on a guard case or on ambiguous-after-reading.
5. **Record and re-bless** as section 6 says, adding: each step's stamp and
   commit, each candidate's cheap-loop table, the labels of every failing
   ambiguous rep, and the question of every passing ambiguous-request rep,
   so a reader can see whether a frame was filled in.

**Reading a guard drop.** A drop is the description's if a failing rep on
the candidate called `ask` or asked in prose, or if the candidate's fail
rate is at least 0.2 above PR 1's code's, which at 14 reps on each side is
3 more failures. Otherwise it is noise in the model: #45 records it, and
the gate does not count it. If PR 1's code also stops short on
read-only-question, the fix is the one section 4.1 made, a larger
`max_turns`, in its own change with its own re-bless, not here.

**fix-failing-test** is not one of section 2's guards, but an `ask` on it
would be the failure #38 fixed. A confirmed regression on it is listed, and
its failing reps on the candidate's code labelled as a guard's are. It
fails the wording only if one of them called `ask` or asked in prose. It is
not pooled, so this costs no runs.

**Reading an ambiguous-after-reading drop.** Three of the five baselines
hold ambiguous-after-reading at 1/3. A first pass of 0/3 there is re-run at
7, and 2 of 7, close to 1/3, is then a confirmed regression, so a candidate
that changed nothing would be out with a probability of roughly a quarter
to a half. So a confirmed regression on ambiguous-after-reading is pooled
as a guard drop is, to at least 14 judged reps on the candidate's code and
on PR 1's, and it holds up, counting against the candidate, only if the
candidate's fail rate is at least 0.2 above PR 1's. Asking is the right
answer on this case, so labels play no part in the reading.

**How the pools are made.** Both sides of a reading are made the same way,
runs of 3 reps with the runner's 7-rep confirmation, and that slants a pool
toward passes: a first pass at the baseline's rate is kept, and one below
it is replaced by 7 fresh reps. The slant is the same on both sides, so the
comparison stands. A pool counts no run that was the reason it was made,
since that run was chosen for being low: a regression that a cheap loop or
the gate flagged is pooled from fresh runs on both sides, while
`gpt-5-nano` on read-only-question, pooled whatever the gate shows, counts
the gate run's reps. The labels are another matter: the run that flagged a
guard case is on the candidate's code, so its failing reps are labelled
with the pool's, and one that called `ask` or asked in prose makes the drop
the description's. The first measurement's 2 of 7 is one confirmation, a
fresh sample with no slant, so its reading compares it with the 7-rep runs
in step 1's pool only, the same kind of sample. If that pool has none, no
first pass on PR 1's code fell below 3/3 (one with errors, at 2 of 2 or
inconclusive, is kept without a confirmation too), and PR 1's rate is its
whole pool's. That reading settles the first measurement's drop for #45.

**When the wording fails.** It does not ship if, on the gate run:

- a guard case (edit-docstring, multi-file-rename, read-only-question) has a
  confirmed regression on some model that the reading above does not call
  noise
- fix-failing-test has a confirmed regression on some model, and a failing
  rep of it on the candidate's code called `ask` or asked in prose
- ambiguous-after-reading has a confirmed regression on some model that
  holds up when pooled
- the widened leak test fails

If B was picked and fails the first or second condition with a rep that
called `ask`, run step 4 on A before giving up: A leaves the cost of asking
as it was.

It **misses** if `gpt-6.1-sol` or `gpt-6-luna` stays below 2/3 on
ambiguous-request. A miss that does not fail still ships if it scored at
least the section 6 text's 2 in step 3, because the leak cannot come back
(section 6) and nothing better was measured. #45 then records the miss with
its labels, and the next lever is a rule in the default system prompt
(section 9), in its own change.

### 6.2 After the second measurement

**What was measured.** Section 6.1's two candidates were run by its protocol
on 2026-10-27 (`docs/evidence/45/README.md`, "After the second wording").
Both scored 4 of 10 cells in the cheap loop, against the section 6 text's 2
and the baselines' 3, and both are out. spark's ambiguous-after-reading was a
confirmed regression in each loop, and it held up when pooled from fresh
runs:

| code | judged | failed | fail rate |
| --- | --- | --- | --- |
| PR 1 | 14 | 6 | 0.43 |
| A | 17 | 13 | 0.76 |
| B | 18 | 14 | 0.78 |

A rebase on 2026-10-27 gave the commits new hashes, which this section uses:
section 6's text is `947cc4c`, A `6b2eb75`, B `7a4b5cb`, and PR 1's tip
`6ca24f5`. The evidence names them `54f175f`, `3d1fee5`, `d3edf82` and
`477160c`. Each pair holds the same `src`, `tests`, cases and baselines;
only docs differ. Every run of this section is made on this stacked branch
as it stands, which is rebased onto `main` only when PR 2 is assembled,
after #116 merges; the trees match, and the evidence names the commit each
run was made at.

**What the reps show,** from the evidence's tables:

- **spark's reps on ambiguous-after-reading differ in one call.** On every
  side, passing and failing reps read `main.py` and `utils.py`, nearly all
  search the repo for both helpers, and the median rep decides at turn 4.
  The next call is `ask`, or an edit that deletes both helpers and
  `import re`, which is the default spark names when it asks. The edit came
  in 19 of 24 reps on A and 19 of 23 on B, against 6 of 14 on PR 1.
- **The drop came with text A and B share.** B's last sentence changed
  nothing (0.76 and 0.78). What A and B share and PR 1 lacks is section 6's
  two changes (the sentence about text replies, and the log-level example
  in place of the leaked one) and A's two (the reordered exclusions and "A
  new value the request leaves open is not such a detail."). The reps cannot
  say which.
- **A's sentence did its job on ambiguous-request.** sol went from 0/7 under
  section 6 to 3/3 under A and B, and its questions name 30 and default to
  60 without the old frame. spark went from 0/3 to 2/3 under A. luna stayed
  at 2/7 under both.
- **nano's ambiguous-request passes are blind.** All 7 of its kept passes on
  that case, over every run of 2026-10-27, called `ask` as their only tool
  call and asked about the timeout of its own commands. None opened
  `config.py`. The judge passed them: the case asks only that `config.py` is
  unchanged and that a question offers values.
- **nano's read-only-question drop was noise,** by section 6.1's reading.
  PR 1's code also stops short there, after the countdown that
  `max_turns = 6` starts after turn 1. The fix, a larger `max_turns` with
  its own re-bless, stays out of this section.

**Readings use what a rep did.** From here on, every label and every reading
of a rep cites only its tool calls and their arguments, its diff, its
closing, its `ask` questions, and yes-or-no facts read from its tool
results, such as whether a file read returned content. A tool result is
what a tool returned, not model reasoning. Saved model reasoning in
`session.json`, its text and thinking blocks, is not read. The quotes in
section 6.1 stay as recorded.

#### The check on ambiguous-request

ambiguous-request gets a `bash` check, as ambiguous-after-reading has one:
the questions must name `TIMEOUT`, or its value 30 as a whole number.

```toml
# The questions are about config.py's setting: they name TIMEOUT or its
# value. A question about the agent's own command timeout names neither.
[[check]]
kind = "bash"
cmd = '''
python3 -I - <<'EOF'
import json, re, sys
asked = " ".join(json.load(open("/out/session.json"))["questions"])
named = "TIMEOUT" in asked or re.search(r"(?<![\d.])30(?!\d)", asked)
sys.exit(0 if named else "the questions name neither TIMEOUT nor its value, 30")
EOF
'''
```

The value 30 is in the check for two right questions. Of the 33 passing
reps of this case kept from 2026-10-27, 26 looked at the files first and
asked about the setting, and 7, all nano's, asked blind. All 26 name 30,
and 24 name `TIMEOUT`; luna's other two say "the timeout" (A's loop rep 1,
B's loop rep 3). None of the 7 names either. So `TIMEOUT` alone would fail
two right questions, and this check fails exactly the 7. The judge keeps
its two claims.

A test in `benchmarks/tests/test_cases.py` runs the check with `run_here`,
as ambiguous-after-reading's is tested, on questions that name `TIMEOUT`,
that name only 30, that offer 300 or 600 seconds for the agent's own
commands, and none. `bin/bench verify` now checks the case instead of
exempting it. The pristine fixture's session has no questions, so the check
fails there, as `verify` requires.

**The "before" numbers are re-run.** At the before commit (below),
ambiguous-request alone is run for each of the five models, 3 reps with the
runner's 7-rep confirmation, and `bin/bench bless --case ambiguous-request`
blesses it, one model at a time, as the two ambiguous cases were re-blessed
at 12 turns (section 4.1). No results file is written by hand.

The kept reps are also re-scored, read-only, as evidence of what the check
alone changes: a script outside the bench runs the check on each kept
`session.json` and writes nothing the bench reads. A pass whose questions
fail the check becomes a fail, and nothing else moves. On section 5's five
baseline files, one number moves: nano's, from 1/3 to 0/3. The first
measurement and A's and B's loops lose only nano's blind passes (1, 1 and
3), and every score above stands.

**The before commit.** The check lands in one commit on
`45-ask-description` that also returns `src/nare/tools.py` and
`tests/test_tools.py` to `main`'s, so B's code leaves the tip. The re-runs
and re-blesses are made at that commit, so each baseline's `[meta] commit`
names a tree whose case has the check and whose `src/nare` is `main`'s.
PR 1's side of any new pool runs there too. The PR 1 pool above still
serves: neither its code nor ambiguous-after-reading changed. Each
candidate is one commit on top of the before commit. A docs-only commit on
top of it, such as a review fix to the plan, changes no code, case or
baseline, so the runs may be made at the branch's tip instead.

#### Step 1: where the drop came from

spark on ambiguous-after-reading is pooled to at least 14 judged reps on
section 6's text, `947cc4c`, from fresh runs made as section 6.1's pools
were. spark is free in practice (see Cost below), so the judge is the only
real spend. The pool's fail rate decides which candidates are built. One
fail rate, F failed of N judged, is at least 0.2 above another, F0 of N0,
when `5*(F*N0 - F0*N) >= N*N0`, in whole numbers. The decimals in the table
are rounded; where they and the rule disagree, the rule wins.

| section 6's fail rate | Reading | Built |
| --- | --- | --- |
| at least 0.2 above PR 1's 6 of 14: about 0.63 or more (rounded), 9 or more of 14 | section 6's changes carry the drop | D |
| at least 0.2 below A's 13 of 17: about 0.56 or less (rounded), 7 or fewer of 14 | A's changes carry it | C |
| between the two: 8 of 14 | the reps cannot place it | C and D |

A's two changes count as one: the exclusions were reordered only to put
"such a detail" next to the exclusion it answers. A candidate this step does
not build is not built later.

#### The candidates

Both widen A's sentence to name a second kind of open choice:

> A new value the request leaves open is not such a detail, and neither is
> which of several things the request could mean.

The first kind is an amount the request asks for and does not give. The
second is a request that names one thing where the files hold several that
fit. A named only the first, and under A spark asked about the second less
often than on PR 1. Naming both still names no case's file, identifier,
value or verb. A general form, "a choice the request leaves open", was
shorter and was dropped: a name or a wording is a choice the request leaves
open too, so that form would contradict the exclusion it follows and invite
an `ask` on edit-docstring.

Neither ambiguous case now measures recognition without a hint. Each has its
kind named in words and no question to fill in, which section 6.1 accepted
for ambiguous-request: a pass shows a model applying a stated rule to files
it had to read.

Both drop B's sentence and end "This ends the session." B's sentence changed
nothing on spark's cell, and it was the one change that could make asking
look cheap enough to fire on a guard case.

**Candidate C** is A with the widened sentence. Section 6's sentence about
text replies and its log-level `questions` example stay:

```python
"description": (
    "Stop and ask the caller to resolve an ambiguous request. Use this "
    "only when the request can reasonably be read more than one way and "
    "the readings lead to different changes, and only after reading the "
    "files involved. Ask only with this tool: a text reply ends the "
    "session as finished, so a question written there may never be "
    "answered. Do not use it for anything you can find out by reading "
    "files or running commands, to ask permission, to offer follow-up "
    "work, or for details you can decide yourself such as wording, "
    "style or names. A new value the request leaves open is not such a "
    "detail, and neither is which of several things the request could "
    "mean. Call ask on its own, before making any change. This ends the "
    "session."
),
```

**Candidate D** starts from `main`'s description. It has no sentence about
text replies, and its `questions` description is `main`'s with the leaked
example removed and none in its place. Its exclusions are reordered as A's
are, and the widened sentence follows them:

```python
"description": (
    "Stop and ask the caller to resolve an ambiguous request. Use this "
    "only when the request can reasonably be read more than one way and "
    "the readings lead to different changes, and only after reading the "
    "files involved. Do not use it for anything you can find out by "
    "reading files or running commands, to ask permission, to offer "
    "follow-up work, or for details you can decide yourself such as "
    "wording, style or names. A new value the request leaves open is not "
    "such a detail, and neither is which of several things the request "
    "could mean. Call ask on its own, before making any change. This ends "
    "the session."
),
```

```python
"description": (
    "One question per item, naming the readings you are choosing between "
    "and the one you will use if it goes unanswered."
),
```

D removes both of section 6's changes, since step 1 cannot tell them apart,
and keeps the leak out (section 6). It adds no example: a new one would be
an untested change of its own. D gives up the sentence after which nano's
prose questions fell from 4 of its 6 ambiguous reps to 1 (section 6.1). If
they return, section 7 is their lever.

**Tokens,** counted as section 6.1 counts them: the two descriptions
together are 196 (230) in C and 148 (180) in D, against 151 (172) on
`main` and 184 (214) in A.

**Tests,** in `tests/test_tools.py`. For C, A's tests, with the open-value
test pinning the widened sentence directly after "such as wording, style or
names.", and B's test of its last sentence removed. For D, the open-value
test and the leak test; the tests that pin the text-reply sentence and the
log-level example go, since D has neither.
`test_the_ask_question_states_its_default` holds for both. Every string of
both candidates passes the leak test's patterns. The test gains a pattern
for the second kind's question under other names: "delete", "remove" or
"drop", then "or", within one question, where a dot inside a word, as in a
file name, does not end the question. It catches "Delete parse_a or
parse_b? Unanswered, I will delete both.", "Delete helpers.py or
strings.py?" and "Drop helper_a or helper_b?", and passes "Do not delete or
rename files.". Two more catch frames of the first kind that the old
patterns missed: the old example's list ("Raise MAX_WORKERS to 8, 16, or
another value?") and "to 8 or to 16" ("Set the pool size to 8 or to 16?").
Each near copy names the pattern it must trigger, and every pattern is the
only match of at least one near copy, so removing any one pattern fails the
test.

#### Step 2: spark first

Each built candidate's spark ambiguous-after-reading is pooled to at least
14 judged reps from fresh runs at its commit, before any paid model runs on
it. Section 6.1's pooled rule decides: a fail rate at least 0.2 above PR
1's 0.43 (9 or more of 14) puts the candidate out, with no paid run. This
pool was not chosen for being low, so if the cheap loop later flags this
cell, the pool already reads it and no new runs are made.

#### Step 3 on: the loop, the pick, the gate

Section 6.1's "Measuring it", steps 2 to 5, run on the candidates step 2
kept, with C in A's place and D in B's, against the re-blessed baselines.
Its step 1 pools any confirmed regression they find, with PR 1's side at the
before commit. Section 6.1's readings of a guard drop, of fix-failing-test
and of an ambiguous-after-reading drop, its rule for making pools, and "When
the wording fails" stand, with these changes:

- **The pick.** Of the candidates not out, the one with the higher score; on
  a tie, D, the smaller change from `main`.
- **No second gate.** Section 6.1 ran the gate on A when B failed it with an
  `ask`, because only B changed the cost of asking. C and D share the
  widened sentence, the change most likely to make a guard case ask, so the
  pick's gate is the only one.
- **nano on read-only-question** needs at least 14 judged reps on the picked
  wording, counting the gate run's. PR 1's side is done: 15 of 17 passed.
- **A gate cell inconclusive twice,** in the gate run and in one re-run at
  the gate's commit, is recorded in the evidence as unmeasured, not as a
  miss, naming the number its baseline keeps and the commit that number
  came from, and it is not dropped from the baseline, since `bless` never
  lets an inconclusive result displace a good number.
- **The record** adds step 1's pool and its row, and each candidate's spark
  pool, to what section 6.1's step 5 lists, under its own heading in the
  evidence README.

#### If nothing passes

Nothing ships to `src/nare` if step 2 keeps no candidate, the pick rule
leaves none, or the pick fails the gate. A revert returns the tip's code to
the before commit's. #45 records each pool and table and the labels of every
failing ambiguous rep, and names the next lever: a rule about `ask` in the
default system prompt (section 9), in its own change. The check and its
re-blessed baselines stay, since they hold whatever the description says.
Whether PR 2 goes up with them alone is the user's call.

#### Cost

The judge, `gpt-6.1-sol`, is called on every rep that ends with a result
line. It reads under 2k tokens and writes at most 1,024
(`JUDGE_MAX_TOKENS`), so at section 3.4's prices a call costs at most about
1.4 cents, and a retry doubles that for its rep. The agents' own tokens,
from the usage kept on 2026-10-27, cost about 1 cent a rep on sol and
under half a cent on gemini, nano and luna. spark is free in practice: at
the $0.15 and $0.50 per million the proxy publishes (section 3.4), it would
cost about 0.2 cents a rep.

| Step | Reps run | Paid | Dollars, at most |
| --- | --- | --- | --- |
| the "before" re-run, five models | 15, or 36 with three confirmations | the judge, four paid agents | 0.70 |
| 1, section 6's text | 14 to 21 | the judge | 0.30 |
| 2, per candidate | 14 to 21 | the judge | 0.30 |
| cheap loop, per candidate | 30, or 58 with four confirmations | the judge, four paid agents | 1.10 |
| a flagged cell, pooled on both sides | 28 to 42 | the judge, that model | 1.10 |
| gate | 90, or 118 with four confirmations | the judge, four paid agents | 2.30 |
| nano on read-only-question | 7 to 18 | the judge, nano | 0.30 |
| a guard cell the gate leaves inconclusive, re-run once for its verdict | 3, or 10 with a confirmation | the judge, that model | 0.30 |
| sol's or luna's ambiguous-request, if the gate leaves it inconclusive, re-run once for the miss | 3, or 10 with a confirmation | the judge, that model | 0.30 |
| a cell the gate leaves inconclusive, re-run once more to re-bless it, if the wording ships | 3, or 10 with a confirmation | the judge, that model | 0.30 |
| the read-only re-score, and the blesses | 0 | nothing | 0 |

One candidate, with no flagged cell and no cell the gate leaves
inconclusive, is at most about 270 reps and $5.00. A second candidate adds
up to 80 reps and $1.40, each flagged cell up to 42 reps and $1.10, and each
cell the gate leaves inconclusive up to 20 reps and $0.60, for at most two
re-runs: one for its verdict and one to re-bless it. If step 2 keeps
nothing, the pass ends under $2.
Streamed turns report a cost of 0.0 through this proxy, so the PR reads the
spend from the proxy.

## 7. If prose questions remain: a nudge in the loop

Build this only if, after the wording section 6.2 picks, "asked in prose"
is the most common label among some matrix model's failing ambiguous-case
reps. If it is not, record in #45 that the description was enough, and
stop. If 6.2 picks nothing, this is not decided.

**Outcome, 2026-10-28:** not built in #45. 6.2 picked D, and the condition
held: 7 of `gpt-5-nano`'s 8 failing ambiguous reps in the gate run asked in
prose. But none of the 18 prose closings in the third pass ends its last
non-blank line with `?`, so the rule below would not have fired on any of
them. #117 takes up a nudge with another trigger, or the system-prompt rule
of section 9.

**The rule.** When a turn's reply is text with no tool calls, nare normally
ends the session `done`. It instead appends one user message and takes
another turn when all of these hold:

- the policy allows `ask`
- no `write` or `edit` call has succeeded in this session
- the reply's last non-blank line ends with `?`
- the session has not been nudged before
- the run has no `--schema`, whose retry already owns text replies
- the session is a headless `nare run`, never `nare tui`

The message:

> Your reply ends with a question, but a text reply ends the session as
> finished, so the question may never be answered. If the request can be read
> more than one way and the readings lead to different changes, call ask with
> your question. Otherwise, reply again without the question.

The next reply settles it under the existing rules: `ask` on its own blocks,
and text ends `done`, question or not.

**Why each condition:**

- **No `write` or `edit` yet.** A model that has done the work and ends with
  "Want me to also add tests?" is offering follow-up work. Nudging it invites
  the blocked-on-finished-work failure that #38 removed. `bash` is not
  counted: models search with it, and nare cannot tell a read from a change.
- **Once per session.** It is recorded in `ask_nudged`, a new session field
  that defaults to false and is saved like `schema_retried`. Unlike
  `schema_retried`, which `reopen()` clears as per-run state, `reopen()`
  leaves it set, so it holds across a resume. A model that ends on a
  question twice has been told once, and a second nudge would be a loop.
- **A trailing `?`.** This is crude on purpose. A false positive costs one
  turn and cannot block a session by itself, because only an `ask` call
  blocks. A false negative is today's behaviour.
- **Headless only.** In `nare tui` a person reads every text reply and can
  answer it, so a question in prose is not lost there, and the message's
  claim that a text reply ends the session would be false. Every TUI
  follow-up also goes through `reopen()`, so "once per session" would mean
  once per conversation. Both `nare run` and the TUI call `run()` in
  `loop.py`, so the nudge sits behind a keyword on `run()`, off by default,
  that `cli.py` turns on and the TUI never passes.

**It is visible.** The nudge emits a `progress` event, which the schema
retry does not, and its message is in the transcript, as the schema retry's
is.

**The contract stays at 2,** if reviewers agree with this argument: `done`
still means the model ended with a text reply, and `blocked` still means it
called `ask` on its own under a policy that allows it. The loop takes one
more turn before those rules apply, as the schema retry does. Callers are
already behind: qare and coordinare still pass `--contract 1` (qare#341,
coordinare#360). If reviewers read the nudge as "changing when a status is
reported", it needs contract 3 and a release note, and that cost should
count against building it.

**What the guard case measures.** read-only-question meets every condition
when its answer ends with an offer. After the nudge, the model should reply
again without the offer. If it calls `ask` instead, the case fails, which is
a gate failure, and the nudge does not ship.

**Tests,** in `tests/test_loop.py` with `FakeProvider`:

- A text reply ending in `?`, with no earlier write or edit, gets one nudge.
  A following lone `ask` blocks with its questions.
- The same, followed by a text reply, ends `done`.
- After a successful `edit`, a text reply ending in `?` ends `done` with no
  nudge.
- A second text reply ending in `?` ends `done`. There is no second nudge.
- Under a policy without `ask`, there is no nudge.
- With `--schema`, the schema retry path is unchanged.
- Without the keyword, as the TUI calls `run()`, a text reply ending in `?`
  ends `done` with no nudge.
- `ask_nudged` survives a save and resume.

## 8. Sequencing

| PR | Contains | Changes nare? |
| --- | --- | --- |
| 1 | `--matrix`, `compare --matrix`, `models` and its pointer in `benchmarks/README.md`, the preflight, both cases, five baselines, the `ada/qwen3-14b` baseline deleted, `docs/evidence/45/` | No |
| 2 | Section 6 as revised in 6.1 and 6.2: 6.1's candidates and their record, ambiguous-request's check and its re-blessed baselines, 6.2's step 1 and each built candidate's spark pool and cheap loop, then the matrix table before and after on the one picked, and all five baselines re-blessed after the change | The description only, or nothing if 6.2 picks none |
| 3 | Section 7, only if section 7's first paragraph calls for it | The loop, under `nare run` only |

PR 1 is useful on its own. #43's benchmark for the compaction point needs to
run across models too.

## 9. Out of scope

- **Adding models to the proxy.** Its configuration is not in this repo.
  Whoever runs the proxy adds models. `bin/bench models` shows a new one the
  same day, and adding it to the matrix takes one bless.
- **A `nare models` command.** Callers choosing a model could use one, but it
  is new CLI surface over a proxy-specific endpoint, and #13 asks nare to stay
  caller-agnostic. `bin/bench models` covers the issue. Revisit with #44,
  which reads the same endpoint.
- **Refusing an `ask` made before any read** (failure 3). That changes when
  `blocked` is reported, so it bumps the contract. ambiguous-after-reading
  will show how common asking blind is, and that number decides whether the
  bump is worth it.
- **A rule about `ask` in the default system prompt.** The description
  reaches more callers. If sections 6 and 7 fall short on a model, the prompt
  is the next lever to measure.
- **`cli.py`'s default `--model claude-sonnet-5`,** which the proxy does not
  serve. Noticed while writing this; it is a separate fix.
- **Local `ada/*` models in the matrix.** See section 3.4.

## 10. Acceptance criteria

- [ ] `bin/bench run --matrix` runs every baseline's model in turn, with its
      provider and the base URL that provider needs, refuses a matrix with
      mixed judges with exit 2, and prints the case-by-model table
- [ ] `bin/bench compare --matrix` prints the table without running
- [ ] `bin/bench models` lists the endpoint's models with window, price and
      baselines, and exits 2 with a one-line message when there is no base
      URL
- [ ] `benchmarks/README.md` tells a person or an agent to run
      `bin/bench models` before choosing a model, and `.agents/skills/` is
      unchanged
- [ ] A matrix model the endpoint does not list stops the run before any
      model call
- [ ] ambiguous-after-reading and read-only-question are in the smoke tier
      and pass `bin/bench verify`
- [ ] Five smoke baselines are blessed with one judge, and the
      `ada/qwen3-14b` baseline is deleted
- [ ] `docs/evidence/45/README.md` holds the baseline matrix table and the
      failure labels
- [ ] ambiguous-request has a `bash` check that its questions name
      `TIMEOUT` or its value 30, a test runs it with `run_here`, and
      `bin/bench verify` checks the case rather than exempting it
- [ ] ambiguous-request is re-run for all five models at a commit whose
      `src/nare` is `main`'s and whose case has that check, and each
      baseline is re-blessed from it with `--case ambiguous-request`; no
      results file is written by hand, and the evidence gives the
      read-only re-score of the kept reps beside the new numbers
- [ ] spark on ambiguous-after-reading has at least 14 judged reps on
      section 6's text (`947cc4c`), and the section 6 PR states which row
      of section 6.2's step 1 it fell in and which candidates were built
- [ ] Each built candidate's spark on ambiguous-after-reading has at least
      14 judged reps at its commit before any paid model ran on it, and a
      candidate out by section 6.1's pooled rule there had no paid run
- [ ] The `ask` and `questions` descriptions are the section 6.2 candidate
      its rule picked, and a test checks that no string in the `ask` schema
      names an ambiguous case's identifiers, values or prompt, or copies
      either case's shape under other names, a choice of what to delete
      asked as a question among them
- [ ] The section 6 PR shows each candidate's cheap-loop table with its
      stamp and commit (6.1's two and those 6.2 built), the score that
      picked one, and the matrix table before and after on the one picked,
      and re-blesses all five baselines after the change
- [ ] Every label and reading of a rep in the evidence cites only its tool
      calls, diff, closing, `ask` questions and yes-or-no facts read from
      its tool results, never saved model reasoning
- [ ] If section 6.2 picks nothing, `src/nare` is `main`'s, and #45 records
      each pool and table with the labels of every failing ambiguous rep
      and names the rule in the default system prompt as the next lever
- [ ] The section 6 PR gives the labels of every failing ambiguous rep and
      the question of every passing ambiguous-request rep
- [ ] `gpt-5-nano` on read-only-question has at least 14 judged reps on PR
      1's code and 14 on the picked wording, and the section 6 PR states
      section 6.1's reading of the picked wording's reps and of the first
      measurement's 5 of 7
- [ ] No matrix model has a confirmed regression on edit-docstring,
      multi-file-rename or read-only-question, other than one section
      6.1's reading calls noise, no confirmed regression on
      fix-failing-test with a failing rep that asked, and no confirmed
      regression on ambiguous-after-reading that holds up when pooled
      (section 6.1)
- [ ] `gpt-6.1-sol` and `gpt-6-luna` reach 2 of 3 on ambiguous-request, or
      #45 records the miss with its labels and names the next lever
- [ ] Each matrix model either reaches 2 of 3 on both ambiguous cases, or
      #45 records its failure labels
- [ ] Section 7 is built, nudging only under `nare run`, or #45 records why
      it was not
- [ ] `bin/build` passes
