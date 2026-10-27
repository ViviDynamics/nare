# Ask Wording, Second Pass: Implementation and Measurement Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `ask` description with spec section 6.1's candidate A, then candidate B, one commit each, with tests that pin the new sentences and keep every ambiguous case's answer out of the whole `ask` schema. Then measure as section 6.1 says: a noise check of `gpt-5-nano` on read-only-question on PR 1's code, the cheap loop on each candidate, the pick, the full gate on the pick, and the record and re-bless.

**Architecture:** Part D changes one string, the `ask` entry's `description` in `src/nare/tools.py`, twice, and its tests in `tests/test_tools.py`. The `questions` description and the sentence about where a question goes stay as section 6 made them. Part E runs `bin/bench` from two checkouts: `nare-45`, always on the branch tip, and a detached side worktree, `nare-45-side`, for every other run. A sourced shell file holds the setup and small helpers, and logs every results file with the tag of its step and the commit it ran on.

**Tech Stack:** Python 3.12+, pytest, ruff, mypy strict, `uv`, `jq`, Docker (Part E only).

**Spec:** `docs/superpowers/specs/2026-10-23-ask-across-models-design.md`, section 6.1 (the design), with sections 3, 5, 8 and 10. **Previous plan:** `docs/superpowers/plans/2026-10-26-ask-across-models.md` (Part C is the first measurement's operator steps). **Evidence:** `docs/evidence/45/README.md`. Issue: #45.

## Decisions (the plan's calls)

1. **Each candidate is one commit, B on top of A.** Task D1 commits A and Task D2 commits B, so `B^` is `A`. A review fix goes into the candidate's own commit (Task D3, Step 2 says how), so each candidate stays one commit the measurement can run from.
2. **A is measured from a detached side worktree at A's commit, `/home/smadinya/vividynamics/nare-45-side`.** `bin/bench` builds its image from the checkout it runs in: `repo_root()` is three levels above `benchmarks/runner/sandbox.py`, and `benchmarks/Dockerfile` copies that checkout's `src` ("Installs the WORKING TREE's nare"). So whatever a checkout has checked out is what gets measured. The side worktree is needed anyway, for E1 on PR 1's code and for E4's extra single runs, which must stay out of `nare-45` (decision 3). With it, `nare-45` never leaves `45-ask-description`, and nothing can be committed on a detached HEAD there. The side worktree is created detached and only ever switched between commits. It gets its own `.venv` from `uv run` on first use, and needs no `.env`: the shell file reads `nare-45/.env` by absolute path.
3. **`nare-45` holds only the branch tip's matrix runs.** `bless` reads the newest run in `nare-45/benchmarks/results/` (`latest_run`, the newest stamp by file name) and, after a matrix run, takes `--model`'s own `<stamp>.<model>.jsonl` from it (`results_for`). A single run started after the gate would be the newest run instead: `bless --model gpt-5-nano` would bless one case from it, and every other model's bless would exit 2. So the gate is the last run in `nare-45` before E5's bless, and every single run goes to the side worktree.
4. **If A is picked while B is the tip, B is reverted as a new commit.** The branch is unpublished, so a reset is allowed too, and each is one command. The revert is chosen because it keeps B's commit on the branch: when PR 2 is pushed, the commit that B's cheap-loop stamp names can be fetched and read, which spec 6.1 step 5 and section 10 ask for ("each candidate's cheap-loop table with its stamp and commit"). After a reset that SHA would be on no branch, and no reviewer could see it. E4 checks that the revert's tree is A's.
5. **"A case's answer", for the leak test,** is a fixed list traced to `benchmarks/cases` in a comment: each ambiguous case's prompt verb and subject (`increase`, `timeout`, `delete`, `unused helper`), its fixture's file and identifier names (`config.py`, `TIMEOUT`, `RETRIES`, `utils.py`, `main.py`, `format_bytes`, `slugify`, `parse_csv_line`), the values ambiguous-request's judge accepts in a question (the current `30`, and the `60` and `120` the old example offered; every guess set 60), and the old example's frame, `to what`. It is matched lower-cased against `json.dumps` of the whole `ask` schema, so the name, both descriptions and the property names are all covered. That is spec 6.1's list plus the prompt verbs (6.1 says A "names no case's file, setting, value or verb"), the three file names, `120` and the frame. `total`, the fourth helper, is left out as an everyday word that is not one of the readings. The test first asserts that the list catches the old example, so a guard that could never fail would show.
6. **The widened test is renamed** `test_the_ask_schema_names_no_benchmark_cases_answer`, because it now covers the whole schema, and the log-level assertion it held moves to `test_the_ask_example_is_the_log_level_choice`, which spec 6.1 says to keep.
7. **Counting reps.** The runner has no reps flag. A smoke run is 3 reps per case (`TIER_DEFAULTS` in `case.py`; no smoke case sets `reps`), and a case whose pass rate falls below its baseline is re-run at 7 (`CONFIRM_REPS`), which replace the 3 in the results file and in the evidence. `--case` is a set filter (`_select`), so naming a case twice adds nothing. 14 judged reps therefore take several runs, each its own results file. A pool is every rep of one model on one case in the files logged under the step's tags, counting `pass` and `fail` and not `error`, as `summarize` does. A confirmation's replaced first-pass reps are in no file and count on neither side.
8. **Reading a guard drop when n is not 14.** Spec 6.1's "fails at least 3 more of 14 reps than PR 1's code does, a rate 0.2 higher" is read as: the candidate's fail rate minus PR 1's is at least 0.2. At 14 and 14 that is 3 more failures.
9. **The nano read-only-question pool counts as confirmed.** At 14 or more judged reps it is a larger sample than the runner's confirmation (7). If its pass rate is below the baseline's 3/3 and the reading calls the drop the description's, the gate fails, even when the gate run's own 3 reps passed.
10. **If both candidates are out** (each has a confirmed regression on ambiguous-after-reading in its cheap loop), nothing is picked, E4 does not run, and E5 records the failure.
11. **An inconclusive guard cell, on a model whose baseline has that case,** is re-run once in the side worktree on the gate's code, and that run's verdict stands. If it is inconclusive again, the cell is recorded as unchecked, as spark's and sol's multi-file-rename are (`docs/evidence/45/README.md`).
12. **Both sides reach 14 for any other confirmed guard regression in the gate run.** Spec 6.1 step 1 runs PR 1's side. The reading compares the two sides, so the pick's side is brought to 14 too, counting the gate run's reps.
13. **"Every failing ambiguous rep"** (6.1 step 5, section 10) is every failing rep of the two ambiguous cases in A's loop, B's loop and the gate run, and the questions are those of every passing ambiguous-request rep in the same three runs.
14. **`NARE_STREAM=1` for every run,** as the section 5 baselines were run (previous plan, decision 10).

## Global Constraints

- Work only in `/home/smadinya/vividynamics/nare-45` and, in Part E, `/home/smadinya/vividynamics/nare-45-side`. Never touch `/home/smadinya/vividynamics/nare`: it is the user's checkout and the editable install behind `nare`.
- Commit only on `45-ask-description`. Never commit to, rebase or push `45-ask-across-models`: it is PR 1, published as ViviDynamics/nare#116. Checking it out detached in the side worktree only reads it. `45-ask-description` is unpublished: no pushes and no PRs in this plan.
- Prose we publish (docs, commit messages, code comments) uses no em dashes.
- `.agents/skills/` does not change.
- `bin/build` (ruff format --check, ruff check, mypy strict, pytest) passes at A's commit and at B's (Part D), and at the gate's commit (E4).
- Tests never reach the proxy or Docker.
- Bench commands that touch Docker (`run`, `verify`) need Docker Desktop running on Windows, and `DOCKER_CONFIG` pointing at a directory whose `config.json` is `{}`, because `~/.docker/config.json` uses `credsStore: desktop.exe`, which fails under WSL. Never run two at once, from any checkout (`nare`, `nare-45`, `nare-45-side`, `nare-63`, `nare-96b`): every run tags `nare-bench:local`. The shell file's `bench` helper refuses to start while another bench runs.
- Never print a key or the base URL. The shell file shows the base URL as `$ROOT`.
- `bin/bench bless` runs only in E5, in `nare-45`, on the gate run. Never in the side worktree, and never after a cheap loop.
- A bench run takes longer than a foreground command may (10 minutes). Run every block that calls `bench` or `to14` with the Bash tool's `run_in_background`, wait for its exit notification, and start nothing that runs a bench meanwhile.
- Commit style `type(scope): summary (#45)`.

## Review Focus

1. **The texts are spec 6.1's, word for word.** D1 Step 4 and D2 Step 4 assert equality of the joined `ask` description with candidate A and with candidate B, and of `questions.description` with section 6's.
2. **#38's four exclusions keep their words.** Only their order changes, so "details you can decide yourself" is last and "such a detail" follows it directly. The open-value test pins that pair.
3. **The leak list** (decision 5): every entry traced to a case file, matched on the whole schema, and shown to catch the old example.
4. **One commit per candidate, and `B^` is `A`.** E0 checks it before any money is spent.

## File map

| File | Change | Task |
| --- | --- | --- |
| `src/nare/tools.py` | the `ask` entry's `description`: candidate A, then B | D1, D2 |
| `tests/test_tools.py` | `import json`; the open-value pin; the log-level pin; `CASE_ANSWERS`, `case_answers_in` and the widened leak test; B's last-sentence pin | D1, D2 |
| `docs/evidence/45/README.md` | section "After the second wording" | E5 |
| `benchmarks/baselines/*.smoke.toml` | re-blessed on the gate run, only if the wording ships | E5 |
| `nare-45/benchmarks/results/45-2-*` (gitignored, never committed) | the shell file, the run log, transcripts, tables, scores, notes | E0 to E5 |

---

# Part D: the code (implementer, branch `45-ask-description`)

Do not run the bench in Part D. Part E measures, after code review.

### Task D0: Start

- [ ] **Step 1: Check the worktree**

```bash
cd /home/smadinya/vividynamics/nare-45
git status --short                          # expect no output
git branch --show-current                   # 45-ask-description
git log --oneline 45-ask-across-models..HEAD
```

Expected, newest first: `docs: plan for the second ask wording pass (#45)` (this file), `73d867d docs: revise the ask wording after the first measurement (#45)`, `54f175f feat(tools): ask says where a question goes, with an example no case uses (#45)`. If it differs, stop and report.

- [ ] **Step 2: Build once**

Run: `bin/build`
Expected: clean.

---

### Task D1: Candidate A

**Files:**
- Modify: `src/nare/tools.py` (the `ask` entry in `TOOL_SCHEMAS`, around line 500)
- Test: `tests/test_tools.py`

**Interfaces:**
- Produces: the `ask` schema's `description`, verbatim spec 6.1 candidate A. The `questions` description, the name, the `input_schema` shape and `required` are unchanged. `test_the_ask_question_states_its_default` and `test_the_ask_description_says_where_a_prose_question_goes` are unchanged and keep passing.

- [ ] **Step 1: Write the tests**

In `tests/test_tools.py`, add `import json` to the standard-library imports, so the top reads:

```python
import asyncio
import json
import logging
```

Then replace the whole of `test_the_ask_example_is_no_benchmark_cases_answer`:

```python
def test_the_ask_example_is_no_benchmark_cases_answer() -> None:
    # The old example was ambiguous-request's fixture and the answer its judge
    # looks for, so a model could pass that case by copying it (#45).
    questions = schema_of("ask")["input_schema"]["properties"]["questions"]
    assert "TIMEOUT" not in questions["description"]
    assert "Set the log level to INFO or to WARNING?" in questions["description"]
```

with:

```python
def test_the_ask_description_says_an_open_value_is_no_detail() -> None:
    # Without the old example, sol and luna filed the new timeout value under
    # "details you can decide yourself" and set one (#45). The value sentence
    # follows that exclusion directly, so "such a detail" means it.
    assert (
        "or for details you can decide yourself such as wording, style or names. "
        "A new value the request leaves open is not such a detail."
    ) in schema_of("ask")["description"]


def test_the_ask_example_is_the_log_level_choice() -> None:
    questions = schema_of("ask")["input_schema"]["properties"]["questions"]
    assert "Set the log level to INFO or to WARNING?" in questions["description"]


# What a model could copy from the ask schema to pass an ambiguous case, from
# benchmarks/cases: each prompt's verb and subject, the fixture's names, and
# the values ambiguous-request's judge accepts in a question (the current 30,
# and the 60 and 120 the old example offered; every guess set 60). "to what"
# is the old example's frame, an amount left open. Matched lower-cased.
CASE_ANSWERS = (
    # ambiguous-request: "Increase the timeout."; config.py holds TIMEOUT = 30
    # and RETRIES = 3.
    "increase",
    "timeout",
    "config.py",
    "retries",
    "30",
    "60",
    "120",
    "to what",
    # ambiguous-after-reading: "Delete the unused helper."; utils.py holds
    # format_bytes and slugify, which nothing calls, and parse_csv_line and
    # total, which main.py calls. "total" is an everyday word, so it is left out.
    "delete",
    "unused helper",
    "utils.py",
    "format_bytes",
    "slugify",
    "parse_csv_line",
    "main.py",
)


def case_answers_in(text: str) -> list[str]:
    lowered = text.lower()
    return [answer for answer in CASE_ANSWERS if answer in lowered]


def test_the_ask_schema_names_no_benchmark_cases_answer() -> None:
    # The old example was ambiguous-request's fixture and the answer its judge
    # looks for, so a model could pass that case by copying it (#45). The whole
    # schema is checked, descriptions and all.
    old = (
        "Raise TIMEOUT from 30 to what: 60, 120, or another value? "
        "Unanswered, I will use 60."
    )
    assert case_answers_in(old) == ["timeout", "30", "60", "120", "to what"]
    assert case_answers_in(json.dumps(schema_of("ask"))) == []
```

The open-value test pins A's new sentence together with the exclusion it follows, which also pins the reorder. Whether the wording works is the matrix's job (spec 6.1).

- [ ] **Step 2: Run the tests to verify the new pin fails**

Run: `uv run pytest tests/test_tools.py -q -k the_ask`
Expected: `1 failed, 4 passed`. `test_the_ask_description_says_an_open_value_is_no_detail` fails on its assertion. The widened leak test already passes, because section 6 removed the leak, and its first assertion shows that the list catches the old example.

- [ ] **Step 3: Replace the `ask` description's last five lines**

In `src/nare/tools.py`, in the `ask` entry's `"description": (...)`, replace:

```python
            "answered. Do not use it for anything you can find out by reading "
            "files or running commands, for details you can decide yourself "
            "such as wording, style or names, to ask permission, or to offer "
            "follow-up work. Call ask on its own, before making any change. "
            "This ends the session."
```

with:

```python
            "answered. Do not use it for anything you can find out by reading "
            "files or running commands, to ask permission, to offer follow-up "
            "work, or for details you can decide yourself such as wording, "
            "style or names. A new value the request leaves open is not such a "
            "detail. Call ask on its own, before making any change. This ends "
            "the session."
```

The five lines above them, and the `questions` property, do not change. The longest line is 80 columns.

- [ ] **Step 4: Check the texts word for word**

```bash
uv run python - <<'PY'
from nare.tools import TOOL_SCHEMAS

A = (
    "Stop and ask the caller to resolve an ambiguous request. Use this only when "
    "the request can reasonably be read more than one way and the readings lead "
    "to different changes, and only after reading the files involved. Ask only "
    "with this tool: a text reply ends the session as finished, so a question "
    "written there may never be answered. Do not use it for anything you can "
    "find out by reading files or running commands, to ask permission, to offer "
    "follow-up work, or for details you can decide yourself such as wording, "
    "style or names. A new value the request leaves open is not such a detail. "
    "Call ask on its own, before making any change. This ends the session."
)
QUESTIONS = (
    "One question per item, naming the readings you are choosing between and "
    "the one you will use if it goes unanswered, e.g. 'Set the log level to "
    "INFO or to WARNING? Unanswered, I will use WARNING.'"
)
ask = next(s for s in TOOL_SCHEMAS if s["name"] == "ask")
assert ask["description"] == A, ask["description"]
assert ask["input_schema"]["properties"]["questions"]["description"] == QUESTIONS
print("candidate A, word for word")
PY
```

Expected: `candidate A, word for word`. `A` is spec 6.1's candidate A joined, and `QUESTIONS` is spec section 6's `questions` text.

- [ ] **Step 5: Run the tests and build**

```bash
uv run pytest tests/test_tools.py -q -k the_ask     # 5 passed
bin/build
git diff --stat                                      # src/nare/tools.py and tests/test_tools.py only
```

- [ ] **Step 6: Commit**

```bash
git add src/nare/tools.py tests/test_tools.py
git commit -m "feat(tools): ask says a value the request leaves open is no detail (#45)"
```

E0 finds this commit by that subject. Keep it exactly.

---

### Task D2: Candidate B

**Files:**
- Modify: `src/nare/tools.py` (the same entry)
- Test: `tests/test_tools.py`

**Interfaces:**
- Produces: candidate B, which is A with its last sentence, "This ends the session.", replaced by "This stops the session until the caller answers." No test pins "This ends the session.": before this task, `grep -rn "ends the session" tests` finds only the prose-question pin's "ends the session as finished". So this commit changes no existing test; it adds the pin spec 6.1 calls for when B is picked.

- [ ] **Step 1: Write the failing test**

In `tests/test_tools.py`, directly after `test_the_ask_description_says_an_open_value_is_no_detail`, add:

```python
def test_the_ask_description_says_the_session_waits_for_the_answer() -> None:
    # "This ends the session." read as the price of asking: spark weighed it
    # against asking, and once wrote its question in prose instead (#45).
    assert schema_of("ask")["description"].endswith(
        "Call ask on its own, before making any change. This stops the session "
        "until the caller answers."
    )
```

- [ ] **Step 2: Run the tests to verify it fails**

Run: `uv run pytest tests/test_tools.py -q -k the_ask`
Expected: `1 failed, 5 passed`; the new test fails on its assertion.

- [ ] **Step 3: Replace the last sentence**

In `src/nare/tools.py`, replace:

```python
            "detail. Call ask on its own, before making any change. This ends "
            "the session."
```

with:

```python
            "detail. Call ask on its own, before making any change. This stops "
            "the session until the caller answers."
```

- [ ] **Step 4: Check the text word for word**

```bash
uv run python - <<'PY'
from nare.tools import TOOL_SCHEMAS

B = (
    "Stop and ask the caller to resolve an ambiguous request. Use this only when "
    "the request can reasonably be read more than one way and the readings lead "
    "to different changes, and only after reading the files involved. Ask only "
    "with this tool: a text reply ends the session as finished, so a question "
    "written there may never be answered. Do not use it for anything you can "
    "find out by reading files or running commands, to ask permission, to offer "
    "follow-up work, or for details you can decide yourself such as wording, "
    "style or names. A new value the request leaves open is not such a detail. "
    "Call ask on its own, before making any change. This stops the session "
    "until the caller answers."
)
ask = next(s for s in TOOL_SCHEMAS if s["name"] == "ask")
assert ask["description"] == B, ask["description"]
print("candidate B, word for word")
PY
```

Expected: `candidate B, word for word`.

- [ ] **Step 5: Run the tests and build**

```bash
uv run pytest tests/test_tools.py -q -k the_ask     # 6 passed
bin/build
git diff --stat                                      # src/nare/tools.py and tests/test_tools.py only
```

- [ ] **Step 6: Commit**

```bash
git add src/nare/tools.py tests/test_tools.py
git commit -m "feat(tools): ask says the session stops until the caller answers (#45)"
```

E0 finds this commit by that subject. Keep it exactly.

---

### Task D3: Hand-off

- [ ] **Step 1: Check the branch**

```bash
cd /home/smadinya/vividynamics/nare-45
git status --short                                   # expect no output
git log --oneline 45-ask-across-models..HEAD         # five: B, A, the plan, 73d867d, 54f175f
git show --stat --format='%h %s' HEAD~1 HEAD         # each: src/nare/tools.py and tests/test_tools.py
grep -nP '\x{2014}' src/nare/tools.py tests/test_tools.py || echo "no em dashes"
```

- [ ] **Step 2: If review asks for a change, fold it into the candidate's own commit**

The branch is unpublished, so rewriting its last two commits is allowed (conventions, ground rule 4). Interactive rebase is not available here; this is the same thing done by hand.

A change to B only:

```bash
# edit, then:
bin/build
git commit -a --amend --no-edit
```

A change to A (B is replayed on top):

```bash
A=$(git rev-parse HEAD~1); B=$(git rev-parse HEAD)
git switch --detach "$A"
# edit, then:
bin/build
git commit -a --amend --no-edit
git cherry-pick "$B"                 # resolve any conflict in favour of B's last sentence
bin/build
git branch -f 45-ask-description HEAD
git switch 45-ask-description
```

Then Step 1 again. Never push.

---

# Part E: the measurement (agents, after code review)

These spend money on paid models through the LiteLLM proxy; the user has approved the spend. Run the steps in order, E0 to E5. One bench at a time, from any checkout. Every block starts by sourcing the shell file that E0 writes:

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
```

Shell state does not carry from one command to the next, so a block that skips that line has no keys, no `DOCKER_CONFIG` and no helpers.

**Cost and time,** from the first measurement (a five-model smoke matrix took 40 minutes, `05:56Z` to `06:37Z`; a two-case run of one model took 1.5 to 4 minutes):

| Step | Reps, about | Wall clock, about |
| --- | --- | --- |
| E1 | 14 to 21 of `gpt-5-nano` on read-only-question | 10 to 20 min |
| E2 | 30 per candidate, plus 7-rep confirmations | 15 to 30 min per candidate |
| E4 | 90 for the gate, plus confirmations; 11 to 18 more of `gpt-5-nano`; up to 28 more per other guard regression | 45 to 75 min |

The judge, `gpt-6.1-sol` at 2 and 10 dollars per million tokens, is most of the spend. Streamed turns report a cost of 0.0 through this proxy, so read the spend from the proxy, not from nare.

### Where everything lands, and how runs are told apart

`MAIN` is `/home/smadinya/vividynamics/nare-45`, `SIDE` is `/home/smadinya/vividynamics/nare-45-side`, and `R` is `$MAIN/benchmarks/results`. A results file is named by its run's stamp, the UTC time the run started, and nothing in it says which commit or worktree it ran on. So the `bench` helper appends one line per results file to the run log `$R/45-2-runs.tsv`: the step's tag, the commit, and the file's absolute path. Every count, score and label below reads files through that log by tag.

| Step | Runs in | At commit | Tag | Results files |
| --- | --- | --- | --- | --- |
| first measurement (spec 6.1) | `MAIN` | `54f175f` | `s6` | `$MAIN/benchmarks/results/2026-10-27T05-56-45Z.<model>.jsonl` (already there) |
| E1 | `SIDE` | `$PR1` | `pr1` | `$SIDE/benchmarks/results/<stamp>.jsonl`, one per run, with `<stamp>/read-only-question-<rep>/` |
| E2, candidate A | `SIDE` | `$A` | `loop-A` | `$SIDE/benchmarks/results/<stamp>.<model>.jsonl`, five under one stamp, with `<stamp>.<model>/<case>-<rep>/` |
| E2, candidate B | `MAIN` | `$B` | `loop-B` | `$MAIN/benchmarks/results/<stamp>.<model>.jsonl`, five under one stamp |
| E4, the gate | `MAIN` | `$GATE` | `gate-A` or `gate-B` | `$MAIN/benchmarks/results/<stamp>.<model>.jsonl`, five under one stamp |
| E4, extra reps on the pick | `SIDE` | `$GATE` | `gate-A-extra` or `gate-B-extra` | `$SIDE/benchmarks/results/<stamp>.jsonl`, one per run |
| E4, PR 1's side of another guard | `SIDE` | `$PR1` | `pr1` | as E1 |

`<model>` is the model's baseline name, such as `gpt-6-1-sol`. The other files, all in `$R`, all gitignored:

| File | Holds |
| --- | --- |
| `45-2-env.sh` | the shell file (E0) |
| `45-2-commits.env` | `PR1`, `A`, `B`, then `PICK` (E3) and `GATE` (E4); a later line wins |
| `45-2-runs.tsv` | the run log |
| `45-2-<tag>-<UTC time>.txt` | each bench command's output, as printed |
| `45-2-<tag>-table.md` | a matrix run's table, from `bin/bench compare --matrix` |
| `45-2-<tag>-score.tsv` | a run's ambiguous cells and whether each is at target |
| `45-2-notes.md` | what each step found, appended as it goes; E5 writes the README from it |
| `45-2-docker/` | `DOCKER_CONFIG` |

Keep both worktrees until PR 2 merges: the README cites stamps whose evidence is only on this disk.

### E0: The shell, the commits, and the side worktree

Nothing here spends money.

- [ ] **Step 1: Write the shell file**

```bash
mkdir -p /home/smadinya/vividynamics/nare-45/benchmarks/results
cat > /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh <<'SH'
# Part E's shell: sourced at the top of every block. Prints no key and no URL.
MAIN=/home/smadinya/vividynamics/nare-45
SIDE=/home/smadinya/vividynamics/nare-45-side
R="$MAIN/benchmarks/results"   # gitignored: the log, transcripts, tables, scores
LOG="$R/45-2-runs.tsv"         # one line per results file: tag, commit, path
[ -f "$R/45-2-commits.env" ] && . "$R/45-2-commits.env"   # PR1, A, B (step E0)
export DOCKER_CONFIG="$R/45-2-docker"   # ~/.docker uses desktop.exe, which fails
mkdir -p "$DOCKER_CONFIG" && echo '{}' > "$DOCKER_CONFIG/config.json"
set -a; . "$MAIN/.env"; set +a
export OPENAI_API_KEY="$ANTHROPIC_API_KEY"   # the same key on this proxy
export NARE_STREAM=1                         # as the baselines were run
unset NARE_BENCH_MAX_TOKENS NARE_BENCH_JUDGE_MODEL   # no baseline sets a cap
ROOT="${NARE_BASE_URL%/}"; ROOT="${ROOT%/v1}"
JUDGE=gpt-6.1-sol

# no_bench: fails, naming it, when a bench runs from any checkout.
no_bench() {
  if pgrep -af "[b]enchmarks.runner"; then
    echo "a bench is running (above): wait for it to exit" >&2; return 1
  fi
  if [ -n "$(docker ps -q --filter name=nare-bench)" ]; then
    echo "a nare-bench container is running: wait for it" >&2; return 1
  fi
}

# bench W TAG ARGS...: bin/bench ARGS in worktree W, its output kept in a
# transcript, and every results file it wrote logged under TAG with W's commit.
# Returns bench's exit code (2 if another bench is running).
bench() {
  local w=$1 tag=$2; shift 2
  no_bench || return 2
  local commit out code
  commit=$(git -C "$w" rev-parse --short HEAD)
  out="$R/45-2-$tag-$(date -u +%Y%m%dT%H%M%SZ).txt"
  # The base URL is shown as $ROOT, never spelled out.
  echo "== $tag in $w at $commit: bin/bench ${*//"$ROOT"/\$ROOT}" | tee "$out"
  (cd "$w" && bin/bench "$@") 2>&1 | tee -a "$out"
  code=${PIPESTATUS[0]}
  sed -n 's/^results: //p' "$out" | while read -r f; do
    printf '%s\t%s\t%s\n' "$tag" "$commit" "$f" >> "$LOG"
  done
  echo "exit=$code transcript=$out"
  return "$code"
}

# side_at COMMIT: the side worktree, clean and detached at COMMIT, or a failure.
side_at() {
  if [ -n "$(git -C "$SIDE" status --short)" ]; then
    git -C "$SIDE" status --short; echo "the side worktree is not clean" >&2; return 1
  fi
  git -C "$SIDE" switch --quiet --detach "$1" || return 1
  echo "side worktree at $(git -C "$SIDE" rev-parse --short HEAD)"
}

# files TAG...: the results files logged under any of the TAGs.
files() {
  [ -f "$LOG" ] || return 0
  awk -F'\t' -v tags=" $* " 'index(tags, " " $1 " ") {print $3}' "$LOG" | sort -u
}

# judged MODEL CASE TAG...: MODEL's reps of CASE pooled over the TAGs' files.
# Judged means pass or fail; an error counts in neither, as in the runner.
judged() {
  local model=$1 cid=$2; shift 2
  local f; f=$(files "$@")
  if [ -z "$f" ]; then echo "0 judged, 0 failed, 0 errored"; return; fi
  # shellcheck disable=SC2086
  jq -rs --arg m "$model" --arg c "$cid" '
    map(select(.model == $m and .case == $c))
    | "\(map(select(.outcome != "error")) | length) judged, \(map(select(.outcome == "fail")) | length) failed, \(map(select(.outcome == "error")) | length) errored"' $f
}

# score TAG: spec 6.1 step 3. A cell is one model on one ambiguous case, at
# target when its pass rate over the reps the run kept is at least 2/3 and the
# case is not inconclusive (errors more than half its reps).
score() {
  local f; f=$(files "$1")
  if [ -z "$f" ]; then echo "no results file is logged under $1" >&2; return 1; fi
  # shellcheck disable=SC2086
  jq -rs '
    map(select(.case == "ambiguous-request" or .case == "ambiguous-after-reading"))
    | group_by([.model, .case])[]
    | { model: .[0].model, case: .[0].case,
        p: (map(select(.outcome == "pass")) | length),
        f: (map(select(.outcome == "fail")) | length),
        e: (map(select(.outcome == "error")) | length) }
    | .at = (.e * 2 <= (.p + .f + .e) and (.p + .f) > 0 and .p * 3 >= (.p + .f) * 2)
    | [.model, .case, "\(.p)/\(.p + .f)", "\(.e) errored",
       (if .at then "at target" else "below" end)]
    | @tsv' $f > "$R/45-2-$1-score.tsv"
  cat "$R/45-2-$1-score.tsv"
  echo "score: $(grep -c 'at target$' "$R/45-2-$1-score.tsv") of $(wc -l < "$R/45-2-$1-score.tsv") cells"
}

# reps TAG CASE OUTCOME: model, rep, status and evidence directory of each rep
# of CASE with that OUTCOME in the TAG's files.
reps() {
  files "$1" | while read -r f; do
    jq -r --arg c "$2" --arg o "$3" --arg d "$(dirname "$f")" \
      'select(.case == $c and .outcome == $o)
       | [.model, .rep, .status, "\($d)/\(.artifacts)"] | @tsv' "$f"
  done
}

# calls DIR: a rep's tool calls, in order.
calls() {
  jq -r '[.messages[] | select(.role == "assistant") | .content[]?
          | select(.type == "tool_use") | .name] | join(" ")' "$1/session.json"
}

