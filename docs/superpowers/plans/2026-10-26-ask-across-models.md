# Ask Reliably Across Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The benchmark runs a matrix of models, each against its own baseline, with one judge. Two new smoke cases measure asking after reading and not asking on a plain question. `bin/bench models` lists what the endpoint serves. Then the `ask` description says where a question in prose goes, and stops leaking ambiguous-request's answer.

**Architecture:** All of Part A is in `benchmarks/`; `src/nare` does not change. The matrix for a tier is its set of `benchmarks/baselines/*.<tier>.toml` files. `config.baseline_config` builds one `Config` per baseline, routing one base URL to each provider. A new `benchmarks/runner/models.py` reads the endpoint's listing with httpx2, for `bin/bench models` and for the matrix preflight. `__main__.py` gains `run --matrix` (sequential, one stamp, one results file per model), `compare --matrix` (and `--baselines`), and `models`. Each results line now records the provider and judge, so `bless` and `compare` read them back as they already read the cap. `report.render_matrix` prints the case-by-model table as Markdown. Part B changes two strings in `src/nare/tools.py`.

**Tech Stack:** Python 3.12+, pytest (`asyncio_mode = "auto"`), ruff, mypy strict, `uv`, httpx2 (already installed with `anthropic`), Docker (bench runs only).

**Spec:** `docs/superpowers/specs/2026-10-23-ask-across-models-design.md`. Issue: #45.

## Decisions (the plan's calls)

1. **Results layout.** A matrix run takes one stamp and writes each model's results the way a single run does, under `<stamp>.<model slug>`: `benchmarks/results/<stamp>.<slug>.jsonl` and `benchmarks/results/<stamp>.<slug>/<case>-<rep>/`. Why: each file holds one model, so `compare-judges --results <file>` and the evidence paths (relative to `benchmarks/results/`) work unchanged, and the shared stamp keeps the matrix one run that `compare --matrix` can find.
2. **compare and bless after a matrix run.** They still read the newest run. When that run is a matrix run, they read the `--model`'s own file from it, and exit 2 if it has none. A newer single run is still the newest run. Why: no new flag, and "the newest run" keeps its meaning.
3. **A bless after a matrix run gets its provider and judge from the results.** `RepRecord` gains `provider` and `judge_model`, and `bless` and `compare` take them, and the cap, from the results, falling back to flags and environment only for results written before. Why: the matrix takes both from the baseline, not the shell; a forgotten `--provider openai` would otherwise write `anthropic` into an openai baseline and silently move that model to the other route. The cap already works this way.
4. **The matrix table is Markdown,** padded so it also reads in a terminal. Why: a PR and `docs/evidence/45/README.md` paste it as printed.
5. **`compare --matrix --baselines`** prints the tier's baselines as the same table. Why: the spec wants the baseline matrix table in `docs/evidence/45/README.md`, but the seven baselines are blessed from seven single runs, so there is no matrix run to print it from. Without this it is a second paid run or a hand-copied table.
6. **`run --matrix` and `compare --matrix` refuse `--model`, `--provider` and `--judge-model`** (exit 2), and the matrix never reads `NARE_MODEL`, `NARE_PROVIDER`, `NARE_BENCH_JUDGE_MODEL` or `NARE_BENCH_MAX_TOKENS`. Why: the baselines name all four, and this repo's `.env` sets `NARE_MODEL` and `NARE_PROVIDER`. `bless` has no `--matrix` at all.
7. **The preflight lists once per distinct key.** Why: a LiteLLM key can be limited to some models. On this proxy both keys are the same, so it is one call.
8. **`bin/bench models` exits 1 when the endpoint does not answer,** and 2 only for what is wrong before any call: no base URL, an unknown provider, no key. It sends the key of `--provider`, else `NARE_PROVIDER`, else `anthropic`.
9. **A listing error names the path and the HTTP status or exception type, never the URL.** Why: httpx2's exception text carries the URL, and the base URL is not printed.
10. **Part C sets `NARE_STREAM=1` for every run.** Why: Cloudflare in front of the proxy answers 524 to a turn that takes over 120 s to return whole, and a reasoning model's non-streamed reply once arrived empty (`config.py`). It changes how a turn arrives, not what is measured (`sandbox.py`), so before and after must simply use the same setting.

## Global Constraints

