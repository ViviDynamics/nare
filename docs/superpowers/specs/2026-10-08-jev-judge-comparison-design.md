# nare - Comparing Jev Against the LLM Judge

**Date:** 2026-10-08
**Status:** Draft for review
**Scope:** Development tooling. Adds two evidence files to every benchmark
repetition, a TypeSafe Jev client, and a `compare-judges` subcommand under
`benchmarks/`. Nothing under `src/nare/` changes, and neither does grading,
`min_met` gating, or any baseline. Addresses #46.

## 1. Why, and what this is not

#46 asks whether Jev could judge benchmark runs instead of a plain LLM judge:
it is fast, cheap, and returns a typed answer rather than text that has to be
parsed. The current judge's fragility is real. `judge.py` digs JSON out of
prose and fences and retries an empty reply, because `qwen3-14b` sometimes
reasons past its token cap and answers nothing.

This spec produces **numbers, not an adoption.** Its output is a report of how
Jev and the current LLM judge each agree with a strong reference model, on the
same evidence, at what cost and latency. Whether to adopt Jev, as a
replacement or behind a confidence gate, is decided afterwards from that
report. #46 already warns that an outside analysis of OpenRouter's Jev router
found no clear win, so the experiment has to be able to say "no".

Two jobs are measured:

- **Claim judging.** The judge's existing job: is each of a case's `[judge]`
  claims true of the diff?
- **Failure triage.** New: why did a failed repetition fail? The
  bench-fixes spec (`2026-09-24-bench-fixes-design.md`, section 1) did this by
  hand for 16 failures. A label per failure would let a report say why a pass
  rate moved, not only that it did.

**Agreement is with the reference model, not with ground truth.** Nobody
hand-labels anything here. The reference is a strong model (Opus) given the
same prompt as the LLM judge, so the report measures how close each judge
gets to it. Every summary repeats this.

## 2. What Jev is

The naming in #46 needs correcting. **Jev** is TypeSafe's model. **Noul** is
one of its three question types:

| Type | Asks | Returns |
| --- | --- | --- |
| `noul` | yes or no | `noul`: P(yes), 0 to 1. No separate confidence |
| `choice` | one of up to 255 labels | `choice`, `probabilities` per label, `confidence` |
| `score` | one of 2 to 10 ordered levels | `score`, `probabilities`, `confidence`, `legend` |

A request is `{"model", "state", "questions"}`. `state` is the thing judged, as
a string, object, or array. `questions` maps an id we choose to a question,
and the answers come back under the same ids. Questions are defined per
request, so arbitrary per-case claims need no training.

Limits that shape the design:

- 64k tokens per request, of which at most 32k for the state plus the
  longest question.
- Text only. No rationale: there is no equivalent of the judge's `why`.
- TypeSafe documents Jev 1.13 as unreliable at arithmetic, counting, and
  date comparisons.
- About $0.042 per million input tokens; output tokens are free.

**It is not Chat Completions.** nare's `openai` transport cannot call it.
LiteLLM v1.103.0-rc added a pass-through route,
`POST <proxy>/typesafe/v1/systemone`. The proxy holds the TypeSafe key, the
caller sends a LiteLLM virtual key, and the response is TypeSafe's own,
unchanged. LiteLLM books spend under the pinned version the response reports
(`jev-1.13.0`) even when the request says `jev-latest`.

## 3. Step 0: prove access before building anything

Operational, not code. It requires the proxy to run LiteLLM 1.103 or later
with `TYPESAFE_API_KEY` set, and our TypeSafe account to have Jev access
(launch was early access). The check, with the proxy key the benchmark
already uses (`$ANTHROPIC_API_KEY` in place of `$OPENAI_API_KEY` when
`NARE_PROVIDER=anthropic`):

    curl -sS -D - "${NARE_BASE_URL%/v1}/typesafe/v1/systemone" \
      -H "Authorization: Bearer $OPENAI_API_KEY" \
      -H "Content-Type: application/json" \
      -d '{"model": "jev-latest",
           "state": "def add(a, b):\n    return a - b",
           "questions": {"bug": {"type": "noul",
                                 "instructions": "Does this function have a bug?"}}}'