# saw_body DIR: true when a tool result showed parse_duration's return line.
saw_body() {
  jq -r '[.messages[] | .content[]? | select(.type == "tool_result")
          | .content | tostring | contains("return timedelta(")] | any' "$1/session.json"
}

# to14 W TAG MODEL CASE COUNT_TAGS...: single runs of MODEL on CASE in W,
# logged under TAG, until MODEL's judged reps of CASE pooled over COUNT_TAGS
# reach 14. A run keeps 3 reps, or 7 when it re-confirms a drop. At most six
# runs; a setup error (exit 2) stops it.
to14() {
  local w=$1 tag=$2 model=$3 cid=$4; shift 4
  local provider=openai url="$ROOT/v1" n attempt
  case $model in claude-*) provider=anthropic url="$ROOT" ;; esac
  for attempt in 1 2 3 4 5 6; do
    n=$(judged "$model" "$cid" "$@" | cut -d' ' -f1)
    [ "$n" -ge 14 ] && break
    echo "$model on $cid: $n judged over $*; run $attempt in $w"
    bench "$w" "$tag" run --tier smoke --case "$cid" --model "$model" \
      --provider "$provider" --base-url "$url" --judge-model "$JUDGE"
    [ $? -le 1 ] || return 2
  done
  judged "$model" "$cid" "$@"
}

