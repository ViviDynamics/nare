# Ask Wording, Third Pass: Implementation and Measurement Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build spec section 6.2's before commit (ambiguous-request's new check, with `src/nare/tools.py` and `tests/test_tools.py` back to `main`'s) and its two candidates, C and D, each one commit with the leak test restored and widened. Then measure as section 6.2 says: a read-only re-score of the kept reps, step 1's spark pool on section 6's text, the before numbers re-run and re-blessed, a spark-first pool per built candidate, the cheap loop, the pick, the gate, and the record and re-bless, or the record of why nothing passed.

**Architecture:** Part F makes three commits: the before commit on `45-ask-description`, and C and D on two sibling branches, `45-ask-c` and `45-ask-d`, each one commit on the before commit. Part G re-runs ambiguous-request at the before commit in `nare-45`, commits the re-blessed baselines on top of it, and moves each candidate that step 1 selects onto that commit, so a candidate is measured against the re-blessed baselines. Every other run is made from the side worktree, `nare-45-side`, detached at the commit it measures. The pick is fast-forwarded onto `45-ask-description`, and the gate runs there. A sourced shell file holds the setup and small helpers, and logs every results file with its step's tag and the commit it ran at.

**Tech Stack:** Python 3.12+, pytest, ruff, mypy strict, `uv`, `jq`, Docker (Part G only).

**Spec:** `docs/superpowers/specs/2026-10-23-ask-across-models-design.md`, section 6.2 (the design), with sections 6.1, 8 and 10. **Previous plan:** `docs/superpowers/plans/2026-10-27-ask-wording-second-pass.md` (its helpers and readings are reused here). **Evidence:** `docs/evidence/45/README.md`. Issue: #45.

## Decisions (the plan's calls)