It passes when the status is 200 and the body has `answers.bug.type ==
"noul"` and a pinned `model` such as `jev-1.13.0`. Record one more fact from
the headers: whether `x-litellm-response-cost` is present. Section 5 depends
on it.

If the check fails, stop. Sections 4 onward are not built.

## 4. Keep the judge's inputs with each repetition

`run_rep` takes `diff = git_diff(workdir)` before the bash checks, and
computes the closing message with `line_text(result.stdout, line.questions)`.
Neither is kept, so no judge can be re-run against a past repetition.

**Change.** Right after `diff` is taken, `run_rep` writes two files into the
repetition's evidence directory:

    benchmarks/results/<stamp>/<case>-<rep>/
      session.json      # unchanged
      stdout.jsonl      # unchanged
      stderr.txt        # unchanged
      judge.txt         # unchanged, judged cases only
      diff.patch        # new: the diff, exactly as the judge receives it
      closing.txt       # new: line_text(...), exactly as the judge receives it

- Written for every repetition that produced a result line and was not an
  infrastructure error, judged or not, because triage reads the diff too.
- A repetition with no result line, or an `infra_error`, writes neither: it
  returns before the diff is taken, as today.
- `keep_evidence` already clears the directory, so a confirmation re-run
  replaces both files.
- `RepRecord` and the results format do not change. `compare-judges` finds the
  files through the existing `artifacts` field.

This ships first and on its own. It is useful whether or not Jev is kept:
anyone can re-judge a past repetition with any judge.

## 5. The Jev client

`benchmarks/runner/jev.py` sends one TypeSafe request and returns typed
answers. It uses `httpx2`, which already arrives with `anthropic`, so there is
no new dependency and no TypeSafe SDK, matching how the `openai` rail works.

**Request.** `POST {base}/typesafe/v1/systemone`, where `base` is
`NARE_BASE_URL` with a trailing `/v1` removed (the normalization
`discover_context_window` already does). Body `{"model", "state",
"questions"}`. Header `Authorization: Bearer <key>`, where the key is the one
`resolve_config` resolved for the run's provider. Through the proxy that is
the LiteLLM virtual key; the TypeSafe key never leaves the proxy.

**Answers.**

    @dataclass(frozen=True)
    class Noul:
        p_yes: float

    @dataclass(frozen=True)
    class Choice:
        choice: str
        probabilities: dict[str, float]
        confidence: float

    @dataclass(frozen=True)
    class JevReply:
        model: str                       # the pinned version that answered
        answers: dict[str, Noul | Choice]
        input_tokens: int
        cost: float | None
        cost_estimated: bool
        latency_s: float                 # wall clock, retries included
        attempts: int

`score` answers are not parsed; nothing here asks one.

**Cost.** `reported_cost(response.headers)` first. If it returns `None`,
`input_tokens * JEV_PRICE_IN / 1e6` with `JEV_PRICE_IN = 0.042`, and
`cost_estimated = True`. Step 0 says which of the two will happen in practice.

**Errors.** Every failure raises `JevError(message, body)`:

- 429 or 529: one retry after `Retry-After` seconds (capped at 30, default
  2), then `JevError`.
- 401, 422, any other non-200: `JevError` carrying the response body. A 422
  means our request is malformed. That is a bug to fix, not to retry.
- A 200 whose body lacks an answer for a question id we sent, or carries one
  of the wrong type: `JevError`.

**Why a second client is acceptable.** `judge.py` runs through nare's own
transport so that judging reaches every provider nare reaches, with no second
credential path. Jev is not a nare provider and has no Chat Completions form,
so there is nothing to reuse. The client stays in `benchmarks/`, is reached
only through the proxy, and reuses the run's proxy key. The module docstring
states this.

## 6. Judging claims three ways

For every judged repetition that has both evidence files, three judges see
the same task prompt, diff, and closing text.

**Jev.** One request per repetition, one noul per claim.

    state = {"task": case.prompt,
             "diff": <diff.patch>,
             "closing_message": <closing.txt>}

    questions = {"c1": {...}, ..., "cN": {...}}   # one per claim, in order