# guard_facts MODEL CASE TAG...: what labelling MODEL's failing reps of a
# guard CASE needs, over the TAGs' files.
guard_facts() {
  local model=$1 cid=$2 t; shift 2
  for t in "$@"; do reps "$t" "$cid" fail; done | awk -F'\t' -v m="$model" '$1 == m' |
  while IFS=$'\t' read -r m rep st d; do
    printf '\n## %s (%s): calls [%s], ask called: %s, saw the body: %s\n' \
      "$d" "$st" "$(calls "$d")" "$(calls "$d" | grep -qw ask && echo yes || echo no)" \
      "$(saw_body "$d")"
    tail -n 6 "$d/closing.txt"; echo
  done
}

# amb_facts TAG: what labelling the TAG's failing ambiguous reps needs.
amb_facts() {
  local cid
  for cid in ambiguous-request ambiguous-after-reading; do
    reps "$1" "$cid" fail | while IFS=$'\t' read -r m rep st d; do
      printf '\n## %s %s rep %s (%s): calls [%s], fixture %s\n  %s\n' \
        "$m" "$cid" "$rep" "$st" "$(calls "$d")" \
        "$([ -s "$d/diff.patch" ] && echo changed || echo unchanged)" "$d"
      tail -n 6 "$d/closing.txt"; echo
    done
  done
}

