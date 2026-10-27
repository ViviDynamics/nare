# nare - Ask Reliably Across Models

**Date:** 2026-10-23
**Status:** Reviewed against code 2026-10-26
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
| Add more capable models to the test matrix | Sections 3 and 4: a matrix the benchmark runs, with Sonnet and two newer OpenAI models in it |
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
in `benchmarks/runner/__main__.py`). It checks after the reps have run, and
it does not compare the provider. So the matrix needs no new configuration:
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
command would make it too easy to bless away a regression. A results line
carries the model and the output cap but not the provider or the judge, so
`bless` writes the provider and judge it resolves from its own flags and
environment. A bless after a matrix run must get the baseline's provider and
the matrix judge, either from the person or from what the matrix run
recorded. The plan picks which.

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
| `claude-haiku-5-5` | `anthropic` | The model the issue is about, as the proxy serves it now | 0.10, 0.50 |
| `gpt-5-nano` | `openai` | Called `ask` in the call's test | 0.05, 0.40 |
| `gpt-6-luna` | `openai` | A newer OpenAI model at about nano's price, and the likely replacement if we move off nano | 0.10, 0.50 |
| `gemini-3.1-flash-lite` | `openai` | Called `ask` in the call's test | 0.25, 1.50 |
| `spark/glm-5.3-flash` | `openai` | The default for live runs, and a reasoning model | 0.15, 0.50 |
| `claude-sonnet-5-5` | `anthropic` | Capable tier, named on the call | 2, 10 |
| `gpt-6.1-sol` | `openai` | Capable tier, the newer OpenAI model | 2, 10 |

**Provider:** `openai` for every model that is not Claude, `anthropic` for
the two that are. When a proxy translates a reasoning model's thinking onto
the Anthropic route, nare warns that words or whole answers can be dropped
and suggests `--provider openai` (`_warn_proxy_reasoning` in
`transport/anthropic.py`). The proxy reports reasoning support for all four
non-Claude models besides spark, and spark is a reasoning model too. The
Claude models' thinking is native to the Anthropic route.

**Judge:** `claude-sonnet-5-5` for every baseline. It is a capable model, and
the judge's claims are binary and checkable, so judging its own reps is a
small bias. The matrix table notes it.

**`ada/qwen3-14b` leaves the matrix.** Its baseline predates contract 2 and
the system prompt, so nothing can be compared against it. The `ada/*` models
do tool calls through LiteLLM's JSON mode, which answered `{}` to every call
on 2026-09-30. Delete the baseline. The local models can still be run by hand
with `--model`, and one rejoins the matrix when someone blesses a baseline
for it that works.

**Cost.** A smoke matrix run is 6 cases × 3 reps × 7 models: 126 reps, plus
any seven-rep confirmations. Estimated from the smoke tier's token medians
(about 1k to 5k per rep) and the prices above, that is a few dollars,
most of it the judge and the two capable models. It is about an hour of
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

The questions are the session's top-level `questions`, which only an `ask`
that blocks fills in (`step` in `loop.py`). The `case.toml`:

```toml
id = "ambiguous-after-reading"
tier = "smoke"
# The ambiguity is in the files, not the prompt: two helpers are unused, and
# "the unused helper" is singular. A correct run reads, then asks which.
prompt = "Delete the unused helper."
# Room to read both files and search for callers before asking.
max_turns = 8

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
  exempts it, as it does ambiguous-request.
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

Before nare changes, bless all seven baselines on the matrix PR's head,
whose `src/nare` is unchanged from `main`, with the one judge.

The matrix is empty until they exist, so each is made one model at a time.
`bless` writes the provider and judge it is given, so both flags go on both
commands:

    bin/bench run   --tier smoke --model M --provider P --judge-model claude-sonnet-5-5
    bin/bench bless --tier smoke --model M --provider P --judge-model claude-sonnet-5-5

`P` is the row's provider from section 3.4. An `openai` row's `run` needs
`OPENAI_API_KEY`, which on this proxy is the same key as
`ANTHROPIC_API_KEY`, and a `--base-url` that ends in `/v1` (section 3.2).
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
seven baselines, one model at a time, so the baselines on `main` measure
the description on `main`.

## 7. If prose questions remain: a nudge in the loop

Build this only if, after section 6, "asked in prose" is the most common
label among some matrix model's failing ambiguous-case reps. If it is not,
record in #45 that the description was enough, and stop.

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
| 1 | `--matrix`, `compare --matrix`, `models` and its pointer in `benchmarks/README.md`, the preflight, both cases, seven baselines, the `ada/qwen3-14b` baseline deleted, `docs/evidence/45/` | No |
| 2 | Section 6, with the matrix table before and after, and all seven baselines re-blessed after the change | The description only |
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
- [ ] Seven smoke baselines are blessed with one judge, and the
      `ada/qwen3-14b` baseline is deleted
- [ ] `docs/evidence/45/README.md` holds the baseline matrix table and the
      failure labels
- [ ] The `ask` and `questions` descriptions are as in section 6
- [ ] The section 6 PR shows the matrix table before and after, and
      re-blesses all seven baselines after the change
- [ ] No matrix model has a confirmed regression on edit-docstring,
      multi-file-rename or read-only-question
- [ ] Each matrix model either reaches 2 of 3 on both ambiguous cases, or
      #45 records its failure labels
- [ ] Section 7 is built, nudging only under `nare run`, or #45 records why
      it was not
- [ ] `bin/build` passes