Each question is `type: "noul"` with:

- `instructions`: the claim, followed by the rules `JUDGE_SYSTEM` gives the
  LLM judge, restated against the state's field names. Judge only what `diff`
  shows. A claim about a file's contents is true only if `diff` shows it.
  `closing_message` is evidence only for claims about what the agent said or
  asked.
- `criteria.true`: "The evidence shows the claim holds."
- `criteria.false`: "The evidence does not show the claim holds, including
  when it says nothing about it."

Recorded: the raw `p_yes` per claim. No cutoff is applied when recording;
cutoffs are applied in the summary (section 9).

**Size.** Estimated at four characters per token, as `compact.py` does:
`len(json.dumps(state)) + len(longest instructions)`, over 4. Above 32,000,
Jev is not called and its row records `error: "too_large"`. Nothing is
truncated: a truncated diff asks a different question. Every current case
is far below the limit.

**The LLM judge.** `score()` from `judge.py`, unchanged, on a transport from
`judge_transport(config)`. `config.judge_model` resolves as it does for `run`:
`--judge-model`, then `NARE_BENCH_JUDGE_MODEL`, then the model under test.

**The reference.** The same `score()`, on a transport built the same way but
with the reference model. Its prompt is identical to the LLM judge's; only
the model differs.

**Usage and cost for both LLM judges.** `score()` does not return usage. A
small recording wrapper that implements `Transport` sits around each judge
transport and keeps every `Reply`'s usage and cost. `judge.py` is not
changed. Tokens are `input + output + cache_read + cache_write`, as the
benchmark counts them elsewhere. Cost is `Reply.cost`, and unknown when the
backend reported none.

**Not measured: the reason.** Jev returns no `why`. The comparison does not
penalize that, and the summary says so.

## 7. Failure triage

Only repetitions whose outcome is `fail` are triaged. `error` repetitions
already carry a reason and say nothing about the agent.

**Labelled by code, not by a model.** These are exact, and counting is a
documented Jev weakness:

| Label | Condition |
| --- | --- |
| `ran_out` | `stop_reason` is `max_turns`, `budget`, or `context` |
| `judge_gate` | every check passed, and `judge_met < min_met` |

Every triaged repetition also gets a `claimed_done` flag, set by code: the
status was `done` and at least one check failed. It is a fact recorded beside
the cause, not a cause, because it can accompany any of them.

**Labelled by Jev and by the reference.** Every other failure. Jev gets one
`choice` question, `cause`, whose `criteria` map each label to its
description. The reference gets the same labels and descriptions as text.

| Label | Description given to both |
| --- | --- |
| `asked_when_clear` | Stopped to ask, though the request had one reasonable reading |
| `did_not_ask` | Went ahead on a request that needed a question first |
| `no_change` | Ended without making the change the task required, and without asking |
| `wrong_change` | Changed the right place, but the change does not do what was asked |
| `incomplete_change` | Did part of the task, such as renaming in some files and not others |
| `off_target_edit` | Edited the wrong file, or one the task said to leave alone, such as the tests |
| `other` | None of the above |

**State, identical for both:**

    {"task": case.prompt,
     "status": <result line status>,
     "failed_checks": [{"label": ..., "detail": ...}],   # from RepRecord.checks
     "diff": <diff.patch>,
     "closing_message": <closing.txt>,
     "transcript": [...]}

`transcript` is built from `session.json`'s messages, one entry per content
block, tagged with its role:

- `text` and `tool_result` blocks: the text, clipped to 1,000 characters.
- `tool_use` blocks: the tool name and its arguments as JSON, clipped to
  1,000 characters.
- `thinking` blocks are dropped. Images become `"[image]"`.

If the state's estimate (four characters per token, as above) exceeds 24,000
tokens, the first user message is kept and the oldest of the remaining
messages are dropped until it fits, replaced by one entry,
`"[N messages omitted]"`. That leaves room under Jev's 32k for the question.
If it still does not fit, the row is `too_large` for both judges. A
repetition with no `session.json` gets `transcript: []`.