# questions TAG: the question of every passing ambiguous-request rep.
questions() {
  reps "$1" ambiguous-request pass | while IFS=$'\t' read -r m rep st d; do
    printf '%s rep %s: %s\n' "$m" "$rep" "$(jq -r '.questions | join(" | ")' "$d/session.json")"
  done
}
SH
bash -n /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh && echo "shell file ok"
```

What the helpers do, in the runner's terms:

- `bench` runs `bin/bench` in a worktree and logs each `results:` line the runner prints (`run_model` prints one per results file; a confirmation rewrites that file without a second line).
- `side_at` moves the side worktree to a commit, and fails if the worktree has changes, so a block written `side_at X && ...` never measures the wrong tree.
- `to14` is spec 6.1 step 1's command, repeated: `bin/bench run --tier smoke --case CASE --model MODEL --provider openai --base-url "$ROOT/v1" --judge-model gpt-6.1-sol` (an `anthropic` model gets `--base-url "$ROOT"`, section 3.2), until the pool reaches 14.
- `judged`, `score` and `reps` read the results lines (`case`, `rep`, `outcome`, `status`, `model`, `artifacts`) of the files logged under the tags. `artifacts` is relative to the results file's directory, which `reps` prepends.
- `calls`, `saw_body`, `guard_facts`, `amb_facts` and `questions` read a rep's `session.json` and `closing.txt`. A session's `questions` holds what a blocking `ask` asked.

- [ ] **Step 2: Check the branch, and record the commits**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
cd "$MAIN"
git status --short                                        # expect no output
git branch --show-current                                 # 45-ask-description
git fetch origin
test "$(git rev-parse 45-ask-across-models)" = "$(git rev-parse origin/45-ask-across-models)" && echo "PR 1 matches origin"
git merge-base --is-ancestor 45-ask-across-models HEAD && echo "PR 1 is under this branch"
git diff --stat 45-ask-across-models HEAD -- benchmarks  # expect no output: PR 1's cases and baselines
git diff --stat 45-ask-across-models HEAD -- src         # src/nare/tools.py only
A=$(git log --format=%h -1 -F --grep='feat(tools): ask says a value the request leaves open is no detail' HEAD)
B=$(git log --format=%h -1 -F --grep='feat(tools): ask says the session stops until the caller answers' HEAD)
PR1=$(git rev-parse --short 45-ask-across-models)
test "$(git rev-parse HEAD)" = "$(git rev-parse "$B")" && echo "B is the tip"
test "$(git rev-parse "$B^")" = "$(git rev-parse "$A")" && echo "A is B's parent"
printf 'PR1=%s\nA=%s\nB=%s\n' "$PR1" "$A" "$B" | tee "$R/45-2-commits.env"
```