- Work only in `/home/smadinya/vividynamics/nare-45`. Never touch `/home/smadinya/vividynamics/nare`: it is the user's checkout and the editable install behind `nare`. Run every command from the worktree root. `uv run` creates the worktree's `.venv` on first use (the machine is aarch64).
- Part A is on branch `45-ask-across-models`. Part B is on `45-ask-description`, which Task 12 creates from Part A's last commit. No pushes, no PRs.
- `src/nare` does not change in Part A. Task 11, Step 1 checks it with `git diff --stat origin/main -- src/nare` (empty).
- `.agents/skills/` does not change (it is synced from upstream, PR #75).
- Tests never reach the proxy or Docker. Fake them as the existing tests do: monkeypatch names on `benchmarks.runner.__main__` (`run_case`, `build_image`, `judge_transport`, `list_models`, `new_stamp`, `repo_root`), and `httpx2.MockTransport` for the listing.
- Prose we publish (README, docs, commit bodies) uses no em dashes.
- `bin/build` (ruff format --check, ruff check, mypy strict over `src tests benchmarks`, pytest) passes at the end of every task. If `ruff format --check` complains, run `uv run ruff format src tests benchmarks` and re-run.
- Commit style `type(scope): summary (#45)`.
- Bench commands that touch Docker (`verify`, `run`) run under WSL with `DOCKER_CONFIG` pointing at a directory whose `config.json` is `{}`: `~/.docker/config.json` uses `credsStore: desktop.exe`, which fails. Never run two of them at once, even from different checkouts: every run tags `nare-bench:local`.
- Credentials come from the worktree's gitignored `.env`, loaded in the same command: `set -a; . ./.env; set +a`. It holds `ANTHROPIC_API_KEY`, `NARE_BASE_URL` (without `/v1`), `NARE_MODEL` and `NARE_PROVIDER`, and no `OPENAI_API_KEY`. Never print a key or the base URL.

## Review Focus

1. **Nothing is spent before every refusal.** Mixed judges, a missing key, a cap above the non-streaming ceiling and an unlisted model all exit 2 before `build_image` and `run_case`. Pinned in Task 8 (`ran == []` in each refusal test), and the ceiling in Task 5 (`baseline_config` raises before `run_matrix` builds anything).
2. **Each provider gets its route,** for the agent and the judge: `anthropic` the proxy root, `openai` the root plus `/v1`. Pinned in Tasks 5 and 8.
3. **compare and bless pick the right file.** After a matrix run, `--model`'s own file; a newer single run still wins. Pinned in Task 4.
4. **bless writes what was measured.** Provider and judge come from the results, not the shell. Pinned in Task 3.
5. **The listing prints nothing from `litellm_params` and never the URL.** Pinned in Task 6.
6. **The single run behaves as before** after its body moves into `run_model` and `confirm`. `test_a_confirmation_rerun_leaves_one_results_file` passes unchanged in Task 8.

## File map

| File | Change | Task |
| --- | --- | --- |
| `benchmarks/cases/ambiguous-after-reading/` | new case: `case.toml`, `fixture/utils.py`, `fixture/main.py`, `fixture/.gitignore` | 1 |
| `benchmarks/cases/read-only-question/` | new case: `case.toml`, `fixture/duration.py`, `fixture/.gitignore` | 1 |
| `benchmarks/tests/test_cases.py` | `SEEDS`, case tests, `run_here` test | 1 |
| `benchmarks/baselines/ada-qwen3-14b.smoke.toml` | deleted | 2 |
| `benchmarks/runner/report.py` | `RepRecord.provider`, `.judge_model`; `render_matrix` | 3, 7 |
| `benchmarks/runner/__main__.py` | record and read provider and judge; results layout; `models`; `run --matrix`; `compare --matrix` | 3, 4, 6, 8, 9 |
| `benchmarks/runner/config.py` | `route`, `baseline_config`, `_check_ceiling` | 5 |
| `benchmarks/runner/models.py` | new: the listing | 6 |
| `benchmarks/tests/test_main.py`, `test_config.py`, `test_report.py` | tests | 3 to 9 |
| `benchmarks/tests/test_models.py` | new | 6 |
| `benchmarks/README.md` | `models` pointer, matrix, layout | 10 |
| `src/nare/tools.py`, `tests/test_tools.py` | Part B only | 12 |

---

# Part A: the matrix PR (branch `45-ask-across-models`)

### Task 0: Start

- [ ] **Step 1: Check the worktree**

```bash
cd /home/smadinya/vividynamics/nare-45
git status --short
git branch --show-current
git fetch origin
git log --oneline -3 origin/main
git log --oneline -4
```

Expected: a clean tree on `45-ask-across-models`, whose commits are the two spec commits and this plan's commit on top of `origin/main` as of 2026-10-26 (`4a9aed4`). If `origin/main` has moved since, note it in your report; do not rebase without being asked.

- [ ] **Step 2: Build once**

Run: `bin/build`
Expected: clean. This creates `.venv` on first use.

---

### Task 1: Two new smoke cases

**Files:**
- Create: `benchmarks/cases/ambiguous-after-reading/case.toml`, `fixture/utils.py`, `fixture/main.py`, `fixture/.gitignore`
- Create: `benchmarks/cases/read-only-question/case.toml`, `fixture/duration.py`, `fixture/.gitignore`
- Modify: `benchmarks/tests/test_cases.py`

- [ ] **Step 1: Write the failing tests**

In `benchmarks/tests/test_cases.py`, add the two ids to `SEEDS`, so it reads:

```python
SEEDS = {
    "ambiguous-after-reading",
    "ambiguous-request",
    "bash-timeout-recovery",
    "bootstrap-without-database",
    "edit-docstring",
    "expense-tracker-cli",
    "fix-failing-test",
    "multi-file-rename",
    "context-overflow",
    "read-only-question",
}
```

Directly above `def cases() -> dict[str, Case]:`, add:

```python
def test_the_new_ask_cases_are_in_the_smoke_tier() -> None:
    smoke = load_cases(repo_root() / "benchmarks" / "cases", tier="smoke")
    assert {"ambiguous-after-reading", "read-only-question"} <= {c.id for c in smoke}


def test_the_after_reading_case_expects_a_blocked_run() -> None:
    case = cases()["ambiguous-after-reading"]
    assert [c.status for c in case.checks if c.kind == "result"] == ["blocked"]
    assert case.judge is not None
    assert case.judge.min_met == 1


def test_the_read_only_case_expects_an_answer_not_a_question() -> None:
    """The guard: asking here is the failure #38 fixed."""
    case = cases()["read-only-question"]
    assert [c.status for c in case.checks if c.kind == "result"] == ["done"]
    # No bash check: one that nothing changed would pass on the pristine
    # fixture, so the judge's first claim does that job.
    assert case.bash_checks == ()
    assert case.judge is not None
    assert case.judge.min_met == 2
```

Directly above `def test_the_bootstrap_case_outlasts_a_detour() -> None:`, add:

```python
def session_asking(*questions: str) -> str:
    """/out/session.json, cut down to the field the check reads."""
    return json.dumps({"questions": list(questions)})


@pytest.mark.parametrize(
    ("questions", "passes"),
    [
        (["Delete format_bytes, slugify, or both?"], True),
        (["Delete format_bytes?", "Or slugify?"], True),
        (["Delete format_bytes?"], False),
        (["Which helper do you mean?"], False),
        ([], False),
    ],
)
def test_the_after_reading_case_passes_only_questions_naming_both_helpers(
    tmp_path: Path, questions: list[str], passes: bool
) -> None:
    cmd = check_with("ambiguous-after-reading", "session.json")
    (tmp_path / "session.json").write_text(session_asking(*questions))
    assert (run_here(cmd, tmp_path, tmp_path) == 0) is passes
```

The empty list is the session `verify` gives the check: `idle_artifacts` saves `new_session(...)`, whose `questions` is `[]`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_cases.py -q`
Expected: FAIL. `test_every_seed_case_loads` and `test_the_full_tier_is_every_case_and_smoke_is_a_subset` fail on the set comparison, and the new tests fail with `KeyError: 'ambiguous-after-reading'` or `'read-only-question'` or an assertion on the smoke set.

- [ ] **Step 3: Create ambiguous-after-reading**

`benchmarks/cases/ambiguous-after-reading/case.toml`, verbatim from spec section 4.1:

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

`benchmarks/cases/ambiguous-after-reading/fixture/utils.py`. Four helpers; `format_bytes` and `slugify` have no caller:

```python
"""Small helpers for the expense report."""

import re


def format_bytes(size):
    """Return a size in bytes as a short human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.0f} TB"


def parse_csv_line(line):
    """Split one CSV line into its fields, without surrounding spaces."""
    return [field.strip() for field in line.split(",")]


def slugify(text):
    """Lower-case text and join its words with hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def total(values):
    """Add up numbers written as strings."""
    return sum(float(value) for value in values)
```

`benchmarks/cases/ambiguous-after-reading/fixture/main.py`, which calls the other two:

```python
"""Print the total of the amount column of an expenses CSV file."""

import sys

from utils import parse_csv_line, total


def main(path):
    with open(path) as handle:
        rows = [parse_csv_line(line) for line in handle if line.strip()]
    print(total(row[1] for row in rows[1:]))


if __name__ == "__main__":
    main(sys.argv[1])
```

Fixtures are linted by `bin/build` (ruff runs over `benchmarks/`; only mypy excludes `benchmarks/cases/`). Both files above pass ruff as written.

- [ ] **Step 4: Create read-only-question**

`benchmarks/cases/read-only-question/case.toml`, verbatim from spec section 4.2:

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

`benchmarks/cases/read-only-question/fixture/duration.py`:

```python
"""Durations written the way people write them in config files."""

from datetime import timedelta

UNITS = {"s": "seconds", "m": "minutes", "h": "hours"}


def parse_duration(text):
    """Turn a duration such as "90s", "5m" or "2h" into a timedelta."""
    number, unit = text[:-1], text[-1]
    if unit not in UNITS:
        raise ValueError(f"unknown unit in {text!r}; use s, m or h")
    return timedelta(**{UNITS[unit]: int(number)})
```

Do not run the fixtures from inside their directories: a `__pycache__` left there would land in the fixture copy.

Give both fixtures expense-tracker-cli's `.gitignore`, byte for byte (`cp benchmarks/cases/expense-tracker-cli/fixture/.gitignore` into each `fixture/`):

```
__pycache__/
.pytest_cache/
```

A model that runs the code to check its answer leaves a `.pyc`. The rep's diff is `git add -A` against the fixture commit (`git_diff` in `sandbox.py`), so without this the judge reads that `.pyc` as a changed file and fails read-only-question's "No file was changed." The Task 11 spark pass failed two of three reps that way.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest benchmarks/tests/test_cases.py -q`
Expected: PASS, including the five `run_here` cases and `test_no_case_names_a_model`.

- [ ] **Step 6: Build and commit**

```bash
bin/build
git status --short    # only the two case directories and test_cases.py; no __pycache__
git add benchmarks/cases/ambiguous-after-reading benchmarks/cases/read-only-question benchmarks/tests/test_cases.py
git commit -m "feat(bench): ambiguous-after-reading and read-only-question cases (#45)"
```

---

### Task 2: Drop the ada/qwen3-14b baseline

**Files:**
- Delete: `benchmarks/baselines/ada-qwen3-14b.smoke.toml`

It predates contract 2 and the system prompt, and that model's tool calls were broken on the proxy by 2026-09-30 (spec section 3.4). No test reads the committed file: the tests that name `ada/qwen3-14b` write their own baselines under `tmp_path`.

- [ ] **Step 1: Delete it and build**

```bash
git rm benchmarks/baselines/ada-qwen3-14b.smoke.toml
bin/build
```

Expected: clean. `benchmarks/baselines/` is now empty, so git no longer tracks the directory; `bless` creates it again (`baselines.mkdir(parents=True, exist_ok=True)`).

- [ ] **Step 2: Commit**

```bash
git commit -m "chore(bench): drop the ada/qwen3-14b baseline, which predates contract 2 (#45)"
```

---

### Task 3: Results record the provider and the judge

**Files:**
- Modify: `benchmarks/runner/report.py` (`RepRecord`)
- Modify: `benchmarks/runner/__main__.py` (`run_rep`, new `as_recorded`, `main`'s compare and bless path)
- Test: `benchmarks/tests/test_main.py`

**Interfaces:**
- Produces: `RepRecord.provider: str = ""` and `RepRecord.judge_model: str = ""`; `as_recorded(config: Config, records: Sequence[RepRecord]) -> Config` in `benchmarks.runner.__main__`.

- [ ] **Step 1: Write the failing tests**

Append to `benchmarks/tests/test_main.py`:

```python
async def test_a_rep_records_its_provider_and_judge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_sandbox(monkeypatch, RunArtifacts(DONE_LINE, "", 0, False, 1.0))
    evidence = tmp_path / "results" / "stamp"
    config = replace(CONFIG, provider="openai", judge_model="claude-sonnet-5-5")
    rec = await run_rep(sandboxed(tmp_path), config, cast(Transport, None), 0, evidence)
    assert (rec.provider, rec.judge_model) == ("openai", "claude-sonnet-5-5")


def test_bless_writes_the_provider_and_judge_the_results_were_run_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A matrix run takes both from the baseline, not the shell, so bless must
    # read them back from the results, as it reads the cap.
    from benchmarks.runner.report import baseline_path, load_baseline

    baselines = results_dir(tmp_path, monkeypatch)
    ran = replace(
        record("edit-docstring", model="m"),
        provider="openai",
        judge_model="claude-sonnet-5-5",
    )
    write_results(tmp_path, [ran])
    assert main(["bless", "--model", "m"]) == 0
    meta = load_baseline(baseline_path(baselines, "m", "smoke")).meta
    assert (meta.provider, meta.judge_model) == ("openai", "claude-sonnet-5-5")


def test_bless_takes_the_flags_for_results_written_before_they_were_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.runner.report import baseline_path, load_baseline

    baselines = results_dir(tmp_path, monkeypatch)
    write_results(tmp_path, [record("edit-docstring", model="m")])
    flags = ["--provider", "openai", "--judge-model", "j"]
    assert main(["bless", "--model", "m", *flags]) == 0
    meta = load_baseline(baseline_path(baselines, "m", "smoke")).meta
    assert (meta.provider, meta.judge_model) == ("openai", "j")


def test_compare_reads_the_judge_from_the_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.runner.report import baseline_path

    baselines = results_dir(tmp_path, monkeypatch)
    baselines.mkdir()
    baseline_path(baselines, "m", "smoke").write_text(
        dumps_baseline(
            BaselineMeta("t", "abc1234", "openai", "m", "claude-sonnet-5-5"),
            [summary("edit-docstring", 1.0)],
        )
    )
    judged = replace(
        record("edit-docstring", model="m"), judge_model="claude-sonnet-5-5"
    )
    write_results(tmp_path, [judged])
    # No --judge-model: the default, the model under test, is not the judge.
    assert main(["compare", "--model", "m"]) == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_main.py -q -k "provider_and_judge or before_they_were_recorded or judge_from_the_results"`
Expected: 3 failed, 1 passed. The three fail with `TypeError: RepRecord.__init__() got an unexpected keyword argument 'provider'` (or `'judge_model'`). `test_bless_takes_the_flags_for_results_written_before_they_were_recorded` passes already; it pins the fallback for older results.

- [ ] **Step 3: Add the fields**

In `benchmarks/runner/report.py`, at the end of `RepRecord`, after `max_tokens: int | None = None`:

```python
    # The agent's provider and the judge, so bless and compare read them from
    # the results as they read the cap. Empty in results written before.
    provider: str = ""
    judge_model: str = ""
```

- [ ] **Step 4: Record them in every rep**

In `benchmarks/runner/__main__.py`, `run_rep` builds `RepRecord` twice. In both, after `max_tokens=config.max_tokens,` add:

```python
                provider=config.provider,
                judge_model=config.judge_model,
```

(The first, inside the error branch, is indented 16 spaces; the second, the final `return RepRecord(`, 8.)

- [ ] **Step 5: Read them back in compare and bless**

Directly above `def main(argv: list[str] | None = None) -> int:`, add:

```python
def as_recorded(config: Config, records: Sequence[RepRecord]) -> Config:
    """The cap, provider and judge the results were run with, not the ones in
    this shell: compare and bless never run the agent. Results written before
    the provider and judge were recorded keep the flags' values."""
    return replace(
        config,
        max_tokens=next((r.max_tokens for r in records), None),
        provider=next((r.provider for r in records if r.provider), config.provider),
        judge_model=next(
            (r.judge_model for r in records if r.judge_model), config.judge_model
        ),
    )
```

In `main`, replace:

```python
    records = read_results(results_file)
    # The cap the results were run at, not one in this shell: compare and
    # bless never run the agent.
    config = replace(config, max_tokens=next((r.max_tokens for r in records), None))
```

with:

```python
    records = read_results(results_file)
    config = as_recorded(config, records)
```

and replace bless's closing print:

```python
        print(f"blessed {baseline_file} from {results_file.name}")
```

with:

```python
        print(
            f"blessed {baseline_file} from {results_file.name} "
            f"(provider {config.provider}, judge {config.judge_model})"
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest benchmarks/tests -q`
Expected: PASS, including `test_a_results_line_without_artifacts_still_loads` and the compare-judges tests (old results lines load with the defaults).

- [ ] **Step 7: Build and commit**

```bash
bin/build
git add benchmarks/runner/report.py benchmarks/runner/__main__.py benchmarks/tests/test_main.py
git commit -m "feat(bench): results record the provider and judge, and bless reads them (#45)"
```

---

### Task 4: One results file per model in a matrix run

**Files:**
- Modify: `benchmarks/runner/__main__.py` (import `slugify`; add `stamp_of`, `is_matrix_file`, `latest_run`, `results_for`; `main`'s results selection)
- Test: `benchmarks/tests/test_main.py`

**Interfaces:**
- Produces: `stamp_of(path) -> str`, `is_matrix_file(path) -> bool`, `latest_run(root, *, matrix=False) -> list[Path]`, `results_for(files, model) -> Path | None`. A matrix run (Task 8) writes `results/<stamp>.<slugify(model)>.jsonl`. `latest_results` stays as it is for `compare-judges`.

- [ ] **Step 1: Write the failing tests**

Append to `benchmarks/tests/test_main.py`:

```python
MATRIX_STAMP = "2026-10-26T00-00-00Z"


def test_bless_reads_its_models_file_from_a_matrix_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from benchmarks.runner.report import baseline_path, load_baseline

    baselines = results_dir(tmp_path, monkeypatch)
    for model in ("claude-haiku-5-5", "gpt-5-nano"):
        ran = record("edit-docstring", model=model)
        write_results(tmp_path, [ran], f"{MATRIX_STAMP}.{model}")
    for model in ("claude-haiku-5-5", "gpt-5-nano"):
        assert main(["bless", "--model", model]) == 0
        assert load_baseline(baseline_path(baselines, model, "smoke")).meta.model == (
            model
        )
    assert f"from {MATRIX_STAMP}.claude-haiku-5-5.jsonl" in capsys.readouterr().out


def test_a_matrix_run_without_the_model_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    results_dir(tmp_path, monkeypatch)
    ran = record("edit-docstring", model="gpt-5-nano")
    write_results(tmp_path, [ran], f"{MATRIX_STAMP}.gpt-5-nano")
    assert main(["bless", "--model", "claude-haiku-5-5"]) == 2
    assert "no results for 'claude-haiku-5-5'" in capsys.readouterr().err


def test_a_single_run_after_a_matrix_run_is_the_newest_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    results_dir(tmp_path, monkeypatch)
    write_results(tmp_path, [record("edit-docstring", model="m")], f"{MATRIX_STAMP}.m")
    write_results(
        tmp_path, [record("edit-docstring", model="m")], "2026-10-26T01-00-00Z"
    )
    assert main(["bless", "--model", "m"]) == 0
    assert "from 2026-10-26T01-00-00Z.jsonl" in capsys.readouterr().out


def test_the_newest_matrix_run_skips_a_newer_single_run(tmp_path: Path) -> None:
    from benchmarks.runner.__main__ import latest_run

    directory = tmp_path / "benchmarks" / "results"
    directory.mkdir(parents=True)
    for name in (
        "2026-10-25T00-00-00Z.a.jsonl",
        f"{MATRIX_STAMP}.a.jsonl",
        f"{MATRIX_STAMP}.b.jsonl",
        "2026-10-26T01-00-00Z.jsonl",
    ):
        (directory / name).write_text("")
    assert [p.name for p in latest_run(tmp_path)] == ["2026-10-26T01-00-00Z.jsonl"]
    assert [p.name for p in latest_run(tmp_path, matrix=True)] == [
        f"{MATRIX_STAMP}.a.jsonl",
        f"{MATRIX_STAMP}.b.jsonl",
    ]
```

`write_results(root, records, stamp)` already names the file `f"{stamp}.jsonl"`, so passing `f"{MATRIX_STAMP}.{model}"` writes a matrix-shaped file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_main.py -q -k "matrix_run or newest"`
Expected: 3 failed, 2 passed. `test_bless_reads_its_models_file_from_a_matrix_run` exits 2 ("was produced by gpt-5-nano"), the refusal test does not find "no results for", and `latest_run` is an `ImportError`. `test_a_single_run_after_a_matrix_run_is_the_newest_run` passes already (it pins that a matrix run never hides a newer single run), and so does the existing `test_latest_results_picks_the_newest`.

- [ ] **Step 3: Implement**

In the `from benchmarks.runner.report import (` block of `benchmarks/runner/__main__.py`, add `slugify,` after `render,`.

Directly above `def read_results(path: Path) -> list[RepRecord]:`, add:

```python
def stamp_of(path: Path) -> str:
    """The run a results file belongs to.

    A run writes `<stamp>.jsonl`. A matrix run writes one file per model under
    one stamp, `<stamp>.<model slug>.jsonl`, so the matrix stays one run.
    """
    return path.name.split(".", 1)[0]


def is_matrix_file(path: Path) -> bool:
    return path.name.count(".") == 2


def latest_run(root: Path, *, matrix: bool = False) -> list[Path]:
    """Every results file of the newest run, or with `matrix`, of the newest
    matrix run."""
    directory = root / "benchmarks" / "results"
    if not directory.is_dir():
        return []
    files = sorted(directory.glob("*.jsonl"))
    if matrix:
        files = [p for p in files if is_matrix_file(p)]
    if not files:
        return []
    newest = stamp_of(files[-1])
    return [p for p in files if stamp_of(p) == newest]


def results_for(files: Sequence[Path], model: str) -> Path | None:
    """One model's results in a run: its own file from a matrix run, or the
    run's single file, which the caller still checks was this model's."""
    for path in files:
        if is_matrix_file(path) and path.name.split(".")[1] == slugify(model):
            return path
    single = [p for p in files if not is_matrix_file(p)]
    return single[0] if single else None
```

Stamps (`%Y-%m-%dT%H-%M-%SZ`) and slugs (`[a-z0-9-]`) hold no dot, so the dot count tells the two shapes apart.

In `main`, replace:

```python
    results_file = latest_results(root)
    if results_file is None:
        print("bench: no results yet; run `bin/bench run` first", file=sys.stderr)
        return 2
    records = read_results(results_file)
```

with:

```python
    run_files = latest_run(root)
    if not run_files:
        print("bench: no results yet; run `bin/bench run` first", file=sys.stderr)
        return 2
    results_file = results_for(run_files, config.model)
    if results_file is None:
        print(
            f"bench: the newest run, {stamp_of(run_files[0])}, is a matrix run "
            f"with no results for {config.model!r}",
            file=sys.stderr,
        )
        return 2
    records = read_results(results_file)
```

The existing `produced` check that follows still refuses a single run made by another model.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest benchmarks/tests -q`
Expected: PASS, including `test_results_from_another_model_are_refused` and `test_latest_results_picks_the_newest`.

- [ ] **Step 5: Build and commit**

```bash
bin/build
git add benchmarks/runner/__main__.py benchmarks/tests/test_main.py
git commit -m "feat(bench): compare and bless read one model's file from a matrix run (#45)"
```

---

### Task 5: One config per baseline, with each provider's route

**Files:**
- Modify: `benchmarks/runner/config.py`
- Test: `benchmarks/tests/test_config.py`

**Interfaces:**
- Produces: `route(base_url: str | None, provider: str) -> str | None`; `baseline_config(*, model, provider, judge_model, max_tokens, base_url, env) -> Config`. Raises `ConfigError` for an unsupported provider, a missing key, or a cap above the non-streaming ceiling.

- [ ] **Step 1: Write the failing tests**

In `benchmarks/tests/test_config.py`, replace the import line

```python
from benchmarks.runner.config import ConfigError, resolve_config
```

with:

```python
from benchmarks.runner.config import (
    Config,
    ConfigError,
    baseline_config,
    resolve_config,
    route,
)
```

and append:

```python
@pytest.mark.parametrize(
    ("base_url", "provider", "routed"),
    [
        ("https://proxy.example", "openai", "https://proxy.example/v1"),
        ("https://proxy.example/v1/", "openai", "https://proxy.example/v1"),
        ("https://proxy.example/v1", "anthropic", "https://proxy.example"),
        ("https://proxy.example/", "anthropic", "https://proxy.example"),
        (None, "openai", None),
    ],
)
def test_one_base_url_gives_each_provider_its_route(
    base_url: str | None, provider: str, routed: str | None
) -> None:
    # The openai transport posts to <base>/chat/completions; the Anthropic
    # SDK adds /v1/messages itself.
    assert route(base_url, provider) == routed


MATRIX_ENV = {
    "ANTHROPIC_API_KEY": "k",
    "OPENAI_API_KEY": "k",
    # What a shell set up for a single run holds. The baseline wins.
    "NARE_MODEL": "ada/qwen3-14b",
    "NARE_PROVIDER": "anthropic",
    "NARE_BENCH_JUDGE_MODEL": "ada/qwen3-14b",
    "NARE_BENCH_MAX_TOKENS": "16000",
}


def test_a_matrix_model_runs_as_its_baseline_names() -> None:
    config = baseline_config(
        model="gpt-5-nano",
        provider="openai",
        judge_model="claude-sonnet-5-5",
        max_tokens=None,
        base_url="https://proxy.example",
        env=MATRIX_ENV,
    )
    assert (config.model, config.provider, config.judge_model) == (
        "gpt-5-nano",
        "openai",
        "claude-sonnet-5-5",
    )
    assert config.base_url == "https://proxy.example/v1"
    assert config.api_key_var == "OPENAI_API_KEY"
    # The baseline's cap, which is nare's default, not the shell's 16000.
    assert config.max_tokens is None


def test_a_matrix_model_keeps_its_baselines_cap() -> None:
    config = baseline_config(
        model="spark/glm-5.3-flash",
        provider="openai",
        judge_model="j",
        max_tokens=32000,
        base_url=None,
        env=MATRIX_ENV,
    )
    assert config.max_tokens == 32000
    assert config.base_url is None


def test_a_matrix_model_needs_its_providers_key() -> None:
    env = {k: v for k, v in MATRIX_ENV.items() if k != "OPENAI_API_KEY"}
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        baseline_config(
            model="gpt-5-nano",
            provider="openai",
            judge_model="j",
            max_tokens=None,
            base_url=None,
            env=env,
        )


def test_a_matrix_cap_above_the_unstreamed_ceiling_needs_streaming() -> None:
    def config(env: dict[str, str]) -> Config:
        return baseline_config(
            model="claude-haiku-5-5",
            provider="anthropic",
            judge_model="j",
            max_tokens=32000,
            base_url=None,
            env=env,
        )

    with pytest.raises(ConfigError, match="NARE_STREAM"):
        config(MATRIX_ENV)
    assert config({**MATRIX_ENV, "NARE_STREAM": "1"}).max_tokens == 32000


def test_a_baseline_without_a_provider_is_refused() -> None:
    with pytest.raises(ConfigError, match="provider"):
        baseline_config(
            model="m",
            provider="",
            judge_model="j",
            max_tokens=None,
            base_url=None,
            env=MATRIX_ENV,
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_config.py -q`
Expected: collection error, `ImportError: cannot import name 'baseline_config' from 'benchmarks.runner.config'`.

- [ ] **Step 3: Implement**

In `benchmarks/runner/config.py`, change `from dataclasses import dataclass` to `from dataclasses import dataclass, replace`.

In `_max_tokens_from`, replace everything from the comment `# nare refuses this at startup, ...` to the end of the function with a call, and move the check into its own function, so the two read:

```python
    if value <= 0:
        raise ConfigError(f"NARE_BENCH_MAX_TOKENS must be a positive integer: {raw!r}")
    _check_ceiling("NARE_BENCH_MAX_TOKENS", value, provider, model, stream)
    return value


def _check_ceiling(
    source: str, value: int, provider: str, model: str, stream: bool
) -> None:
    # nare refuses this at startup, so every rep would end with no result
    # line: a paid run that measures nothing.
    if provider == "anthropic" and not stream:
        ceiling = nonstreaming_ceiling(model)
        if value > ceiling:
            raise ConfigError(
                f"{source} {value} is above {ceiling}, the most "
                f"{model!r} takes without streaming; set NARE_STREAM=1"
            )
```

Append to the end of the file:

```python
def route(base_url: str | None, provider: str) -> str | None:
    """One proxy, two routes: Chat Completions under /v1, Messages at its root.

    The openai transport posts to <base>/chat/completions, so its base ends in
    /v1. The Anthropic SDK adds /v1/messages itself, so its base does not.
    """
    if not base_url:
        return None
    root = base_url.rstrip("/").removesuffix("/v1")
    return f"{root}/v1" if provider == "openai" else root


def baseline_config(
    *,
    model: str,
    provider: str,
    judge_model: str,
    max_tokens: int | None,
    base_url: str | None,
    env: Mapping[str, str],
) -> Config:
    """One matrix model's config: the model, provider, judge and cap its
    baseline names, with the endpoint, key and streaming from the run.

    NARE_BENCH_MAX_TOKENS is not read, since the cap is the baseline's, but
    the cap is still held to the ceiling nare enforces without streaming.
    """
    if provider not in KEY_VARS:
        raise ConfigError(
            f"provider {provider!r} is not one nare supports: " + ", ".join(KEY_VARS)
        )
    config = resolve_config(
        model=model,
        provider=provider,
        base_url=route(base_url, provider),
        judge_model=judge_model,
        env={k: v for k, v in env.items() if k != "NARE_BENCH_MAX_TOKENS"},
    )
    if max_tokens is not None:
        _check_ceiling(
            "the baseline's max_tokens", max_tokens, provider, model, config.stream
        )
    return replace(config, max_tokens=max_tokens)
```

`resolve_config` gets every value as a flag, so `NARE_MODEL`, `NARE_PROVIDER` and `NARE_BENCH_JUDGE_MODEL` never apply, and it still demands the provider's key and reads `NARE_STREAM`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest benchmarks/tests/test_config.py -q`
Expected: PASS, including the existing `test_a_cap_above_what_anthropic_takes_unstreamed_needs_streaming` (its message still names `NARE_STREAM`).

- [ ] **Step 5: Build and commit**

```bash
bin/build
git add benchmarks/runner/config.py benchmarks/tests/test_config.py
git commit -m "feat(bench): a config per baseline, with each provider's route (#45)"
```

---

### Task 6: `bin/bench models`

**Files:**
- Create: `benchmarks/runner/models.py`
- Create: `benchmarks/tests/test_models.py`
- Modify: `benchmarks/runner/__main__.py` (imports, parser, `show_models`, dispatch)
- Test: `benchmarks/tests/test_main.py`

**Interfaces:**
- Produces: `ListingError`, `ServedModel(name, window, input_price, output_price)`, `Listing(models, detailed)` with `.names`, `proxy_root`, `list_models(base_url, api_key, *, client=None) -> Listing`, `baseline_tiers(directory) -> dict[str, list[str]]`, `render_models(listing, tiers) -> str`. `main(["models"])`.

- [ ] **Step 1: Write the failing tests**

Create `benchmarks/tests/test_models.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest

from benchmarks.runner.models import (
    Listing,
    ListingError,
    ServedModel,
    baseline_tiers,
    list_models,
    render_models,
)
from benchmarks.runner.report import BaselineMeta, dumps_baseline

INFO = {
    "data": [
        {
            "model_name": "claude-haiku-5-5",
            "litellm_params": {"api_base": "https://upstream.example/secret"},
            "model_info": {
                "max_input_tokens": 1_000_000,
                "input_cost_per_token": 1e-7,
                "output_cost_per_token": 5e-7,
            },
        },
        {"model_name": "gpt-5-nano", "model_info": {"max_input_tokens": 400_000}},
        # A second deployment of the same alias.
        {"model_name": "gpt-5-nano", "model_info": {"max_input_tokens": 1}},
    ]
}
NAMES = {"data": [{"id": "gpt-5-nano"}, {"id": "claude-haiku-5-5"}]}


def client(
    answer: Callable[[httpx2.Request], httpx2.Response],
    seen: list[httpx2.Request] | None = None,
) -> httpx2.Client:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(request)
        return answer(request)

    return httpx2.Client(transport=httpx2.MockTransport(handler))


def by_path(**bodies: Any) -> Callable[[httpx2.Request], httpx2.Response]:
    """Answer each path with its body, or with the status it maps to."""

    def answer(request: httpx2.Request) -> httpx2.Response:
        body = bodies.get(request.url.path.strip("/").replace("/", "_"))
        if isinstance(body, int):
            return httpx2.Response(body, text="no")
        if body is None:
            return httpx2.Response(404, text="no")
        return httpx2.Response(200, json=body)

    return answer


def test_model_info_gives_window_and_price_per_million() -> None:
    seen: list[httpx2.Request] = []
    listing = list_models(
        "https://proxy.example/v1/",
        "sk-proxy",
        client=client(by_path(v1_model_info=INFO), seen),
    )
    assert listing.detailed
    haiku, nano = listing.models
    assert (haiku.name, haiku.window) == ("claude-haiku-5-5", 1_000_000)
    assert (haiku.input_price, haiku.output_price) == pytest.approx((0.1, 0.5))
    # The first deployment speaks for the alias, and missing prices are None.
    assert nano == ServedModel("gpt-5-nano", 400_000, None, None)
    # The listing hangs off the root, with the run's key as a Bearer token.
    assert [str(r.url) for r in seen] == ["https://proxy.example/v1/model/info"]
    assert seen[0].headers["authorization"] == "Bearer sk-proxy"


def test_without_model_info_the_names_come_from_v1_models() -> None:
    listing = list_models(
        "https://proxy.example",
        "k",
        client=client(by_path(v1_model_info=404, v1_models=NAMES)),
    )
    assert not listing.detailed
    assert listing.names == {"claude-haiku-5-5", "gpt-5-nano"}
    assert all(m.window is None for m in listing.models)


@pytest.mark.parametrize(
    "fail",
    [
        lambda request: httpx2.Response(401, text="no"),
        lambda request: httpx2.Response(200, text="not json"),
        lambda request: httpx2.Response(200, json={"error": "x"}),
    ],
)
def test_a_listing_neither_endpoint_answers_raises(
    fail: Callable[[httpx2.Request], httpx2.Response],
) -> None:
    with pytest.raises(ListingError):
        list_models("https://proxy.example", "k", client=client(fail))


def test_a_listing_error_never_names_the_endpoint() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("failed: https://secret.example", request=request)

    with pytest.raises(ListingError) as caught:
        list_models("https://secret.example", "k", client=client(refuse))
    assert "secret.example" not in str(caught.value)
    assert "ConnectError" in str(caught.value)


def test_render_shows_window_price_and_baselines_but_no_upstream() -> None:
    listing = list_models(
        "https://proxy.example", "k", client=client(by_path(v1_model_info=INFO))
    )
    text = render_models(listing, {"gpt-5-nano": ["full", "smoke"]})
    haiku = next(line for line in text.splitlines() if "haiku" in line)
    nano = next(line for line in text.splitlines() if "nano" in line)
    assert haiku.split() == ["claude-haiku-5-5", "1,000,000", "0.1", "0.5", "-"]
    assert nano.split() == ["gpt-5-nano", "400,000", "-", "-", "full,", "smoke"]
    assert "upstream" not in text


def test_render_says_when_only_names_came_back() -> None:
    text = render_models(Listing((ServedModel("m"),), detailed=False), {})
    assert "names only" in text


def test_render_names_a_baseline_the_endpoint_does_not_serve() -> None:
    listing = Listing((ServedModel("gpt-5-nano"),), detailed=True)
    text = render_models(listing, {"claude-haiku-4-5": ["smoke"]})
    assert "not served, though it has a baseline: claude-haiku-4-5 (smoke)" in text


def test_baselines_are_matched_on_their_meta_model(tmp_path: Path) -> None:
    for name, model in (
        ("spark-glm-5-3-flash.smoke.toml", "spark/glm-5.3-flash"),
        ("spark-glm-5-3-flash.full.toml", "spark/glm-5.3-flash"),
        ("gpt-5-nano.smoke.toml", "gpt-5-nano"),
    ):
        meta = BaselineMeta("t", "abc1234", "openai", model, "j")
        (tmp_path / name).write_text(dumps_baseline(meta, []))
    assert baseline_tiers(tmp_path) == {
        "gpt-5-nano": ["smoke"],
        "spark/glm-5.3-flash": ["full", "smoke"],
    }


def test_no_baselines_directory_means_no_baselines(tmp_path: Path) -> None:
    assert baseline_tiers(tmp_path / "missing") == {}
```

Append to `benchmarks/tests/test_main.py`:

```python
def endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *served: str
) -> list[tuple[str, str]]:
    """A checkout and an endpoint listing `served`; returns each listing call."""
    import benchmarks.runner.__main__ as bench
    from benchmarks.runner.models import Listing, ServedModel

    calls: list[tuple[str, str]] = []

    def listing(base_url: str, api_key: str) -> Listing:
        calls.append((base_url, api_key))
        return Listing(tuple(ServedModel(name) for name in served), detailed=True)

    results_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(bench, "list_models", listing)
    monkeypatch.setenv("NARE_BASE_URL", "https://proxy.example")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    return calls


def test_models_lists_what_the_endpoint_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = endpoint(tmp_path, monkeypatch, "gpt-5-nano")
    assert main(["models"]) == 0
    assert calls == [("https://proxy.example", "k")]
    assert "gpt-5-nano" in capsys.readouterr().out


def test_models_without_a_base_url_exits_two_with_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = endpoint(tmp_path, monkeypatch, "gpt-5-nano")
    monkeypatch.delenv("NARE_BASE_URL")
    assert main(["models"]) == 2
    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) == 1
    assert "no base URL" in err
    assert calls == []


def test_models_reports_a_listing_that_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import benchmarks.runner.__main__ as bench
    from benchmarks.runner.models import Listing, ListingError

    endpoint(tmp_path, monkeypatch)

    def refuse(base_url: str, api_key: str) -> Listing:
        raise ListingError("/v1/models: HTTP 401")

    monkeypatch.setattr(bench, "list_models", refuse)
    assert main(["models"]) == 1
    assert "HTTP 401" in capsys.readouterr().err
```

`results_dir` (already in the file) clears `NARE_PROVIDER`, so the listing sends `ANTHROPIC_API_KEY`.

- [ ] **Step 2: Run the tests to verify they fail**

Run each file on its own, since pytest stops at a collection error:

```bash
uv run pytest benchmarks/tests/test_models.py -q
uv run pytest benchmarks/tests/test_main.py -q -k "test_models_"
```

Expected: the first fails to collect, `ModuleNotFoundError: No module named 'benchmarks.runner.models'`. The second runs the three `test_models_*` tests, and each fails with the same `ModuleNotFoundError` from the import inside `endpoint`.

- [ ] **Step 3: Create the listing module**

Create `benchmarks/runner/models.py`:

```python
"""What the endpoint serves, read live.

Aliases on a proxy change without notice, and a list in a document goes
stale. `bin/bench models` prints this before a person or an agent picks a
model, and `run --matrix` checks its models against it before spending
anything.

httpx2, never urllib: Cloudflare in front of the proxy refuses urllib's
default User-Agent (error 1010). It reads the `model_info` keys ModelMetadata
reads, and nothing from `litellm_params`, which names upstream endpoints.
ModelMetadata is not reused: a transport builds it with that transport's
client and auth header, and it keeps only its own model's entry.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2

from benchmarks.runner.report import load_baseline

TIMEOUT = 10.0


class ListingError(Exception):
    """The endpoint did not say what it serves. Never carries the endpoint."""


@dataclass(frozen=True)
class ServedModel:
    name: str
    window: int | None = None
    # Dollars per million tokens.
    input_price: float | None = None
    output_price: float | None = None


@dataclass(frozen=True)
class Listing:
    models: tuple[ServedModel, ...]
    # False when only /v1/models answered: names, with no window or price.
    detailed: bool

    @property
    def names(self) -> set[str]:
        return {m.name for m in self.models}


def proxy_root(base_url: str) -> str:
    """As ModelMetadata.load does: the listing hangs off the root, not /v1."""
    return base_url.rstrip("/").removesuffix("/v1")


def _window(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _per_million(value: Any) -> float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value < 0
    ):
        return None
    return value * 1_000_000


def _get(client: httpx2.Client, root: str, path: str, api_key: str) -> Any:
    try:
        response = client.get(
            f"{root}{path}", headers={"authorization": f"Bearer {api_key}"}
        )
    except httpx2.HTTPError as exc:
        # The type only: an exception's text can carry the URL.
        raise ListingError(f"{path}: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise ListingError(f"{path}: HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise ListingError(f"{path}: the body is not JSON") from exc


def _entries(payload: Any, path: str) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ListingError(f"{path}: no data list")
    return [entry for entry in data if isinstance(entry, dict)]


def _from_model_info(payload: Any) -> tuple[ServedModel, ...]:
    served: dict[str, ServedModel] = {}
    for entry in _entries(payload, "/v1/model/info"):
        name = entry.get("model_name")
        if not isinstance(name, str) or name in served:
            # A proxy lists one entry per deployment; the first one speaks.
            continue
        info = entry.get("model_info")
        info = info if isinstance(info, dict) else {}
        served[name] = ServedModel(
            name,
            _window(info.get("max_input_tokens")),
            _per_million(info.get("input_cost_per_token")),
            _per_million(info.get("output_cost_per_token")),
        )
    if not served:
        raise ListingError("/v1/model/info: no models")
    return tuple(served[name] for name in sorted(served))


def _from_models(payload: Any) -> tuple[ServedModel, ...]:
    names = {
        entry["id"]
        for entry in _entries(payload, "/v1/models")
        if isinstance(entry.get("id"), str)
    }
    return tuple(ServedModel(name) for name in sorted(names))


def list_models(
    base_url: str, api_key: str, *, client: httpx2.Client | None = None
) -> Listing:
    """LiteLLM's /v1/model/info, with each model's window and prices, or
    /v1/models, with names only, when that does not answer.

    Raises ListingError when neither answers. The key goes as a Bearer token.
    """
    root = proxy_root(base_url)
    http = client or httpx2.Client(timeout=TIMEOUT)
    try:
        try:
            info = _get(http, root, "/v1/model/info", api_key)
            return Listing(_from_model_info(info), detailed=True)
        except ListingError:
            pass
        return Listing(_from_models(_get(http, root, "/v1/models", api_key)), False)
    finally:
        if client is None:
            http.close()


def baseline_tiers(directory: Path) -> dict[str, list[str]]:
    """Each model that has a baseline, and the tiers it has one for.

    Matched on the baseline's [meta] model, since file names are slugs.
    """
    found: dict[str, list[str]] = {}
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.toml")):
        tier = path.stem.rsplit(".", 1)[-1]
        found.setdefault(load_baseline(path).meta.model, []).append(tier)
    return {model: sorted(tiers) for model, tiers in found.items()}


def _price(value: float | None) -> str:
    return "-" if value is None else f"{value:g}"


def render_models(listing: Listing, tiers: Mapping[str, Sequence[str]]) -> str:
    rows = [("model", "window", "$/M in", "$/M out", "baselines")]
    for m in listing.models:
        rows.append(
            (
                m.name,
                "-" if m.window is None else f"{m.window:,}",
                _price(m.input_price),
                _price(m.output_price),
                ", ".join(tiers.get(m.name, ())) or "-",
            )
        )
    width = max(len(row[0]) for row in rows)
    lines = [
        f"{name:<{width}}  {window:>9}  {cost_in:>6}  {cost_out:>7}  {held}"
        for name, window, cost_in, cost_out, held in rows
    ]
    if not listing.detailed:
        lines.append(
            "names only: /v1/model/info did not answer, so windows and prices "
            "are unknown"
        )
    for name in sorted(set(tiers) - listing.names):
        lines.append(
            f"not served, though it has a baseline: {name} ({', '.join(tiers[name])})"
        )
    return "\n".join(lines)
```

- [ ] **Step 4: Wire the subcommand**

In `benchmarks/runner/__main__.py`:

Replace `from benchmarks.runner.config import Config, ConfigError, resolve_config` with:

```python
from benchmarks.runner.config import KEY_VARS, Config, ConfigError, resolve_config
```

After the `from benchmarks.runner.judge import (...)` block, add:

```python
from benchmarks.runner.models import (
    ListingError,
    baseline_tiers,
    list_models,
    render_models,
)
```

In `build_parser`, directly above the final `return parser`, add:

```python
    models = sub.add_parser(
        "models", help="list what the endpoint serves, with window, price, baselines"
    )
    models.add_argument("--provider", help="whose key to send (env: NARE_PROVIDER)")
    models.add_argument("--base-url", help="the proxy (env: NARE_BASE_URL)")
```

Directly above `def main(argv: list[str] | None = None) -> int:`, add:

```python
def show_models(args: argparse.Namespace, root: Path) -> int:
    """`bin/bench models`: free, it lists and calls no model."""
    env = os.environ
    base_url = args.base_url or env.get("NARE_BASE_URL")
    if not base_url:
        print(
            "bench: no base URL, so there is no proxy to ask; pass --base-url or "
            "set NARE_BASE_URL",
            file=sys.stderr,
        )
        return 2
    provider = args.provider or env.get("NARE_PROVIDER") or "anthropic"
    key_var = KEY_VARS.get(provider)
    if key_var is None:
        print(
            f"bench: unknown provider {provider!r}; nare supports: "
            + ", ".join(KEY_VARS),
            file=sys.stderr,
        )
        return 2
    api_key = env.get(key_var)
    if not api_key:
        print(f"bench: {key_var} is not set", file=sys.stderr)
        return 2
    try:
        listing = list_models(base_url, api_key)
    except ListingError as exc:
        print(f"bench: the endpoint did not list its models: {exc}", file=sys.stderr)
        return 1
    print(render_models(listing, baseline_tiers(root / "benchmarks" / "baselines")))
    return 0
```

In `main`, replace the no-subcommand message and the `compare-judges` dispatch:

```python
        print(
            "bench needs a subcommand: run, compare, bless, verify, or compare-judges",
            file=sys.stderr,
        )
        return 2

    root = repo_root()
    if args.command == "compare-judges":
        # No tier: it compares whatever cases the results file holds.
        return compare_judges(args, root)
```

with:

```python
        print(
            "bench needs a subcommand: run, compare, bless, verify, "
            "compare-judges, or models",
            file=sys.stderr,
        )
        return 2

    root = repo_root()
    if args.command == "compare-judges":
        # No tier: it compares whatever cases the results file holds.
        return compare_judges(args, root)
    if args.command == "models":
        return show_models(args, root)
```

If `ruff format` rewraps the `KEY_VARS` import, keep its version.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest benchmarks/tests -q`
Expected: PASS.

- [ ] **Step 6: Build and commit**

```bash
bin/build
git add benchmarks/runner/models.py benchmarks/runner/__main__.py benchmarks/tests/test_models.py benchmarks/tests/test_main.py
git commit -m "feat(bench): bin/bench models lists what the endpoint serves (#45)"
```

---

### Task 7: The case-by-model table

**Files:**
- Modify: `benchmarks/runner/report.py`
- Test: `benchmarks/tests/test_report.py`

**Interfaces:**
- Produces: `render_matrix(columns: Sequence[tuple[str, Comparison]], judge: str) -> str`, a padded Markdown table and a judge note.

- [ ] **Step 1: Write the failing tests**

In `benchmarks/tests/test_report.py`, add `render_matrix,` after `render,` in the `from benchmarks.runner.report import (` block, and append:

```python
def rated(case: str, passes: int, fails: int, errors: int = 0) -> CaseSummary:
    inconclusive = errors > (passes + fails + errors) / 2
    return CaseSummary(
        case=case,
        passes=passes,
        fails=fails,
        errors=errors,
        pass_rate=None if inconclusive else passes / (passes + fails),
        inconclusive=inconclusive,
        tokens_median=1000,
        turns_median=4,
        judge_met_median=None,
    )


def test_the_matrix_table_puts_cases_down_and_models_across() -> None:
    haiku = compare(
        [rated("ask", 0, 7), rated("edit", 3, 0)],
        baseline_of(rated("ask", 2, 1), rated("edit", 3, 0)),
        confirmed=True,
    )
    nano = compare(
        [rated("ask", 3, 0), rated("edit", 0, 0, errors=3)],
        baseline_of(rated("ask", 1, 2), rated("edit", 3, 0)),
        confirmed=True,
    )
    text = render_matrix([("haiku", haiku), ("nano", nano)], "sonnet")
    assert text.splitlines() == [
        "| case | haiku          | nano         |",
        "| ---- | -------------- | ------------ |",
        "| ask  | 0/7 regression | 3/3          |",
        "| edit | 3/3            | inconclusive |",
        "",
        "Judge: sonnet, for every model.",
    ]


def test_a_case_a_model_did_not_run_is_a_dash() -> None:
    # --case limits the run; the baseline's other cases come back missing.
    ran = compare([rated("ask", 1, 2)], baseline_of(rated("ask", 1, 2)))
    skipped = compare([], baseline_of(rated("ask", 1, 2), rated("edit", 3, 0)))
    text = render_matrix([("a", ran), ("b", skipped)], "j")
    assert "| ask  | 1/3 | -   |" in text.splitlines()
    assert "edit" not in text


def test_the_matrix_table_says_when_the_judge_judged_itself() -> None:
    comparison = compare([rated("ask", 1, 2)], baseline_of(rated("ask", 1, 2)))
    text = render_matrix([("sonnet", comparison)], "sonnet")
    assert text.splitlines()[-1] == (
        "Judge: sonnet, for every model. It judged its own repetitions too."
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_report.py -q`
Expected: collection error, `ImportError: cannot import name 'render_matrix'`.

- [ ] **Step 3: Implement**

Append to `benchmarks/runner/report.py` (`defaultdict`, `Sequence` and `_rate` are already there):

```python
def render_matrix(columns: Sequence[tuple[str, Comparison]], judge: str) -> str:
    """Cases down the side, models across: the table a PR pastes.

    Markdown, so it pastes into a PR or docs/evidence as printed. A cell is
    the pass rate with its n, marked when it is a confirmed regression or
    inconclusive, and `-` when that model did not run the case.
    """
    cells: dict[str, dict[str, str]] = defaultdict(dict)
    for model, comparison in columns:
        for delta in comparison.deltas:
            if delta.summary is None:
                continue
            if delta.verdict == "inconclusive":
                cell = "inconclusive"
            elif delta.verdict == "regression":
                cell = f"{_rate(delta.summary)} regression"
            else:
                cell = _rate(delta.summary)
            cells[delta.case][model] = cell
    models = [model for model, _ in columns]
    rows = [["case", *models]]
    rows += [
        [case, *(cells[case].get(m, "-") for m in models)] for case in sorted(cells)
    ]
    widths = [max(3, *(len(row[i]) for row in rows)) for i in range(len(rows[0]))]

    def line(row: Sequence[str]) -> str:
        padded = (cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        return "| " + " | ".join(padded) + " |"

    lines = [line(rows[0]), line(["-" * width for width in widths])]
    lines += [line(row) for row in rows[1:]]
    note = f"Judge: {judge}, for every model."
    if judge in models:
        note += " It judged its own repetitions too."
    return "\n".join([*lines, "", note])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest benchmarks/tests/test_report.py -q`
Expected: PASS.

- [ ] **Step 5: Build and commit**

```bash
bin/build
git add benchmarks/runner/report.py benchmarks/tests/test_report.py
git commit -m "feat(bench): render the case-by-model matrix table (#45)"
```

---

### Task 8: `bin/bench run --matrix`

**Files:**
- Modify: `benchmarks/runner/__main__.py` (imports, parser, `run_model`, `confirm`, `load_matrix`, `mixed_judges`, `served_check`, `run_matrix`, `main`)
- Test: `benchmarks/tests/test_main.py`

**Interfaces:**
- Consumes: `baseline_config` (Task 5), `list_models` and `ListingError` (Task 6), `render_matrix` (Task 7), the results layout (Task 4), `RepRecord.provider` and `.judge_model` (Task 3).
- Produces: `run_model(root, cases, config, name) -> tuple[Transport, list[RepRecord]]`, `confirm(root, cases, config, transport, name, records, baseline, strict_tokens) -> Comparison`, `load_matrix(root, tier) -> list[tuple[Path, Baseline]]`, `mixed_judges(matrix) -> str | None`, `served_check(matrix, base_url) -> int`, `run_matrix(args, root, cases) -> int`.

- [ ] **Step 1: Write the failing tests**

Append to `benchmarks/tests/test_main.py`:

```python
SONNET = "claude-sonnet-5-5"
HAIKU = BaselineMeta("t", "abc1234", "anthropic", "claude-haiku-5-5", SONNET)
NANO = BaselineMeta("t", "abc1234", "openai", "gpt-5-nano", SONNET)


def matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *metas: BaselineMeta,
    failing: str = "",
    served: tuple[str, ...] | None = None,
) -> list[Config]:
    """A checkout whose baselines are `metas`, each passing edit-docstring.

    Every model passes every rep except `failing`, which fails them all. The
    endpoint lists `served`, by default every baseline's model. Returns the
    config of every run_case call; the image build is one too.
    """
    import benchmarks.runner.__main__ as bench
    from benchmarks.runner.report import baseline_path

    listed = served if served is not None else tuple(meta.model for meta in metas)
    endpoint(tmp_path, monkeypatch, *listed)
    baselines = tmp_path / "benchmarks" / "baselines"
    baselines.mkdir()
    for meta in metas:
        baseline_path(baselines, meta.model, "smoke").write_text(
            dumps_baseline(meta, [summary("edit-docstring", 1.0)])
        )
    ran: list[Config] = []

    async def reps(
        case: Case, config: Config, transport: Transport, n: int, evidence: Path
    ) -> list[RepRecord]:
        ran.append(config)
        base = replace(
            record(case.id, model=config.model),
            outcome="fail" if config.model == failing else "pass",
            provider=config.provider,
            judge_model=config.judge_model,
        )
        return [replace(base, rep=rep) for rep in range(n)]

    monkeypatch.setattr(bench, "new_stamp", lambda: MATRIX_STAMP)
    monkeypatch.setattr(bench, "build_image", lambda root: ran.append(CONFIG))
    monkeypatch.setattr(bench, "judge_transport", lambda config: None)
    monkeypatch.setattr(bench, "run_case", reps)
    monkeypatch.setenv("NARE_MODEL", "ada/qwen3-14b")  # the matrix ignores it
    return ran


def table_row(out: str, case: str) -> list[str]:
    line = next(row for row in out.splitlines() if row.startswith(f"| {case} "))
    return [cell.strip() for cell in line.strip("|").split("|")]


def test_the_matrix_runs_each_model_on_its_own_provider_and_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = matrix(tmp_path, monkeypatch, HAIKU, NANO)
    assert main(["run", "--matrix", "--case", "edit-docstring"]) == 0
    assert [(c.model, c.provider, c.base_url, c.judge_model) for c in ran[1:]] == [
        ("claude-haiku-5-5", "anthropic", "https://proxy.example", SONNET),
        ("gpt-5-nano", "openai", "https://proxy.example/v1", SONNET),
    ]
    results = tmp_path / "benchmarks" / "results"
    assert sorted(p.name for p in results.glob("*.jsonl")) == [
        f"{MATRIX_STAMP}.claude-haiku-5-5.jsonl",
        f"{MATRIX_STAMP}.gpt-5-nano.jsonl",
    ]
    out = capsys.readouterr().out
    assert table_row(out, "case") == ["case", "claude-haiku-5-5", "gpt-5-nano"]
    assert table_row(out, "edit-docstring") == ["edit-docstring", "3/3", "3/3"]
    assert f"Judge: {SONNET}, for every model." in out


def test_a_matrix_regression_on_any_model_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    matrix(tmp_path, monkeypatch, HAIKU, NANO, failing="gpt-5-nano")
    assert main(["run", "--matrix", "--case", "edit-docstring"]) == 1
    row = table_row(capsys.readouterr().out, "edit-docstring")
    assert row == ["edit-docstring", "3/3", f"0/{CONFIRM_REPS} regression"]


def test_a_matrix_with_two_judges_exits_two_before_any_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = matrix(tmp_path, monkeypatch, HAIKU, replace(NANO, judge_model="gpt-5-nano"))
    assert main(["run", "--matrix"]) == 2
    assert ran == []
    err = capsys.readouterr().err
    assert "claude-haiku-5-5.smoke.toml" in err
    assert "gpt-5-nano.smoke.toml" in err


def test_a_matrix_model_without_its_key_exits_two_before_any_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = matrix(tmp_path, monkeypatch, HAIKU, NANO)
    monkeypatch.delenv("OPENAI_API_KEY")
    assert main(["run", "--matrix"]) == 2
    assert ran == []
    err = capsys.readouterr().err
    assert "gpt-5-nano.smoke.toml" in err
    assert "OPENAI_API_KEY" in err


def test_a_model_the_endpoint_does_not_list_stops_the_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = matrix(tmp_path, monkeypatch, HAIKU, NANO, served=("claude-haiku-5-5",))
    assert main(["run", "--matrix"]) == 2
    assert ran == []
    err = capsys.readouterr().err
    assert "'gpt-5-nano' (gpt-5-nano.smoke.toml)" in err


def test_a_listing_that_fails_warns_and_the_matrix_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import benchmarks.runner.__main__ as bench
    from benchmarks.runner.models import Listing, ListingError

    ran = matrix(tmp_path, monkeypatch, HAIKU)

    def refuse(base_url: str, api_key: str) -> Listing:
        raise ListingError("/v1/models: HTTP 503")

    monkeypatch.setattr(bench, "list_models", refuse)
    assert main(["run", "--matrix", "--case", "edit-docstring"]) == 0
    assert [c.model for c in ran[1:]] == ["claude-haiku-5-5"]
    assert "warning" in capsys.readouterr().err


def test_a_matrix_without_a_base_url_warns_and_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = matrix(tmp_path, monkeypatch, HAIKU, NANO)
    monkeypatch.delenv("NARE_BASE_URL")
    assert main(["run", "--matrix", "--case", "edit-docstring"]) == 0
    assert [c.base_url for c in ran[1:]] == [None, None]
    assert "no base URL" in capsys.readouterr().err


def test_the_matrix_takes_no_model_provider_or_judge_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = matrix(tmp_path, monkeypatch, HAIKU)
    for flag in (["--model", "m"], ["--provider", "openai"], ["--judge-model", "j"]):
        assert main(["run", "--matrix", *flag]) == 2
    assert ran == []


def test_an_empty_matrix_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = matrix(tmp_path, monkeypatch)
    assert main(["run", "--matrix"]) == 2
    assert ran == []


def test_bless_stays_per_model() -> None:
    # Blessing the whole matrix at once would make it too easy to bless away
    # a regression.
    with pytest.raises(SystemExit):
        build_parser().parse_args(["bless", "--matrix"])
```

`ran[0]` is the image build (`build_image` appends `CONFIG`), so `ran == []` proves nothing was built or run, and `ran[1:]` are the `run_case` calls. `edit-docstring` has `reps = 3` (smoke default).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_main.py -q -k "matrix or bless_stays"`
Expected: the nine new `run --matrix` tests FAIL with `SystemExit: 2` (argparse: `unrecognized arguments: --matrix`). `test_bless_stays_per_model` passes already (it pins that `bless` never gains `--matrix`), as do Task 4's tests that `-k matrix` also selects.

- [ ] **Step 3: Imports and parser**

In `benchmarks/runner/__main__.py`:

- `from collections import Counter` becomes `from collections import Counter, defaultdict`.
- The config import becomes:

```python
from benchmarks.runner.config import (
    KEY_VARS,
    Config,
    ConfigError,
    baseline_config,
    resolve_config,
)
```

- In the `from benchmarks.runner.report import (` block, add `render_matrix,` after `render,`.

In `build_parser`, inside the `for name, help_text in (...)` loop, directly after the `--allow-model-change` argument, add:

```python
        if name == "run":
            child.add_argument(
                "--matrix",
                action="store_true",
                help="run every model with a baseline in this tier, each against "
                "its own",
            )
```

- [ ] **Step 4: Move the single run's body into two helpers**

Directly above `def show_models(`, add:

```python
def run_model(
    root: Path, cases: Sequence[Case], config: Config, name: str
) -> tuple[Transport, list[RepRecord]]:
    """Every case at its own reps: results/<name>.jsonl, and each rep's
    evidence under results/<name>/."""
    transport = judge_transport(config)
    evidence = root / "benchmarks" / "results" / name
    records: list[RepRecord] = []
    for case in cases:
        records += asyncio.run(run_case(case, config, transport, case.reps, evidence))
    print(f"results: {write_results(root, records, name)}")
    return transport, records


def confirm(
    root: Path,
    cases: Sequence[Case],
    config: Config,
    transport: Transport,
    name: str,
    records: list[RepRecord],
    baseline: Baseline,
    strict_tokens: bool,
) -> Comparison:
    """Re-run each case whose pass rate dropped at CONFIRM_REPS, then compare.

    The re-run rewrites results/<name>.jsonl and its evidence instead of
    starting a second of each.
    """
    first = compare(
        summarize(records),
        baseline,
        token_band=TOKEN_BAND,
        strict_tokens=strict_tokens,
    )
    suspects = [d.case for d in first.deltas if d.verdict == "suspect"]
    if suspects:
        print(f"re-confirming {', '.join(suspects)} at {CONFIRM_REPS} reps...")
        evidence = root / "benchmarks" / "results" / name
        for case in [c for c in cases if c.id in suspects]:
            extra = asyncio.run(
                run_case(case, config, transport, CONFIRM_REPS, evidence)
            )
            records = [r for r in records if r.case != case.id] + extra
        write_results(root, records, name)
    return compare(
        summarize(records),
        baseline,
        confirmed=True,
        token_band=TOKEN_BAND,
        strict_tokens=strict_tokens,
    )
```

In `main`, replace the whole `if args.command == "run":` block (from that line through its `return final.exit_code`) with:

```python
    if args.command == "run":
        try:
            build_image(root)
        except SandboxError as exc:
            print(f"bench: {exc}", file=sys.stderr)
            return 2
        stamp = new_stamp()
        transport, records = run_model(root, cases, config, stamp)
        baseline_file = baseline_path(
            root / "benchmarks" / "baselines", config.model, args.tier
        )
        if not baseline_file.exists():
            print(render_first_run(summarize(records)))
            print(
                f"\nno baseline yet; bless one with: bin/bench bless --tier {args.tier}"
            )
            return 0

        baseline = load_baseline(baseline_file)
        if model_changed(baseline, config, args.allow_model_change):
            return 2
        final = confirm(
            root, cases, config, transport, stamp, records, baseline, args.strict_tokens
        )
        print(render(final))
        return final.exit_code
```

The single run behaves exactly as before: same stamp, same files, same confirmation, same exit code.

- [ ] **Step 5: Add the matrix**

Directly above `def show_models(`, after `confirm`, add:

```python
def load_matrix(root: Path, tier: str) -> list[tuple[Path, Baseline]]:
    """The matrix for a tier is its set of baselines."""
    directory = root / "benchmarks" / "baselines"
    if not directory.is_dir():
        return []
    return [(p, load_baseline(p)) for p in sorted(directory.glob(f"*.{tier}.toml"))]


def mixed_judges(matrix: Sequence[tuple[Path, Baseline]]) -> str | None:
    """Why the matrix has more than one judge, or None when it has one.

    When each model judges itself, a cross-model table measures the judges as
    much as the agents.
    """
    judges: dict[str, list[str]] = defaultdict(list)
    for path, baseline in matrix:
        judges[baseline.meta.judge_model].append(path.name)
    if len(judges) < 2:
        return None
    named = "; ".join(
        f"{judge or '(none)'} in {', '.join(names)}"
        for judge, names in sorted(judges.items())
    )
    return f"the matrix needs one judge, and its baselines name {len(judges)}: {named}"


def served_check(matrix: Sequence[tuple[Path, Config]], base_url: str | None) -> int:
    """The preflight: 2 when the endpoint does not list a matrix model.

    A listing that fails, or a run with no base URL and so no proxy to ask,
    warns and lets the run go on: an outage of the listing should not block a
    measurement. Each key lists once, since a proxy key can be limited to
    some models.
    """
    if not base_url:
        print(
            "bench: warning: no base URL, so the matrix's models were not checked "
            "against the endpoint's list",
            file=sys.stderr,
        )
        return 0
    served: dict[str, set[str]] = {}
    missing: list[str] = []
    for path, config in matrix:
        key = config.api_key or ""
        if key not in served:
            try:
                served[key] = list_models(base_url, key).names
            except ListingError as exc:
                print(
                    f"bench: warning: could not list the endpoint's models ({exc}); "
                    "running without that check",
                    file=sys.stderr,
                )
                return 0
        if config.model not in served[key]:
            missing.append(f"{config.model!r} ({path.name})")
    if missing:
        print(
            f"bench: the endpoint does not list {', '.join(missing)}; "
            "`bin/bench models` shows what it serves",
            file=sys.stderr,
        )
        return 2
    return 0


def run_matrix(args: argparse.Namespace, root: Path, cases: Sequence[Case]) -> int:
    """`run --matrix`: each baseline's model in turn, against its own baseline.

    Everything that can refuse the run does so before the image is built: an
    empty matrix, a second judge, a missing key, a cap above the non-streaming
    ceiling, a model the endpoint does not list. Sequential, never parallel:
    every run tags its image nare-bench:local.
    """
    matrix = load_matrix(root, args.tier)
    if not matrix:
        print(
            f"bench: no {args.tier} baselines, so the matrix is empty; bless one "
            f"with bin/bench bless --tier {args.tier} --model M",
            file=sys.stderr,
        )
        return 2
    why = mixed_judges(matrix)
    if why is not None:
        print(f"bench: {why}", file=sys.stderr)
        return 2
    base_url = args.base_url or os.environ.get("NARE_BASE_URL") or None
    configs: list[tuple[Path, Config]] = []
    for path, baseline in matrix:
        try:
            config = baseline_config(
                model=baseline.meta.model,
                provider=baseline.meta.provider,
                judge_model=baseline.meta.judge_model,
                max_tokens=baseline.meta.max_tokens,
                base_url=base_url,
                env=os.environ,
            )
        except ConfigError as exc:
            print(f"bench: {path.name}: {exc}", file=sys.stderr)
            return 2
        configs.append((path, config))
    code = served_check(configs, base_url)
    if code:
        return code
    try:
        build_image(root)
    except SandboxError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2

    stamp = new_stamp()
    columns: list[tuple[str, Comparison]] = []
    for (_, baseline), (_, config) in zip(matrix, configs, strict=True):
        print(f"\n== {config.model} ({config.provider}) ==")
        name = f"{stamp}.{slugify(config.model)}"
        transport, records = run_model(root, cases, config, name)
        final = confirm(
            root, cases, config, transport, name, records, baseline, args.strict_tokens
        )
        print(render(final))
        columns.append((config.model, final))
    print()
    print(render_matrix(columns, matrix[0][1].meta.judge_model))
    return max(comparison.exit_code for _, comparison in columns)
```

In `main`, directly after the `verify` block (after its `return code`) and before `try:\n        config = resolve_config(`, add:

```python
    if getattr(args, "matrix", False):
        if args.model or args.provider or args.judge_model:
            print(
                "bench: --matrix takes the model, provider and judge from each "
                "baseline; drop --model, --provider and --judge-model",
                file=sys.stderr,
            )
            return 2
        return run_matrix(args, root, cases)
```

It sits before `resolve_config`, which would demand `--model`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest benchmarks/tests -q`
Expected: PASS, including `test_a_confirmation_rerun_leaves_one_results_file` (the single run, now through `run_model` and `confirm`).

- [ ] **Step 7: Build and commit**

```bash
bin/build
git add benchmarks/runner/__main__.py benchmarks/tests/test_main.py
git commit -m "feat(bench): run --matrix runs each baseline's model against its own (#45)"
```

---

### Task 9: `bin/bench compare --matrix`

**Files:**
- Modify: `benchmarks/runner/__main__.py` (parser, `baselines_table`, `compare_matrix`, `main`)
- Test: `benchmarks/tests/test_main.py`

**Interfaces:**
- Produces: `baselines_table(root, tier, selected) -> int`, `compare_matrix(args, root, cases) -> int`. `compare --matrix` reads exactly the files of the newest matrix run (`latest_run(root, matrix=True)`).

- [ ] **Step 1: Write the failing tests**

Append to `benchmarks/tests/test_main.py`:

```python
def test_compare_matrix_prints_the_last_matrix_run_without_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = matrix(tmp_path, monkeypatch, HAIKU, NANO, failing="gpt-5-nano")
    assert main(["run", "--matrix", "--case", "edit-docstring"]) == 1
    printed = capsys.readouterr().out
    # A later single run is not part of the matrix run.
    write_results(
        tmp_path, [record("edit-docstring", model="m")], "2026-10-27T00-00-00Z"
    )
    del ran[:]
    assert main(["compare", "--matrix", "--case", "edit-docstring"]) == 1
    out = capsys.readouterr().out
    assert ran == []
    assert table_row(out, "edit-docstring") == table_row(printed, "edit-docstring")


def test_compare_matrix_without_a_matrix_run_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    matrix(tmp_path, monkeypatch, HAIKU)
    write_results(tmp_path, [record("edit-docstring", model="m")])
    assert main(["compare", "--matrix"]) == 2


def test_compare_matrix_baselines_prints_the_tiers_baselines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = matrix(tmp_path, monkeypatch, HAIKU, NANO)
    assert main(["compare", "--matrix", "--baselines"]) == 0
    assert ran == []
    out = capsys.readouterr().out
    assert table_row(out, "edit-docstring") == ["edit-docstring", "3/3", "3/3"]


def test_baselines_goes_with_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    matrix(tmp_path, monkeypatch, HAIKU)
    assert main(["compare", "--baselines", "--model", "claude-haiku-5-5"]) == 2
```

The baselines' `summary("edit-docstring", 1.0)` has `passes=3, fails=0`, hence `3/3`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_main.py -q -k "compare_matrix or baselines_goes"`
Expected: FAIL with `SystemExit: 2` (argparse: `unrecognized arguments: --matrix` or `--baselines`).

- [ ] **Step 3: Parser**

In `build_parser`, directly after the `if name == "run":` block added in Task 8, add:

```python
        if name == "compare":
            child.add_argument(
                "--matrix",
                action="store_true",
                help="print the last matrix run's case-by-model table",
            )
            child.add_argument(
                "--baselines",
                action="store_true",
                help="with --matrix: the table of the tier's baselines, not a run",
            )
```

- [ ] **Step 4: Implement**

Directly above `def show_models(`, after `run_matrix`, add:

```python
def baselines_table(root: Path, tier: str, selected: set[str]) -> int:
    """`compare --matrix --baselines`: the tier's baselines as the matrix
    table, with no results needed."""
    matrix = load_matrix(root, tier)
    if not matrix:
        print(f"bench: no {tier} baselines", file=sys.stderr)
        return 2
    why = mixed_judges(matrix)
    if why is not None:
        print(f"bench: {why}", file=sys.stderr)
        return 2
    columns = [
        (
            baseline.meta.model,
            compare(
                [s for s in baseline.cases.values() if s.case in selected],
                baseline,
                confirmed=True,
            ),
        )
        for _, baseline in matrix
    ]
    print(render_matrix(columns, matrix[0][1].meta.judge_model))
    return 0


def compare_matrix(args: argparse.Namespace, root: Path, cases: Sequence[Case]) -> int:
    """`compare --matrix`: the last matrix run's table, without running.

    Each model's results are compared with its own baseline under the rules
    `compare` uses, so the exit code is what `run --matrix` returned.
    """
    selected = {c.id for c in cases}
    if args.baselines:
        return baselines_table(root, args.tier, selected)
    files = latest_run(root, matrix=True)
    if not files:
        print(
            "bench: no matrix run yet; run `bin/bench run --matrix` first",
            file=sys.stderr,
        )
        return 2
    columns: list[tuple[str, Comparison]] = []
    judges: set[str] = set()
    for path in files:
        records = read_results(path)
        if not records:
            continue
        model = records[0].model
        baseline_file = baseline_path(
            root / "benchmarks" / "baselines", model, args.tier
        )
        if not baseline_file.exists():
            print(f"bench: no baseline at {baseline_file}", file=sys.stderr)
            return 2
        baseline = load_baseline(baseline_file)
        config = as_recorded(
            resolve_config(
                model=model,
                provider=None,
                base_url=None,
                judge_model=None,
                env={},
                need_key=False,
            ),
            records,
        )
        if model_changed(baseline, config, args.allow_model_change):
            return 2
        judges.add(config.judge_model)
        summaries = [s for s in summarize(records) if s.case in selected]
        columns.append(
            (
                model,
                compare(
                    summaries,
                    baseline,
                    confirmed=True,
                    token_band=TOKEN_BAND,
                    strict_tokens=args.strict_tokens,
                ),
            )
        )
    if not columns:
        print(f"bench: {stamp_of(files[0])} holds no results", file=sys.stderr)
        return 2
    print(render_matrix(columns, ", ".join(sorted(judges))))
    return max(comparison.exit_code for _, comparison in columns)
```

In `main`, replace the block added in Task 8:

```python
    if getattr(args, "matrix", False):
        if args.model or args.provider or args.judge_model:
            print(
                "bench: --matrix takes the model, provider and judge from each "
                "baseline; drop --model, --provider and --judge-model",
                file=sys.stderr,
            )
            return 2
        return run_matrix(args, root, cases)
```

with:

```python
    if getattr(args, "baselines", False) and not args.matrix:
        print("bench: --baselines goes with --matrix", file=sys.stderr)
        return 2
    if getattr(args, "matrix", False):
        if args.model or args.provider or args.judge_model:
            print(
                "bench: --matrix takes the model, provider and judge from each "
                "baseline; drop --model, --provider and --judge-model",
                file=sys.stderr,
            )
            return 2
        if args.command == "run":
            return run_matrix(args, root, cases)
        return compare_matrix(args, root, cases)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest benchmarks/tests -q`
Expected: PASS.

- [ ] **Step 6: Build and commit**

```bash
bin/build
git add benchmarks/runner/__main__.py benchmarks/tests/test_main.py
git commit -m "feat(bench): compare --matrix prints the table again, or the baselines' (#45)"
```

---

### Task 10: README

**Files:**
- Modify: `benchmarks/README.md`

No test reads the README. Write it without em dashes.

- [ ] **Step 1: The pointer to `models`, in "Running it"**

Replace:

```markdown
Docker is required; every repetition runs in a throwaway container.

    export ANTHROPIC_API_KEY=...        # OPENAI_API_KEY for NARE_PROVIDER=openai
    export NARE_MODEL=claude-haiku
```

with:

```markdown
Docker is required; every repetition runs in a throwaway container.

Before choosing a model, run `bin/bench models`. It lists what the endpoint
serves today, so do not pick a model from a list in a document: proxy aliases
change without notice. See "Which models the endpoint serves" below.

    export ANTHROPIC_API_KEY=...        # OPENAI_API_KEY for NARE_PROVIDER=openai
    export NARE_MODEL=...               # one that `bin/bench models` lists
```

In the command block below it, add a line above `bin/bench verify`:

```markdown
    bin/bench models                    # free: what can I run?
```

- [ ] **Step 2: bless and the recorded provider and judge**

After the paragraph that ends "three repetitions are not conclusive on their own.", add:

```markdown
`bless` writes the provider, judge and output cap the results were run with,
which each results line records, and not the ones in your shell. Results
written before the provider and judge were recorded take them from the flags.
```

- [ ] **Step 3: Two new sections**

Directly above `## What a run leaves behind`, add:

```markdown
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
```

- [ ] **Step 4: The matrix layout, in "What a run leaves behind"**

After the paragraph that ends "so let one run finish before starting another.", add:

```markdown
A matrix run is one run with one stamp, and writes each model's results the
same way under `<stamp>.<model>`, the model's name as its baseline file
spells it:

    benchmarks/results/<stamp>.<model>.jsonl
    benchmarks/results/<stamp>.<model>/<case>-<rep>/

After a matrix run, `compare --model M` and `bless --model M` read M's file
from it. `compare-judges` reads one model's run, so pass it that file with
`--results`.
```

- [ ] **Step 5: Check and commit**

```bash
grep -nP '\x{2014}' benchmarks/README.md || echo "no em dashes"
bin/build
git add benchmarks/README.md
git commit -m "docs(bench): run bin/bench models first, and how the matrix works (#45)"
```

---

### Task 11: Build, free checks, and the Part A gate

Nothing here spends money. Do not run two bench commands at once, and check that no other bench is running first, from any checkout.

- [ ] **Step 1: Build, and check what Part A must not touch**

```bash
cd /home/smadinya/vividynamics/nare-45
bin/build
git diff --stat origin/main -- src/nare .agents    # expect no output
grep -nP '\x{2014}' benchmarks/README.md docs/superpowers/plans/2026-10-26-ask-across-models.md || echo "no em dashes"
```

Expected: `bin/build` clean; no change under `src/nare` or `.agents`.

- [ ] **Step 2: `bin/bench verify` on the new cases (Docker, no model call)**

`verify` returns before `resolve_config` and never builds a transport (`main`, the `verify` branch): it builds the image, then runs each `bash` check against a pristine fixture with an idle session (`pristine_check`, `idle_artifacts`). It needs no key.

```bash
SCRATCH=/tmp/claude-1000/-home-smadinya-vividynamics-nare/3e63e146-0c68-47f0-96a4-418267733fa1/scratchpad
mkdir -p "$SCRATCH/docker" && echo '{}' > "$SCRATCH/docker/config.json"
export DOCKER_CONFIG="$SCRATCH/docker"
pgrep -af "[b]enchmarks.runner" || echo "no bench running"
docker ps --filter name=nare-bench --format '{{.Names}}'    # expect no output
bin/bench verify --case ambiguous-after-reading --case read-only-question
```

Expected, exit 0:

```
ambiguous-after-reading  ok (fails on the pristine fixture)
read-only-question       exempt (no bash checks to verify)
```

If anything is already running, wait for it to finish. Do not kill it.

- [ ] **Step 3: `bin/bench models` against the proxy (one free listing call)**

```bash
set -a; . ./.env; set +a; bin/bench models
```

Expected, exit 0: a table that lists `claude-haiku-5-5`, `claude-sonnet-5-5`, `gemini-3.1-flash-lite`, `gpt-5-nano`, `gpt-6-luna`, `gpt-6.1-sol` and `spark/glm-5.3-flash` with windows and prices, the `ada/*` models besides, and `-` in the baselines column everywhere (Task 2 deleted the only baseline). If a matrix model is missing, stop and report it: Part C cannot bless it. The output names models and prices only; do not echo any variable from `.env`.

- [ ] **Step 4 (optional, free): one spark pass of the new cases**

`spark/glm-5.3-flash` is free to the user. The runner has no reps flag, so this is three reps of each new case, judged by spark itself. It proves the result line, the in-container check that reads `/out/session.json`, and the judge work end to end. Its pass rates mean nothing for the gate. Run only when nothing else is running.

```bash
SCRATCH=/tmp/claude-1000/-home-smadinya-vividynamics-nare/3e63e146-0c68-47f0-96a4-418267733fa1/scratchpad
set -a; . ./.env; set +a
ROOT="${NARE_BASE_URL%/}"; ROOT="${ROOT%/v1}"
OPENAI_API_KEY="$ANTHROPIC_API_KEY" NARE_STREAM=1 DOCKER_CONFIG="$SCRATCH/docker" \
  bin/bench run --tier smoke \
    --case ambiguous-after-reading --case read-only-question \
    --model spark/glm-5.3-flash --provider openai \
    --base-url "$ROOT/v1" --judge-model spark/glm-5.3-flash
```

Expected: a `results:` line, a first-run table with six reps, and `no baseline yet`. Read one rep's `benchmarks/results/<stamp>/ambiguous-after-reading-0/judge.txt` and `stdout.jsonl` to see the judge answered and the run ended with a result line. **Do not bless.** The results are gitignored. A rep that stops with `max_tokens` is the model's output cap, not a bug here.

- [ ] **Step 5: Part A is done**

```bash
git status --short     # clean
git log --oneline origin/main..HEAD
```

Expected: the two spec commits, the plan commit, and Tasks 1 to 10's commits. Nothing is pushed.

---

# Part B: the description PR (branch `45-ask-description`)

> **Create this branch only after Task 11 has passed (Part A's last commit is Task 10's).** It is stacked on `45-ask-across-models`. It **must not be merged, or have its PR opened for review, before the seven "before" baselines from Part C step C1 are committed on `45-ask-across-models`** and this branch is rebased onto them: the description has to be measured against baselines blessed on unchanged `src/nare`.

### Task 12: The `ask` description says where a question goes, and stops leaking a case's answer

**Files:**
- Modify: `src/nare/tools.py` (the `ask` entry in `TOOL_SCHEMAS`, around line 500)
- Test: `tests/test_tools.py`

**Interfaces:**
- Produces: the `ask` schema's `description` and `questions.description`, verbatim from spec section 6. Name, `input_schema` shape and `required` unchanged. The existing `test_the_ask_question_states_its_default` keeps passing: the new `questions` text keeps "the one you will use if it goes unanswered".

- [ ] **Step 1: Branch from Part A's tip**

```bash
cd /home/smadinya/vividynamics/nare-45
git status --short                      # clean
git switch -c 45-ask-description 45-ask-across-models
```

- [ ] **Step 2: Write the failing tests**

In `tests/test_tools.py`, directly after `test_the_ask_question_states_its_default`, add:

```python
def test_the_ask_description_says_where_a_prose_question_goes() -> None:
    # Haiku saw the ambiguity and asked in its closing text, so the session
    # ended done and a headless caller moved on (#45).
    assert (
        "Ask only with this tool: a text reply ends the session as finished, so "
        "a question written there may never be answered."
    ) in schema_of("ask")["description"]


def test_the_ask_example_is_no_benchmark_cases_answer() -> None:
    # The old example was ambiguous-request's fixture and the answer its judge
    # looks for, so a model could pass that case by copying it (#45).
    questions = schema_of("ask")["input_schema"]["properties"]["questions"]
    assert "TIMEOUT" not in questions["description"]
    assert "Set the log level to INFO or to WARNING?" in questions["description"]
```

These pin the two sentences that change, as the file already pins phrases of the other descriptions. Whether the wording works is the matrix's job (spec section 6).

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -q -k "ask"`
Expected: the two new tests FAIL on their assertions; `test_the_ask_question_states_its_default` passes.

- [ ] **Step 4: Replace the two descriptions**

In `src/nare/tools.py`, replace the `ask` entry's `"description": (...)`:

```python
        "description": (
            "Stop and ask the caller to resolve an ambiguous request. Use this "
            "only when the request can reasonably be read more than one way and "
            "the readings lead to different changes, and only after reading the "
            "files involved. Ask only with this tool: a text reply ends the "
            "session as finished, so a question written there may never be "
            "answered. Do not use it for anything you can find out by reading "
            "files or running commands, for details you can decide yourself "
            "such as wording, style or names, to ask permission, or to offer "
            "follow-up work. Call ask on its own, before making any change. "
            "This ends the session."
        ),
```

and the `questions` property's `"description": (...)`:

```python
                    "description": (
                        "One question per item, naming the readings you are "
                        "choosing between and the one you will use if it goes "
                        "unanswered, e.g. 'Set the log level to INFO or to "
                        "WARNING? Unanswered, I will use WARNING.'"
                    ),
```

The joined strings are exactly spec section 6's text; only the line breaks differ, to stay under 88 columns at this indentation. Check it:

```bash
uv run python - <<'PY'
from nare.tools import TOOL_SCHEMAS
ask = next(s for s in TOOL_SCHEMAS if s["name"] == "ask")
print(ask["description"])
print(ask["input_schema"]["properties"]["questions"]["description"])
PY
```

Expected: the two texts of spec section 6, word for word.

- [ ] **Step 5: Run the tests and build**

```bash
uv run pytest tests/test_tools.py -q
bin/build
git diff --stat 45-ask-across-models    # only src/nare/tools.py and tests/test_tools.py
```

- [ ] **Step 6: Commit, and leave the worktree on Part A**

```bash
git add src/nare/tools.py tests/test_tools.py
git commit -m "feat(tools): ask says where a question goes, with an example no case uses (#45)"
git switch 45-ask-across-models
```

Do not run the bench on this branch. Measuring it is Part C step C3, after the "before" baselines exist.

---

# Part C: operator steps (not run by the implementer)

These spend money on paid models through the LiteLLM proxy. They are for the user to run or approve, one block at a time, in order. Never run two of them at once, and never while another bench runs from any checkout.

**Cost.** C1 is 7 models x 6 smoke cases x 3 reps = 126 reps; C3 is 126 more plus any seven-rep confirmations. The spec estimates a few dollars per pass, most of it the judge and the two capable models, and about an hour of wall clock per pass. Streamed turns report a cost of 0.0 through this proxy, so read the spend from the proxy, not from nare.

**The shell for every block** (run once per new shell, from the worktree):

```bash
cd /home/smadinya/vividynamics/nare-45
export DOCKER_CONFIG=/tmp/claude-1000/-home-smadinya-vividynamics-nare/3e63e146-0c68-47f0-96a4-418267733fa1/scratchpad/docker
mkdir -p "$DOCKER_CONFIG" && echo '{}' > "$DOCKER_CONFIG/config.json"
set -a; . ./.env; set +a
export OPENAI_API_KEY="$ANTHROPIC_API_KEY"   # the same key on this proxy
export NARE_STREAM=1                         # decision 10; the same for before and after
ROOT="${NARE_BASE_URL%/}"; ROOT="${ROOT%/v1}"
JUDGE=gpt-6.1-sol                            # spec section 3.4
pgrep -af "[b]enchmarks.runner" || echo "no bench running"
```

`.env` sets `NARE_MODEL` and `NARE_PROVIDER`; every command below passes `--model` and `--provider`, which win.

### C1: Bless the seven "before" baselines on Part A's head

> **2026-10-27:** #45 went ahead without the two Claude models (spec section 3.4), so C1, C3 and C4 cover the five `openai` models, and the "After 2026-11-01" lines below are not run.

```bash
git switch 45-ask-across-models
git status --short                  # clean
bin/bench models                    # all seven matrix models listed
```

Then, one model at a time. `run` makes the measurement and `bless` writes it (spec section 5). `bless` calls no model.

The judge is `gpt-6.1-sol`, a capable model on the `openai` route. It was chosen on 2026-10-27, in place of `claude-sonnet-5-5`, because the Anthropic account behind the proxy was out of quota until 2026-11-01, and it also keeps the judge off the account the two Claude models depend on. So the five `openai` models are blessed first, and the two Claude models after 2026-11-01, on this branch's code and with the same judge.

```bash
bin/bench run   --tier smoke --model gpt-5-nano --provider openai --base-url "$ROOT/v1" --judge-model "$JUDGE"
bin/bench bless --tier smoke --model gpt-5-nano --provider openai --judge-model "$JUDGE"

bin/bench run   --tier smoke --model gpt-6-luna --provider openai --base-url "$ROOT/v1" --judge-model "$JUDGE"
bin/bench bless --tier smoke --model gpt-6-luna --provider openai --judge-model "$JUDGE"

bin/bench run   --tier smoke --model gemini-3.1-flash-lite --provider openai --base-url "$ROOT/v1" --judge-model "$JUDGE"
bin/bench bless --tier smoke --model gemini-3.1-flash-lite --provider openai --judge-model "$JUDGE"

bin/bench run   --tier smoke --model spark/glm-5.3-flash --provider openai --base-url "$ROOT/v1" --judge-model "$JUDGE"
bin/bench bless --tier smoke --model spark/glm-5.3-flash --provider openai --judge-model "$JUDGE"

bin/bench run   --tier smoke --model gpt-6.1-sol --provider openai --base-url "$ROOT/v1" --judge-model "$JUDGE"
bin/bench bless --tier smoke --model gpt-6.1-sol --provider openai --judge-model "$JUDGE"

# After 2026-11-01:
bin/bench run   --tier smoke --model claude-haiku-5-5 --provider anthropic --base-url "$ROOT" --judge-model "$JUDGE"
bin/bench bless --tier smoke --model claude-haiku-5-5 --provider anthropic --judge-model "$JUDGE"

bin/bench run   --tier smoke --model claude-sonnet-5-5 --provider anthropic --base-url "$ROOT" --judge-model "$JUDGE"
bin/bench bless --tier smoke --model claude-sonnet-5-5 --provider anthropic --judge-model "$JUDGE"
```

Before each `bless`, read the run's table. `bless` leaves out an inconclusive case (more than half its reps errored), so if one is inconclusive, or reps stopped at `max_tokens` (`jq -r '.stop_reason' benchmarks/results/<stamp>.jsonl | sort | uniq -c`), fix the cause and re-run that model before blessing it. For an output cap, prefix the `run` with `NARE_BENCH_MAX_TOKENS=32000`; `bless` records it and the matrix reuses it. Each `bless` prints `(provider P, judge gpt-6.1-sol)`: check both.

Then check the matrix and commit, once after the five `openai` models:

```bash
ls benchmarks/baselines/            # five *.smoke.toml, no ada-qwen3-14b
grep -h '^judge_model' benchmarks/baselines/*.smoke.toml | sort -u   # one line: gpt-6.1-sol
bin/bench compare --matrix --baselines | tee benchmarks/results/45-before-table.md
bin/bench models                    # each blessed model now shows "smoke"
git add benchmarks/baselines
git commit -m "chore(bench): bless five smoke baselines, judged by gpt-6.1-sol (#45)"
```

and again after the two Claude models, with seven files, the same one judge line, and the commit message `chore(bench): bless the two Claude smoke baselines, judged by gpt-6.1-sol (#45)`.

Optional, and cheap next to C3: prove `--matrix` end to end on real models before C3's full pass, on the one short case. It is 21 reps and 21 judge calls. It writes a matrix run on Part A's head and changes no baseline; do not bless from it.

```bash
bin/bench run --matrix --case read-only-question
```

Expected: the preflight passes, seven `== <model> (<provider>) ==` sections, then a table with seven model columns. Exit 1 here would be a confirmed drop on unchanged code, which is noise worth noting on #45, not a bug.

### C2: Label the failing ambiguous reps, and write `docs/evidence/45/README.md`

List the reps to label, from the seven results files the blesses named (each `bless` prints `from <stamp>.jsonl`; if a model was re-run, use the file its bless named):

```bash
for f in benchmarks/results/<stamp-1>.jsonl benchmarks/results/<stamp-2>.jsonl; do   # all seven
  jq -r 'select((.case == "ambiguous-request" or .case == "ambiguous-after-reading")
               and .outcome == "fail")
         | [.model, .case, .rep, .status, .artifacts] | @tsv' "$f"
done
```

For each, read `benchmarks/results/<artifacts>/closing.txt`, `diff.patch` and `session.json`. The order of its tool calls:

```bash
jq -r '[.messages[] | select(.role == "assistant") | .content[]
        | select(.type == "tool_use") | .name] | join(" ")' \
  benchmarks/results/<artifacts>/session.json
```

Label each by hand (spec section 5): **asked in prose** (ended `done` with no change, and the closing text asks the caller something), **guessed** (ended `done` with a change to the fixture), **asked blind** (called `ask` before any `read` or `bash` call), **other** (one sentence). An `error` rep is not a failure and is not labelled.

Write `docs/evidence/45/README.md`, with no em dashes:

- A title, the date, the commit the baselines were blessed at (each baseline's `[meta] commit`), the judge, and `NARE_STREAM=1`.
- "Baseline matrix": the table from `benchmarks/results/45-before-table.md`, as printed.
- "Failure labels": a table `| model | case | rep | label | note |`, one row per labelled rep, and a count per model and label beneath it.

```bash
grep -nP '\x{2014}' docs/evidence/45/README.md || echo "no em dashes"
git add docs/evidence/45/README.md
git commit -m "docs: baseline matrix and failure labels (#45)"
```

PR 1 (`45-ask-across-models`) is now complete: code, cases, seven baselines, the `ada` baseline deleted, and `docs/evidence/45/`.

### C3: Measure Part B against those baselines

```bash
git switch 45-ask-description
git rebase 45-ask-across-models     # picks up the seven baselines and the evidence
bin/build
bin/bench run --matrix --base-url "$ROOT" | tee benchmarks/results/45-after-run.txt
echo "exit=${PIPESTATUS[0]}"
bin/bench compare --matrix | tee benchmarks/results/45-after-table.md
```

`run --matrix` takes each model's provider, judge and cap from its baseline, and gives `anthropic` models `$ROOT` and `openai` models `$ROOT/v1`. Exit 1 means some model has a confirmed regression or an inconclusive case. Expect token-band notes on the short cases: the description is about 30 tokens longer.

Then:

1. **The gate.** If any model shows `regression` on `edit-docstring`, `multi-file-rename` or `read-only-question`, stop. Do not re-bless. Record the table and the failing reps on #45: the description change fails the gate.
2. **Labels.** Label every failing rep of the two ambiguous cases from this matrix run, as in C2. Its files are `benchmarks/results/<stamp>.<model>.jsonl` and `benchmarks/results/<stamp>.<model>/<case>-<rep>/`, so C2's loop runs over `benchmarks/results/<stamp>.*.jsonl`, where `<stamp>` is the one the run printed in its `results:` lines.
3. **The target.** Note each model that does not reach 2 of 3 (or the matching rate after a seven-rep confirmation) on both ambiguous cases. #45 records those models with their labels.
4. **What drove a change.** If ambiguous-request dropped while ambiguous-after-reading held or rose, the old leaked example was doing the work (spec section 6). Say so in the PR.

### C4: Re-bless all seven on Part B

Only if C3's gate passed. Each `bless` reads that model's file from the matrix run, with the provider and judge the run recorded (decision 3), so no flags are needed:

```bash
for M in claude-haiku-5-5 claude-sonnet-5-5 gemini-3.1-flash-lite gpt-5-nano gpt-6-luna gpt-6.1-sol spark/glm-5.3-flash; do
  bin/bench bless --tier smoke --model "$M" || break
done
grep -h '^judge_model\|^provider' benchmarks/baselines/*.smoke.toml | sort | uniq -c
```

Add the after table and the after labels to `docs/evidence/45/README.md` under "After the description change", then:

```bash
git add benchmarks/baselines docs/evidence/45/README.md
git commit -m "chore(bench): re-bless the smoke matrix on the new ask description (#45)"
```

The PR 2 body carries the before table (from `docs/evidence/45/README.md`), the after table (`benchmarks/results/45-after-table.md`), and the labels of any ambiguous-case rep that still fails. No em dashes.

### C5: Decide on section 7

Count the labels from C3. If "asked in prose" is the most common label among some matrix model's failing ambiguous-case reps, section 7 (the loop nudge) is next, in its own plan and PR 3. If not, record on #45 that the description was enough, and stop.

---

## Section 7 is not planned

The loop nudge (spec section 7) is built only if C5 calls for it, so this plan has no task for it.

## Acceptance criteria (spec section 10)

| Criterion | Met by |
| --- | --- |
| `bin/bench run --matrix` runs every baseline's model in turn, with its provider and the base URL that provider needs, refuses a matrix with mixed judges with exit 2, and prints the case-by-model table | Tasks 5, 7, 8 (unit tests); first real run in Part C, C3 |
| `bin/bench compare --matrix` prints the table without running | Task 9 |
| `bin/bench models` lists the endpoint's models with window, price and baselines, and exits 2 with a one-line message when there is no base URL | Task 6; live check in Task 11, Step 3 |
| `benchmarks/README.md` tells a person or an agent to run `bin/bench models` before choosing a model, and `.agents/skills/` is unchanged | Task 10; checked in Task 11, Step 1 |
| A matrix model the endpoint does not list stops the run before any model call | Task 8 (`test_a_model_the_endpoint_does_not_list_stops_the_matrix`) |
| ambiguous-after-reading and read-only-question are in the smoke tier and pass `bin/bench verify` | Task 1; Task 11, Step 2 |
| Seven smoke baselines are blessed with one judge, and the `ada/qwen3-14b` baseline is deleted | Task 2 (deletion); Part C, C1 (blessing) |
| `docs/evidence/45/README.md` holds the baseline matrix table and the failure labels | Part C, C2 (the table from Task 9's `--baselines`) |
| The `ask` and `questions` descriptions are as in section 6 | Task 12 |
| The section 6 PR shows the matrix table before and after, and re-blesses all seven baselines after the change | Part C, C3 and C4 |
| No matrix model has a confirmed regression on edit-docstring, multi-file-rename or read-only-question | Part C, C3 (the gate) |
| Each matrix model either reaches 2 of 3 on both ambiguous cases, or #45 records its failure labels | Part C, C3 |
| Section 7 is built, nudging only under `nare run`, or #45 records why it was not | Section 7, conditional: Part C, C5 decides |
| `bin/build` passes | Every task; Task 11, Step 1; Task 12, Step 5 |