**The reference's prompt.** `TRIAGE_SYSTEM` gives the labels and descriptions
and asks for `{"cause": "<label>"}` only. The state goes in the user message as
JSON. The reply is parsed with `judge.py`'s `_extract_json`, with the same two
attempts `score()` makes. A label outside the list is a `JudgeError`.

## 8. The `compare-judges` subcommand

    bin/bench compare-judges [--results PATH] [--case ID ...]
        [--provider P] [--base-url URL] [--judge-model M]
        [--jev-model M] [--reference-model M]

**Configuration.**

| Setting | Flag / environment | Default |
| --- | --- | --- |
| Results to compare | `--results` | the newest, via `latest_results` |
| Proxy | `--base-url` / `NARE_BASE_URL` | required: Jev is reached only through it |
| Provider and key | `--provider` / `NARE_PROVIDER`, its key variable | as `run` |
| LLM judge | `--judge-model` / `NARE_BENCH_JUDGE_MODEL` | the model under test |
| Jev model | `--jev-model` / `NARE_BENCH_JEV_MODEL` | `jev-latest` |
| Reference | `--reference-model` / `NARE_BENCH_REFERENCE_MODEL` | required. The README suggests `claude-opus-5-5` |

The model under test is read from the results records' `model` field, so
`NARE_MODEL` is not needed. Records naming more than one model exit 2. The
reference has no default because its alias depends on the proxy, and the
benchmark already refuses to name models in cases for the same reason.

**Order.** Repetitions are processed one at a time, and within one the judges
run in a fixed order: Jev, the LLM judge, the reference. Sequential for the
same reason as `run_case`, and so that latency is measured without
contention.

**Output.** Written into the compared run's evidence directory, under the
comparison's own stamp, so a second comparison never overwrites a first:

    benchmarks/results/<stamp>/
      compare-<cstamp>.jsonl
      compare-<cstamp>.txt          # the summary, as printed

One `.jsonl` line per repetition, task, and judge:

| Field | Meaning |
| --- | --- |
| `case`, `rep` | the repetition |
| `task` | `claims` or `triage` |
| `judge` | `jev`, `llm`, `reference`, or `code` |
| `model` | the model that answered. For Jev, the pinned version it reported |
| `verdicts` | claims only: P(yes) per claim for Jev, booleans for the others |
| `cause` | triage only: the label |
| `probabilities`, `confidence` | triage, Jev only |
| `claimed_done` | triage only |
| `tokens`, `cost`, `cost_estimated` | usage of this call. `cost` is null when unknown |
| `latency_s`, `attempts` | wall clock and calls made |
| `error` | null, `too_large`, or the error message |

**Exit codes.** 0 when the comparison finished, including when some judges
failed on some repetitions, because those failures are in the report. 1 when
there is nothing to compare: no results, or no repetition with evidence files.
2 for configuration errors, before any call is made.

## 9. The summary

Every percentage is printed with its `n`.

1. **Header.** The results stamp (results records carry no commit). The
   model under test, the LLM judge, the reference, and Jev's pinned version.
   Repetitions compared, and repetitions skipped with the reason: no evidence
   files, `too_large`, a judge error. The line "Agreement is with the
   reference model, not ground truth."
2. **Claim agreement with the reference**, over claims where both judges
   answered:
   - The LLM judge, overall.
   - Jev at a cutoff, for t in {0.5, 0.6, 0.7, 0.8, 0.9}: P(yes) >= t is true.
   - Jev with an uncertain band, for t in {0.6, 0.7, 0.8, 0.9}: claims with
     P(yes) between 1 - t and t are left unanswered. Reported as coverage (the
     share of claims outside the band) and agreement on those claims. This is
     the data a later confidence-gate decision needs.
   - Per claim: the claim text, n, Jev's agreement at 0.5, the LLM judge's
     agreement. A claim Jev handles badly shows up here rather than being
     averaged away.
3. **Outcome flips.** For each judge, and for Jev at each cutoff, how many
   gated repetitions get a different `rep_outcome` from the reference's, using
   the recorded checks. Only repetitions whose checks passed can flip. This is
   the number that changes what the benchmark reports.