Expected: every `echo` prints, and the two `diff --stat` lines are as commented; if not, stop and report. If local `45-ask-across-models` and `origin/45-ask-across-models` differ, do not move either. If PR 1 has moved past this branch (a review fix, or the two Claude baselines after 2026-11-01), the ancestor check fails: stop and report. Rebasing this branch would change A's and B's SHAs after review, which is the user's call.

- [ ] **Step 3: Log the first measurement under `s6`**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
for f in "$R"/2026-10-27T05-56-45Z.*.jsonl; do printf 's6\t54f175f\t%s\n' "$f"; done >> "$LOG"
judged gpt-5-nano read-only-question s6
score s6 | tail -1
```

Expected: `7 judged, 2 failed, 0 errored` (spec 6.1's 5 of 7) and `score: 2 of 10 cells` (spec 6.1: "The section 6 text scored 2"). This proves the helpers on known numbers. A line logged twice is harmless: `files` removes duplicates.

- [ ] **Step 4: Create the side worktree**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
test -e "$SIDE" || git -C "$MAIN" worktree add --detach "$SIDE" "$PR1"
side_at "$PR1"                                            # side worktree at $PR1
(cd "$SIDE" && uv run python -c 'import nare, pathlib; print(pathlib.Path(nare.__file__).parent)')
```

Expected: the last line is `/home/smadinya/vividynamics/nare-45-side/src/nare`, which also creates `$SIDE/.venv`. Never commit in the side worktree, never run `bless` there, and never copy `.env` into it: the shell file reads `$MAIN/.env`, and the image build ignores `.env` and `.venv` (`benchmarks/Dockerfile.dockerignore`).

- [ ] **Step 5: Docker, idleness and the endpoint (free)**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
[ -n "$ROOT" ] && echo "base URL set"
docker info --format '{{.ServerVersion}}' >/dev/null && echo "docker up"
no_bench && echo "no bench running"
ls "$MAIN"/benchmarks/baselines/
(cd "$MAIN" && bin/bench models --provider openai) | grep -E '^(model|gpt-5-nano|gpt-6-luna|gpt-6\.1-sol|gemini-3\.1-flash-lite|spark/glm-5\.3-flash) '
```

Expected: the three `echo` lines; five baselines (`gemini-3-1-flash-lite`, `gpt-5-nano`, `gpt-6-1-sol`, `gpt-6-luna`, `spark-glm-5-3-flash`, each `.smoke.toml`); the five models listed, each with `smoke` in its baselines column. If Docker is down, start Docker Desktop on Windows. If a model is not listed, stop and report.

If the two Claude baselines exist (seven files), they join every step without any change here: the matrix runs every baseline, `to14` and the bless loop take each model's provider, and a score is out of 14 cells.

- [ ] **Step 6: Start the notes**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
printf '# #45 second pass: notes (not committed)\n\ncommits: PR 1 %s, A %s, B %s\n' "$PR1" "$A" "$B" > "$R/45-2-notes.md"
```

### E1: Is nano's read-only-question drop noise? (PR 1's code)