1. **C and D are siblings, each one commit on the before commit.** Spec 6.2: "Each candidate is one commit on top of the before commit." D is not C plus a change, so the two cannot share one linear branch. Task F2 commits C on `45-ask-c` and Task F3 commits D on `45-ask-d`, both at the before commit, and `45-ask-description` gains nothing above the before commit until Part G but docs commits (review fixes to this plan or the spec).
2. **A candidate is measured on the re-bless commit, not the before commit.** `run` compares against the baselines of the checkout it runs in (`benchmarks/baselines/`), and spec 6.2 measures the candidates "against the re-blessed baselines". Those can only be committed after the re-run at the before commit, so the plan's tree is: the before commit (`BASE`, which G0 finds by its subject), any docs-only review fixes on it (the tip, `BEFORE`, is `BASE` itself when there are none), then `REBLESS`, which changes only the five baseline files, then each candidate as one commit on `REBLESS`. `BEFORE`'s code, cases and baselines are `BASE`'s, so the before numbers and PR 1's side of every new pool run at `BEFORE`, where `nare-45` is, and each re-blessed baseline's `[meta] commit` names a tree whose case has the check and whose `src/nare` is `main`'s. G4 moves a selected candidate with `git rebase --onto "$REBLESS" "$BASE" 45-ask-c`, and checks that its `src` and `tests` are the reviewed commit's and that it differs from `REBLESS` only in `src/nare/tools.py` and `tests/test_tools.py`. Every measurement of a candidate names the moved commit, `$C` or `$D`. Read this way, "one commit on top of the before commit" means one commit on top of the before commit and its re-blessed baselines.
3. **Both candidates are written and reviewed up front; step 1 decides which are built.** Reviewing both before any money is spent means the review cannot lean on step 1's result. Spec 6.2's "A candidate this step does not build is not built later" is read as: only a candidate step 1 selects is moved onto `REBLESS` (G4) and measured. The other stays on its branch at `BASE`, is never moved, measured or merged, and the evidence says it was written and not built.
4. **Where runs are made.** `bless` reads the newest run in its checkout (`latest_run`) and writes `[meta] commit` from that checkout's `HEAD`. So `nare-45` holds only the runs that are blessed: the before re-run (G3, at `BEFORE`), the gate (G7, at the pick's commit), and, if the wording ships, G9's re-run of each cell the gate left inconclusive. Every other run is made in `nare-45-side`, detached at the commit it measures, and is never blessed. `nare-45` never leaves `45-ask-description` in Part G.
5. **The gate commit is the pick's commit.** G7 fast-forwards `45-ask-description` onto the pick (`git merge --ff-only`), so `$GATE` is `$C` or `$D` itself, the commit its spark pool and cheap loop ran at. No docs commit lands on `45-ask-description` between `BEFORE` and the gate.
6. **A pool at the same commit reads a later flag.** Spec 6.2 step 2: the spark pool "was not chosen for being low, so if the cheap loop later flags this cell, the pool already reads it and no new runs are made". The gate runs at the same commit (decision 5), so the same holds there: a gate flag on spark's ambiguous-after-reading is read with step 2's pool, and a gate flag on a cell G6 already pooled at the pick is read with G6's pool. Only a cell with no pool at the pick's commit gets fresh runs. A candidate step 2 kept has a spark pool less than 0.2 above PR 1's, so a later spark flag never holds up.
7. **Readings in whole numbers.** "At least 0.2 above" is decided as `5 * (F*N0 - F0*N) >= N*N0` (the `above` helper), never from rounded rates. Step 1's three rows are spec 6.2's rule with PR 1's 6 of 14 and A's 13 of 17: section 6's text is at least 0.2 above PR 1's (row 1, D), A's is at least 0.2 above section 6's (row 2, C), or neither (row 3, C and D). At 14 judged reps this is the spec's 9 or more, 7 or fewer, and 8. At other counts the rule decides, not the rounded 0.63 and 0.56 in the spec's table: at 16 judged with 9 failed (0.5625), A's 0.765 is 0.202 above, so the row is 2. A pooled reading is made only when both pools hold at least 14 judged reps (`full`, which otherwise prints "pool short: stop"), and `above` fails with 2, an error and never a "no", when either side has no judged rep; every caller stops on it.
8. **The fixed pools.** Three numbers from the second pass are inputs to this one: PR 1's spark on ambiguous-after-reading, 6 failed of 14 judged; A's, 13 of 17; and PR 1's `gpt-5-nano` on read-only-question, 2 of 17. They were made at the old hashes (`477160c`, `3d1fee5`), whose code, cases and baselines are `6ca24f5`'s and `6b2eb75`'s (spec 6.2). G0 re-counts them from the second pass's run log. Any new pool on PR 1's side runs at `BEFORE` (spec 6.2: "PR 1's side of any new pool runs there too"), under the tag `pr1`, and serves both candidates and the gate.
9. **Counting reps, and the slant in a pool,** as the second pass's decision 7: a smoke run is 3 reps, a drop is re-run at 7 (`CONFIRM_REPS`), which replace the 3, and a pool counts `pass` and `fail`, not `error`. Both sides of every reading are made the same way, so the slant toward passes is the same on both. A pool counts no run that was the reason it was made (spec 6.1), and labels cover every run on the candidate's code, the flagging run included.
10. **An inconclusive cell.** In the before re-run, a model whose ambiguous-request is inconclusive (errors over half its reps) is not blessed from that run, because `merge_baseline` would keep its old, unchecked number. It is re-run once on its own at `BEFORE` and blessed from that. In the gate, an inconclusive guard cell on a model whose baseline has the case is re-run once on the gate's code, and that verdict stands (the second pass's decision 11). If the wording ships, `bless` would keep the old number of every cell the gate run left inconclusive, under the gate's `[meta] commit`, which never measured it. So G9 re-runs each such cell once in `nare-45` at the gate's commit and blesses it with `--case`; that run sets the number, not the verdict. A cell inconclusive again is dropped from its baseline and recorded as unmeasured in the evidence, as spark's and sol's multi-file-rename were left out after section 5's 403s.
11. **The nano read-only-question pool counts as confirmed** (the second pass's decision 9): at 14 judged reps or more, a pass rate below the baseline's 3/3 that the reading calls the description's fails the gate, whatever the gate run's own 3 reps showed.
12. **"Every failing ambiguous rep"** (spec 6.1 step 5, section 10) is every failing rep of the two ambiguous cases in the before re-run (with any `before-again` run), each kept candidate's cheap loop, and the gate run (with any G9 re-run of an ambiguous cell). The questions are those of every passing ambiguous-request rep in the same runs. Pools are counted, not labelled: labels play no part in reading an ambiguous-after-reading drop.
13. **The re-score runs first.** It is free, and it proves the new check on 2026-10-27's kept reps before any money is spent. If it moves anything but the six blind `gpt-5-nano` passes spec 6.2 names, Part G stops there.
14. **What a reading may cite** (spec 6.2, "Readings use what a rep did"): a rep's tool calls and their arguments, its diff, its closing, its `ask` questions, and yes-or-no facts read from its tool results, such as whether a file read returned content. A tool result is what a tool returned, not model reasoning; the model's reasoning saved in `session.json`, its text and thinking blocks, is never read. The helpers read `tool_use` blocks, the `questions` field, `diff.patch` and `closing.txt`, and `saw_body` answers only yes or no from tool results. None prints a text block, a thinking block or a tool result.
15. **`NARE_STREAM=1` for every run,** as the baselines were run.

## Global Constraints

- Work only in `/home/smadinya/vividynamics/nare-45` and, in Part G, `/home/smadinya/vividynamics/nare-45-side`. Never touch `/home/smadinya/vividynamics/nare`: it is the user's checkout and the editable install behind `nare`. Never switch its branch.
- Commit only on `45-ask-description`, `45-ask-c` and `45-ask-d`. Never commit to, rebase, move or push `45-ask-across-models`: it is PR 1, ViviDynamics/nare#116, waiting on the user to merge. The side worktree is on that branch today; G0 detaches it before anything else, and it is never put back on it. Nothing in this plan pushes or posts to GitHub.
- Prose we publish (docs, commit messages, code comments) uses no em dashes.
- `.agents/skills/` does not change.
- `bin/build` (ruff format --check, ruff check, mypy strict, pytest) passes at the before commit, C and D (Part F), at each candidate after G4 moves it, and at the gate's commit (G7).
- Tests never reach the proxy or Docker.
- Part G follows the rules under "Rules every operator step repeats", before G0.
- Commit style `type(scope): summary (#45)`.

## Review Focus

1. **The before commit's `src` and `tests` are `main`'s and PR 1's,** and ambiguous-request's check is spec 6.2's, word for word (F1, Steps 3 and 5).
2. **C's and D's texts are spec 6.2's, word for word.** F2 Step 5 and F3 Step 5 assert equality of the joined descriptions.
3. **The leak test's patterns.** Each near copy names the pattern it must trigger, every pattern is the only match of at least one near copy, the new shapes are caught ("Delete parse_a or parse_b? Unanswered, I will delete both.", "Delete helpers.py or strings.py?", "Drop helper_a or helper_b?", "Raise MAX_WORKERS to 8, 16, or another value?", "Set the pool size to 8 or to 16?"), the harmless sentences pass ("Do not delete or rename files." among them), and every string of both candidates passes every pattern.
4. **The branch layout** (decisions 1 to 5): C and D siblings at `BASE`, moved onto `REBLESS` only when step 1 selects them, and the gate at the pick's own commit.

## File map

| File | Change | Task |
| --- | --- | --- |
| `benchmarks/cases/ambiguous-request/case.toml` | the `bash` check on the questions | F1 |
| `benchmarks/tests/test_cases.py` | the check run with `run_here`; `verify` checks the case | F1 |
| `src/nare/tools.py` | `main`'s (F1); the `ask` and `questions` descriptions of C (F2) and of D (F3) | F1, F2, F3 |
| `tests/test_tools.py` | `main`'s (F1); C's pins and the leak test (F2); D's (F3) | F1, F2, F3 |
| `benchmarks/baselines/*.smoke.toml` | ambiguous-request re-blessed at `BEFORE` (G3); all re-blessed on the gate run if the wording ships, with each cell the gate left inconclusive re-run and blessed, or dropped as unmeasured (G9) | G3, G9 |
| `docs/evidence/45/README.md` | section "After the third wording" | G10 |
| `nare-45/benchmarks/results/45-3-*` (gitignored, never committed) | the shell file, the run log, transcripts, tables, scores, notes | G0 to G12 |

---

# Part F: the code (implementer)

Do not run the bench in Part F, not even `verify`: it builds the shared image. G0 runs `verify`.

### Task F0: Start

- [ ] **Step 1: Check the worktree**

```bash
cd /home/smadinya/vividynamics/nare-45
git status --short                          # expect no output
git branch --show-current                   # 45-ask-description
git log --oneline 45-ask-across-models..HEAD
git branch --list '45-ask-c' '45-ask-d'     # expect no output
```

Expected log, newest first: any review fixes to this plan, `docs: plan for the third ask wording pass (#45)` (this file), `0d9e11e`, `64535e6`, `d2c295e`, `bb7084e`, `a2e11d3`, `a12898e`, `7a4b5cb` (B), `6b2eb75` (A), `a4f17d9`, `acbe9e0`, `947cc4c` (section 6's text). If it differs, or either branch exists, stop and report.

- [ ] **Step 2: Build once**

Run: `bin/build`
Expected: clean.

---

### Task F1: The before commit

**Files:**
- Modify: `benchmarks/cases/ambiguous-request/case.toml`
- Test: `benchmarks/tests/test_cases.py`
- Restore: `src/nare/tools.py`, `tests/test_tools.py` (from `origin/main`)

**Interfaces:**
- Produces: ambiguous-request with a second check, of kind `bash`, which `bin/bench verify` now runs instead of exempting the case, and `src` and `tests` identical to `main`'s and to PR 1's. Nothing else in `benchmarks/` changes; the baselines are re-blessed in G3.

- [ ] **Step 1: Write the failing tests**

In `benchmarks/tests/test_cases.py`, add the imports, keeping the block sorted:

```python
from benchmarks.runner.__main__ import verify
from benchmarks.runner.case import Case, load_cases
from benchmarks.runner.sandbox import repo_root
from nare.session import new_session, save
```

and directly after `test_the_after_reading_case_passes_only_questions_naming_both_helpers`, add:

```python
@pytest.mark.parametrize(
    ("questions", "passes"),
    [
        (["What should TIMEOUT be increased to?"], True),
        # "the timeout" and its value, as two of luna's questions read.
        (["The timeout is 30 now. Raise it to 45 or 60?"], True),
        (["Which file do you mean?", "Raise TIMEOUT to 60 or 120?"], True),
        # The timeout of the agent's own commands, as nano's blind passes asked.
        (["Set my command timeout to 300 or 600 seconds?"], False),
        (["Which timeout do you mean?"], False),
        ([], False),
    ],
)
def test_the_ask_case_passes_only_questions_naming_timeout_or_30(
    tmp_path: Path, questions: list[str], passes: bool
) -> None:
    # nano's blind passes asked about the timeout of its own commands, never
    # opened config.py, and passed on the judge alone (#45).
    cmd = check_with("ambiguous-request", "session.json")
    (tmp_path / "session.json").write_text(session_asking(*questions))
    assert (run_here(cmd, tmp_path, tmp_path) == 0) is passes


def test_verify_checks_the_ask_case_instead_of_exempting_it(tmp_path: Path) -> None:
    # verify's idle run saves the session nare starts with, which asks nothing:
    # the check fails on it, so verify can tell the case measures something.
    case = cases()["ambiguous-request"]
    save(new_session(case.prompt), tmp_path / "session.json")
    check = check_with("ambiguous-request", "session.json")
    assert run_here(check, tmp_path, tmp_path) != 0

    def pristine(_: Case, cmd: str, timeout: int) -> tuple[int, str]:
        return run_here(cmd, tmp_path, tmp_path), ""

    text, code = verify([case], run_check=pristine)
    assert code == 0
    assert "ok (fails on the pristine fixture)" in text
```

The parameters are spec 6.2's four kinds: a question naming `TIMEOUT`, one naming only 30, one offering 300 or 600 seconds for the agent's own commands, and none, with a second `TIMEOUT` question behind an unrelated one and a vague question that names neither. The `verify` test saves the session nare starts with, as `verify`'s idle run does (`idle_artifacts`), runs the case's check on it with `run_here`, which must fail, and hands `verify` a check runner that does the same, so `verify` sees the real check fail on a real pristine session rather than a stub's exit code.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_cases.py -q`
Expected: `7 failed, 31 passed`. The six parameters fail in `check_with` (no check reads `session.json` yet), and the `verify` test fails because the case is exempt.

- [ ] **Step 3: Add the check**

In `benchmarks/cases/ambiguous-request/case.toml`, directly after the `result` check and before `[judge]`, add spec 6.2's check, word for word:

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

Leave a blank line between the `result` check and the comment, and between `'''` and `[judge]`. The judge keeps its two claims.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest benchmarks/tests/test_cases.py -q`
Expected: `38 passed`.

- [ ] **Step 5: Return the `ask` text and its tests to `main`'s**

```bash
cd /home/smadinya/vividynamics/nare-45
git fetch --quiet origin
git show origin/main:src/nare/tools.py > src/nare/tools.py
git show origin/main:tests/test_tools.py > tests/test_tools.py
git diff --quiet 45-ask-across-models -- src tests && echo "src and tests are PR 1's"
git diff --quiet origin/main -- src tests && echo "and main's"
uv run pytest tests/test_tools.py -q -k the_ask      # 1 passed
```

Both `echo` lines must print. If the second does not, `main` has moved past PR 1's base in `src` or `tests`: stop and report, since the before numbers must measure `main`'s code and rebasing this branch is the user's call.

- [ ] **Step 6: Build and commit**

```bash
bin/build
git status --short    # the case, its test, src/nare/tools.py, tests/test_tools.py
git add benchmarks/cases/ambiguous-request/case.toml benchmarks/tests/test_cases.py \
  src/nare/tools.py tests/test_tools.py
git commit -F - <<'MSG'
fix(bench): ambiguous-request checks what its questions name, on main's ask (#45)

Its questions must name TIMEOUT, or its value 30 as a whole number, so a
blind question about the agent's own command timeout no longer passes on the
judge alone, and verify checks the case instead of exempting it.
src/nare/tools.py and tests/test_tools.py return to main's, so this is the
commit the before numbers are re-run at (spec section 6.2).
MSG
```

G0 finds this commit by its subject. Keep it exactly.

---

### Task F2: Candidate C, on `45-ask-c`

**Files:**
- Modify: `src/nare/tools.py` (the `ask` entry in `TOOL_SCHEMAS`, around line 500)
- Test: `tests/test_tools.py`

**Interfaces:**
- Produces: the `ask` description, spec 6.2's candidate C verbatim, and the `questions` description of section 6 (the log-level example). The name, the `input_schema` shape and `required` are unchanged. `test_the_ask_question_states_its_default` is unchanged and keeps passing.

- [ ] **Step 1: Branch at the before commit**

```bash
cd /home/smadinya/vividynamics/nare-45
git switch -c 45-ask-c 45-ask-description
```

- [ ] **Step 2: Write the tests**

In `tests/test_tools.py`, directly after `test_the_ask_question_states_its_default`, add the blocks below in this order: the prose pin, the open-value pin, the log-level pin, then the leak block. They are A's tests (commit `6b2eb75`) with the open-value pin widened, B's last-sentence pin left out, and the leak check widened: its patterns are named, each near copy names the pattern it must trigger, every pattern is the only match of at least one near copy, and four shapes are added or widened (the old example's list, "to 8 or to 16", a choice of what to delete across a file name, and "drop"), each with a near copy, and two harmless sentences for their edges.

The prose pin:

```python
def test_the_ask_description_says_where_a_prose_question_goes() -> None:
    # Haiku saw the ambiguity and asked in its closing text, so the session
    # ended done and a headless caller moved on (#45).
    assert (
        "Ask only with this tool: a text reply ends the session as finished, so "
        "a question written there may never be answered."
    ) in schema_of("ask")["description"]
```

The open-value pin (F3 uses it too):

```python
def test_the_ask_description_says_an_open_value_or_reading_is_no_detail() -> None:
    # Without the old example, sol and luna set a new timeout value themselves
    # instead of asking, and spark deleted both unused helpers more often than
    # on main's text (#45). The sentence follows the exclusion for details you
    # can decide yourself, so "such a detail" means it.
    assert (
        "or for details you can decide yourself such as wording, style or names. "
        "A new value the request leaves open is not such a detail, and neither is "
        "which of several things the request could mean."
    ) in schema_of("ask")["description"]
```

The log-level pin:

```python
def test_the_ask_example_is_the_log_level_choice() -> None:
    questions = schema_of("ask")["input_schema"]["properties"]["questions"]
    assert "Set the log level to INFO or to WARNING?" in questions["description"]
```

The leak block (F3 uses it too):

```python
# What a model could copy from the ask schema to pass an ambiguous case, from
# benchmarks/cases, as case-insensitive patterns, each under a short name. A
# name counts inside a longer identifier (HTTP_TIMEOUT, MAX_RETRIES), so only a
# letter or digit before it stops a match, and a value counts with a unit after
# it (60s). Shapes, under any names: an amount left open, as in the old
# example's "from 30 to what", an unused helper to choose, as in
# ambiguous-after-reading's prompt, and which of several things to delete,
# asked as a question. The prompts' verbs alone are not here: a description may
# say "delete" or "increase".
CASE_ANSWERS = {
    # ambiguous-request: "Increase the timeout."; config.py holds TIMEOUT = 30
    # and RETRIES = 3. Its judge accepts a question naming the current 30; the
    # old example offered 60 and 120, and every guess set 60.
    "TIMEOUT": r"(?<![a-z0-9])timeout",
    "RETRIES": r"(?<![a-z0-9])retries",
    "config.py": r"(?<![a-z0-9])config\.py",
    "30": r"(?<![\d.])30(?!\d)",
    "60": r"(?<![\d.])60(?!\d)",
    "120": r"(?<![\d.])120(?!\d)",
    # ambiguous-after-reading: "Delete the unused helper."; utils.py holds
    # format_bytes and slugify, which nothing calls, and parse_csv_line and
    # total, which main.py calls. "total" is an everyday word, so it is left out.
    "utils.py": r"(?<![a-z0-9])utils\.py",
    "main.py": r"(?<![a-z0-9])main\.py",
    "format_bytes": r"(?<![a-z0-9])format_bytes",
    "slugify": r"(?<![a-z0-9])slugify",
    "parse_csv_line": r"(?<![a-z0-9])parse_csv_line",
    # An amount left open: "from 30 to", "from 45 seconds to", "to 8 or 16",
    # "to 8 or to 16", the old example's list "to 8, 16, or", and "to what"
    # asked as a question ("to what:", "to what?", "to what value").
    "from N to": r"\bfrom \d[\w.]*(?: \w+)? to\b",
    "to N or M": r"\bto \d[\w.]* or \d",
    "to N or to M": r"\bto \d[\w.]* or to \d",
    "to N, M": r"\bto \d[\w.]*,\s*\d",
    "to what": r"\bto what(?=\s*[:?]| value)",
    # An unused helper to choose.
    "unused": r"\bunused\b",
    "dead helper": r"\b(?:dead|uncalled|unreferenced) (?:helper|function)s?\b",
    # Which of several things to delete, asked as a question: "delete",
    # "remove" or "drop", then "or", within one question. A dot inside a word,
    # as in a file name, does not end the question.
    "delete A or B?": (
        r"\b(?:delete|remove|drop)\b(?:[^.?!]|\.\w)*\bor\b(?:[^.?!]|\.\w)*\?"
    ),
}


def case_answers_in(text: str) -> list[str]:
    """The name of each pattern that matches `text`."""
    return [
        name
        for name, pattern in CASE_ANSWERS.items()
        if re.search(pattern, text, re.IGNORECASE)
    ]


def strings_in(value: object) -> list[str]:
    """Every string value in a schema, each checked on its own."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in strings_in(item)]
    if isinstance(value, list):
        return [s for item in value for s in strings_in(item)]
    return []


# Each near copy, with the pattern it must trigger. Every pattern is the only
# match of at least one near copy, so taking any pattern out, or narrowing it
# past its near copies, fails a test.
NEAR_COPIES = [
    # The old example, ambiguous-request's fixture and answer.
    (
        "Raise TIMEOUT from 30 to what: 60, 120, or another value? "
        "Unanswered, I will use 60.",
        "to what",
    ),
    # ambiguous-request's shape under other names, which spec 6.1 rules out.
    ("Raise MAX_WORKERS from 4 to 8 or 16? Unanswered, I will use 8.", "from N to"),
    ("Raise MAX_WORKERS to 8 or 16? Unanswered, I will use 8.", "to N or M"),
    ("Raise MAX_WORKERS to 8, 16, or another value?", "to N, M"),
    ("Set the pool size to 8 or to 16?", "to N or to M"),
    ("Lengthen the time limit from 45 to 90 or 180 seconds?", "from N to"),
    ("Lengthen the time limit from 45 seconds to 90?", "from N to"),
    ("Raise the pool size to what?", "to what"),
    # The case's names inside longer ones, and its values with a unit.
    ("Set HTTP_TIMEOUT to 60s or 120s?", "TIMEOUT"),
    ("Raise HTTP_TIMEOUT?", "TIMEOUT"),
    ("Raise MAX_RETRIES?", "RETRIES"),
    ("Edit app_config.py?", "config.py"),
    ("Keep the 30s default?", "30"),
    ("Wait 60s between tries?", "60"),
    ("Wait 120s between tries?", "120"),
    ("Which helper in string_utils.py?", "utils.py"),
    ("Is it called from cli_main.py?", "main.py"),
    ("Delete format_bytes?", "format_bytes"),
    ("Delete slugify?", "slugify"),
    ("Is parse_csv_line still called?", "parse_csv_line"),
    # ambiguous-after-reading's shape under other names.
    ("Remove the unused function, format_size or make_slug?", "unused"),
    ("Which unused helper do you mean?", "unused"),
    ("Delete the dead helper, or the uncalled function too?", "dead helper"),
    ("Which uncalled function do you mean?", "dead helper"),
    # Which of several to delete, asked as a question, under other names.
    ("Delete parse_a or parse_b? Unanswered, I will delete both.", "delete A or B?"),
    ("Delete helpers.py or strings.py?", "delete A or B?"),
    ("Drop helper_a or helper_b?", "delete A or B?"),
]


@pytest.mark.parametrize(("near_copy", "pattern"), NEAR_COPIES)
def test_the_ask_leak_check_catches_a_near_copy(near_copy: str, pattern: str) -> None:
    assert pattern in case_answers_in(near_copy)


def test_the_ask_leak_check_proves_each_pattern_alone() -> None:
    # Some near copy is caught by each pattern and by no other.
    caught = [case_answers_in(near_copy) for near_copy, _ in NEAR_COPIES]
    assert {found[0] for found in caught if len(found) == 1} == set(CASE_ANSWERS)


@pytest.mark.parametrize(
    "harmless",
    [
        "Do not delete files to make room.",
        "Do not delete or rename files.",
        # "delete", then a question with "or", in two sentences.
        "Do not delete files. Is it one or both?",
        "Keep the change to what the request asks for.",
        "Return to what you were doing.",
        "Wait up to 300 seconds.",
        # A number and a comma, with no second number.
        "Count up to 3, then stop.",
    ],
)
def test_the_ask_leak_check_passes_harmless_wording(harmless: str) -> None:
    assert case_answers_in(harmless) == []


def test_the_ask_schema_names_no_benchmark_cases_answer() -> None:
    # The old example was ambiguous-request's fixture and the answer its judge
    # looks for, so a model could pass that case by copying it (#45). Every
    # string in the schema is checked on its own, descriptions and all.
    leaks = {s: case_answers_in(s) for s in strings_in(schema_of("ask"))}
    assert {s: found for s, found in leaks.items() if found} == {}
```

`re` and `pytest` are already imported on `main`. The near-copy test asserts that each near copy's named pattern is among its matches, and `test_the_ask_leak_check_proves_each_pattern_alone` that every pattern is the only match of at least one near copy, so taking any one pattern out, or narrowing it past its near copies, fails a test. "Delete parse_a or parse_b?", "Delete helpers.py or strings.py?" and "Drop helper_a or helper_b?" are caught by the delete pattern alone. "Do not delete or rename files." has "delete" and "or" but no question, "Do not delete files. Is it one or both?" has them in two sentences, and "Count up to 3, then stop." has a number and a comma but no second number.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -q -k the_ask`
Expected: `4 failed, 36 passed`. The prose, open-value and log-level pins fail on `main`'s text, and so does the schema leak test, on the old example. The default pin, the 27 near copies, the test that each pattern is proven alone, and the seven harmless sentences pass.

- [ ] **Step 4: Replace the two descriptions**

In `src/nare/tools.py`, in the `ask` entry's `"description": (...)`, replace:

```python
            "files involved. Do not use it for anything you can find out by "
            "reading files or running commands, for details you can decide "
            "yourself such as wording, style or names, to ask permission, or to "
            "offer follow-up work. Call ask on its own, before making any "
            "change. This ends the session."
```

with:

```python
            "files involved. Ask only with this tool: a text reply ends the "
            "session as finished, so a question written there may never be "
            "answered. Do not use it for anything you can find out by reading "
            "files or running commands, to ask permission, to offer follow-up "
            "work, or for details you can decide yourself such as wording, "
            "style or names. A new value the request leaves open is not such a "
            "detail, and neither is which of several things the request could "
            "mean. Call ask on its own, before making any change. This ends the "
            "session."
```

and in the `questions` property's `"description": (...)`, replace:

```python
                        "unanswered, e.g. 'Raise TIMEOUT from 30 to what: 60, "
                        "120, or another value? Unanswered, I will use 60.'"
```

with:

```python
                        "unanswered, e.g. 'Set the log level to INFO or to "
                        "WARNING? Unanswered, I will use WARNING.'"
```

The three lines above each replaced block do not change. The longest new line is 81 columns, and `ruff format` leaves both blocks as they are.

- [ ] **Step 5: Check the texts word for word**

```bash
uv run python - <<'PY'
from nare.tools import TOOL_SCHEMAS

C = (
    "Stop and ask the caller to resolve an ambiguous request. Use this only when "
    "the request can reasonably be read more than one way and the readings lead "
    "to different changes, and only after reading the files involved. Ask only "
    "with this tool: a text reply ends the session as finished, so a question "
    "written there may never be answered. Do not use it for anything you can "
    "find out by reading files or running commands, to ask permission, to offer "
    "follow-up work, or for details you can decide yourself such as wording, "
    "style or names. A new value the request leaves open is not such a detail, "
    "and neither is which of several things the request could mean. Call ask on "
    "its own, before making any change. This ends the session."
)
QUESTIONS = (
    "One question per item, naming the readings you are choosing between and "
    "the one you will use if it goes unanswered, e.g. 'Set the log level to "
    "INFO or to WARNING? Unanswered, I will use WARNING.'"
)
ask = next(s for s in TOOL_SCHEMAS if s["name"] == "ask")
assert ask["description"] == C, ask["description"]
assert ask["input_schema"]["properties"]["questions"]["description"] == QUESTIONS
print("candidate C, word for word")
PY
```

Expected: `candidate C, word for word`. `C` is spec 6.2's candidate C joined, and `QUESTIONS` is section 6's `questions` text.

- [ ] **Step 6: Run the tests and build**

```bash
uv run pytest tests/test_tools.py -q -k the_ask     # 40 passed
bin/build
git diff --stat 45-ask-description                  # src/nare/tools.py and tests/test_tools.py only
```

- [ ] **Step 7: Commit, and go back**

```bash
git add src/nare/tools.py tests/test_tools.py
git commit -m "feat(tools): ask says an open value or reading is no detail, with section 6's text (#45)"
git switch 45-ask-description
```

---

### Task F3: Candidate D, on `45-ask-d`

**Files:**
- Modify: `src/nare/tools.py` (the same entry)
- Test: `tests/test_tools.py`

**Interfaces:**
- Produces: the `ask` description, spec 6.2's candidate D verbatim, and the `questions` description of `main` with its example removed and none in its place. D has no sentence about text replies and no log-level example, so neither pin is added. `test_the_ask_question_states_its_default` holds.

- [ ] **Step 1: Branch at the before commit**

```bash
cd /home/smadinya/vividynamics/nare-45
git switch -c 45-ask-d 45-ask-description
```

- [ ] **Step 2: Write the tests**

In `tests/test_tools.py`, directly after `test_the_ask_question_states_its_default`, add F2's open-value pin, then F2's leak block, exactly as Task F2, Step 2 gives them. Do not add the prose pin or the log-level pin.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -q -k the_ask`
Expected: `2 failed, 36 passed`. The open-value pin and the schema leak test fail on `main`'s text.

- [ ] **Step 4: Replace the two descriptions**

In `src/nare/tools.py`, in the `ask` entry's `"description": (...)`, replace the same five lines as F2, Step 4:

```python
            "files involved. Do not use it for anything you can find out by "
            "reading files or running commands, for details you can decide "
            "yourself such as wording, style or names, to ask permission, or to "
            "offer follow-up work. Call ask on its own, before making any "
            "change. This ends the session."
```

with:

```python
            "files involved. Do not use it for anything you can find out by "
            "reading files or running commands, to ask permission, to offer "
            "follow-up work, or for details you can decide yourself such as "
            "wording, style or names. A new value the request leaves open is not "
            "such a detail, and neither is which of several things the request "
            "could mean. Call ask on its own, before making any change. This ends "
            "the session."
```

and in the `questions` property's `"description": (...)`, replace:

```python
                        "unanswered, e.g. 'Raise TIMEOUT from 30 to what: 60, "
                        "120, or another value? Unanswered, I will use 60.'"
```

with:

```python
                        "unanswered."
```

The longest new line is 83 columns, and `ruff format` leaves both blocks as they are.

- [ ] **Step 5: Check the texts word for word**

```bash
uv run python - <<'PY'
from nare.tools import TOOL_SCHEMAS

D = (
    "Stop and ask the caller to resolve an ambiguous request. Use this only when "
    "the request can reasonably be read more than one way and the readings lead "
    "to different changes, and only after reading the files involved. Do not use "
    "it for anything you can find out by reading files or running commands, to "
    "ask permission, to offer follow-up work, or for details you can decide "
    "yourself such as wording, style or names. A new value the request leaves "
    "open is not such a detail, and neither is which of several things the "
    "request could mean. Call ask on its own, before making any change. This "
    "ends the session."
)
QUESTIONS = (
    "One question per item, naming the readings you are choosing between and "
    "the one you will use if it goes unanswered."
)
ask = next(s for s in TOOL_SCHEMAS if s["name"] == "ask")
assert ask["description"] == D, ask["description"]
assert ask["input_schema"]["properties"]["questions"]["description"] == QUESTIONS
print("candidate D, word for word")
PY
```

Expected: `candidate D, word for word`.

- [ ] **Step 6: Run the tests and build**

```bash
uv run pytest tests/test_tools.py -q -k the_ask     # 38 passed
bin/build
git diff --stat 45-ask-description                  # src/nare/tools.py and tests/test_tools.py only
```

- [ ] **Step 7: Commit, and go back**

```bash
git add src/nare/tools.py tests/test_tools.py
git commit -m "feat(tools): ask says an open value or reading is no detail, on main's text (#45)"
git switch 45-ask-description
```

---

### Task F4: Hand-off

- [ ] **Step 1: Check the branches**

```bash
cd /home/smadinya/vividynamics/nare-45
git status --short                                          # expect no output
git branch --show-current                                   # 45-ask-description
BASE=$(git log --format=%H -1 -F --grep="fix(bench): ambiguous-request checks what its questions name, on main's ask" HEAD)
git log --oneline "$BASE^..HEAD"                            # the before commit, then any docs-only review fixes
test -n "$BASE" && git diff --quiet "$BASE" HEAD -- . ':!docs' && echo "only docs sit above the before commit"
for b in 45-ask-c 45-ask-d; do
  test "$(git rev-parse "$b^")" = "$BASE" && echo "$b is one commit on the before commit"
  git diff --name-only "$BASE" "$b"                         # src/nare/tools.py, tests/test_tools.py
done
git diff --name-only 45-ask-across-models 45-ask-description -- . ':!docs'   # the case, its test
for b in 45-ask-description 45-ask-c 45-ask-d; do
  for p in src/nare/tools.py tests/test_tools.py benchmarks/cases/ambiguous-request/case.toml \
      benchmarks/tests/test_cases.py; do
    git show "$b:$p" | grep -qP '\x{2014}' && echo "$b:$p has an em dash"
  done
done
echo "em dash check done"                                  # with no "has an em dash" line above
```

- [ ] **Step 2: If review asks for a change, fold it in and keep the layout**

The three branches are unpublished, so rewriting them is allowed (conventions, ground rule 4). Interactive rebase is not available; these do it by hand. After any of them, run Step 1 again and `bin/build` on each branch (`git switch <branch> && bin/build`, then `git switch 45-ask-description`).

A change to C (or D):

```bash
git switch 45-ask-c                 # or 45-ask-d
# edit, then:
bin/build
git commit -a --amend --no-edit
git switch 45-ask-description
```

A change to the before commit, keeping its subject (G0 finds it by its subject); any docs commits above it follow it:

```bash
OLD=$(git log --format=%H -1 -F --grep="fix(bench): ambiguous-request checks what its questions name, on main's ask" HEAD)
git switch --detach "$OLD"
# edit, then:
bin/build
git commit -a --amend --no-edit
NEW=$(git rev-parse HEAD)
git rebase --onto "$NEW" "$OLD" 45-ask-description
git rebase --onto "$NEW" "$OLD" 45-ask-c
git rebase --onto "$NEW" "$OLD" 45-ask-d
git switch 45-ask-description
```

A change to this plan or the spec after F1 exists is a new docs commit on top of `45-ask-description`. It changes no code, case or baseline, so G0 takes the tip as `BEFORE` and finds the before commit, `BASE`, by its subject (decision 2).

Never push. Part G starts only after review, and after the user approves the spend.

---

# Rules every operator step repeats

Every step in Part G starts with "Rules: R1 to R6". Read them again there.

- **R1. No secrets on screen or on disk.** Never print the API key, `NARE_BASE_URL`, `ROOT` or the proxy host: no `echo "$ROOT"`, `env`, `printenv`, `set -x`, `bash -x`, `cat .env`, and no command line copied from `ps` (a bench's holds the URL). The helpers show the base URL as the literal text `$ROOT`, and `no_bench` prints PIDs only. Run `no_secrets FILE...` on every file before it is committed or quoted in a hand-back.
- **R2. One bench at a time.** Never run two benches at once from any checkout (`nare`, `nare-45`, `nare-45-side`, `nare-63`, `nare-96b`): every `run` and `verify` builds and tags `nare-bench:local`, and two at once replace each other's image. Start a bench only through `bench` or `to14`, and move the side worktree only through `side_at` (or G4's restack, which calls it); all three refuse while one runs. A bench takes longer than a foreground command may (10 minutes), so run every block that calls `bench` or `to14` with the Bash tool's `run_in_background`, wait for its exit notification, and start nothing that runs a bench meanwhile.
- **R3. Never read model reasoning.** A session's text blocks and thinking blocks are the model's reasoning: never open, print, quote or summarise them, or a session's messages as a whole. A tool result is what a tool returned, not model reasoning, and a label may use a yes-or-no fact read from one, such as whether a file read returned the function's body (`saw_body`); still, nothing prints, quotes or summarises a tool result. Diagnose and label only from a rep's tool calls and their arguments (`calls`, `callargs`), its `diff.patch`, its `closing.txt`, its `ask` questions (`questions`, `amb_facts`), and yes-or-no facts from its tool results (`saw_body`). `judge.txt` and a results line's `judge_why` may be read only to say why a rep errored; no label cites them.
- **R4. Counts before any bless.** Before every `bless`, read the results' status counts (`counts`). A model with no judged rep is not blessed, and neither is a case that is inconclusive (errors over half its reps): `bless` silently keeps the old number for it, and an all-error run compares as "no regressions".
- **R5. Cloudflare 403s.** The proxy sits behind Cloudflare, which answers some requests, the agent's or the judge's, with an HTTP 403 and its "Sorry, you have been blocked" page. That rep is an `error`, counted in no pool or rate; a case with errors over half its reps is inconclusive. `spark/glm-5.3-flash` and `gpt-6.1-sol` have no multi-file-rename baseline for this reason (`docs/evidence/45/README.md`), so their multi-file-rename cells are unchecked. Never retry a rep by hand: `to14` keeps running until its pool is full.
- **R6. Checkouts and branches.** Never touch `/home/smadinya/vividynamics/nare`. Never move, commit to or check out `45-ask-across-models`. `nare-45` stays on `45-ask-description`. The side worktree stays detached, except while G4's restack holds a candidate branch. `bless` runs only in `nare-45`, in G3 and G9. Never push, never post to GitHub.

---

# Part G: the measurement (operator, after review)

These spend money on paid models through the LiteLLM proxy: at most about $5 for one candidate with no flagged cell, $1.40 more for a second, and $1.10 per flagged cell (spec 6.2, Cost). Start only once the user has approved this plan and its spend. Run the steps in order, G0 to G12. Every block starts by sourcing the shell file that G0 writes:

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
```

Shell state does not carry from one command to the next, so a block that skips that line has no keys, no `DOCKER_CONFIG` and no helpers.

**Cost and time,** from the second pass's runs (a 3-rep single run took 2 to 3 minutes, a 7-rep one 7 to 9; a two-case matrix loop about 23 minutes; the first measurement's full matrix 40):

| Step | Reps | Paid | Wall clock, about |
| --- | --- | --- | --- |
| G0, G1 | 0 | nothing | minutes |
| G2, step 1 | 14 to 21, spark | the judge | 10 to 30 min |
| G3, the before re-run | 15, or 36 with three confirmations | the judge, four paid agents | 10 to 25 min |
| G4, per built candidate | 14 to 21, spark | the judge | 10 to 30 min |
| G5, per kept candidate | 30, or 58 with four confirmations | the judge, four paid agents | 20 to 35 min |
| G6 and G8, per pooled cell | 28 to 42 | the judge, that model | 20 to 45 min |
| G7, the gate | 90, or 118 with four confirmations | the judge, four paid agents | 45 to 75 min |
| G8, `gpt-5-nano` on read-only-question | 7 to 18 | the judge, nano | 5 to 20 min |
| G9, per cell the gate left inconclusive (only if it ships) | 3, or 7 with a confirmation | the judge, that model | 3 to 9 min |

Streamed turns report a cost of 0.0 through this proxy, so the spend is read from the proxy, which is the user's to do.

### Where everything lands, and how runs are told apart

`MAIN` is `/home/smadinya/vividynamics/nare-45`, `SIDE` is `/home/smadinya/vividynamics/nare-45-side`, and `R` is `$MAIN/benchmarks/results`. A results file is named by its run's stamp and says nothing of the commit or worktree, so `bench` appends one line per results file to the run log `$R/45-3-runs.tsv`: the step's tag, the full commit its worktree was at, and the file's absolute path. Every count, score and label reads files through that log, by tag and commit.

| Step | Runs in | At commit | Tag |
| --- | --- | --- | --- |
| G0, `verify` | `MAIN` | `$BEFORE` | `verify` |
| G2, step 1 | `SIDE` | `$S6` (`947cc4c`) | `s6-pool` |
| G3, the before re-run | `MAIN` | `$BEFORE` | `before`, and `before-again` for an inconclusive model |
| G4, spark first | `SIDE` | `$C`, `$D` | `spark-C`, `spark-D` |
| G5, the cheap loop | `SIDE` | `$C`, `$D` | `loop-C`, `loop-D` |
| G6, a flagged cell's pool | `SIDE` | `$C` or `$D`; PR 1's side at `$BEFORE` | `loop-C-pool`, `loop-D-pool`; `pr1` |
| G7, the gate | `MAIN` | `$GATE`, which is `$C` or `$D` | `gate-C` or `gate-D` |
| G7 and G8, re-runs and nano's reps | `SIDE` | `$GATE` | `gate-C-extra` or `gate-D-extra` |
| G8, a flagged cell's pool | `SIDE` | `$GATE`; PR 1's side at `$BEFORE` | `gate-C-pool` or `gate-D-pool`; `pr1` |
| G9, a cell the gate left inconclusive, re-run to bless | `MAIN` | `$GATE` | `gate-C-again` or `gate-D-again` |

`$BEFORE` is the tip of `45-ask-description` when Part G starts: the before commit, `$BASE`, or a docs-only commit on it (decision 2).

A single run writes `<stamp>.jsonl` with `<stamp>/<case>-<rep>/`; a matrix run writes `<stamp>.<model>.jsonl` per model under one stamp. The other files, all in `$R`, all gitignored:

| File | Holds |
| --- | --- |
| `45-3-env.sh` | the shell file (G0) |
| `45-3-commits.env` | full SHAs and choices: `S6`, `PR1`, `BASE`, `BEFORE`, `C_REVIEWED`, `D_REVIEWED`, then `ROW1` and `BUILT` (G2), `REBLESS` (G3), `C`, `D`, `KEPT` (G4), `PICK` (G6), `GATE` (G7); a later line wins |
| `45-3-runs.tsv` | the run log |
| `45-3-<tag>-<UTC time>.txt` | each bench command's output, as printed |
| `45-3-<tag>-table.md`, `45-3-<tag>-score.tsv` | a matrix run's table and its ambiguous cells |
| `45-3-rescore.tsv`, `45-3-rescore.txt` | G1's re-score |
| `45-3-baselines-*.md` | `compare --matrix --baselines` at each stage |
| `45-3-amb-facts.md`, `45-3-questions.md`, `45-3-guard-facts.md` | what labelling needs |
| `45-3-notes.md` | what each step found; G10 writes the README from it |
| `45-3-issue-comment.md` | the comment for #45, for the user to post |
| `45-3-docker/` | `DOCKER_CONFIG` |

The re-score script lives in the operator's scratchpad (`$SCRATCH`), not in the repo. Keep both worktrees until PR 2 merges: the README cites stamps whose evidence is only on this disk.

### G0: The shell, the commits, the side worktree, and `verify` (free)

Rules: R1 to R6.

- [ ] **Step 1: Write the shell file**

Set `SCRATCH` on the first line to this session's scratchpad directory (an absolute path), then run the block.

```bash
SCRATCH=/the/absolute/path/of/your/scratchpad
[ -d "$SCRATCH" ] || { echo "set SCRATCH to your scratchpad directory"; exit 1; }
mkdir -p /home/smadinya/vividynamics/nare-45/benchmarks/results
{ printf 'SCRATCH=%q\n' "$SCRATCH"; cat <<'SH'
# Part G's shell: sourced at the top of every block. Prints no key and no URL.
MAIN=/home/smadinya/vividynamics/nare-45
SIDE=/home/smadinya/vividynamics/nare-45-side
R="$MAIN/benchmarks/results"   # gitignored: the log, transcripts, tables, notes
LOG="$R/45-3-runs.tsv"         # one line per results file: tag, full commit, path
[ -f "$R/45-3-commits.env" ] && . "$R/45-3-commits.env"   # SHAs and choices
export DOCKER_CONFIG="$R/45-3-docker"   # ~/.docker uses desktop.exe, which fails
mkdir -p "$DOCKER_CONFIG" && echo '{}' > "$DOCKER_CONFIG/config.json"
set -a; . "$MAIN/.env"; set +a
export OPENAI_API_KEY="$ANTHROPIC_API_KEY"   # the same key on this proxy
export NARE_STREAM=1                         # as the baselines were run
unset NARE_BENCH_MAX_TOKENS NARE_BENCH_JUDGE_MODEL   # no baseline sets a cap
ROOT="${NARE_BASE_URL%/}"; ROOT="${ROOT%/v1}"
JUDGE=gpt-6.1-sol
# The second pass's pools that spec 6.2 reads against (G0 re-counts them):
# PR 1's and A's spark on ambiguous-after-reading, PR 1's nano on
# read-only-question. Failed, then judged.
PR1_AAR_F=6 PR1_AAR_N=14
A_AAR_F=13 A_AAR_N=17
PR1_ROQ_F=2 PR1_ROQ_N=17

# no_bench: fails when a bench runs from any checkout, naming only its PIDs (a
# bench's command line holds the base URL). Anchored on the python process, so
# a shell whose command text mentions the module, this one included, is not one.
no_bench() {
  local pids
  pids=$(pgrep -f '^[^ ]*python[0-9.]* -m benchmarks\.runner( |$)' | tr '\n' ' ')
  if [ -n "$pids" ]; then
    echo "a bench is running (pid ${pids% }): wait for it to exit" >&2; return 1
  fi
  if [ -n "$(docker ps -q --filter name=nare-bench)" ]; then
    echo "a nare-bench container is running: wait for it" >&2; return 1
  fi
}

# side_at COMMIT: the side worktree, clean and detached at COMMIT. Refuses while
# a bench runs, so it never moves the tree under a run, or when it has changes.
side_at() {
  no_bench || return 1
  if [ -n "$(git -C "$SIDE" status --short)" ]; then
    git -C "$SIDE" status --short; echo "the side worktree is not clean" >&2; return 1
  fi
  git -C "$SIDE" switch --quiet --detach "$1" || return 1
  echo "side worktree at $(git -C "$SIDE" rev-parse --short HEAD)"
}

# bench W TAG ARGS...: bin/bench ARGS in worktree W, its output kept in a
# transcript, and every results file it wrote logged under TAG with the full
# commit W was at when it started. If W's HEAD moved during the run, the files
# are logged as "moved", which matches no commit, so nothing counts them.
# Returns bench's exit code (2 if another bench is running).
bench() {
  local w=$1 tag=$2; shift 2
  no_bench || return 2
  local commit out code
  commit=$(git -C "$w" rev-parse HEAD)
  out="$R/45-3-$tag-$(date -u +%Y%m%dT%H%M%SZ).txt"
  # The base URL is shown as $ROOT, never spelled out.
  echo "== $tag in $w at ${commit:0:7}: bin/bench ${*//"$ROOT"/\$ROOT}" | tee "$out"
  (cd "$w" && bin/bench "$@") 2>&1 | tee -a "$out"
  code=${PIPESTATUS[0]}
  if [ "$(git -C "$w" rev-parse HEAD)" != "$commit" ]; then
    echo "$w moved during the run: its results are logged as moved" | tee -a "$out" >&2
    commit=moved
  fi
  sed -n 's/^results: //p' "$out" | while read -r f; do
    printf '%s\t%s\t%s\n' "$tag" "$commit" "$f" >> "$LOG"
  done
  echo "exit=$code transcript=$out"
  return "$code"
}

# files COMMIT TAG...: the results files logged under any of the TAGs at
# COMMIT (a full SHA). A file from any other commit is never returned.
files() {
  local commit=$1; shift
  [ -f "$LOG" ] || return 0
  awk -F'\t' -v c="$commit" -v tags=" $* " \
    '$2 == c && index(tags, " " $1 " ") {print $3}' "$LOG" | sort -u
}

# logged TAG: each file logged under TAG, with its commit, short.
logged() {
  awk -F'\t' -v t="$1" '$1 == t {print substr($2, 1, 7), $3}' "$LOG" | sort -u
}

# nfe MODEL CASE COMMIT TAG...: "judged failed errored" for MODEL's reps of
# CASE pooled over the TAGs' files at COMMIT. Judged is pass or fail; an error
# counts in neither, as in the runner.
nfe() {
  local model=$1 cid=$2 commit=$3 f; shift 3
  f=$(files "$commit" "$@")
  if [ -z "$f" ]; then echo "0 0 0"; return; fi
  # shellcheck disable=SC2086
  jq -rs --arg m "$model" --arg c "$cid" '
    map(select(.model == $m and .case == $c))
    | "\(map(select(.outcome != "error")) | length) \(map(select(.outcome == "fail")) | length) \(map(select(.outcome == "error")) | length)"' $f
}

# judged MODEL CASE COMMIT TAG...: the same, in words.
judged() {
  local n f e; read -r n f e < <(nfe "$@")
  echo "$n judged, $f failed, $e errored"
}

# blocks MODEL CASE COMMIT TAG...: the same pool, one line per run: 3 reps is
# a first pass the runner kept, 7 a confirmation that replaced one.
blocks() {
  local model=$1 cid=$2 commit=$3 f; shift 3
  files "$commit" "$@" | while read -r f; do
    jq -rs --arg m "$model" --arg c "$cid" --arg f "$(basename "$f")" '
      map(select(.model == $m and .case == $c)) | select(length > 0)
      | "\($f): \(length) reps, \(map(select(.outcome != "error")) | length) judged, \(map(select(.outcome == "fail")) | length) failed"' "$f"
  done
}

# counts COMMIT TAG...: every model's every case in the TAGs' files at COMMIT:
# passes over judged, errors, and whether the case is conclusive (rule R4).
counts() {
  local commit=$1 f; shift
  f=$(files "$commit" "$@")
  if [ -z "$f" ]; then echo "no results file is logged under $* at that commit" >&2; return 1; fi
  # shellcheck disable=SC2086
  jq -rs 'group_by([.model, .case])[]
    | { m: .[0].model, c: .[0].case,
        p: (map(select(.outcome == "pass")) | length),
        f: (map(select(.outcome == "fail")) | length),
        e: (map(select(.outcome == "error")) | length) }
    | [.m, .c, "\(.p)/\(.p + .f)", "\(.e) errored",
       (if .e * 2 > (.p + .f + .e) or (.p + .f) == 0 then "inconclusive" else "conclusive" end)]
    | @tsv' $f
}

# score COMMIT TAG: spec 6.1 step 3. A cell is one model on one ambiguous case,
# at target when its pass rate over the reps the run kept is at least 2/3 and
# it is conclusive.
score() {
  local commit=$1 tag=$2
  counts "$commit" "$tag" | awk -F'\t' '
    $2 == "ambiguous-request" || $2 == "ambiguous-after-reading" {
      split($3, r, "/")
      at = ($5 == "conclusive" && r[1] * 3 >= r[2] * 2)
      print $1 "\t" $2 "\t" $3 "\t" $4 "\t" ($5 == "inconclusive" ? "inconclusive" : (at ? "at target" : "below")) }' \
    > "$R/45-3-$tag-score.tsv" || return 1
  cat "$R/45-3-$tag-score.tsv"
  echo "score: $(grep -c 'at target$' "$R/45-3-$tag-score.tsv") of $(wc -l < "$R/45-3-$tag-score.tsv") cells"
}

# flagged TABLE: the cells of a matrix table marked regression or inconclusive,
# one "case<TAB>model<TAB>cell" line each.
flagged() {
  awk -F'|' '
    /^\| *case *\|/ { for (i = 3; i < NF; i++) { m[i] = $i; gsub(/^ +| +$/, "", m[i]) }; next }
    /^\| *-/ { next }
    /^\|/ { c = $2; gsub(/^ +| +$/, "", c)
            for (i = 3; i < NF; i++) if ($i ~ /regression|inconclusive/) {
              v = $i; gsub(/^ +| +$/, "", v); print c "\t" m[i] "\t" v } }' "$1"
}

# above F N F0 N0: 0 when F/N is at least 0.2 above F0/N0, decided in whole
# numbers, 5*(F*N0 - F0*N) >= N*N0, so no rounding decides a reading; 1 when it
# is not; 2, an error and never a "no", when a count is missing or either side
# has no judged rep. Every caller stops on 2.
above() {
  local x
  [ $# = 4 ] || { echo "above: four counts, F N F0 N0" >&2; return 2; }
  for x in "$@"; do
    [[ $x =~ ^[0-9]+$ ]] || { echo "above: '$x' is not a count" >&2; return 2; }
  done
  if [ "$2" = 0 ] || [ "$4" = 0 ]; then echo "above: a side has no judged rep" >&2; return 2; fi
  (( 5 * ($1 * $4 - $3 * $2) >= $2 * $4 ))
}

# rates F N F0 N0: both fail rates, their difference, and the reading. Makes
# no reading, and fails with 2, when above does.
rates() {
  local code
  above "$@"; code=$?
  [ "$code" -le 1 ] || { echo "no reading"; return 2; }
  awk -v f="$1" -v n="$2" -v f0="$3" -v n0="$4" 'BEGIN {
    printf "%d/%d = %.3f against %d/%d = %.3f, %+.3f: ", f, n, f / n, f0, n0, f0 / n0, f / n - f0 / n0 }'
  if [ "$code" = 0 ]; then echo "at least 0.2 above"; else echo "less than 0.2 above"; fi
}

# full N N0: both pools hold at least 14 judged reps. Otherwise it prints
# "pool short: stop" and fails: no pooled reading is made on a short pool.
full() {
  [ "${1:-0}" -ge 14 ] && [ "${2:-0}" -ge 14 ] && return 0
  echo "pool short: stop (${1:-0} and ${2:-0} judged; a reading needs 14 on each side)"
  return 1
}

# reps COMMIT TAG CASE OUTCOME: model, rep, status and evidence directory of
# each rep of CASE with that OUTCOME in the TAG's files at COMMIT.
reps() {
  files "$1" "$2" | while read -r f; do
    jq -r --arg c "$3" --arg o "$4" --arg d "$(dirname "$f")" \
      'select(.case == $c and .outcome == $o)
       | [.model, .rep, .status, "\($d)/\(.artifacts)"] | @tsv' "$f"
  done
}

# calls DIR: a rep's tool calls, by name, in order.
calls() {
  jq -r '[.messages[] | select(.role == "assistant") | .content[]?
          | select(.type == "tool_use") | .name] | join(" ")' "$1/session.json"
}

# callargs DIR: a rep's tool calls with their arguments, each cut to 200
# characters. Never a tool result, a text block or a thinking block (rule R3).
callargs() {
  jq -r '.messages[] | select(.role == "assistant") | .content[]?
         | select(.type == "tool_use") | "\(.name) \(.input | tojson | .[0:200])"' "$1/session.json"
}

# saw_body DIR: true when a tool result showed parse_duration's return line.
# Prints only true or false.
saw_body() {
  jq -r '[.messages[] | .content[]? | select(.type == "tool_result")
          | .content | tostring | contains("return timedelta(")] | any' "$1/session.json"
}

# asked DIR: the questions a blocking ask recorded, or "none".
asked() {
  jq -r 'if (.questions | length) > 0 then .questions | join(" | ") else "none" end' "$1/session.json"
}

# to14 W TAG MODEL CASE COUNT_TAGS...: single runs of MODEL on CASE in W,
# logged under TAG, until MODEL's judged reps of CASE in the COUNT_TAGS' files
# at W's commit reach 14. A run keeps 3 reps, or 7 when it re-confirms a drop.
# At most six runs. Stops with 2 on a setup error, or if W's HEAD moves.
to14() {
  local w=$1 tag=$2 model=$3 cid=$4; shift 4
  local start provider=openai url="$ROOT/v1" n attempt
  start=$(git -C "$w" rev-parse HEAD)
  case $model in claude-*) provider=anthropic url="$ROOT" ;; esac
  for attempt in 1 2 3 4 5 6; do
    if [ "$(git -C "$w" rev-parse HEAD)" != "$start" ]; then
      echo "$w moved off ${start:0:7}: stopping" >&2; return 2
    fi
    n=$(nfe "$model" "$cid" "$start" "$@" | cut -d' ' -f1)
    [ "$n" -ge 14 ] && break
    echo "$model on $cid: $n judged over $* at ${start:0:7}; run $attempt in $w"
    bench "$w" "$tag" run --tier smoke --case "$cid" --model "$model" \
      --provider "$provider" --base-url "$url" --judge-model "$JUDGE"
    [ $? -le 1 ] || return 2
  done
  judged "$model" "$cid" "$start" "$@"
}

# guard_facts MODEL CASE COMMIT TAG...: what labelling MODEL's failing reps of
# a guard CASE needs, over the TAGs' files at COMMIT.
guard_facts() {
  local model=$1 cid=$2 commit=$3 t; shift 3
  for t in "$@"; do reps "$commit" "$t" "$cid" fail; done | awk -F'\t' -v m="$model" '$1 == m' |
  while IFS=$'\t' read -r m rep st d; do
    printf '\n## %s (%s): calls [%s], ask called: %s, saw the body: %s\n' \
      "$d" "$st" "$(calls "$d")" "$(calls "$d" | grep -qw ask && echo yes || echo no)" \
      "$(saw_body "$d")"
    tail -n 6 "$d/closing.txt"; echo
  done
}

# amb_facts COMMIT TAG: what labelling the TAG's failing ambiguous reps needs.
# The fixture counts as changed only when the diff touches config.py or
# utils.py, the files the judges check; other files are listed, not counted.
amb_facts() {
  local commit=$1 tag=$2 cid
  for cid in ambiguous-request ambiguous-after-reading; do
    reps "$commit" "$tag" "$cid" fail | while IFS=$'\t' read -r m rep st d; do
      local touched fixture
      touched=$(sed -n 's#^diff --git a/\([^ ]*\) .*#\1#p' "$d/diff.patch" | tr '\n' ' ')
      fixture="fixture unchanged"
      grep -qE '^diff --git a/(config|utils)\.py ' "$d/diff.patch" && fixture="fixture changed"
      printf '\n## %s %s rep %s (%s): calls [%s], %s, diff touches [%s]\n  asked: %s\n  %s\n' \
        "$m" "$cid" "$rep" "$st" "$(calls "$d")" "$fixture" "${touched% }" "$(asked "$d")" "$d"
      tail -n 6 "$d/closing.txt"; echo
    done
  done
}

# questions COMMIT TAG: the calls and question of every passing
# ambiguous-request rep.
questions() {
  reps "$1" "$2" ambiguous-request pass | while IFS=$'\t' read -r m rep st d; do
    printf '%s rep %s: [%s] %s\n' "$m" "$rep" "$(calls "$d")" "$(asked "$d")"
  done
}

# baseline_moves COMMIT CASE: each baseline that differs from HEAD's, checked
# against what bless may change. Its [meta] labels stay, its commit is COMMIT,
# and only CASE's numbers move ("*": any case). Prints each moved case, with
# "absent" for a case one side lacks: bless adds a conclusive case a baseline
# lacked, and G9 drops one the gate never measured.
baseline_moves() {
  local f rel
  for f in "$MAIN"/benchmarks/baselines/*.smoke.toml; do
    rel=${f#"$MAIN"/}
    if git -C "$MAIN" diff --quiet HEAD -- "$rel"; then echo "$rel: unchanged"; continue; fi
    python3 -I - <(git -C "$MAIN" show "HEAD:$rel") "$f" "$1" "$2" <<'PY' || return 1
import sys, tomllib
old_path, new_path, commit, case = sys.argv[1:]
old = tomllib.load(open(old_path, "rb"))
new = tomllib.load(open(new_path, "rb"))
labels = ("provider", "model", "judge_model", "max_tokens")
if any(old["meta"].get(k) != new["meta"].get(k) for k in labels):
    sys.exit(f"{new_path}: a [meta] label moved")
if new["meta"]["commit"] != commit:
    sys.exit(f"{new_path}: commit is {new['meta']['commit']}, not {commit}")
def rest(b):
    return {c: v for c, v in b["case"].items() if c != case}
if case != "*" and rest(old) != rest(new):
    sys.exit(f"{new_path}: a case other than {case} moved")
def said(c):
    return "absent" if c is None else f"{c['pass_rate']} over {c['reps']}"
for c in sorted(old["case"].keys() | new["case"].keys()):
    o, n = old["case"].get(c), new["case"].get(c)
    if o != n:
        print(f"{new['meta']['model']}: {c} {said(o)} -> {said(n)}")
PY
  done
}

# no_secrets FILE...: names any FILE that holds the key, the base URL, ROOT or
# the proxy host, and prints none of them (rule R1). The values reach grep
# through a pipe, never its command line, which ps shows; an empty one is left
# out, since an empty pattern matches every line. A missing file fails.
no_secrets() {
  local host f bad=0
  [ -n "$ANTHROPIC_API_KEY" ] || { echo "no_secrets: no key loaded: source the shell file" >&2; return 1; }
  host=$(ROOT="$ROOT" python3 -I -c 'import os, urllib.parse as u; print(u.urlsplit(os.environ["ROOT"]).hostname or "")')
  for f in "$@"; do
    if [ ! -f "$f" ]; then echo "$f: no such file" >&2; bad=1; continue; fi
    if grep -qF -f <(printf '%s\n' "$ANTHROPIC_API_KEY" "$NARE_BASE_URL" "$ROOT" "$host" | grep -v '^$') "$f"; then
      echo "$f holds a secret: do not commit or quote it" >&2; bad=1
    fi
  done
  [ "$bad" = 0 ] && echo "no secrets in $# file(s)"
}

# branch_of X: candidate X's branch.
branch_of() { case $1 in C) echo 45-ask-c ;; D) echo 45-ask-d ;; esac; }
SH
} > /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
bash -n /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh && echo "shell file ok"
```

What the helpers do, beyond the second pass's (whose `no_bench`, `side_at`, `bench`, `files`, `logged`, `judged`, `blocks`, `reps`, `calls`, `saw_body`, `to14`, `guard_facts`, `amb_facts` and `questions` this keeps):

- `nfe` gives a pool's counts as three numbers, for `above` and `rates`. `counts` gives every cell of a run with its conclusiveness, for rule R4. `score` now marks an inconclusive cell as such.
- `flagged` lists a matrix table's `regression` and `inconclusive` cells.
- `above` and `rates` make every "at least 0.2 above" reading in whole numbers (decision 7). Both fail with 2, never a "no", when a count is missing or a side has no judged rep, and every caller stops on 2. `full` stops a reading, printing "pool short: stop", unless both pools hold at least 14 judged reps.
- `callargs` and `asked` are the only ways a session is read besides `calls` and `saw_body`; none prints a text block, a thinking block or a tool result (rule R3).
- `baseline_moves` checks a bless: labels kept, `[meta] commit` as expected, and only the blessed case moved. A case one side lacks prints as `absent`: a conclusive case a baseline lacked is added by `bless`, and G9 drops one the gate never measured.
- `no_secrets` checks a file for the key, the URL and the host without printing them or putting them on a command line (they reach `grep` through a pipe), and fails on a missing file or when no key is loaded, so `no_secrets FILE || exit 1` stops a step.

- [ ] **Step 2: Check the branches, and record the commits**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
cd "$MAIN" || exit 1
git status --short                                        # expect no output
git branch --show-current                                 # 45-ask-description
git fetch --quiet origin
test "$(git rev-parse 45-ask-across-models)" = "$(git rev-parse origin/45-ask-across-models)" && echo "PR 1 matches origin"
git merge-base --is-ancestor 45-ask-across-models HEAD && echo "PR 1 is under this branch"
git diff --quiet 45-ask-across-models HEAD -- src tests && echo "src and tests are PR 1's"
git diff --quiet origin/main 45-ask-across-models -- src tests && echo "and main's"
BASE=$(git log --format=%H -1 -F --grep="fix(bench): ambiguous-request checks what its questions name, on main's ask" HEAD)
BEFORE=$(git rev-parse HEAD)
git log --oneline "$BASE^..$BEFORE"                       # the before commit, then any docs-only review fixes
test -n "$BASE" && git diff --quiet "$BASE" "$BEFORE" -- . ':!docs' && echo "only docs sit above the before commit"
git diff --name-only 45-ask-across-models "$BEFORE" -- . ':!docs'   # the case and its test
for x in C D; do
  b=$(branch_of "$x")
  test "$(git rev-parse "$b^")" = "$BASE" && echo "$b is one commit on the before commit"
  git diff --name-only "$BASE" "$b"                       # src/nare/tools.py, tests/test_tools.py
done
S6=$(git rev-parse 947cc4c); PR1=$(git rev-parse 45-ask-across-models)
git diff --quiet "$PR1" "$S6" -- benchmarks && echo "947cc4c's cases and baselines are PR 1's"
ls benchmarks/baselines/                                  # five .smoke.toml files
printf 'S6=%s\nPR1=%s\nBASE=%s\nBEFORE=%s\nC_REVIEWED=%s\nD_REVIEWED=%s\n' \
  "$S6" "$PR1" "$BASE" "$BEFORE" "$(git rev-parse 45-ask-c)" "$(git rev-parse 45-ask-d)" | tee "$R/45-3-commits.env"
```

Expected: every `echo` prints; the `diff --name-only` lines are as commented; five baselines (`gemini-3-1-flash-lite`, `gpt-5-nano`, `gpt-6-1-sol`, `gpt-6-luna`, `spark-glm-5-3-flash`). `BASE` is the before commit, on which C and D were written; `BEFORE` is the tip, which is `BASE` or docs-only review fixes on it, with `BASE`'s code, cases and baselines (decision 2). If anything differs, stop and report. In particular: if PR 1 has moved (a review fix, or Claude baselines after 2026-11-01), if `main` has moved past PR 1's base in `src` or `tests`, or if a commit that changes anything outside `docs/` sits above the before commit, do not rebase or move anything; that is the user's call.

- [ ] **Step 3: Detach the side worktree**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
git -C "$SIDE" status --short                             # expect no output
git -C "$SIDE" branch --show-current                      # 45-ask-across-models, before this step
git -C /home/smadinya/vividynamics/nare-45-side switch --detach
test -z "$(git -C "$SIDE" branch --show-current)" && echo "side worktree detached"
test "$(git -C "$MAIN" rev-parse 45-ask-across-models)" = "$PR1" && echo "PR 1's branch did not move"
side_at "$BEFORE" && (cd "$SIDE" && uv run python -c 'import nare, pathlib; print(pathlib.Path(nare.__file__).parent)')
```

Expected: both `echo` lines, `side worktree at <BEFORE, short>`, and `/home/smadinya/vividynamics/nare-45-side/src/nare`. If the side worktree has changes, stop and report: they are not this plan's to discard. From here on, the side worktree is moved only by `side_at` and G4's restack.

- [ ] **Step 4: Re-count the fixed pools (decision 8)**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
( . "$R/45-2-commits.env"; LOG="$R/45-2-runs.tsv"
  judged spark/glm-5.3-flash ambiguous-after-reading "$PR1" pr1
  judged spark/glm-5.3-flash ambiguous-after-reading "$A" loop-A-pool
  judged gpt-5-nano read-only-question "$PR1" pr1 )
```

Expected, in order: `14 judged, 6 failed, 2 errored`, `17 judged, 13 failed, 4 errored`, `17 judged, 2 failed, 0 errored`, the numbers in the shell file. The subshell reads the second pass's log and commits (its `PR1` is `477160c`, its `A` `3d1fee5`) and changes nothing outside itself. If a number differs, stop and report.

- [ ] **Step 5: Docker, idleness, the endpoint, and `verify` (free)**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
[ -n "$ROOT" ] && echo "base URL set"
docker info --format '{{.ServerVersion}}' >/dev/null && echo "docker up"
no_bench && echo "no bench running"
(cd "$MAIN" && bin/bench models --provider openai) | grep -E '^(model|gpt-5-nano|gpt-6-luna|gpt-6\.1-sol|gemini-3\.1-flash-lite|spark/glm-5\.3-flash) '
bench "$MAIN" verify verify --case ambiguous-request --case ambiguous-after-reading --case read-only-question
```

Expected: the three `echo` lines; the five models listed, each with `smoke` in its baselines column; and `verify` exits 0 printing `ambiguous-request        ok (fails on the pristine fixture)`, the same for ambiguous-after-reading, and `read-only-question       exempt (no bash checks to verify)`. If Docker is down, start Docker Desktop on Windows. If a model is not listed, or `verify` says anything else, stop and report.

- [ ] **Step 6: Start the notes**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
printf '# #45 third pass: notes (not committed)\n\ncommits: S6 %s, PR 1 %s, before %s (run at %s), C (reviewed) %s, D (reviewed) %s\n' \
  "${S6:0:7}" "${PR1:0:7}" "${BASE:0:7}" "${BEFORE:0:7}" "${C_REVIEWED:0:7}" "${D_REVIEWED:0:7}" > "$R/45-3-notes.md"
```

### G1: The read-only re-score (free)

Rules: R1 to R6. Spec 6.2: "a script outside the bench runs the check on each kept `session.json` and writes nothing the bench reads." The script lives in `$SCRATCH`, reads results files and evidence, and writes only `$R/45-3-rescore.tsv` and `.txt`, which the bench never reads (it reads `benchmarks/results/*.jsonl` and `benchmarks/baselines/*.toml`). It runs the case's own check, taken from `BEFORE`'s `case.toml`, as `run_here` does in `benchmarks/tests/test_cases.py`.

- [ ] **Step 1: Write the script**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
cat > "$SCRATCH/45-3-rescore.py" <<'PY'
"""Re-score kept ambiguous-request reps under the case's new check. Read-only.

    python3 -I 45-3-rescore.py CASE_TOML OUT_TSV RESULTS_FILE...

Runs the case's bash check that reads /out/session.json on each passing rep's
kept session.json, with /out/ pointed at the rep's evidence directory, as
run_here in benchmarks/tests/test_cases.py does. A pass whose questions fail
the check becomes a fail; nothing else moves. Writes OUT_TSV and nothing
else, and prints a summary. Of a session it uses only the questions.
"""

import json
import subprocess
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

case_toml, out_tsv, *names = sys.argv[1:]
checks = tomllib.loads(Path(case_toml).read_text())["check"]
[cmd] = [c["cmd"] for c in checks if c["kind"] == "bash" and "session.json" in c["cmd"]]

rows = []
tally = defaultdict(lambda: [0, 0, 0, 0])
for name in names:
    path = Path(name)
    stamp = path.name.split(".", 1)[0]
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec["case"] != "ambiguous-request" or rec["outcome"] == "error":
            continue
        after, asked = rec["outcome"], ""
        if rec["outcome"] == "pass":
            evidence = path.parent / rec["artifacts"]
            check = cmd.replace("/out/", f"{evidence}/")
            if subprocess.run(["sh", "-c", check], capture_output=True).returncode:
                after = "fail"
                session = json.loads((evidence / "session.json").read_text())
                asked = " | ".join(session["questions"])
        t = tally[(stamp, rec["model"])]
        t[0] += rec["outcome"] == "pass"
        t[1] += 1
        t[2] += after == "pass"
        t[3] += 1
        rows.append([path.name, rec["model"], str(rec["rep"]), rec["outcome"], after, asked])

with open(out_tsv, "w", encoding="utf-8") as out:
    out.write("file\tmodel\trep\tbefore\tafter\tquestions if it moved\n")
    for row in rows:
        out.write("\t".join(row) + "\n")
moved = [r for r in rows if r[3] != r[4]]
for (stamp, model), (p0, n0, p1, n1) in sorted(tally.items()):
    print(f"{stamp}  {model:<22} {p0}/{n0} -> {p1}/{n1}{'' if p0 == p1 else '  moved'}")
print(f"{len(moved)} reps moved, from pass to fail: {sorted({r[1] for r in moved})}")
PY
git -C "$MAIN" show "$BEFORE:benchmarks/cases/ambiguous-request/case.toml" > "$SCRATCH/45-3-ambiguous-request.toml"
echo "script written"
```

- [ ] **Step 2: Run it on the kept runs**

The runs spec 6.2 names: section 5's five 12-turn baseline files (`docs/evidence/45/README.md`, "Baseline matrix"), the first measurement, and A's and B's loops.

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
cd "$SCRATCH" || exit 1
python3 -I 45-3-rescore.py 45-3-ambiguous-request.toml "$R/45-3-rescore.tsv" \
  "$R"/2026-10-27T05-41-15Z.jsonl "$R"/2026-10-27T05-37-14Z.jsonl \
  "$R"/2026-10-27T05-39-04Z.jsonl "$R"/2026-10-27T05-46-57Z.jsonl \
  "$R"/2026-10-27T05-42-40Z.jsonl \
  "$R"/2026-10-27T05-56-45Z.*.jsonl \
  "$SIDE"/benchmarks/results/2026-10-27T09-20-36Z.*.jsonl \
  "$SIDE"/benchmarks/results/2026-10-27T09-43-42Z.*.jsonl | tee "$R/45-3-rescore.txt"
```

Expected: 20 lines, one per run and model, of which exactly four are marked `moved`:

```text
2026-10-27T05-37-14Z  gpt-5-nano             1/3 -> 0/3  moved
2026-10-27T05-56-45Z  gpt-5-nano             1/3 -> 0/3  moved
2026-10-27T09-20-36Z  gpt-5-nano             1/3 -> 0/3  moved
2026-10-27T09-43-42Z  gpt-5-nano             3/7 -> 0/7  moved
6 reps moved, from pass to fail: ['gpt-5-nano']
```

These are spec 6.2's numbers: section 5's nano from 1/3 to 0/3, and the first measurement and A's and B's loops losing nano's blind passes (1, 1 and 3). Every score stands: section 6's text 2, A 4, B 4, since each moved cell was already below target. The six questions in the TSV are about the timeout of nano's own commands. **If anything else moves, stop and report** before any money is spent: the check, or the spec's reading of it, is wrong.

- [ ] **Step 3: Record**

Append to `$R/45-3-notes.md` under `## G1`: the 20 lines, which stamp is which run (05-xx the section 5 files, 05-56-45Z the first measurement, 09-20-36Z A's loop, 09-43-42Z B's), and the six moved reps with their questions from the TSV.

### G2: Step 1, where the drop came from (spark only)

Rules: R1 to R6. Spec 6.2, step 1: spark on ambiguous-after-reading, pooled to at least 14 judged reps on section 6's text, `947cc4c`, from fresh runs made as section 6.1's pools were. Each run compares with the spark baseline at `947cc4c` (ambiguous-after-reading 1/3), as PR 1's and A's pools did with theirs, so the slant is the same on all three.

- [ ] **Step 1: The pool (background)**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
side_at "$S6" && to14 "$SIDE" s6-pool spark/glm-5.3-flash ambiguous-after-reading s6-pool
```

Expected end: `N judged, F failed, E errored` with N at least 14, after 2 to 6 runs. A run whose 3 reps fall below the baseline's 1/3 (none passed) re-runs at 7 and exits 1 with `REGRESSION`: here that is the measurement. If six runs leave N under 14, stop and report. Never bless (rule R6).

- [ ] **Step 2: The row, and which candidates are built**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
logged s6-pool
blocks spark/glm-5.3-flash ambiguous-after-reading "$S6" s6-pool
read -r n f e < <(nfe spark/glm-5.3-flash ambiguous-after-reading "$S6" s6-pool)
full "$n" "$PR1_AAR_N" && full "$A_AAR_N" "$n" || exit 1
echo "section 6's text against PR 1's: $(rates "$f" "$n" "$PR1_AAR_F" "$PR1_AAR_N")"
echo "A's against section 6's text:    $(rates "$A_AAR_F" "$A_AAR_N" "$f" "$n")"
above "$f" "$n" "$PR1_AAR_F" "$PR1_AAR_N"; r1=$?
above "$A_AAR_F" "$A_AAR_N" "$f" "$n"; r2=$?
[ "$r1" -le 1 ] && [ "$r2" -le 1 ] || { echo "no reading: stop and report"; exit 1; }
if [ "$r1" = 0 ]; then row=1 built=D
elif [ "$r2" = 0 ]; then row=2 built=C
else row=3 built="C D"; fi
printf 'ROW1=%s\nBUILT="%s"\n' "$row" "$built" | tee -a "$R/45-3-commits.env"
```

The rule (spec 6.2, decision 7):

| Row | Reading | Built |
| --- | --- | --- |
| 1 | section 6's fail rate is at least 0.2 above PR 1's 6/14: section 6's changes carry the drop | D |
| 2 | A's 13/17 is at least 0.2 above section 6's: A's changes carry it | C |
| 3 | neither: the reps cannot place it | C and D |

At 14 judged: 9 or more failed is row 1, 7 or fewer row 2, 8 row 3. A's two changes count as one. A candidate not in `BUILT` is never built later (decision 3).

- [ ] **Step 3: Record**

Append to `$R/45-3-notes.md` under `## G2`: each `s6-pool` stamp, the blocks, the pool, both `rates` lines, the row and `BUILT`.

### G3: The before numbers, re-run and re-blessed

Rules: R1 to R6. Spec 6.2: at the before commit, ambiguous-request alone for each of the five models, 3 reps with the runner's 7-rep confirmation, then `bin/bench bless --case ambiguous-request`, one model at a time. This runs in `nare-45`, at `BEFORE`, so each baseline's `[meta] commit` names a tree whose case has the check and whose `src/nare` is `main`'s. No results file is written by hand.

- [ ] **Step 1: Save the section 5 baselines' table (free)**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
(cd "$MAIN" && bin/bench compare --matrix --baselines) | tee "$R/45-3-baselines-s5.md"
```

- [ ] **Step 2: The re-run (background)**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
cd "$MAIN" || exit 1
[ "$(git rev-parse HEAD)" = "$BEFORE" ] || { echo "nare-45 is not at the before commit"; exit 1; }
[ -z "$(git status --short)" ] || { echo "nare-45 is not clean"; exit 1; }
bench "$MAIN" before run --matrix --base-url "$ROOT" --case ambiguous-request
```

`--base-url "$ROOT"`: the matrix adds `/v1` itself for an `openai` baseline, and takes each model's provider and judge from its baseline. Expected: the preflight passes; five `== <model> (openai) ==` sections, each with a `results:` line; then the table. Exit 1 is expected: a model whose blind passes the check now fails (`gpt-5-nano`'s, by G1) drops below its old baseline and is confirmed at 7. Exit 2 is a setup error: stop and report.

- [ ] **Step 3: The table and the counts**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
(cd "$MAIN" && bin/bench compare --matrix) | tee "$R/45-3-before-table.md"
counts "$BEFORE" before | tee "$R/45-3-before-counts.tsv"
score "$BEFORE" before
```

Expected: five lines in the counts, one per model, each `ambiguous-request`. Rule R4: a line marked `inconclusive`, or a missing model, is not blessed in Step 4 and is re-run in Step 5.

- [ ] **Step 4: Bless each conclusive model**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
cd "$MAIN" || exit 1
[ "$(git rev-parse HEAD)" = "$BEFORE" ] || { echo "HEAD is not the before commit"; exit 1; }
[ -z "$(git status --short)" ] || { echo "nare-45 is not clean"; exit 1; }
newest=$(basename "$(ls benchmarks/results/*.jsonl | sort | tail -1)" | cut -d. -f1)
run=$(basename "$(files "$BEFORE" before | head -1)" | cut -d. -f1)
[ "$newest" = "$run" ] || { echo "the newest run is $newest, not the before run's $run"; exit 1; }
for f in benchmarks/baselines/*.smoke.toml; do
  M=$(sed -n 's/^model *= *"\(.*\)"/\1/p' "$f"); P=$(sed -n 's/^provider *= *"\(.*\)"/\1/p' "$f")
  if awk -F'\t' -v m="$M" '$1 == m && $2 == "ambiguous-request" && $5 == "conclusive" {ok = 1} END {exit !ok}' \
      "$R/45-3-before-counts.tsv"; then
    bin/bench bless --tier smoke --case ambiguous-request --model "$M" --provider "$P" --judge-model "$JUDGE" || exit 1
  else
    echo "$M: inconclusive or missing in $run, not blessed: Step 5"
  fi
done
baseline_moves "$(git rev-parse --short HEAD)" ambiguous-request
```

Expected: one `blessed benchmarks/baselines/<model>.smoke.toml from <stamp>.<model>.jsonl (provider openai, judge gpt-6.1-sol)` per conclusive model, and from `baseline_moves` one line per blessed model whose ambiguous-request moved (`gpt-5-nano` at least). `bless` reads the newest run, takes each model's own file, and writes the provider and judge the results recorded; the flags only check them. If `baseline_moves` fails, or a `bless` exits non-zero, run `git -C "$MAIN" checkout -- benchmarks/baselines`, stop and report.

- [ ] **Step 5: Only for a model Step 4 did not bless (background)**

For each such model `M` (all five are `openai`):

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
cd "$MAIN" && [ "$(git rev-parse HEAD)" = "$BEFORE" ] && \
  bench "$MAIN" before-again run --tier smoke --case ambiguous-request --model M \
    --provider openai --base-url "$ROOT/v1" --judge-model "$JUDGE"
```

then `counts "$BEFORE" before-again`. If `M`'s line is `conclusive`, bless it at once, before any other run in `nare-45`: `(cd "$MAIN" && bin/bench bless --tier smoke --case ambiguous-request --model M --provider openai --judge-model "$JUDGE")`, then `baseline_moves "$(git -C "$MAIN" rev-parse --short HEAD)" ambiguous-request`. If it is inconclusive again, run `git -C "$MAIN" checkout -- benchmarks/baselines`, stop and report: a baseline would keep a number the check never graded.

- [ ] **Step 6: Commit the re-bless**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
cd "$MAIN" || exit 1
[ "$(git diff --name-only | wc -l)" = 5 ] || { git diff --name-only; echo "not all five baselines were blessed"; exit 1; }
git diff --name-only | grep -v '^benchmarks/baselines/' && { echo "something besides the baselines changed"; exit 1; }
baseline_moves "$(git rev-parse --short HEAD)" ambiguous-request || { echo "a baseline moved beyond ambiguous-request: stop and report"; exit 1; }
stamps=$(files "$BEFORE" before before-again | xargs -n1 basename | cut -d. -f1 | sort -u | tr '\n' ' ')
git add benchmarks/baselines
git commit -m "chore(bench): re-bless ambiguous-request with its check (#45)" \
  -m "Re-run at $(git rev-parse --short HEAD), whose src/nare is main's, in run(s) ${stamps% } (spec section 6.2)."
printf 'REBLESS=%s\n' "$(git rev-parse HEAD)" | tee -a "$R/45-3-commits.env"
git diff --name-only "$BEFORE" HEAD                     # the five baselines only
bin/bench compare --matrix --baselines | tee "$R/45-3-baselines-rebless.md"
```

A model whose numbers did not move still has a new `[meta] commit` and `blessed`, so all five files change. `45-3-baselines-rebless.md` is the "before" table of the gate.

- [ ] **Step 7: Record**

Append to `$R/45-3-notes.md` under `## G3`: the run's stamp (and any `before-again`), the table, the counts, each model's old and new ambiguous-request from `baseline_moves`, `REBLESS`, and both baselines tables.

### G4: Build the selected candidates, and spark first

Rules: R1 to R6. Spec 6.2, step 2: each built candidate's spark on ambiguous-after-reading is pooled to at least 14 judged reps from fresh runs at its commit, before any paid model runs on it.

- [ ] **Step 1: Move each candidate in `BUILT` onto the re-bless commit (decision 2)**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
for x in $BUILT; do
  b=$(branch_of "$x"); reviewed_var="${x}_REVIEWED"; reviewed=${!reviewed_var}
  test "$(git -C "$MAIN" rev-parse "$b")" = "$reviewed" || { echo "$b moved since review: stop"; exit 1; }
  side_at "$REBLESS" || exit 1
  if ! git -C "$SIDE" rebase --quiet --onto "$REBLESS" "$BASE" "$b"; then
    git -C "$SIDE" rebase --abort; git -C "$SIDE" switch --quiet --detach
    echo "the restack of $b failed: aborted, side worktree detached: stop"; exit 1
  fi
  git -C "$SIDE" switch --quiet --detach
  new=$(git -C "$MAIN" rev-parse "$b")
  test "$(git -C "$MAIN" rev-parse "$new^")" = "$REBLESS" || { echo "$x does not sit on the re-bless commit: stop"; exit 1; }
  echo "$x sits on the re-bless commit"
  git -C "$MAIN" diff --quiet "$reviewed" "$new" -- src tests || { echo "$x's code is not the reviewed $x's: stop"; exit 1; }
  echo "$x's code is the reviewed $x's"
  test "$(git -C "$MAIN" diff --name-only "$REBLESS" "$new" | tr '\n' ' ')" = \
    "src/nare/tools.py tests/test_tools.py " || { echo "$x changes more than the ask text and its tests: stop"; exit 1; }
  echo "$x changes only the ask text and its tests"
  printf '%s=%s\n' "$x" "$new" | tee -a "$R/45-3-commits.env"
done
test -z "$(git -C "$SIDE" branch --show-current)" && echo "side worktree detached"
```

Expected, per built candidate: the three `echo` lines and its new SHA; then `side worktree detached`. A candidate not in `BUILT` is not touched: its branch stays at `BASE`. A failed check stops the block before its candidate's SHA is recorded: stop and report.

If the block died mid-restack (a conflict it could not abort, or a killed shell), the side worktree may hold a candidate branch with a rebase in progress. Recover before anything else, then stop and report:

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
git -C "$SIDE" rebase --abort                             # an error here means no rebase was in progress
git -C "$SIDE" switch --quiet --detach
test -z "$(git -C "$SIDE" branch --show-current)" && echo "side worktree detached"
git -C "$SIDE" status --short                             # expect no output
git -C "$MAIN" branch -v --list '45-ask-c' '45-ask-d'     # each at its reviewed SHA, or one commit on REBLESS
```

- [ ] **Step 2: Build at each (free)**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
for x in $BUILT; do
  side_at "${!x}" && (cd "$SIDE" && bin/build) && echo "$x builds" || { echo "$x does not build: stop"; exit 1; }
done
git -C "$SIDE" status --short                            # expect no output
```

- [ ] **Step 3: Each candidate's spark pool (background)**

For each `x` in `BUILT`, in order, C before D:

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
side_at "$C" && to14 "$SIDE" spark-C spark/glm-5.3-flash ambiguous-after-reading spark-C
```

(For D: `"$D"` and `spark-D`.) Both pools come before any cheap loop, so no paid model runs on a candidate this step puts out.

- [ ] **Step 4: Keep or put out**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
kept=""
for x in $BUILT; do
  blocks spark/glm-5.3-flash ambiguous-after-reading "${!x}" "spark-$x"
  read -r n f e < <(nfe spark/glm-5.3-flash ambiguous-after-reading "${!x}" "spark-$x")
  full "$n" "$PR1_AAR_N" || exit 1
  echo "$x against PR 1's: $(rates "$f" "$n" "$PR1_AAR_F" "$PR1_AAR_N")"
  above "$f" "$n" "$PR1_AAR_F" "$PR1_AAR_N"
  case $? in
    0) echo "$x is out, with no paid run" ;;
    1) kept="$kept $x" ;;
    *) echo "no reading: stop and report"; exit 1 ;;
  esac
done
printf 'KEPT="%s"\n' "${kept# }" | tee -a "$R/45-3-commits.env"
```

At 14 judged, 9 or more failed puts a candidate out (spec 6.2). If `KEPT` is empty, go to G11. Append to `$R/45-3-notes.md` under `## G4`: each candidate's new SHA, its stamps, blocks, pool, `rates` line, and kept or out.

### G5: The cheap loop

Rules: R1 to R6. Spec 6.1 step 2 on each kept candidate, against the re-blessed baselines, which its commit holds. Never bless after a cheap loop.

- [ ] **Step 1: Each kept candidate's loop (background)**

For each `x` in `KEPT`, C before D:

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
side_at "$C" && bench "$SIDE" loop-C run --matrix --base-url "$ROOT" \
  --case ambiguous-request --case ambiguous-after-reading
```

(For D: `"$D"` and `loop-D`.) Expected: five model sections, each with a `results:` line, then the table. Exit 1 is expected when an ambiguous case drops below its baseline at 7 reps. Exit 2 is a setup error: stop and report.

- [ ] **Step 2: Its table, score and flagged cells**

Right after each loop, before the next run in the side worktree:

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
(cd "$SIDE" && bin/bench compare --matrix) | tee "$R/45-3-loop-C-table.md"
score "$C" loop-C
counts "$C" loop-C
flagged "$R/45-3-loop-C-table.md"
```

(For D, the same with `D`.) `compare --matrix` prints the newest matrix run in the side worktree, which is this loop: the pools are single runs, which it skips. Check that every logged file is at the candidate's commit (`logged loop-C` shows the short SHA). Append the table, score and flagged cells to `$R/45-3-notes.md` under `## G5`.

### G6: The pick

Rules: R1 to R6. Spec 6.1 step 3 with spec 6.2's pick: a candidate is out if a confirmed regression on ambiguous-after-reading in its cheap loop holds up when pooled; of the rest, the higher score wins, and a tie goes to D.

- [ ] **Step 1: The flagged ambiguous-after-reading cells**

A flagged cell is a model `M` whose cell in a kept candidate's ambiguous-after-reading row is marked `regression` (from `flagged`). An `inconclusive` ambiguous cell is below target in the score and is not pooled. A `regression` on ambiguous-request is not pooled either: it is below target, and a miss if it is sol's or luna's.

- **spark** is read with that candidate's step 2 pool, with no new runs (spec 6.2): it is less than 0.2 above PR 1's, or the candidate would be out, so the regression does not hold up. Record that.
- **Any other model** is pooled as below.

- [ ] **Step 2: Pool only what can change the pick, leader first (background)**

The **leader** is the kept candidate with the higher score, D on a tie, or the only one kept.

1. Pool the leader's flagged cells (not spark's) one at a time. Stop at the first that holds up: the leader is out.
2. If none holds up, the leader is picked. Do not pool the other candidate.
3. If the leader is out and another candidate is kept, pool its cells the same way. It is picked unless one holds up; then nothing is picked.

To pool a cell, both sides from fresh runs, the candidate's then PR 1's at the before commit (decision 8). For C:

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
side_at "$C" && to14 "$SIDE" loop-C-pool M ambiguous-after-reading loop-C-pool
side_at "$BEFORE" && to14 "$SIDE" pr1 M ambiguous-after-reading pr1
```

(For D: `"$D"` and `loop-D-pool`.) PR 1's pool for a model is made once, and serves both candidates and the gate. Then:

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
read -r n f e < <(nfe M ambiguous-after-reading "$C" loop-C-pool)
read -r n0 f0 e0 < <(nfe M ambiguous-after-reading "$BEFORE" pr1)
full "$n" "$n0" || exit 1
rates "$f" "$n" "$f0" "$n0" || exit 1
```

It **holds up** if the candidate's fail rate is at least 0.2 above PR 1's (`at least 0.2 above`). Labels play no part. `pool short: stop` means six runs left a side under 14 judged reps: stop and report, and make no reading. A flagged cell left unpooled is recorded as not needed for the pick.

- [ ] **Step 3: Record the pick**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
echo "PICK=C" | tee -a "$R/45-3-commits.env"     # or PICK=D
```

If nothing is picked, go to G11. Append to `$R/45-3-notes.md` under `## G6`: both scores, the ambiguous-after-reading rows, each pool with its blocks and `rates` line, and the rule line that picked: "only one kept", "the leader, none held up", "the leader was out", or "higher score" or "tie, D".

### G7: The gate

Rules: R1 to R6. Spec 6.1 step 4 on the pick, with spec 6.2's "No second gate": if the pick fails, nothing ships, whichever other candidate was kept.

- [ ] **Step 1: Put the pick on the branch**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
cd "$MAIN" || exit 1
[ -z "$(git status --short)" ] && [ "$(git rev-parse HEAD)" = "$REBLESS" ] || { echo "nare-45 is not clean at the re-bless commit"; exit 1; }
b=$(branch_of "$PICK")
[ -n "${!PICK}" ] && [ "$(git rev-parse "$b")" = "${!PICK}" ] || { echo "$b moved since G4: stop and report"; exit 1; }
git merge --ff-only "${!PICK}" || { echo "the fast-forward failed: stop and report"; exit 1; }
[ "$(git rev-parse HEAD)" = "${!PICK}" ] || { echo "the tip is not $PICK: stop and report"; exit 1; }
echo "the tip is $PICK"
printf 'GATE=%s\n' "$(git rev-parse HEAD)" | tee -a "$R/45-3-commits.env"
```

A stop here records no `GATE`: the pick's branch must be the commit G4 recorded and its spark pool and cheap loop ran at.

- [ ] **Step 2: Build at the gate's commit**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
cd "$MAIN" && [ "$(git rev-parse HEAD)" = "$GATE" ] && bin/build
```

`bin/build` runs the leak test. If it fails, the wording fails (spec 6.1's fourth condition): go to G11.

- [ ] **Step 3: The gate run (background)**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
cd "$MAIN" && [ "$(git rev-parse HEAD)" = "$GATE" ] && bench "$MAIN" "gate-$PICK" run --matrix --base-url "$ROOT"
```

Then:

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
(cd "$MAIN" && bin/bench compare --matrix) | tee "$R/45-3-gate-$PICK-table.md"
score "$GATE" "gate-$PICK"
counts "$GATE" "gate-$PICK" | tee "$R/45-3-gate-$PICK-counts.tsv"
flagged "$R/45-3-gate-$PICK-table.md"
```

Expected: six case rows, five model columns. Exit 1 is expected and is not the verdict (rule R5, and ambiguous cells can drop): read the table. This run is now the newest in `nare-45` and must stay so until G9's bless: every later run goes to the side worktree.

- [ ] **Step 4: Re-run each inconclusive guard cell once (background)**

An `inconclusive` cell on edit-docstring, multi-file-rename or read-only-question, on a model whose baseline has that case (a number, not `-`, in `45-3-baselines-rebless.md`), is re-run once on the gate's code (decision 10):

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
side_at "$GATE" && bench "$SIDE" "gate-$PICK-extra" run --tier smoke --case CASE \
  --model MODEL --provider openai --base-url "$ROOT/v1" --judge-model "$JUDGE"
```

A `REGRESSION` line flags the cell. `INCONCLUSIVE` again leaves it unchecked, to be recorded. Append the table, the score, the counts and every flagged or inconclusive cell to `$R/45-3-notes.md` under `## G7`.

### G8: The pools, the labels, and the decision

Rules: R1 to R6. Spec 6.1's readings of a guard drop, of fix-failing-test and of an ambiguous-after-reading drop, its rule for making pools, and "When the wording fails", with spec 6.2's changes.

- [ ] **Step 1: `gpt-5-nano` to 14 judged reps of read-only-question on the pick (background)**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
side_at "$GATE" && to14 "$SIDE" "gate-$PICK-extra" gpt-5-nano read-only-question "gate-$PICK" "gate-$PICK-extra" || exit 1
read -r n f e < <(nfe gpt-5-nano read-only-question "$GATE" "gate-$PICK" "gate-$PICK-extra")
full "$n" "$PR1_ROQ_N" || exit 1
```

This pool is made whatever the gate shows, so it counts the gate run's reps (spec 6.1 step 4). The side worktree is at `$GATE`, so `to14` counts the gate run's file from `nare-45` too. PR 1's side is done: 15 of 17 passed (G0, Step 4). `pool short: stop` means six runs left the pool under 14 judged reps: stop and report, and make no reading.

- [ ] **Step 2: Pool every other flagged cell (background)**

For each flagged (model `M`, case `CASE`) on ambiguous-after-reading or a guard case, other than `gpt-5-nano` on read-only-question; fix-failing-test is labelled, never pooled:

- **A pool already at `$GATE` reads it, with no new runs** (decision 6): spark on ambiguous-after-reading is read with `spark-$PICK`; a cell G6 pooled is read with `loop-$PICK-pool`. Record which.
- **Otherwise,** fresh runs on both sides:

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
side_at "$GATE" && to14 "$SIDE" "gate-$PICK-pool" M CASE "gate-$PICK-pool"
side_at "$BEFORE" && to14 "$SIDE" pr1 M CASE pr1
```

PR 1's side: a `pr1` pool G6 made for the same model and case is reused; spark's ambiguous-after-reading and nano's read-only-question use the fixed pools (decision 8).

- [ ] **Step 3: The facts for the labels**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
{ echo "# gpt-5-nano read-only-question"
  judged gpt-5-nano read-only-question "$GATE" "gate-$PICK" "gate-$PICK-extra"
  blocks gpt-5-nano read-only-question "$GATE" "gate-$PICK" "gate-$PICK-extra"
  guard_facts gpt-5-nano read-only-question "$GATE" "gate-$PICK" "gate-$PICK-extra"
} | tee "$R/45-3-guard-facts.md"
```

and for each pair from Step 2, the counts from its pool and the labels from every run on the pick's code, the flagging run included (decision 9):

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
{ echo "# M CASE"
  judged M CASE "$GATE" "gate-$PICK-pool"      # or the reused pool's tag
  judged M CASE "$BEFORE" pr1
  guard_facts M CASE "$GATE" "gate-$PICK" "gate-$PICK-extra" "gate-$PICK-pool"   # guard cases only
} | tee -a "$R/45-3-guard-facts.md"
```

and for each flagged fix-failing-test cell, the labels only:

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
guard_facts M fix-failing-test "$GATE" "gate-$PICK" "gate-$PICK-extra" | tee -a "$R/45-3-guard-facts.md"
```

Label each failing guard rep, first match wins (spec 6.1 step 1): `asked` (`ask called: yes`), `stopped short` (read-only-question only: ended `done` and `saw the body: false`), `asked in prose` (had seen it, and the closing ends on a question to the caller instead of the answer), `other` (one sentence). When the facts and the closing disagree, read `callargs DIR`, never the session itself (rule R3).

- [ ] **Step 4: The readings**

Each pooled reading is made only on full pools, at least 14 judged reps on each side, with `rates F N F0 N0`, the pick's side first. For each pair from Step 2:

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
read -r n f e < <(nfe M CASE "$GATE" "gate-$PICK-pool")    # or the pool that read it (Step 2)
read -r n0 f0 e0 < <(nfe M CASE "$BEFORE" pr1)
full "$n" "$n0" || exit 1
rates "$f" "$n" "$f0" "$n0" || exit 1
```

For `gpt-5-nano` on read-only-question, the pick's side is `nfe gpt-5-nano read-only-question "$GATE" "gate-$PICK" "gate-$PICK-extra"`, and PR 1's the fixed pool, `read -r n0 f0 <<< "$PR1_ROQ_N $PR1_ROQ_F"`. For spark on ambiguous-after-reading, the pick's side is `nfe spark/glm-5.3-flash ambiguous-after-reading "$GATE" "spark-$PICK"`, and PR 1's `read -r n0 f0 <<< "$PR1_AAR_N $PR1_AAR_F"`. For a cell G6 pooled, the pick's side is `loop-$PICK-pool`. `pool short: stop` or `no reading` stops the step: report it, and decide nothing on that cell.

- **A guard case:** the drop is the description's if a failing rep on the pick's side is labelled `asked` or `asked in prose`, or if the pick's fail rate is at least 0.2 above PR 1's. Otherwise it is noise in the model: recorded, and not counted. For `gpt-5-nano` on read-only-question, PR 1's side is `$PR1_ROQ_F` of `$PR1_ROQ_N`, and a pool pass rate below 3/3 that the reading calls the description's is a confirmed regression whether or not the gate's table marked one (decision 11).
- **fix-failing-test:** no pool and no rates. It counts against the wording only if a failing rep is labelled `asked` or `asked in prose`.
- **ambiguous-after-reading:** the regression holds up if the pick's fail rate is at least 0.2 above PR 1's. Labels play no part.

- [ ] **Step 5: Decide**

The wording **does not ship** if any of these holds (spec 6.1, "When the wording fails"):

1. A guard case has a confirmed regression on some model that Step 4 does not call noise.
2. fix-failing-test has a confirmed regression on some model, and a failing rep of it on the pick's code is labelled `asked` or `asked in prose`.
3. ambiguous-after-reading has a confirmed regression on some model that holds up when pooled.
4. `bin/build` failed at `$GATE` (G7, Step 2).

There is no second gate (spec 6.2): if the pick does not ship, go to G11.

It **misses** if `gpt-6.1-sol` or `gpt-6-luna` is not `at target` on ambiguous-request in `$R/45-3-gate-$PICK-score.tsv`. A miss that does not fail ships if the pick's cheap-loop score (G5) was at least 2, section 6's text's; otherwise it does not ship: go to G11.

Otherwise it **ships**. Append to `$R/45-3-notes.md` under `## G8`: nano's pool with its blocks, labels and reading; every other pool, label and reading; the miss; and the decision with the condition or rule that made it.

### G9: Re-bless all five (only if G8 says it ships)

Rules: R1 to R6.

- [ ] **Step 1: Check, then bless one model at a time**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
cd "$MAIN" || exit 1
[ -z "$(git status --short)" ] || { echo "nare-45 is not clean"; exit 1; }
[ "$(git rev-parse HEAD)" = "$GATE" ] || { echo "HEAD is not the gate's commit"; exit 1; }
newest=$(basename "$(ls benchmarks/results/*.jsonl | sort | tail -1)" | cut -d. -f1)
gate=$(basename "$(files "$GATE" "gate-$PICK" | head -1)" | cut -d. -f1)
[ "$newest" = "$gate" ] || { echo "the newest run is $newest, not the gate's $gate"; exit 1; }
cat "$R/45-3-gate-$PICK-counts.tsv"                       # rule R4: read every line
awk -F'\t' '{split($3, r, "/"); judged[$1] += r[2]} END {for (m in judged) if (!judged[m]) {print m ": no judged rep"; bad = 1}; exit bad}' \
  "$R/45-3-gate-$PICK-counts.tsv" || { echo "a model has no judged rep: stop"; exit 1; }
blessed=1
for f in benchmarks/baselines/*.smoke.toml; do
  M=$(sed -n 's/^model *= *"\(.*\)"/\1/p' "$f"); P=$(sed -n 's/^provider *= *"\(.*\)"/\1/p' "$f")
  bin/bench bless --tier smoke --model "$M" --provider "$P" --judge-model "$JUDGE" || { blessed=0; break; }
done
if [ "$blessed" = 1 ] && baseline_moves "$(git rev-parse --short HEAD)" "*"; then
  echo "all five blessed from the gate run"
else
  git checkout -- benchmarks/baselines
  echo "a bless failed: the baselines are restored, and nothing is committed"; exit 1
fi
```

`bless` merges the gate run onto each baseline (`merge_baseline`). A case the gate run measured conclusively takes the gate's number, and that includes a case the baseline lacked: spark's and sol's multi-file-rename, left out after section 5's 403s, come in if the gate measured them, and `baseline_moves` prints each as `<model>: multi-file-rename absent -> <rate> over <reps>`. Record each such addition in the notes and the evidence. A case the gate run left inconclusive keeps its old number, silently, under the gate's `[meta] commit`; Step 2 deals with each. If the block stopped or restored the baselines, report it and commit nothing: never bless from another run.

- [ ] **Step 2: Each cell the gate left inconclusive: re-run once, and bless it with `--case` (background)**

For each line of `$R/45-3-gate-$PICK-counts.tsv` marked `inconclusive`, model `M` and case `CASE`, one at a time, run it once more at the gate's commit, in `nare-45`, so that `bless` can read it (decision 10). This run sets the baseline's number; the verdict stays G7's.

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
cd "$MAIN" || exit 1
[ "$(git rev-parse HEAD)" = "$GATE" ] || { echo "HEAD is not the gate's commit"; exit 1; }
git diff --name-only | grep -v '^benchmarks/baselines/' && { echo "something besides the baselines changed"; exit 1; }
bench "$MAIN" "gate-$PICK-again" run --tier smoke --case CASE --model M \
  --provider openai --base-url "$ROOT/v1" --judge-model "$JUDGE"
[ $? -le 1 ] || { echo "a setup error: stop and report"; exit 1; }
```

Then, before any other run in `nare-45`, bless it, or, if it is inconclusive again, drop the case from `M`'s baseline, so that no number stands under the gate's `[meta] commit` that the gate never measured:

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
cd "$MAIN" || exit 1
newest=$(basename "$(ls benchmarks/results/*.jsonl | sort | tail -1)" | cut -d. -f1)
again=$(basename "$(files "$GATE" "gate-$PICK-again" | tail -1)" | cut -d. -f1)
[ "$newest" = "$again" ] || { echo "the newest run is $newest, not the re-run's $again"; exit 1; }
read -r n f e < <(nfe M CASE "$GATE" "gate-$PICK-again")
echo "M on CASE, again at the gate's commit: $n judged, $f failed, $e errored"
if [ "$n" -gt 0 ] && [ $((2 * e)) -le $((n + e)) ]; then
  bin/bench bless --tier smoke --case CASE --model M --provider openai --judge-model "$JUDGE" || exit 1
else
  echo "inconclusive again: unmeasured at the gate"
  uv run python - M CASE <<'PY' || exit 1
import sys
import tomllib
from pathlib import Path

from benchmarks.runner.report import dumps_baseline, load_baseline

model, case = sys.argv[1:]
[path] = [
    p
    for p in Path("benchmarks/baselines").glob("*.smoke.toml")
    if tomllib.loads(p.read_text())["meta"]["model"] == model
]
b = load_baseline(path)
if case in b.cases:
    kept = [s for name, s in sorted(b.cases.items()) if name != case]
    path.write_text(dumps_baseline(b.meta, kept), encoding="utf-8")
    print(f"{path}: {case} dropped, unmeasured at the gate")
else:
    print(f"{path}: {case} was absent, and stays so")
PY
fi
baseline_moves "$(git rev-parse --short HEAD)" "*" || { echo "a baseline moved as bless may not: stop and report"; exit 1; }
```

The drop writes the file with the runner's own `load_baseline` and `dumps_baseline`, which give back every committed baseline byte for byte, so only that case's block goes. `baseline_moves` prints it as `<model>: CASE <rate> over <reps> -> absent`. Record each re-run's stamp and counts in the notes, and each cell left unmeasured in the evidence, as spark's and sol's multi-file-rename were after section 5.

- [ ] **Step 3: Commit the re-bless**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
cd "$MAIN" || exit 1
[ "$(git rev-parse HEAD)" = "$GATE" ] || { echo "HEAD is not the gate's commit"; exit 1; }
git diff --name-only | grep -v '^benchmarks/baselines/' && { echo "something besides the baselines changed"; exit 1; }
baseline_moves "$(git rev-parse --short HEAD)" "*" || { echo "a baseline moved as bless may not: stop and report"; exit 1; }
bin/bench compare --matrix --baselines | tee "$R/45-3-baselines-after.md"
git add benchmarks/baselines
git commit -m "chore(bench): re-bless the smoke matrix on the third ask wording (#45)"
```

Append to `$R/45-3-notes.md` under `## G9`: every `baseline_moves` line, each case a baseline gained, and each cell Step 2 re-ran, blessed or left unmeasured.

### G10: Labels, the evidence, section 7, and the comment

Rules: R1 to R6. Done whether or not the wording shipped; G11 says what to leave out when it did not.

- [ ] **Step 1: Label every failing ambiguous rep (decision 12)**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
runs=("before $BEFORE" "before-again $BEFORE" "loop-C ${C:-none}" "loop-D ${D:-none}"
      "gate-${PICK:-none} ${GATE:-none}" "gate-${PICK:-none}-again ${GATE:-none}")
for tc in "${runs[@]}"; do
  set -- $tc
  [ -n "$(files "$2" "$1")" ] && { printf '\n# %s\n' "$1"; amb_facts "$2" "$1"; }
done > "$R/45-3-amb-facts.md"
for tc in "${runs[@]}"; do
  set -- $tc
  [ -n "$(files "$2" "$1")" ] && { printf '\n# %s\n' "$1"; questions "$2" "$1"; }
done > "$R/45-3-questions.md"
for f in "$R/45-3-amb-facts.md" "$R/45-3-questions.md" "$R/45-3-guard-facts.md"; do
  [ ! -f "$f" ] || no_secrets "$f" || exit 1             # guard facts exist only after G8
done
```

`before-again` is G3 Step 5's re-run of a model the before run left inconclusive, and `gate-X-again` G9 Step 2's re-run of a cell the gate left inconclusive; each is listed only if it ran. Label each failing rep by hand with spec section 5's labels, citing only its calls, diff, closing, questions and yes-or-no facts from its tool results (rule R3):

| Label | Means |
| --- | --- |
| asked in prose | ended `done` with no change, and the closing asks the caller something |
| guessed | ended `done` with a change to the fixture (`fixture changed`) |
| asked blind | called `ask` before any `read` or `bash` call (its calls start with `ask`) |
| other | anything else, in one sentence, such as an `ask` after reading whose questions name neither `TIMEOUT` nor 30 |

A rep whose diff touches only other files did not change the fixture; say what it touched. An `error` rep is not listed. Each note starts with the rep's tool calls in backticks, as the README's do. For each passing ambiguous-request question, note any words of the old example's frame it uses ("from 30 to what", "60, 120, or another value", "Unanswered, I will use 60"), which is in the `questions` description again at `BEFORE`.

- [ ] **Step 2: Write the README section**

In `docs/evidence/45/README.md`, insert this section directly before `## Left out of the matrix`, filled from `$R/45-3-notes.md`, the tables as printed, the labels and the questions. Results files are named by file name and worktree (`side worktree` for `nare-45-side`), with the commit from the run log. No em dashes, and no quote from saved model reasoning.

```markdown
## After the third wording

**Date:** <the UTC date, and the first and last run times>
**Commits:** section 6's text `947cc4c`; PR 1 `6ca24f5`, whose `src/nare` is `main`'s; the before commit `<BASE>`, whose `src/nare` is `main`'s and whose ambiguous-request has its check, run at `<BEFORE>` (<the same commit, or: with docs-only review fixes on it>); its re-bless `<REBLESS>`; candidate C `<C>` and candidate D `<D>`, each one commit on `<REBLESS>` (<which were built; a candidate step 1 did not select was written and reviewed at `<X_REVIEWED>` and never built>); gate `<GATE>`, the pick's own commit (<or: none>).
**Judge:** `gpt-6.1-sol`, for every model.
**Runs:** smoke tier, 3 reps per case and 7 on a confirmation, `NARE_STREAM=1`. The before re-run and the gate were made in `nare-45`; every other run in the side worktree (`nare-45-side`), detached at the commit named.

Spec section 6.2 is the protocol. The second pass, above, names PR 1 `477160c`, A `3d1fee5` and B `d3edf82`; after a rebase they are `6ca24f5`, `6b2eb75` and `7a4b5cb`, with the same code, cases and baselines.

<The outcome in two or three sentences: which row step 1 fell in, which candidates were built and kept, the pick, and whether it shipped, or the condition that stopped it.>

### The check on ambiguous-request

<The check, in a sentence. The re-score of the kept reps (G1): the four moved lines and the six questions. The before re-run at `<BEFORE>`, matrix run `<stamp>` (and any `before-again`), its table as printed, and each model's ambiguous-request before and after the re-bless, beside the re-score. The baselines' table after the re-bless, as printed.>

### Step 1: where the drop came from

<`s6-pool`'s runs (stamp, reps, judged, failed), the pool, the arithmetic against PR 1's 6/14 and A's 13/17, the row, and the candidates built.>

### Step 2: spark first

<Per built candidate: its runs, pool, arithmetic against PR 1's 6/14, and kept or out.>

### The cheap loop

#### Candidate <X>, `<X>`, matrix run `<stamp>` (side worktree)

<45-3-loop-X-table.md, as printed>

| model | case | C | D |
| --- | --- | --- | --- |

Score, cells at target: <C c of 10, D d of 10> (section 6's text 2, A and B 4 each, and these stand under the check). <Each flagged ambiguous-after-reading cell: read by the spark pool, or its two pools and whether it held up.> <The pick, and the rule line that picked it.>

### The gate

The matrix before is the re-blessed baselines' table above. After, on `<GATE>`, matrix run `<stamp>`, as printed:

<45-3-gate-X-table.md, as printed>

<Each flagged and inconclusive cell, its pool or the pool that read it, its labels and its reading. `gpt-5-nano` on read-only-question: the pick's pool, its runs, labels and reading against PR 1's 15 of 17; the first measurement's 5 of 7 was read as noise above ("Is the read-only-question drop noise?"). Whether sol and luna reach 2 of 3 on ambiguous-request. The decision: shipped and re-blessed at `<GATE>`, or did not ship, naming the condition. If it shipped: each case a baseline gained (spark's or sol's multi-file-rename, if the gate measured it), and each cell the gate left inconclusive, with its G9 re-run (stamp and counts) and whether it was blessed or left unmeasured, its case dropped from that model's baseline.>

### Failure labels

| run | model | case | rep | label | note |
| --- | --- | --- | --- | --- | --- |

| run | model | asked in prose | guessed | asked blind | other | total |
| --- | --- | --- | --- | --- | --- | --- |

### Questions that passed ambiguous-request

| run | model | rep | calls | question | old example's words |
| --- | --- | --- | --- | --- | --- |

### What it shows

<Each model: both ambiguous cases at 2 of 3 in the gate run, or its labels. Each miss, with the next lever (a rule in the default system prompt, spec section 9). Section 7: Step 3's result.>
```

- [ ] **Step 3: Section 7**

Only if the wording shipped: count the gate run's labels per model (Step 1's table, `run` = the gate). If "asked in prose" is the most common label among some matrix model's failing ambiguous reps, section 7 (the loop nudge) is next, in its own plan and PR 3. Otherwise it is not built, because the description was enough for that failure. If nothing shipped, section 7 is not decided (spec 7: "If 6.2 picks nothing, this is not decided"); give the gate run's counts if there was one. Put the result and its counts in "What it shows".

- [ ] **Step 4: Commit the evidence**

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
cd "$MAIN" || exit 1
no_secrets docs/evidence/45/README.md || exit 1
grep -qP '\x{2014}' docs/evidence/45/README.md && { echo "em dash in the README: remove it, then run this step again"; exit 1; }
git add docs/evidence/45/README.md
git commit -m "docs: #45 evidence after the third ask wording (#45)"
```

- [ ] **Step 5: The comment for #45**

Write `$R/45-3-issue-comment.md`: step 1's row, which candidates were built and kept, the pick and its score, the gate table (or why there was none), the noise readings, each miss with its labels and the next lever, and section 7's result, pointing to `docs/evidence/45/README.md` for the rest. Run `no_secrets` on it. Posting it is the user's call; this plan posts nothing.

### G11: If nothing passes

Rules: R1 to R6. Spec 6.2: nothing ships to `src/nare` if step 2 keeps no candidate (G4), the pick rule leaves none (G6), or the pick fails the gate (G7, Step 2, or G8).

- [ ] **Step 1: Leave the code at `main`'s**

If G4 or G6 stopped, `45-ask-description` is still at `REBLESS`, whose code is `main`'s: nothing to revert. If the gate failed, the pick is on the branch; revert it as a new commit, so its SHA stays where the evidence can name it:

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
cd "$MAIN" || exit 1
[ "$(git rev-parse HEAD)" = "$GATE" ] || { echo "HEAD is not the gate's commit"; exit 1; }
git revert --no-commit "$GATE"
git commit -m "revert(tools): back to main's ask, since candidate $PICK failed the gate (#45)"
git diff --quiet "$BEFORE" HEAD -- src tests && echo "the code is main's again"
```

- [ ] **Step 2: Do not bless**

No bless beyond G3's. `git -C /home/smadinya/vividynamics/nare-45 diff --stat HEAD -- benchmarks/baselines` is empty. The check and its re-blessed baselines stay: they hold whatever the description says.

- [ ] **Step 3: Record, and hand back**

Do G10, Steps 1, 2, 4 and 5. The README section has the same title, says nothing shipped and which condition decided it, and gives every pool, table and label measured up to that point, with the steps that did not run named as such. It names the next lever: a rule about `ask` in the default system prompt (spec section 9), in its own change. Section 7 is not decided. Then G12. Whether PR 2 goes up with the check and its baselines alone is the user's call.

### G12: Wrap up

Rules: R1 to R6.

```bash
. /home/smadinya/vividynamics/nare-45/benchmarks/results/45-3-env.sh
git -C "$MAIN" status --short                            # expect no output
git -C "$MAIN" log --oneline 45-ask-across-models..HEAD
git -C "$MAIN" branch -v --list '45-ask-*'
git -C "$SIDE" status --short                            # expect no output
test -z "$(git -C "$SIDE" branch --show-current)" && echo "side worktree detached"
git -C "$SIDE" log --oneline -1                          # the commit side_at last put it at
test "$(git -C "$MAIN" rev-parse 45-ask-across-models)" = "$PR1" && echo "PR 1's branch did not move"
```

Expected log on `45-ask-description`, newest first: the evidence commit; the re-bless (if it shipped) or the revert (if the gate failed); the pick (if one was picked); `REBLESS`; any docs-only review fixes (`BEFORE`, if there are any); the before commit, `BASE`; this plan and anything below it. `45-ask-c` and `45-ask-d` remain: a built candidate on `REBLESS`, one not built on `BASE`. Nothing is pushed. Keep `nare-45-side` until PR 2 merges.

Hand back: the commits above, step 1's row, the candidates built and kept, the pick and the decision, the misses, section 7's result, the path of the issue comment, and that `nare-45-side` was left detached at the commit named above, off PR 1's branch, `45-ask-across-models`, which G0 took it off (the user can put it back with `git -C /home/smadinya/vividynamics/nare-45-side switch 45-ask-across-models`; this plan does not). Then the open decisions: whether to push the unpicked candidate's branch so the SHA its cheap loop names can be fetched, and, if nothing shipped, whether PR 2 goes up with the check alone.

---

## Coverage (spec section 6.2, and section 10's lines for this pass)

The other lines of section 10 are PR 1's (the matrix, `compare --matrix`, `models`, the README pointer, the preflight, the two cases, the first baselines) or were met by the second pass's record (6.1's two candidates).

| Requirement | Met by |
| --- | --- |
| 6.2: readings cite only tool calls and their arguments, diff, closing, `ask` questions and yes-or-no facts from tool results; saved reasoning is not read | Decision 14, rule R3, the helpers (`calls`, `callargs`, `asked`, `saw_body`), G8 Step 3, G10 Step 1 |
| 6.2: ambiguous-request's `bash` check, word for word | F1 Steps 3 and 4 |
| 6.2: a `run_here` test on questions naming `TIMEOUT`, only 30, 300 or 600 seconds for the agent's own commands, and none | F1 Step 1 |
| 6.2: `bin/bench verify` checks the case instead of exempting it, and the check fails on the pristine fixture | F1 Step 1 (the `verify` test, and the empty-questions case), G0 Step 5 (`bin/bench verify`) |
| 6.2: the before commit returns `tools.py` and `test_tools.py` to `main`'s, in one commit with the check | F1 Steps 5 and 6, G0 Step 2 |
| 6.2: the before numbers re-run at the before commit, 3 reps with the 7-rep confirmation, blessed with `--case ambiguous-request` one model at a time; no results file by hand | G3, decision 4, decision 10 |
| 6.2: the read-only re-score, a script outside the bench that writes nothing the bench reads, with nano's 1/3 to 0/3 and the 1, 1 and 3 blind passes | G1, decision 13 |
| 6.2: PR 1's side of any new pool at the before commit; the old PR 1 pool still serves | Decision 8, G0 Step 4, G6 Step 2, G8 Step 2 |
| 6.2: each candidate one commit on top of the before commit | F2, F3, decisions 1 and 2, G4 Step 1 |
| 6.2 step 1: spark on ambiguous-after-reading to 14 judged at `947cc4c` from fresh runs; the row decides what is built; A's changes count as one; not built later | G2, decisions 3 and 7 |
| 6.2: candidate C's text, and D's two texts, word for word; both end "This ends the session." | F2 Steps 4 and 5, F3 Steps 4 and 5 |
| 6.2: tests for C (A's, the widened open-value pin, B's pin removed) and for D (the open-value pin and the leak test, no prose or log-level pin); the default pin holds | F2 Steps 2 and 6, F3 Steps 2 and 6 |
| 6.2: the leak test's patterns, each proven alone by a near copy that names it; the delete pattern ("delete", "remove" or "drop", across a dot inside a word) catches "Delete parse_a or parse_b? ..." and passes "Do not delete or rename files."; the old example's list and "to 8 or to 16" are caught; every string of both candidates passes | F2 Step 2, F2 Step 6 and F3 Step 6 (the near-copy, alone and schema tests) |
| 6.2 step 2: each built candidate's spark pool to 14 at its commit before any paid run on it; out at 9 of 14; a later flag read by it with no new runs | G4 Steps 3 and 4, G6 Step 1, G8 Step 2, decision 6 |
| 6.2 step 3 on: the cheap loop with C and D in A's and B's places, against the re-blessed baselines | G5 |
| 6.2: the pick, the higher score, D on a tie | G6 |
| 6.2: no second gate | G8 Step 5 |
| 6.2: nano on read-only-question to 14 judged on the pick, counting the gate run's; PR 1's 15 of 17 | G8 Steps 1 and 4 |
| 6.2: the record adds step 1's pool and row and each candidate's spark pool, under its own heading | G10 Step 2 |
| 6.2: if nothing passes, `src/nare` is `main`'s, each pool, table and label recorded, the next lever named; the check and its baselines stay | G11 |
| 6.2: cost | Part G's cost table |
| 10: ambiguous-request has the check, a `run_here` test, and `verify` checks it | F1, G0 Step 5 |
| 10: ambiguous-request re-run for all five at a commit whose `src/nare` is `main`'s with the check, re-blessed with `--case`; the evidence gives the re-score beside the new numbers | G3, G1, G10 Step 2 |
| 10: spark has 14 judged on `947cc4c`, and the PR states the row and the candidates built | G2, G10 Step 2 |
| 10: each built candidate's spark has 14 judged before any paid run, and one out there had no paid run | G4 |
| 10: the descriptions are the candidate the rule picked, and the leak test covers a choice of what to delete asked as a question | F2, F3, G6, G7 Step 1 |
| 10: each candidate's cheap-loop table with stamp and commit, the score, the matrix before and after, all five re-blessed | G5, G6, G3 Step 6 (before), G7 Step 3 (after), G9, G10 Step 2 |
| 10: every label and reading cites only calls, diff, closing, questions and yes-or-no facts from tool results | Decision 14, rule R3, G10 Step 1 |
| 10: if 6.2 picks nothing, `src/nare` is `main`'s and #45 records it all with the next lever | G11, G10 Step 5 |
| 10: the labels of every failing ambiguous rep and the question of every passing ambiguous-request rep | G10 Steps 1 and 2, decision 12 |
| 10: nano on read-only-question has 14 on PR 1's code and 14 on the pick, with 6.1's readings of both and of the first measurement's 5 of 7 | G0 Step 4 (PR 1's 17), G8 Steps 1 and 4, G10 Step 2 (citing the second pass's reading of the 5 of 7) |
| 10: no confirmed guard regression but noise, no fix-failing-test regression with an asking rep, no ambiguous-after-reading regression that holds up | G7 Step 4, G8 |
| 10: sol and luna reach 2 of 3 on ambiguous-request, or the miss is recorded with the next lever | G8 Step 5, G10 Steps 2 and 5 |
| 10: each model reaches 2 of 3 on both ambiguous cases, or its labels are recorded | G7 Step 3 (`score`), G10 Steps 1 and 2 |
| 10: section 7 built, or why not recorded | G10 Step 3 |
| 10: `bin/build` passes | F1 Step 6, F2 Step 6, F3 Step 6, F4 Step 2, G4 Step 2, G7 Step 2 |

"#45 records" is met by the README section, which PR 2 carries, and the comment G10 Step 5 drafts for the user to post.