4. **Triage.** The code-labelled counts. A confusion table of Jev against the
   reference, in counts, not percentages: one run yields few failures. Jev's
   median `confidence` where it agrees with the reference and where it does
   not. How many triaged failures had `claimed_done`.
5. **Cost and speed**, per judge: calls, total tokens, total dollars (marked
   as an estimate where it is), median and p95 latency.

There is no mode that re-renders the summary without model calls. The cutoffs
and bands above cover the decision the summary feeds, and the `.jsonl` holds
every raw value for any other cut.

## 10. Failure handling

| What fails | What happens |
| --- | --- |
| Configuration | exit 2 before any call, as `resolve_config` does |
| A repetition without `diff.patch` or `closing.txt` | skipped, counted in the header with the reason |
| State too large for Jev | Jev row `too_large`; the other judges still run |
| Jev returns 429 or 529 | one retry honoring `Retry-After`, then an error row |
| Jev returns another error, or a malformed answer | error row with the response body; the comparison continues |
| The LLM judge or reference replies badly | the existing two-attempt `JudgeError` path, then an error row |
| The reference fails on a repetition | dropped from every judge's agreement figures; counted in the header |
| Any other judge fails on a repetition | dropped from that judge's figures only |

As with `rep_outcome`, a judge that could not answer is never counted as a
wrong answer.

## 11. Code layout

| File | Holds |
| --- | --- |
| `benchmarks/runner/jev.py` | the client, `Noul`, `Choice`, `JevReply`, `JevError` |
| `benchmarks/runner/triage.py` | the labels, the code labels, the triage state, `TRIAGE_SYSTEM`, the reference triage call |
| `benchmarks/runner/compare.py` | the claims state and questions, the recording transport, per-repetition orchestration, writing the `.jsonl` |
| `benchmarks/runner/agreement.py` | pure: every metric in section 9, and rendering the summary |
| `benchmarks/runner/__main__.py` | the `compare-judges` subparser, and the section 4 change to `run_rep` |

`agreement.py` stays pure for the same reason `grade.py` and `report.py` are:
it is handed rows and does arithmetic, so all of section 9 is unit-testable.

## 12. Testing

No network and no Docker, in the idioms the suite already uses.

- **`test_jev.py`**, with `httpx2.MockTransport` as in
  `test_openai_transport.py`: the URL after `/v1` is removed, the bearer key,
  the body; parsing noul and choice answers; one retry on 429 honoring
  `Retry-After` with `sleep` patched; a 422 raising with its body; a missing
  or wrongly typed answer raising; cost from the header, and estimated from
  tokens when the header is absent.
- **`test_triage.py`**: `ran_out`, `judge_gate`, and `claimed_done` from
  hand-built records; the transcript's clipping and dropped blocks; the
  24,000-token trim keeping the first user message and inserting the omission
  marker; the reference's reply parsed, and an unknown label raising.
- **`test_compare.py`**: the claims state and questions for a case; the size
  estimate tipping into `too_large`; an end-to-end `compare-judges` over a
  temporary results directory with a fake Jev client and `FakeJudge`
  transports, asserting the `.jsonl` rows, skip reasons, and summary lines;
  a judge that fails on one repetition does not stop the comparison.
- **`test_agreement.py`**: agreement, cutoffs, bands and coverage, outcome
  flips, the confusion table, and latency percentiles, from hand-built rows.
- **`test_main.py`** additions, with the existing `fake_sandbox`: a repetition
  keeps `diff.patch` and `closing.txt`; one without a result line keeps
  neither; a re-run clears them.

There is no live test. Step 0 is the live check.

## 13. Docs

A "Comparing judges" section in `benchmarks/README.md`: the environment
variables, the command, how to read the summary, and that agreement is with
the reference model, not ground truth.

## 14. Delivery

0. Step 0 against the proxy (section 3). Nothing proceeds until it passes.
1. **PR 1**: section 4, the evidence files, with their tests.
2. **PR 2**: sections 5 to 13.
3. One full-tier run, then `compare-judges` over it. The summary is attached
   to #46, and adoption is decided there.

More runs, across more agent models, make the numbers firmer. Triage in
particular will be thin from one run, because one run yields few failures.