Spec 6.1 step 1, before E2. It runs only in the side worktree, at `$PR1`, whose `src/nare` is `main`'s.

- [ ] **Step 1: 14 judged reps on PR 1's code (background)**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
side_at "$PR1" && to14 "$SIDE" pr1 gpt-5-nano read-only-question pr1
```

Each run is compared with PR 1's `gpt-5-nano` baseline (read-only-question 3/3). A run whose 3 reps all pass adds 3. A run with a failure re-runs the case at 7 and adds those 7, and exits 1 with `REGRESSION: read-only-question 3/3 -> n/7`: here that is the measurement, not an error. An `error` rep (an HTTP 403 from Cloudflare, for one) adds nothing. Expected end: `N judged, F failed, E errored` with N at least 14, after 2 to 5 runs. If six runs leave N under 14, stop and report the transcripts.

**Do not run `bless`.** These runs are on PR 1's code and must never become a baseline.

- [ ] **Step 2: Label the failing reps, and the first measurement's two**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
files pr1
judged gpt-5-nano read-only-question pr1
guard_facts gpt-5-nano read-only-question pr1
guard_facts gpt-5-nano read-only-question s6
```

Label each failing rep by hand, first match wins (spec 6.1 step 1):

| Label | Means | From |
| --- | --- | --- |
| asked | called `ask` | `ask called: yes` |
| stopped short | ended `done` never having seen `parse_duration`'s body, whatever its closing says | `saw the body: false` |
| asked in prose | had seen it, and ended on a question to the caller instead of the answer | read the closing |
| other | anything else, in one sentence | |

`saw the body` is true when some tool result held `return timedelta(`, the function's last line: a `read` or `cat` of `duration.py` shows it, and a search that prints only the `def` or docstring line does not. Read `session.json` when the facts and the closing disagree. Spec 6.1 says both `s6` failures (reps 1 and 3) stopped short; the labels confirm or correct it.

- [ ] **Step 3: Read the first measurement's 5 of 7**

With PR 1's pool `N judged, F failed` from Step 2 and section 6's `7 judged, 2 failed`: the drop was the description's if an `s6` failure is labelled `asked` or `asked in prose`, or if `2/7 - F/N` is at least 0.2 (decision 8). Otherwise it is noise in the model, which #45 records. If PR 1's code also stops short, note that the fix is a larger `max_turns` on read-only-question in its own change with its own re-bless (spec 6.1), not here.

- [ ] **Step 4: Record**

Append to `$R/45-2-notes.md` under `## E1`: each `pr1` stamp (the file names `files pr1` printed), the pool, a `| run | rep | label | note |` table for every failing `pr1` and `s6` rep, and the reading of the 5 of 7 with its arithmetic.

### E2: The cheap loop, A then B

Spec 6.1 step 2, each candidate from its own commit, against the section 5 baselines. `--base-url "$ROOT"`: the matrix adds `/v1` itself for an `openai` baseline (`route` in `config.py`), and takes each model's provider and judge from its baseline, so `--model`, `--provider` and `--judge-model` are refused. Never `bless` after a cheap loop.

- [ ] **Step 1: Put the side worktree at A**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
side_at "$A" && (cd "$SIDE" && uv run pytest tests/test_tools.py -q -k the_ask)   # 5 passed
```

- [ ] **Step 2: A's loop (background)**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
bench "$SIDE" loop-A run --matrix --base-url "$ROOT" \
  --case ambiguous-request --case ambiguous-after-reading
```

Expected: the preflight passes; five `== <model> (openai) ==` sections, each with a `results:` line and its table; then the case-by-model table. Exit 1 is expected when an ambiguous case drops below its baseline at 7 reps (`regression`). Exit 2 is a setup error: stop and report.

- [ ] **Step 3: A's table and score**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
(cd "$SIDE" && bin/bench compare --matrix) | tee "$R/45-2-loop-A-table.md"
score loop-A
```

`compare --matrix` prints the newest matrix run in the side worktree, which is A's loop: E1's runs are single runs, which it skips. The table has two rows and five model columns.

- [ ] **Step 4: Check that `nare-45` is at B**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
cd "$MAIN" && [ -z "$(git status --short)" ] && test "$(git rev-parse --short HEAD)" = "$B" && echo "clean, at B"
uv run pytest tests/test_tools.py -q -k the_ask                    # 6 passed
```

- [ ] **Step 5: B's loop (background)**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
bench "$MAIN" loop-B run --matrix --base-url "$ROOT" \
  --case ambiguous-request --case ambiguous-after-reading
```

Expected as in Step 2.

- [ ] **Step 6: B's table and score**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
(cd "$MAIN" && bin/bench compare --matrix) | tee "$R/45-2-loop-B-table.md"
score loop-B
```

- [ ] **Step 7: Record**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
for t in loop-A loop-B; do
  { printf '\n## E2, %s\n\n' "$t"
    awk -F'\t' -v t="$t" '$1 == t {print "- " $2 " " $3}' "$LOG"
    echo; cat "$R/45-2-$t-table.md"; echo; cat "$R/45-2-$t-score.tsv"
  } >> "$R/45-2-notes.md"
done
```

Each log line gives the commit and the results file, whose name begins with the loop's stamp.

### E3: The pick

Spec 6.1 step 3. Nothing here spends money.

- [ ] **Step 1: Score both, and look for a confirmed regression on ambiguous-after-reading**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
for c in A B; do
  echo "$c: $(grep -c 'at target$' "$R/45-2-loop-$c-score.tsv") cells at target"
  grep '^| ambiguous-after-reading ' "$R/45-2-loop-$c-table.md"
done
```

A cell is at target when its pass rate over the reps the run kept is at least 2/3 (`score` also counts an inconclusive cell as below). For reference, section 6's text scored 2 and the section 5 baselines 3.

The rule, in order:

1. A candidate whose ambiguous-after-reading row has a cell marked `regression` is out.
2. Both out: nothing is picked (decision 10). Skip E4 and go to E5, Step 6.
3. One out: the other is picked.
4. Neither out: B if its score is higher than A's; otherwise A, the smaller change.

- [ ] **Step 2: Record the pick**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
echo "PICK=A" >> "$R/45-2-commits.env"     # or PICK=B
```

Append to `$R/45-2-notes.md` under `## E3`: both scores, both ambiguous-after-reading rows, and which rule line picked.

### E4: The gate on the pick

Spec 6.1 step 4, and "Reading a guard drop" and "When the wording fails".

- [ ] **Step 1: Put the pick at the tip**

If `PICK=B`, B is already the tip: go to Step 2.

If `PICK=A`, revert B as a new commit (decision 4):

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
cd "$MAIN"
git revert --no-commit "$B"
git commit -m "revert(tools): back to candidate A, which the cheap loop picked (#45)"
git diff --stat "$A" HEAD -- src tests                   # expect no output: the tree is A's
```

- [ ] **Step 2: Build at the gate's commit, and record it**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
cd "$MAIN" || exit 1
[ -z "$(git status --short)" ] || { echo "nare-45 is not clean"; exit 1; }
echo "GATE=$(git rev-parse --short HEAD)" | tee -a "$R/45-2-commits.env"
bin/build
```

`bin/build` runs the widened leak test. If it fails, the wording fails (spec 6.1's third condition): go to E5, Step 6.

- [ ] **Step 3: The gate run (background)**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
bench "$MAIN" "gate-$PICK" run --matrix --base-url "$ROOT"
```

Then:

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
(cd "$MAIN" && bin/bench compare --matrix) | tee "$R/45-2-gate-$PICK-table.md"
score "gate-$PICK"
```

Expected: six case rows and five model columns. Exit 1 is expected and is not the verdict: sol's and spark's multi-file-rename were inconclusive in every run so far (Cloudflare 403s, `docs/evidence/45/README.md`), and an ambiguous case can drop. Read the table.

This run is now the newest run in `nare-45`, and must stay so until E5's bless (decision 3): every run below goes to the side worktree.

- [ ] **Step 4: Read the table**

1. **ambiguous-after-reading:** a cell marked `regression` fails the wording (spec 6.1's second condition). Skip Steps 5 to 7 and go to Step 8.
2. **The guards** (edit-docstring, multi-file-rename, read-only-question): list every cell marked `regression`, as (model, case).
3. **An inconclusive guard cell on a model whose baseline has that case** (a number, not `-`, in the README's "Baseline matrix"): re-run it once on the gate's code (decision 11), then read its `REGRESSION`, `no regressions` or `INCONCLUSIVE` line:

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
side_at "$GATE" && bench "$SIDE" "gate-$PICK-extra" run --tier smoke --case CASE \
  --model MODEL --provider openai --base-url "$ROOT/v1" --judge-model "$JUDGE"
```

(A Claude model takes `--provider anthropic --base-url "$ROOT"`.) A `REGRESSION` adds (model, case) to item 2's list. `INCONCLUSIVE` again leaves the cell unchecked, to be recorded.

- [ ] **Step 5: `gpt-5-nano` to 14 judged reps of read-only-question on the pick (background)**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
side_at "$GATE" && to14 "$SIDE" "gate-$PICK-extra" gpt-5-nano read-only-question "gate-$PICK" "gate-$PICK-extra"
```

The pool starts from the gate run's own reps of that case (3, or 7 after a confirmation), as spec 6.1 step 4 says. The side worktree is at `$GATE`, the same tree as the gate run.

- [ ] **Step 6: Both sides to 14 for every other guard regression (background)**

For each (model `M`, case `C`) in Step 4's list other than `gpt-5-nano` on read-only-question (E1 and Step 5 already cover that one):

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
side_at "$GATE" && to14 "$SIDE" "gate-$PICK-extra" M C "gate-$PICK" "gate-$PICK-extra"
side_at "$PR1" && to14 "$SIDE" pr1 M C pr1
```

Spec 6.1 step 1 names PR 1's side; decision 12 adds the pick's.

- [ ] **Step 7: Label, and read each guard drop**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
judged gpt-5-nano read-only-question "gate-$PICK" "gate-$PICK-extra"
judged gpt-5-nano read-only-question pr1
guard_facts gpt-5-nano read-only-question "gate-$PICK" "gate-$PICK-extra"
```

and the same three lines with `M C` for each pair from Step 6. Label each failing rep on the pick's side as in E1, Step 2. On edit-docstring and multi-file-rename the labels are `asked`, `asked in prose` and `other`: `stopped short` is read-only-question's.

The reading (spec 6.1, decisions 8 and 9): a drop is the description's if a failing rep on the pick's side is labelled `asked` or `asked in prose`, or if (pick's failed / pick's judged) minus (PR 1's failed / PR 1's judged) is at least 0.2. Otherwise it is noise in the model: recorded, and not counted by the gate. For `gpt-5-nano` on read-only-question, a pool pass rate below 3/3 that the reading calls the description's counts as a confirmed regression, whether or not the gate's table marked it (decision 9).

- [ ] **Step 8: Decide**

The wording **does not ship** if any of these holds (spec 6.1, "When the wording fails"):

1. A guard case has a confirmed regression on some model that Step 7's reading does not call noise.
2. ambiguous-after-reading has a confirmed regression on some model.
3. `bin/build` failed at `$GATE` (the widened leak test).

**If `PICK=B` and it fails condition 1 with a rep labelled `asked`,** run E4 on A before giving up (spec 6.1: "A leaves the cost of asking as it was"):

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
echo "PICK=A" >> "$R/45-2-commits.env"
```

then Steps 1 to 8 again, which revert B, gate A under the tags `gate-A` and `gate-A-extra`, and make A's gate run the newest run in `nare-45`. E1's `pr1` pools are reused.

It **misses** if, in `$R/45-2-gate-$PICK-score.tsv`, `gpt-6.1-sol` or `gpt-6-luna` is `below` on ambiguous-request. A miss that does not fail ships only if the pick's score in E3 was at least 2 (the section 6 text's); otherwise it does not ship.

Otherwise it **ships**. Append to `$R/45-2-notes.md` under `## E4`: the gate's stamp and `$GATE`, the table, every regression and inconclusive cell, each pool with its arithmetic, the labels, each reading, the miss, and the decision with the condition or rule that made it.

### E5: Labels, the evidence, and the re-bless (or the failure record)

- [ ] **Step 1: Re-bless on the gate run (only if E4 says it ships)**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
cd "$MAIN" || exit 1
[ -z "$(git status --short)" ] || { echo "nare-45 is not clean"; exit 1; }
[ "$(git rev-parse --short HEAD)" = "$GATE" ] || { echo "HEAD is not the gate's commit"; exit 1; }
newest=$(basename "$(ls benchmarks/results/*.jsonl | sort | tail -1)" | cut -d. -f1)
gate=$(basename "$(files "gate-$PICK" | head -1)" | cut -d. -f1)
[ "$newest" = "$gate" ] || { echo "the newest run is $newest, not the gate's $gate"; exit 1; }
echo "clean, at $GATE, and the newest run is the gate's: $gate"
bin/bench compare --matrix --baselines > "$R/45-2-before-baselines.md"
for f in benchmarks/baselines/*.smoke.toml; do
  M=$(sed -n 's/^model *= *"\(.*\)"/\1/p' "$f")
  P=$(sed -n 's/^provider *= *"\(.*\)"/\1/p' "$f")
  bin/bench bless --tier smoke --model "$M" --provider "$P" --judge-model "$JUDGE" || break
done
grep -h '^commit\|^provider\|^judge_model' benchmarks/baselines/*.smoke.toml | sort | uniq -c
bin/bench compare --matrix --baselines | tee "$R/45-2-after-baselines.md"
```

The block stops before blessing anything unless `nare-45` is clean, at `$GATE`, and its newest run is the gate's. `bless` reads that newest run and takes each model's own `<stamp>.<model>.jsonl` from it; its provider and judge come from the results, and the flags only check them (exit 2 on a mismatch). Expected: the `clean, at ...` line; one `blessed benchmarks/baselines/<model>.smoke.toml from <gate stamp>.<model>.jsonl (provider openai, judge gpt-6.1-sol)` per model; and the `uniq -c` shows one `commit` line, `$GATE`, one `judge_model`, and the providers. `bless` writes `[meta] commit` from `HEAD`, so nothing is committed between the gate run and this step. An inconclusive case keeps its old number, and a case absent from the old baseline (spark's and sol's multi-file-rename) stays absent.

If the block stops, or a `bless` exits non-zero, report it and commit nothing: never bless from another run. Otherwise:

```bash
cd /home/smadinya/vividynamics/nare-45
git add benchmarks/baselines
git commit -m "chore(bench): re-bless the smoke matrix on the second ask wording (#45)"
```

- [ ] **Step 2: Label every failing ambiguous rep**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
for t in loop-A loop-B gate-B gate-A; do
  [ -n "$(files "$t")" ] && { printf '\n# %s\n' "$t"; amb_facts "$t"; }
done > "$R/45-2-amb-facts.md"
```

For each rep listed, read its `closing.txt`, `diff.patch` and `session.json`, and label it by hand with spec section 5's labels, as the README's existing tables do:

| Label | Means |
| --- | --- |
| asked in prose | ended `done` with no change, and the closing text asks the caller something |
| guessed | ended `done` with a change to the fixture (`fixture changed`) |
| asked blind | called `ask` before any `read` or `bash` call (its calls start with `ask`) |
| other | anything else, in one sentence |

An `error` rep is not a failure and is not listed. Each note starts with the rep's tool calls in backticks, as the README's do.

- [ ] **Step 3: The question of every passing ambiguous-request rep**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-2-env.sh
for t in loop-A loop-B gate-B gate-A; do
  [ -n "$(files "$t")" ] && { printf '\n# %s\n' "$t"; questions "$t"; }
done > "$R/45-2-questions.md"
cat "$R/45-2-questions.md"
```

For each, note whether it fills a frame from the old example ("from 30 to what", "60, 120, or another value", "Unanswered, I will use 60"): spec 6.1 asks for these so a reader can see whether a frame was filled in.

- [ ] **Step 4: Write the README section, and commit**

In `docs/evidence/45/README.md`, insert this section directly before `## Not yet in the matrix`, filled from `$R/45-2-notes.md`, the tables, `45-2-amb-facts.md` labels and `45-2-questions.md`. Tables go in as printed. Results files are named by file name and worktree (`side worktree` for `nare-45-side`), with the commit from the run log. No em dashes.

```markdown
## After the second wording

**Date:** <the gate run's UTC date>
**Commits:** PR 1 `<PR1>`, whose `src/nare` is `main`'s; candidate A `<A>`; candidate B `<B>`; gate `<GATE>` (<B itself, or the revert of B, whose tree is A's>).
**Judge:** `gpt-6.1-sol`, for every model.
**Runs:** smoke tier, 3 reps per case and 7 on a confirmation, `NARE_STREAM=1`.

The first wording, commit `54f175f`, was measured as matrix run `2026-10-27T05-56-45Z`; why it failed is in spec section 6.1. This section measures the two wordings that replace it, by section 6.1's protocol.

### Is the read-only-question drop noise?

<E1: each `pr1` run (stamp, side worktree, `<PR1>`), the pool, the labels, and the reading of the first measurement's 5 of 7. E4: the gate run's reps and each `gate-<PICK>-extra` run, the pool, the labels, the reading. The same for any other guard pair.>

### The cheap loop

#### Candidate A, `<A>`, matrix run `<stamp>` (side worktree)

<45-2-loop-A-table.md, as printed>

#### Candidate B, `<B>`, matrix run `<stamp>`

<45-2-loop-B-table.md, as printed>

| model | case | A | B |
| --- | --- | --- | --- |
<one row per cell: the pass rate, and "at target" or "below">

Score, cells at target: A <a> of 10, B <b> of 10 (section 6's text 2, the section 5 baselines 3). <The pick, and the rule line that picked it.>

### The gate

The matrix before is "Baseline matrix" above. After, on `<GATE>`, matrix run `<stamp>`, from `bin/bench compare --matrix`, as printed:

<45-2-gate-<PICK>-table.md, as printed>

<Each regression and inconclusive cell, and its reading. Whether sol and luna reach 2 of 3 on ambiguous-request. The decision: shipped and re-blessed at `<GATE>`, or did not ship, naming the condition.>

### Failure labels

| run | model | case | rep | label | note |
| --- | --- | --- | --- | --- | --- |

| run | model | asked in prose | guessed | asked blind | other | total |
| --- | --- | --- | --- | --- | --- | --- |

### Questions that passed ambiguous-request

| run | model | rep | question | frame filled in |
| --- | --- | --- | --- | --- |

### What it shows

<Each model: both ambiguous cases at 2 of 3 in the gate run, or its labels. Each miss, with the next lever (a rule in the default system prompt, spec section 9). Section 7: Step 5's result.>
```

```bash
cd /home/smadinya/vividynamics/nare-45
grep -nP '\x{2014}' docs/evidence/45/README.md || echo "no em dashes"
git add docs/evidence/45/README.md
git commit -m "docs: #45 evidence after the second ask wording (#45)"
```

- [ ] **Step 5: Section 7, and the #45 comment**

Count the gate run's labels per model (Step 2's table, `run` = the gate). If "asked in prose" is the most common label among some matrix model's failing ambiguous reps, section 7 (the loop nudge) is next, in its own plan and PR 3. Otherwise section 7 is not built, because the description was enough for that failure. Put the result and its counts in the README's "What it shows" (amend Step 4's commit with `git commit --amend --no-edit` if it is already made; the branch is unpublished).

Then write the comment for #45 to `$R/45-2-issue-comment.md`: the gate table, the pick and its score, the noise readings, each miss with its labels and the next lever, and section 7's result, pointing to `docs/evidence/45/README.md` for the rest. Posting it is the user's call; this plan pushes and posts nothing.

- [ ] **Step 6: If the wording does not ship**

When E3 picked nothing, or `bin/build` failed in E4, Step 2, or E4, Step 8 says it does not ship:

- Do not bless. `git -C /home/smadinya/vividynamics/nare-45 diff --stat HEAD -- benchmarks/baselines` is empty, and no baseline commit is made.
- Still do Steps 2, 3, 4 and 5. The README section has the same title and says the wording did not ship and which condition or rule decided it, with every table, pool, reading and label measured up to that point. Leave out the parts of the skeleton for steps that did not run, and say which.
- Leave the code at the tip as it is (B, or A after the revert). Do not revert further. What comes next, a new wording or the system-prompt lever (spec section 9), is the user's call.

- [ ] **Step 7: Wrap up**

```bash
cd /home/smadinya/vividynamics/nare-45
git status --short                                         # expect no output
git log --oneline 45-ask-across-models..HEAD
git -C /home/smadinya/vividynamics/nare-45-side status --short   # expect no output
```

Expected log, newest first: the evidence commit, the baseline commit (if it shipped), the revert of B (if A was gated), B, A, the plan, `73d867d`, `54f175f`. Nothing is pushed. Keep `nare-45-side` until PR 2 merges.

---

## Acceptance criteria (spec section 10, PR 2's lines)

The other lines of section 10 are PR 1's (the matrix, `compare --matrix`, `models`, the README pointer, the preflight, the two cases, the "before" baselines and their evidence) or PR 3's.

| Criterion | Met by |
| --- | --- |
| The `ask` description is the section 6.1 candidate its rule picked, the `questions` description is as in section 6, and a test checks that neither names an ambiguous case's identifiers, values or prompt | D1 (A, the widened leak test, the exact-text check), D2 (B), E3 (the pick), E4, Step 1 (the revert when A is picked) |
| The section 6 PR shows each candidate's cheap-loop table with its stamp and commit, the score that picked one, and the matrix table before and after on the one picked, and re-blesses all seven baselines after the change | E2 (tables, stamps, commits through the run log), E3 (the score), E4, Step 3 (the after table), E5, Steps 1 and 4. Five baselines are re-blessed; the two Claude rows join every step once their baselines exist on PR 1 (spec section 3.4), and until then this line is open for them |
| The section 6 PR gives the labels of every failing ambiguous rep and the question of every passing ambiguous-request rep | E5, Steps 2 to 4 (decision 13) |
| `gpt-5-nano` on read-only-question has at least 14 judged reps on PR 1's code and 14 on the picked wording, and the PR states section 6.1's reading of the picked wording's reps and of the first measurement's 5 of 7 | E1 (PR 1's 14, and the 5 of 7), E4, Steps 5 and 7 (the pick's 14 and its reading), E5, Step 4 |
| No matrix model has a confirmed regression on edit-docstring, multi-file-rename or read-only-question, other than one section 6.1's reading calls noise, and no confirmed regression on ambiguous-after-reading | E4, Steps 4 to 8 |
| `gpt-6.1-sol` and `gpt-6-luna` reach 2 of 3 on ambiguous-request, or #45 records the miss with its labels and names the next lever | E4, Step 8 (the miss rule), E5, Steps 2, 4 and 5 |
| Each matrix model either reaches 2 of 3 on both ambiguous cases, or #45 records its failure labels | E4, Step 3 (`score`), E5, Steps 2, 4 and 5 |
| Section 7 is built, nudging only under `nare run`, or #45 records why it was not | E5, Step 5 decides; building it is PR 3's own plan |
| `bin/build` passes | D1, Step 5 (at A); D2, Step 5 (at B); E4, Step 2 (at the gate's commit) |

"#45 records" is met by the README section, which PR 2 carries, and the comment E5, Step 5 drafts for the user to post.
