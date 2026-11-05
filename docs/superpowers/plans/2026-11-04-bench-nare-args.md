# Benchmark Arguments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A case can pass `nare run` flags (`args` in `case.toml`), a run can pass a variant after `--` on `bin/bench run`, both are checked against nare's contract before anything is spent, every results line records them with the context window and compaction count, and results from a different variant do not compare against a baseline unless `--allow-arg-change`.

**Architecture:** All in `benchmarks/`; `src/nare` does not change. A new pure module, `benchmarks/runner/nare_args.py`, checks argument lists against `nare.contract.describe()` data, computes the variant key, and stages run prompt files (its one filesystem function). The run's arguments travel on `Config` as one `run_args: RunArgs` field, so `run_case`, `run_rep` and `run_agent` keep their signatures. `sandbox.run_agent` passes run then case arguments to the script as positional parameters, expanded with `"$@"`, and mounts staged prompt copies read-only at `/in`. `report.py` records the new fields and writes the variant into a baseline only when there is one. `__main__.py` splits off `--`, checks, stages, compares variants, and stops a run whose nare never started.

**Tech Stack:** Python 3.12+, pytest (`asyncio_mode = "auto"`), ruff, mypy strict, `uv`, Docker (bench runs only, never in tests).

**Spec:** `docs/superpowers/specs/2026-11-04-bench-nare-args-design.md` (commit d179283). Issue: #156.

## Spec issues found

None that are wrong against the code or that block building it as written. Five places where the spec is silent are settled under Decisions: abbreviated flags (nare's run parser has `allow_abbrev=True`, so `--mod x` would reach `--model`; Decision 4), a space-form value that starts with `-` (Decision 3), where the run-owned copy directory lives (Decision 5), whether a stopped run writes its results (Decision 7), and how a refusal shows an edited prompt at an unchanged path, where both argument lists read the same (Decision 9).

## Decisions (the plan's calls)

1. **The run's arguments live on `Config`,** as `run_args: RunArgs` (default empty). Why: `run_case`, `run_rep` and `run_agent` keep their signatures, so every fake in `test_main.py` keeps working; `as_recorded` already turns results back into a `Config`; and the matrix's fake `run_case` sees each model's arguments on the config it is handed.
2. **`benchmarks/runner/nare_args.py` imports no other runner module.** `config.py` imports `RunArgs` from it, so it must not import `config`, `case` or `sandbox`. It takes case arguments as `{case id: tokens}`.
3. **A space-form value must not start with `-`.** `--system -x` is refused as "needs a value", with a hint to write `--system=-x`. Why: argparse refuses such a value at startup, so this catches it before a paid rep.
4. **Flags match by exact long name.** An abbreviation (`--mod`, `--context`, `--max-tok`) is an unknown flag. Why: nare's run parser expands abbreviations, so without this a reserved flag gets through under a prefix.
5. **Staged copies live in the run's evidence directory,** `benchmarks/results/<name>/inputs/<n>-<basename>`, where `<name>` is the stamp (or `<stamp>.<model slug>` in a matrix) and `<n>` counts the run's files from 0 in order. nare gets `/in/<n>-<basename>`. Why: the evidence keeps what was measured, two files with one basename cannot collide, and `benchmarks/results` is outside the image's build context (`benchmarks/Dockerfile.dockerignore`). The stamp is therefore taken before staging, which is before the image builds. A run refused after staging leaves that directory, since nothing under `results/` is pruned.
6. **The key's parse needs no contract.** `pairs` treats the next token as a flag's value unless it starts with `-`. On a list `check_args` accepted, that split is exact (Decision 3), and a later rename of a nare flag cannot change an old baseline's key.
7. **A never-started rep raises `NeverStarted` out of `run_rep`.** `run` and `run --matrix` print it and exit 2, and write no results file for that run; the evidence of every rep that ran stays. Why: a results file from a stopped run would be the newest run, and `bless` reads the newest run. Before raising, `run_rep` writes `stopped.txt` in the run's evidence directory (the developer's requirement, 2026-11-04: no results file is needed, but the reason must be written down). It holds the case, rep, model and provider, the exit code, the run and case arguments nare was given, the rep's evidence path, and nare's stderr and stdout verbatim. The printed message names it.
8. **`--` is split off before argparse, at the first `--`.** Only a single `run` takes it. Every other command, and `run --matrix`, exits 2 on a `--`, even with nothing after it. `run --` with nothing after it is the default variant.
9. **Variants print through one formatter,** `report.render_variant`: `none`, or `shlex.join(args)` followed by each file's path and the first 12 hex digits of its digest, so an edited prompt at one path reads as different in the refusal and above the table.
10. **Shapes.** On `RepRecord`, `case_args` and `nare_args` are `list[str]` and `nare_files` a `dict` (they round-trip through JSON, as `usage` does). On `RunArgs` and `BaselineMeta`, arguments are `tuple[str, ...]` and files a `dict[str, str]`.
11. **The window and compactions are read on every rep,** error reps included, from whatever stdout the rep produced: the last `context_window` progress event, and the count of `compaction` progress events.
12. **`run --matrix` checks each baseline's `nare_args` against the contract and the selected cases** before staging them, since nare may have dropped or renamed a flag since the bless.

## Global Constraints

- Branch `feat/156-bench-nare-args`. Run every command from the repository root. Never touch `.claude/worktrees/`.
- `src/nare` does not change and the contract version stays 2. No existing case gains `args`, and no baseline is re-blessed: `git diff --stat main -- src/nare benchmarks/cases benchmarks/baselines` is empty at the end.
- Every existing baseline file renders byte for byte as it is, and every existing results file loads, as the default variant.
- Values never reach the agent script as text: run arguments and case arguments are positional parameters after the script, expanded with `"$@"`.
- Every refusal exits 2, and every refusal in `run` and `verify` happens before `build_image` and before any rep.
- Tests never reach Docker or a model. Fake them as the existing tests do: monkeypatch `repo_root`, `run_case`, `run_agent`, `run_check`, `git_diff`, `build_image`, `judge_transport`, `new_stamp` and `list_models` on `benchmarks.runner.__main__`, and `subprocess.run` or `_docker_run` in `benchmarks.runner.sandbox`; script tests run the real `_AGENT_SCRIPT` under `sh` with a stub `nare`.
- `bin/build` (ruff format --check, ruff check, mypy strict over `src tests benchmarks`, pytest) passes at the end of every task. If `ruff format --check` complains, run `uv run ruff format src tests benchmarks` and re-run.
- Write the failing test first, run it, see it fail for the stated reason, then implement.
- Never disable, skip or weaken a test. Existing tests pass unchanged.
- No em dashes in anything committed: README, docstrings, comments, error messages, commit messages. Use colons, commas, parentheses.
- No infrastructure detail in anything committed: tests use `https://proxy.example` and placeholder keys such as `k`.
- One commit per task, subject `type(bench): summary (#156)`, body ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. No push, no PR.
- No live (paid) benchmark run is a required step. The optional one in Task 12 needs the developer's go-ahead.

## Review Focus

1. **An abbreviated flag** (`--mod gpt-x`, `--max-tok 5`, `--context 9000`). nare's argparse would expand it, possibly into a reserved flag; the checker must call it unknown. Pinned in Task 2.
2. **A space-form value that starts with `-`** (`--system "-be terse"`, `--effort --tools`). argparse refuses it at startup, so a paid rep would never start; the checker refuses it and points at `--flag=value`, which it accepts. Pinned in Task 2.
3. **Shell metacharacters in an argument** (`--system '$(touch pwned) be terse'`). It must reach nare as one argv element, unexpanded. Pinned in Task 4, under the real script.
4. **Two run files with one basename** (`--system-file a/p.md --append-system-file b/p.md`). Neither copy may overwrite the other in `/in`. Pinned in Task 3.
5. **A relative prompt path given from a subdirectory of the checkout.** It is read relative to where `bin/bench` runs and recorded relative to the repository root, so the same file from two directories is one recorded path. Pinned in Tasks 3 and 8.

## File map

| File | Change | Task |
| --- | --- | --- |
| `benchmarks/runner/case.py` | `Case.args`, shape check | 1 |
| `benchmarks/runner/nare_args.py` | new: rules, checker, key; then `RunArgs`, `stage_inputs` | 2, 3 |
| `benchmarks/runner/config.py` | `Config.run_args` | 3 |
| `benchmarks/runner/sandbox.py` | script `"$@"`, argv, `/in` mount, `run_agent` docstring | 4 |
| `benchmarks/runner/grade.py` | `ContextUse`, `context_use` | 5 |
| `benchmarks/runner/report.py` | `RepRecord` fields; `BaselineMeta` fields; `render_variant`; `render_matrix(variants=)` | 5, 7 |
| `benchmarks/runner/__main__.py` | `run_rep` records; `NeverStarted`; `split_run_args`; checks and staging; `args_changed`, `variant_note`, `--allow-arg-change`; matrix | 5, 6, 8, 9, 10 |
| `benchmarks/tests/test_case.py`, `test_sandbox.py`, `test_grade.py`, `test_report.py` | tests | 1, 4, 5, 7 |
| `benchmarks/tests/test_nare_args.py` | new | 2, 3 |
| `benchmarks/tests/test_main_args.py` | new: every `__main__` test for this issue | 5, 6, 8, 9, 10 |
| `benchmarks/README.md` | "Arguments to nare", case `args`, `inputs/` | 11 |

`test_main.py` is already long, so the new `__main__` tests go in `test_main_args.py`, which imports its helpers (`CONFIG`, `DONE_LINE`, `HAIKU`, `KEPT`, `MATRIX_STAMP`, `NANO`, `SONNET`, `fake_sandbox`, `matrix`, `record`, `results_dir`, `sandboxed`, `summary`, `write_matrix_run`) from `benchmarks.tests.test_main`, as `test_main.py` imports `FakeJudge` from `test_judge`. Importing names that do not start with `test_` does not collect anything twice.

---

### Task 0: Start

- [ ] **Step 1: Check the branch**

```bash
git status --short
git branch --show-current
git log --oneline -3
```

Expected: branch `feat/156-bench-nare-args`; the only untracked path is `.claude/worktrees/` (and this plan, until it is committed); the top commit is d179283 (the spec) or this plan's commit on top of it.

- [ ] **Step 2: Build once**

Run: `bin/build`
Expected: clean.

---

### Task 1: A case can pass `args`

**Files:**
- Modify: `benchmarks/runner/case.py`
- Test: `benchmarks/tests/test_case.py`

**Interfaces:**
- Produces: `Case.args: tuple[str, ...]`, default `()`, the last field of `Case`. `load_case` raises `CaseError` naming the file when `args` is not a list of non-empty strings. Nothing here reads nare's contract.

- [ ] **Step 1: Write the failing tests**

Append to `benchmarks/tests/test_case.py`:

```python
def with_args(value: str) -> str:
    """GOOD with an args line among its top-level keys."""
    return GOOD.replace("max_turns = 12", f"max_turns = 12\nargs = {value}")


def test_a_case_without_args_passes_nothing(tmp_path: Path) -> None:
    assert load_case(write_case(tmp_path, "fix-failing-test", GOOD)).args == ()


@pytest.mark.parametrize(
    ("value", "args"),
    [('["--tools", "read,list,search"]', ("--tools", "read,list,search")), ("[]", ())],
)
def test_a_case_passes_its_args_as_written(
    tmp_path: Path, value: str, args: tuple[str, ...]
) -> None:
    case = load_case(write_case(tmp_path, "fix-failing-test", with_args(value)))
    assert case.args == args


@pytest.mark.parametrize(
    "value", ['"--tools read"', '["--tools", ""]', '["--max-tool-calls", 3]', "[[]]"]
)
def test_args_must_be_a_list_of_non_empty_strings(tmp_path: Path, value: str) -> None:
    with pytest.raises(CaseError, match="args must be a list of non-empty strings"):
        load_case(write_case(tmp_path, "fix-failing-test", with_args(value)))
```

`"max_turns = 12"` occurs once in `GOOD` (the result check says `max_turns = 10`), so the line lands among the top-level keys, before the first table.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_case.py -q`
Expected: FAIL. The first two tests fail with `AttributeError: 'Case' object has no attribute 'args'`, and each shape case with `DID NOT RAISE`.

- [ ] **Step 3: Implement `Case.args` in `benchmarks/runner/case.py`**

Add `args: tuple[str, ...] = ()` after `judge`, with a comment that these are flags for `nare run`, checked against nare's contract by `nare_args`, not here. In `load_case`, read `raw.get("args", [])`; anything but a list whose every item is a non-empty `str` raises `CaseError(f"{where}: args must be a list of non-empty strings, as nare run takes them")`. Pass `args=tuple(raw_args)` to `Case`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest benchmarks/tests -q`
Expected: PASS, including `test_cases.py` (no committed case has `args`).

- [ ] **Step 5: Build and commit**

```bash
bin/build
git add benchmarks/runner/case.py benchmarks/tests/test_case.py
git commit -F - <<'EOF'
feat(bench): a case can pass args to nare run (#156)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 2: Check arguments against nare's contract, and the variant key

**Files:**
- Create: `benchmarks/runner/nare_args.py`
- Test: `benchmarks/tests/test_nare_args.py` (new)

**Interfaces:**
- Produces, all in `benchmarks/runner/nare_args.py`:
  - `class ArgsError(Exception)`: arguments nare or the benchmark would refuse. Always exit 2.
  - `Pair = tuple[str, str | None]`: a flag and its value, `None` for a flag that takes none.
  - `RESERVED: dict[str, str]`: flag to its owner, for the 14 flags of spec section 6.2.
  - `RUN_ONLY: frozenset[str]`, `CASE_ONLY: frozenset[str]`, `HOST_FILE_FLAGS: frozenset[str]`.
  - `check_args(tokens: Sequence[str], flags: Mapping[str, Mapping[str, Any]], tools: Collection[str], *, case_id: str | None = None) -> list[Pair]`. `case_id=None` means the run arguments.
  - `check_invocation(run: Sequence[str], cases: Mapping[str, Sequence[str]], flags: Mapping[str, Mapping[str, Any]], tools: Collection[str]) -> None`: the run arguments, then each case's, then each cross-list conflict.
  - `nare_contract() -> tuple[dict[str, dict[str, Any]], list[str]]`: `describe()["flags"]` and `describe()["tools"]` from `nare.contract`.
  - `pairs(tokens: Sequence[str]) -> list[Pair]`: the contract-free split of a checked list.
  - `variant(args: Sequence[str], files: Mapping[str, str]) -> tuple[Pair, ...]`: the key of spec 9.1.

- [ ] **Step 1: Write the failing tests**

Create `benchmarks/tests/test_nare_args.py`:

```python
from __future__ import annotations

import re
from typing import Any

import pytest

from benchmarks.runner.nare_args import (
    CASE_ONLY,
    HOST_FILE_FLAGS,
    RESERVED,
    RUN_ONLY,
    ArgsError,
    Pair,
    check_args,
    check_invocation,
    nare_contract,
    pairs,
    variant,
)

NO_VALUE = {"--yes", "--jsonl", "--stream", "--bash-credentials", "--image-input"}
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
# The contract's shape, cut down to the flags the rules name and a few free ones.
FLAGS: dict[str, dict[str, Any]] = {
    flag: {"takes_value": flag not in NO_VALUE, "choices": None}
    for flag in (
        *RESERVED,
        *RUN_ONLY,
        *CASE_ONLY,
        "--tools",
        "--context-window",
        "--require-reads",
        "--image-input",
    )
}
FLAGS["--effort"] = {"takes_value": True, "choices": EFFORTS}
TOOLS = ["ask", "bash", "edit", "list", "read", "search", "write"]


def as_run(*tokens: str) -> list[Pair]:
    return check_args(tokens, FLAGS, TOOLS)


def as_case(*tokens: str) -> list[Pair]:
    return check_args(tokens, FLAGS, TOOLS, case_id="demo")


def test_the_rules_name_the_specs_flags() -> None:
    assert set(RESERVED) == {
        "--yes", "--jsonl", "--session", "--max-turns", "--model", "--provider",
        "--base-url", "--max-tokens", "--prompt-file", "--resume", "--replay",
        "--stream", "--contract", "--bash-credentials",
    }  # fmt: skip
    assert RUN_ONLY == {
        "--system", "--system-file", "--append-system", "--append-system-file"
    }  # fmt: skip
    assert CASE_ONLY == {"--schema", "--mcp-config", "--require-read", "--root"}
    assert HOST_FILE_FLAGS == {"--system-file", "--append-system-file"}


def test_both_forms_of_a_flag_give_the_same_pair() -> None:
    assert as_run("--effort", "low") == as_run("--effort=low") == [("--effort", "low")]


def test_a_flag_that_takes_no_value_gives_none() -> None:
    assert as_case("--image-input", "--tools", "read") == [
        ("--image-input", None),
        ("--tools", "read"),
    ]


def test_a_value_that_starts_with_a_dash_needs_the_equals_form() -> None:
    with pytest.raises(ArgsError, match=re.escape("--system needs a value")):
        as_run("--system", "-be terse")
    assert as_run("--system=-be terse") == [("--system", "-be terse")]


@pytest.mark.parametrize("value", ["read,list,search", "none", " read , list "])
def test_tools_may_name_the_tools_nare_lists_or_none(value: str) -> None:
    assert as_case("--tools", value) == [("--tools", value)]


@pytest.mark.parametrize(
    ("tokens", "message"),
    [
        (["--no-such-flag"], "unknown flag '--no-such-flag'"),
        # nare's parser expands abbreviations; the check never does.
        (["--mod", "x"], "unknown flag '--mod'"),
        (["--context", "9000"], "unknown flag '--context'"),
        (["--effort"], "--effort needs a value"),
        (["--effort", "--context-window", "9"], "--effort needs a value"),
        (
            ["--effort", "extreme"],
            "--effort 'extreme' is not one of low, medium, high, xhigh, max",
        ),
        (["--image-input=yes"], "--image-input takes no value"),
        (["--tools", "read,teleport"], "--tools names 'teleport'"),
        (["--tools", "none,read"], "--tools names 'none'"),
        (["fix the bug"], "stray 'fix the bug'"),
        (["-"], "stray '-'"),
        (["--"], "a bare '--'"),
    ],
)  # fmt: skip
def test_arguments_nare_would_refuse_are_refused(tokens: list[str], message: str) -> None:
    with pytest.raises(ArgsError, match=re.escape(f"run arguments: {message}")):
        check_args(tokens, FLAGS, TOOLS)


def test_a_case_refusal_names_the_case() -> None:
    with pytest.raises(ArgsError, match=re.escape("case demo: unknown flag '--nope'")):
        as_case("--nope")


@pytest.mark.parametrize("flag", sorted(RESERVED))
@pytest.mark.parametrize("case_id", [None, "demo"])
def test_a_reserved_flag_is_refused_in_either_list_and_either_form(
    flag: str, case_id: str | None
) -> None:
    forms = [[flag, "x"], [f"{flag}=x"]] if FLAGS[flag]["takes_value"] else [[flag]]
    for tokens in forms:
        with pytest.raises(ArgsError, match=re.escape(f"{flag} is reserved for {RESERVED[flag]}")):
            check_args(tokens, FLAGS, TOOLS, case_id=case_id)


@pytest.mark.parametrize("flag", sorted(RUN_ONLY))
def test_a_system_prompt_flag_is_a_run_argument_only(flag: str) -> None:
    assert as_run(flag, "x") == [(flag, "x")]
    with pytest.raises(ArgsError, match=re.escape(f"case demo: {flag} is a run argument")):
        as_case(flag, "x")


@pytest.mark.parametrize("flag", sorted(CASE_ONLY))
def test_a_work_tree_path_flag_is_a_case_argument_only(flag: str) -> None:
    assert as_case(flag, "x") == [(flag, "x")]
    with pytest.raises(
        ArgsError, match=re.escape(f"run arguments: {flag} names a path in the work tree")
    ):
        as_run(flag, "x")


def test_a_flag_in_both_lists_is_refused_naming_the_flag_and_the_case() -> None:
    with pytest.raises(
        ArgsError,
        match=re.escape("--context-window is in the run arguments and in case demo's args"),
    ):
        check_invocation(
            ["--context-window", "16000"], {"demo": ["--context-window=8000"]}, FLAGS, TOOLS
        )


def test_repeats_within_one_list_are_left_alone() -> None:
    check_invocation(
        [], {"demo": ["--require-read", "a.py", "--require-read", "b.py"]}, FLAGS, TOOLS
    )


def test_the_invocation_checks_the_run_arguments_and_every_case() -> None:
    with pytest.raises(ArgsError, match=re.escape("case b: unknown flag '--nope'")):
        check_invocation(
            ["--effort", "low"], {"a": ["--tools", "read"], "b": ["--nope"]}, FLAGS, TOOLS
        )


def test_every_flag_the_rules_name_is_one_nare_has() -> None:
    """The real contract: a renamed nare flag fails here, not in a paid run."""
    flags, tools = nare_contract()
    assert {*RESERVED, *RUN_ONLY, *CASE_ONLY, *HOST_FILE_FLAGS, "--tools"} <= set(flags)
    # What the agent script itself passes is reserved.
    script = {"--yes", "--jsonl", "--session", "--max-turns", "--model", "--max-tokens"}
    assert script <= set(RESERVED)
    check_invocation(
        ["--effort", "low", "--context-window", "16000", "--system-file", "p.md"],
        {"read-only-question": ["--tools", "read,list,search", "--require-reads", "1"]},
        flags,
        tools,
    )


def test_the_variant_ignores_order_and_the_equals_form() -> None:
    assert variant(["--effort", "low", "--context-window", "16000"], {}) == variant(
        ["--context-window=16000", "--effort=low"], {}
    )
    assert variant(["--effort", "low"], {}) == (("--effort", "low"),)


def test_the_variant_keeps_the_order_of_repeats() -> None:
    assert variant(["--b", "2", "--a", "x", "--b", "1"], {}) == (
        ("--a", "x"),
        ("--b", "2"),
        ("--b", "1"),
    )


def test_a_run_file_is_keyed_by_its_digest_not_its_path() -> None:
    one = variant(["--system-file", "a/terse.md"], {"a/terse.md": "sha256:aa"})
    assert one == variant(["--system-file=b/terse.md"], {"b/terse.md": "sha256:aa"})
    assert one != variant(["--system-file", "a/terse.md"], {"a/terse.md": "sha256:bb"})


def test_no_arguments_is_the_default_variant() -> None:
    assert variant([], {}) == ()


@pytest.mark.parametrize(
    ("tokens", "case_id"),
    [
        (["--image-input", "--effort", "low"], "demo"),
        (["--system=-x", "--context-window", "9"], None),
        (["--require-read", "a.py", "--require-read=b.py", "--image-input"], "demo"),
    ],
)
def test_the_key_parse_agrees_with_the_check(
    tokens: list[str], case_id: str | None
) -> None:
    assert pairs(tokens) == check_args(tokens, FLAGS, TOOLS, case_id=case_id)
```

The `# fmt: skip` markers keep the parametrize tables one row per line, as `tests/test_tui_app.py` does; `ruff check` still holds those rows to 88 columns. In the last test, `--system` is run only and `--require-read` case only, so each list names its kind.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_nare_args.py -q`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'benchmarks.runner.nare_args'`.

- [ ] **Step 3: Implement `benchmarks/runner/nare_args.py`**

Module docstring: arguments a case or a run passes to `nare run`, checked against nare's contract before anything is spent, and keyed so results from other run arguments never compare by default; pure except `stage_inputs` (Task 3).

`RESERVED`, verbatim owners from spec 6.2:

| Flags | Owner string |
| --- | --- |
| `--yes`, `--jsonl`, `--session` | `"the runner, which drives nare and reads its stream and session"` |
| `--max-turns` | `"the case's max_turns field"` |
| `--model`, `--provider`, `--base-url` | `"the environment: bin/bench --model, --provider and --base-url, or the NARE_* variables"` |
| `--max-tokens` | `"NARE_BENCH_MAX_TOKENS, which is already part of the baseline key"` |
| `--prompt-file` | `"the case's prompt"` |
| `--resume` | `"nobody: every rep starts from a clean fixture"` |
| `--replay` | `"nobody: the fake provider is never benchmarked"` |
| `--stream` | `"NARE_STREAM, which changes how a turn arrives, not what is measured"` |
| `--contract` | `"the runner, which owns the stream it reads"` |
| `--bash-credentials` | `"nobody: it would hand the provider key to model-written commands"` |

`check_args` walks the tokens once, `where` being `"run arguments"` or `f"case {case_id}"`, and refuses with `ArgsError(f"{where}: ...")` at the first problem, in this order for each token:

1. `--` exactly: `a bare '--'; the prompt is the case's`.
2. Not starting with `-`, or `-` alone: `stray {token!r}; only flags and their values, since the prompt is the case's`.
3. Split `--flag=value` at the first `=`.
4. In `RESERVED`: `{flag} is reserved for {owner}`.
5. In `RUN_ONLY` with a `case_id`: `{flag} is a run argument, since the system prompt is the variant under test; pass it after -- on bin/bench run`. In `CASE_ONLY` without one: `{flag} names a path in the work tree, so it belongs to a case's args`.
6. Not a key of `flags`: `unknown flag {flag!r}; nare run has no such option (abbreviations are not accepted)`.
7. `takes_value`: the value is the `=` part, else the next token when there is one and it does not start with `-` (consumed), else `{flag} needs a value (write {flag}=VALUE for one that starts with -)`. Not `takes_value` with an `=` part: `{flag} takes no value`.
8. `choices` not `None` and the value not in them: `{flag} {value!r} is not one of {", ".join(choices)}`.
9. `--tools`: split on `,`, strip, drop empty names; unless the names are exactly `["none"]`, the first name not in `tools` is refused with `--tools names {name!r}, which nare does not list; it lists {", ".join(sorted(tools))}, or none alone`.

`check_invocation` runs `check_args` on the run arguments, then on each case (in the mapping's order); for each case flag also in the run's flags it raises `ArgsError(f"{flag} is in the run arguments and in case {case_id}'s args; nare would take the last one, so pass it in one place")`.

`pairs` splits like step 7 without a contract: a token starting with `-` is a flag, its value the `=` part or else the next token if that does not start with `-`. `variant` maps each pair whose flag is in `HOST_FILE_FLAGS` to `(flag, files.get(value, value))`, then sorts by flag name with `sorted(..., key=lambda p: p[0])` (stable, so repeats keep their order) and returns a tuple.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest benchmarks/tests/test_nare_args.py -q`
Expected: PASS.

- [ ] **Step 5: Build and commit**

```bash
bin/build
git add benchmarks/runner/nare_args.py benchmarks/tests/test_nare_args.py
git commit -F - <<'EOF'
feat(bench): check nare arguments against its contract, and key a variant (#156)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 3: Stage run prompt files once, and carry the run's arguments on `Config`

**Files:**
- Modify: `benchmarks/runner/nare_args.py`, `benchmarks/runner/config.py`
- Test: `benchmarks/tests/test_nare_args.py`

**Interfaces:**
- Consumes: `ArgsError`, `HOST_FILE_FLAGS` (Task 2).
- Produces:
  - `INPUTS = "/in"` in `nare_args.py`.
  - `@dataclass(frozen=True) class RunArgs` with `recorded: tuple[str, ...] = ()` (the run arguments as a results line records them), `files: dict[str, str] = field(default_factory=dict)` (recorded path to `"sha256:<hex>"`), `passed: tuple[str, ...] = ()` (what nare gets, file values under `/in`), `inputs: Path | None = None` (the host directory mounted at `/in`).
  - `stage_inputs(tokens: Sequence[str], *, base: Path, root: Path, dest: Path) -> RunArgs`. Raises `ArgsError` when a named file cannot be read.
  - `Config.run_args: RunArgs = field(default_factory=RunArgs)`, the last field of `Config`.

- [ ] **Step 1: Write the failing tests**

Add `hashlib`, `Path`, `Config` (from `benchmarks.runner.config`), `RunArgs` and `stage_inputs` to the imports of `test_nare_args.py`, and append:

```python
def digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_arguments_without_a_file_are_not_staged(tmp_path: Path) -> None:
    dest = tmp_path / "inputs"
    staged = stage_inputs(["--effort", "low"], base=tmp_path, root=tmp_path, dest=dest)
    assert staged == RunArgs(recorded=("--effort", "low"), passed=("--effort", "low"))
    assert not dest.exists()


def test_a_system_file_is_copied_once_hashed_and_passed_under_in(tmp_path: Path) -> None:
    prompt = write(tmp_path / "benchmarks" / "prompts" / "terse.md", "Be terse.\n")
    dest = tmp_path / "results" / "stamp" / "inputs"
    staged = stage_inputs(
        ["--effort", "low", "--system-file", str(prompt)],
        base=tmp_path,
        root=tmp_path,
        dest=dest,
    )
    recorded = "benchmarks/prompts/terse.md"
    assert staged.recorded == ("--effort", "low", "--system-file", recorded)
    assert staged.files == {recorded: digest("Be terse.\n")}
    assert staged.passed == ("--effort", "low", "--system-file", "/in/0-terse.md")
    assert staged.inputs == dest
    # The copy is what the run measures: a later edit does not reach it.
    prompt.write_text("Edited mid-run.\n")
    assert (dest / "0-terse.md").read_text() == "Be terse.\n"


def test_the_equals_form_is_staged_in_place(tmp_path: Path) -> None:
    write(tmp_path / "p.md", "x")
    staged = stage_inputs(
        ["--append-system-file=p.md"], base=tmp_path, root=tmp_path, dest=tmp_path / "in"
    )
    assert staged.recorded == ("--append-system-file=p.md",)
    assert staged.passed == ("--append-system-file=/in/0-p.md",)


def test_two_files_with_one_name_do_not_overwrite_each_other(tmp_path: Path) -> None:
    write(tmp_path / "a" / "p.md", "one")
    write(tmp_path / "b" / "p.md", "two")
    dest = tmp_path / "in"
    staged = stage_inputs(
        ["--system-file", "a/p.md", "--append-system-file", "b/p.md"],
        base=tmp_path,
        root=tmp_path,
        dest=dest,
    )
    assert staged.passed == (
        "--system-file", "/in/0-p.md", "--append-system-file", "/in/1-p.md"
    )  # fmt: skip
    assert [(dest / n).read_text() for n in ("0-p.md", "1-p.md")] == ["one", "two"]


def test_a_relative_path_is_read_from_base_and_recorded_from_the_root(
    tmp_path: Path,
) -> None:
    write(tmp_path / "benchmarks" / "prompts" / "terse.md", "x")
    staged = stage_inputs(
        ["--system-file", "prompts/terse.md"],
        base=tmp_path / "benchmarks",
        root=tmp_path,
        dest=tmp_path / "in",
    )
    assert staged.recorded == ("--system-file", "benchmarks/prompts/terse.md")
    assert list(staged.files) == ["benchmarks/prompts/terse.md"]


def test_a_file_outside_the_checkout_is_recorded_by_its_absolute_path(
    tmp_path: Path,
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    outside = write(tmp_path / "mine.md", "x").resolve().as_posix()
    staged = stage_inputs(["--system-file", outside], base=root, root=root, dest=root / "in")
    assert staged.recorded == ("--system-file", outside)
    assert list(staged.files) == [outside]


def test_a_run_file_that_cannot_be_read_is_refused(tmp_path: Path) -> None:
    with pytest.raises(
        ArgsError, match=re.escape("run arguments: --system-file absent.md: cannot read it")
    ):
        stage_inputs(
            ["--system-file", "absent.md"], base=tmp_path, root=tmp_path, dest=tmp_path / "in"
        )


def test_a_config_passes_no_run_arguments_by_default() -> None:
    config = Config("m", "anthropic", None, "m", "k", "ANTHROPIC_API_KEY")
    assert config.run_args == RunArgs()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_nare_args.py -q`
Expected: FAIL at collection with `ImportError: cannot import name 'RunArgs'`.

- [ ] **Step 3: Implement `RunArgs`, `stage_inputs` and `Config.run_args`**

`stage_inputs` docstring: the one function in the module that touches the filesystem. For each `HOST_FILE_FLAGS` occurrence in `tokens`, in either form, with `n` counting files from 0: resolve `(base / value).resolve()`; read its bytes (an `OSError` raises `ArgsError(f"run arguments: {flag} {value}: cannot read it ({exc.strerror or type(exc).__name__})")`); `dest.mkdir(parents=True, exist_ok=True)` and write the bytes to `dest / f"{n}-{Path(value).name}"`; the digest is `"sha256:" + hashlib.sha256(data).hexdigest()`. The recorded path is the resolved path relative to `root.resolve()` as POSIX when it is inside, else the resolved absolute path as POSIX. `recorded` substitutes the recorded path for the value, `passed` substitutes `f"{INPUTS}/{n}-{name}"`, each keeping the operator's form (`--flag value` or `--flag=value`). `inputs` is `dest` when a file was staged, else `None`.

In `config.py`, import `field` and `RunArgs`, and add `run_args: RunArgs = field(default_factory=RunArgs)` after `stream`, with a comment: what a run passes to nare after `--`, as recorded and as the container gets it; compare and bless take it from the results.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest benchmarks/tests -q`
Expected: PASS.

- [ ] **Step 5: Build and commit**

```bash
bin/build
git add benchmarks/runner/nare_args.py benchmarks/runner/config.py benchmarks/tests/test_nare_args.py
git commit -F - <<'EOF'
feat(bench): stage a run's prompt files once, by digest (#156)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 4: The container gets the arguments as positional parameters

**Files:**
- Modify: `benchmarks/runner/sandbox.py`
- Test: `benchmarks/tests/test_sandbox.py`

**Interfaces:**
- Consumes: `Case.args` (Task 1); `Config.run_args.passed`, `.inputs`, `INPUTS` (Task 3).
- Produces: `_docker_run(..., inputs: Path | None = None)`, which mounts `f"{inputs}:{INPUTS}:ro"` when given. `run_agent` runs `["sh", "-c", _AGENT_SCRIPT, "nare-bench", *config.run_args.passed, *case.args]`.

- [ ] **Step 1: Write the failing tests**

Append to `benchmarks/tests/test_sandbox.py`:

```python
def docker_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config: Config, case: Case
) -> list[str]:
    """The `docker run` argv run_agent builds, without Docker."""
    import subprocess

    from benchmarks.runner import sandbox as mod

    seen: list[list[str]] = []

    def fake(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(mod.subprocess, "run", fake)
    mod.run_agent(case, tmp_path, tmp_path, config)
    return seen[0]


def test_arguments_reach_docker_after_the_script_and_never_inside_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from dataclasses import replace

    from benchmarks.runner.nare_args import RunArgs
    from benchmarks.runner.sandbox import _AGENT_SCRIPT

    case = replace(make_case(tmp_path), args=("--tools", "read,list,search"))
    run_args = RunArgs(recorded=("--effort", "low"), passed=("--effort", "low"))
    command = docker_command(
        monkeypatch, tmp_path, replace(CONFIG, run_args=run_args), case
    )
    # Run arguments, then the case's: the order a rep's evidence shows.
    assert command[command.index(IMAGE) + 1 :] == [
        "sh", "-c", _AGENT_SCRIPT, "nare-bench",
        "--effort", "low", "--tools", "read,list,search",
    ]  # fmt: skip
    assert "--effort" not in _AGENT_SCRIPT
    assert "read,list,search" not in _AGENT_SCRIPT


def test_a_run_file_is_mounted_read_only_at_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from dataclasses import replace

    from benchmarks.runner.nare_args import RunArgs

    inputs = tmp_path / "inputs"
    run_args = RunArgs(
        recorded=("--system-file", "p.md"),
        files={"p.md": "sha256:aa"},
        passed=("--system-file", "/in/0-p.md"),
        inputs=inputs,
    )
    command = docker_command(
        monkeypatch, tmp_path, replace(CONFIG, run_args=run_args), make_case(tmp_path)
    )
    mount = command.index(f"{inputs}:/in:ro")
    assert command[mount - 1] == "-v"
    assert mount < command.index(IMAGE)
    assert command[-2:] == ["--system-file", "/in/0-p.md"]


def test_there_is_no_in_mount_without_a_run_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from benchmarks.runner.sandbox import _AGENT_SCRIPT

    command = docker_command(monkeypatch, tmp_path, CONFIG, make_case(tmp_path))
    assert not any(part.endswith(":/in:ro") for part in command)
    assert command[-4:] == ["sh", "-c", _AGENT_SCRIPT, "nare-bench"]


def test_the_agent_script_passes_its_parameters_to_nare_unexpanded(
    tmp_path: Path,
) -> None:
    """The real script under sh: each parameter reaches nare as one argument,
    after the runner's flags and before the prompt, and nothing in one runs."""
    import os
    import subprocess

    from benchmarks.runner.sandbox import _AGENT_SCRIPT

    stubs = tmp_path / "bin"
    stubs.mkdir()
    (stubs / "nare").write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    (stubs / "nare").chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    (work / "a.txt").write_text("x\n")
    env = {
        **os.environ,
        "PATH": f"{stubs}:{os.environ['PATH']}",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "BENCH_PROMPT": "do it",
        "BENCH_MODEL": "m",
        "BENCH_MAX_TURNS": "3",
        "BENCH_MAX_TOKENS": "",
    }
    given = ["--system", "$(touch pwned) `touch pwned`; be terse", "--tools", "read,list"]
    done = subprocess.run(
        ["sh", "-c", _AGENT_SCRIPT, "nare-bench", *given],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    args = done.stdout.splitlines()
    assert args[args.index("m") + 1 : args.index("--")] == given
    assert args[-1] == "do it"
    assert not (work / "pwned").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_sandbox.py -q`
Expected: FAIL. The argv tests find no `"nare-bench"` after the script, the mount test finds no `:/in:ro`, and the script test finds nothing between `m` and `--`.

- [ ] **Step 3: Implement in `benchmarks/runner/sandbox.py`**

The nare line of `_AGENT_SCRIPT` becomes (keep the existing backslash continuations):

```
nare run --yes --jsonl --session /out/session.json \
     --max-turns "$BENCH_MAX_TURNS" --model "$BENCH_MODEL" \
     ${BENCH_MAX_TOKENS:+--max-tokens "$BENCH_MAX_TOKENS"} \
     "$@" -- "$BENCH_PROMPT" \
     || status=$?
```

Extend the comment above it: run arguments, then the case's, arrive as positional parameters after the script and are expanded with `"$@"`, never as text. `_docker_run` takes `inputs: Path | None = None` and appends `["-v", f"{inputs}:{INPUTS}:ro"]` after the `/out` mount. `run_agent` passes the argv in Interfaces and `inputs=config.run_args.inputs`. Replace the `--effort` paragraph of its docstring with:

```
The runner never passes `--effort` of its own accord: which levels a model
takes, and what a level means behind a proxy, vary by model. An operator may
pass it as a run argument. It is then part of the variant, and comparisons
never cross models, so a level is only ever compared with the same level on
the same model.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest benchmarks/tests/test_sandbox.py -q`
Expected: PASS, including `test_the_agent_script_passes_the_output_cap_only_when_set` (no parameters, so `"$@"` adds nothing).

- [ ] **Step 5: Build and commit**

```bash
bin/build
git add benchmarks/runner/sandbox.py benchmarks/tests/test_sandbox.py
git commit -F - <<'EOF'
feat(bench): pass nare arguments to the agent as positional parameters (#156)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 5: Results lines record the arguments, the window and the compactions

**Files:**
- Modify: `benchmarks/runner/grade.py`, `benchmarks/runner/report.py`, `benchmarks/runner/__main__.py`
- Test: `benchmarks/tests/test_grade.py`, `benchmarks/tests/test_main_args.py` (new)

**Interfaces:**
- Consumes: `Case.args`, `Config.run_args`.
- Produces:
  - `grade.ContextUse` (frozen dataclass): `window: dict[str, int | str] | None`, `compactions: int`; `grade.context_use(text: str) -> ContextUse`.
  - `RepRecord` gains, after `judge_model`: `case_args: list[str] = field(default_factory=list)`, `nare_args: list[str] = field(default_factory=list)`, `nare_files: dict[str, str] = field(default_factory=dict)`, `context_window: dict[str, int | str] | None = None`, `compactions: int | None = None`.

- [ ] **Step 1: Write the failing tests**

In `benchmarks/tests/test_grade.py`, add `ContextUse, context_use` to the import and append:

```python
WINDOW = json.dumps(
    {
        "type": "progress",
        "text": "context window 16000 (flag)",
        "detail": {"context_window": {"tokens": 16000, "source": "flag"}},
    }
)
COMPACTED = json.dumps(
    {
        "type": "progress",
        "text": "compacted: elided 3 tool results, ~13000 -> ~6000 tokens",
        "detail": {"compaction": {"elided": 3, "before": 13000.0, "after": 6000.0}},
    }
)


def test_the_window_and_the_compactions_come_from_the_stream() -> None:
    stream = "\n".join([WINDOW, EVENT, COMPACTED, "not json", COMPACTED, RESULT])
    assert context_use(stream) == ContextUse(
        window={"tokens": 16000, "source": "flag"}, compactions=2
    )


def test_a_stream_without_them_has_no_window_and_no_compactions() -> None:
    assert context_use(f"{EVENT}\n{RESULT}\n") == ContextUse(window=None, compactions=0)
    assert context_use("") == ContextUse(window=None, compactions=0)


def test_a_malformed_window_is_not_recorded() -> None:
    bad = json.dumps(
        {"type": "progress", "detail": {"context_window": {"tokens": "many", "source": "x"}}}
    )
    assert context_use(bad).window is None
```

Create `benchmarks/tests/test_main_args.py`:

```python
"""bin/bench with arguments for nare: the __main__ half of #156."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import cast

import pytest

from benchmarks.runner.__main__ import read_results, run_rep, write_results
from benchmarks.runner.nare_args import RunArgs
from benchmarks.runner.sandbox import RunArtifacts
from benchmarks.tests.test_grade import COMPACTED, WINDOW
from benchmarks.tests.test_main import (
    CONFIG,
    DONE_LINE,
    fake_sandbox,
    record,
    sandboxed,
)
from nare.transport import Transport

FILE_ARGS = RunArgs(
    recorded=("--system-file", "p.md"),
    files={"p.md": "sha256:aa"},
    passed=("--system-file", "/in/0-p.md"),
)


async def test_a_rep_records_its_arguments_window_and_compactions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = "\n".join([WINDOW, COMPACTED, DONE_LINE]) + "\n"
    fake_sandbox(monkeypatch, RunArtifacts(stream, "", 0, False, 1.0))
    case = replace(sandboxed(tmp_path), args=("--tools", "read"))
    config = replace(CONFIG, run_args=FILE_ARGS)
    rec = await run_rep(case, config, cast(Transport, None), 0, tmp_path / "r" / "s")
    assert rec.outcome == "pass"
    assert (rec.case_args, rec.nare_args, rec.nare_files) == (
        ["--tools", "read"],
        ["--system-file", "p.md"],
        {"p.md": "sha256:aa"},
    )
    assert (rec.context_window, rec.compactions) == (
        {"tokens": 16000, "source": "flag"},
        1,
    )


async def test_an_errored_rep_records_them_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed = json.dumps(
        {"type": "result", "status": "error", "stop_reason": None, "turns": 1,
         "usage": {}, "error": "RateLimitError: 429"}
    )  # fmt: skip
    fake_sandbox(monkeypatch, RunArtifacts(f"{WINDOW}\n{failed}\n", "", 1, False, 1.0))
    config = replace(CONFIG, run_args=FILE_ARGS)
    rec = await run_rep(
        sandboxed(tmp_path), config, cast(Transport, None), 0, tmp_path / "r" / "s"
    )
    assert rec.outcome == "error"
    assert rec.nare_args == ["--system-file", "p.md"]
    assert (rec.context_window, rec.compactions) == (
        {"tokens": 16000, "source": "flag"},
        0,
    )


def test_a_results_line_from_before_loads_as_the_default_variant(
    tmp_path: Path,
) -> None:
    payload = asdict(record("demo", model="m"))
    for key in ("case_args", "nare_args", "nare_files", "context_window", "compactions"):
        del payload[key]
    path = tmp_path / "old.jsonl"
    path.write_text(json.dumps(payload) + "\n")
    rec = read_results(path)[0]
    assert (rec.case_args, rec.nare_args, rec.nare_files) == ([], [], {})
    assert (rec.context_window, rec.compactions) == (None, None)


def test_the_new_fields_survive_a_results_file(tmp_path: Path) -> None:
    ran = replace(
        record("demo", model="m"),
        case_args=["--tools", "read"],
        nare_args=["--effort", "low"],
        context_window={"tokens": 262144, "source": "backend"},
        compactions=2,
    )
    assert read_results(write_results(tmp_path, [ran])) == [ran]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_grade.py benchmarks/tests/test_main_args.py -q`
Expected: FAIL at collection with `ImportError: cannot import name 'ContextUse'`.

- [ ] **Step 3: Implement**

`grade.context_use` loops over lines as `parse_stdout` does (skip blank lines, lines that are not JSON, and payloads that are not dicts). For a `"progress"` payload with a dict `detail`: a `context_window` dict whose `tokens` is an `int` (not a `bool`) and whose `source` is a `str` sets the window (the last one wins); a `compaction` key counts one. Add the five `RepRecord` fields in Interfaces, with a comment that each defaults to what a results line written before #156 means: no arguments, and a window and compaction count nobody read. In `run_rep`, compute `use = context_use(result.stdout)` right after `parse_stdout`, and pass `case_args=list(case.args)`, `nare_args=list(config.run_args.recorded)`, `nare_files=dict(config.run_args.files)`, `context_window=use.window` and `compactions=use.compactions` to both `RepRecord(...)` calls.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest benchmarks/tests -q`
Expected: PASS.

- [ ] **Step 5: Build and commit**

```bash
bin/build
git add benchmarks/runner/grade.py benchmarks/runner/report.py benchmarks/runner/__main__.py \
    benchmarks/tests/test_grade.py benchmarks/tests/test_main_args.py
git commit -F - <<'EOF'
feat(bench): results record nare arguments, the context window and compactions (#156)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 6: A rep whose nare never started stops the run

**Files:**
- Modify: `benchmarks/runner/__main__.py`
- Test: `benchmarks/tests/test_main_args.py`

**Interfaces:**
- Produces: `class NeverStarted(Exception)` in `__main__.py`, constructed `NeverStarted(case: str, rep: int, stderr: str, log: Path | None = None)`, keeping all four as attributes. `str()` is `f"{case} rep {rep}: nare refused its invocation and never started (exit {NEVER_STARTED}), so the run stopped here. Its stderr:\n{stderr.rstrip()}"`, followed by `f"\nThe reason is kept in {log}"` when `log` is set, with `NEVER_STARTED` imported from `nare.contract`. `run_rep` raises it; `main`'s single `run` and `run_matrix` catch it, print `f"bench: {exc}"` to stderr and return 2.
- Produces: `stopped_log(case: Case, rep: int, config: Config, result: RunArtifacts, kept_ref: str) -> str` in `__main__.py`, the text of `stopped.txt`. Plain text, in this order: a first line `nare never started, so the run stopped.`; then one labelled line each for `case`, `rep`, `model` (as `f"{config.model} ({config.provider})"`), `exit` (`f"{result.exit_code} (never_started), no result line, not timed out"`), `run args` (`shlex.join(config.run_args.passed)`, or `(none)`), `case args` (`shlex.join(case.args)`, or `(none)`) and `evidence` (`kept_ref`); then a blank line, `nare's stderr:` and the stderr verbatim; then a blank line, `nare's stdout:` and the stdout verbatim, or `(empty)`. Nothing in it is redacted or shortened: argv carries no credential (spec section 7), and a shortened stderr can lose the one line that says why.

- [ ] **Step 1: Write the failing tests**

Add `replace` (from `dataclasses`), `Case` (from `benchmarks.runner.case`), `Config` (from `benchmarks.runner.config`), `RunArgs` (from `benchmarks.runner.nare_args`), `RepRecord` (from `benchmarks.runner.report`), `NeverStarted`, `main` and `stopped_log` (from `benchmarks.runner.__main__`), and `HAIKU`, `NANO`, `matrix`, `results_dir` (from `benchmarks.tests.test_main`) to the imports, and append:

```python
REFUSED = "nare: error: argument --context-window: invalid int value: 'abc'\n"


@pytest.mark.parametrize(
    ("code", "timed_out", "stops"),
    [(2, False, True), (2, True, False), (1, False, False), (125, False, False)],
)
async def test_only_a_never_started_rep_stops_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: int,
    timed_out: bool,
    stops: bool,
) -> None:
    fake_sandbox(monkeypatch, RunArtifacts("", REFUSED, code, timed_out, 1.0))
    evidence = tmp_path / "r" / "s"
    rep = run_rep(sandboxed(tmp_path), CONFIG, cast(Transport, None), 0, evidence)
    if stops:
        with pytest.raises(NeverStarted, match="invalid int value: 'abc'") as raised:
            await rep
        log = evidence / "stopped.txt"
        assert raised.value.log == log
        assert str(log) in str(raised.value)
        assert REFUSED.rstrip() in log.read_text(encoding="utf-8")
        # The rep's own evidence is kept beside it, as for any rep.
        kept = evidence / f"{sandboxed(tmp_path).id}-0"
        assert (kept / "stderr.txt").read_text(encoding="utf-8") == REFUSED
    else:
        assert (await rep).outcome == "error"
        assert not (evidence / "stopped.txt").exists()


def test_the_stop_log_says_why_with_the_arguments_and_both_streams(
    tmp_path: Path,
) -> None:
    case = replace(sandboxed(tmp_path), args=("--tools", "read,list"))
    passed = ("--context-window", "abc")
    config = replace(CONFIG, run_args=RunArgs(recorded=passed, passed=passed))
    result = RunArtifacts("partial line\n", REFUSED, 2, False, 0.1)
    text = stopped_log(case, 3, config, result, "s/edit-docstring-3")
    assert text.startswith("nare never started, so the run stopped.\n")
    for expected in (
        case.id,
        f"{CONFIG.model} ({CONFIG.provider})",
        "2 (never_started), no result line, not timed out",
        "--context-window abc",
        "--tools read,list",
        "s/edit-docstring-3",
        REFUSED.rstrip(),
        "partial line",
    ):
        assert expected in text
    assert text.index("nare's stderr:") < text.index("nare's stdout:")


def test_the_stop_log_says_when_there_were_no_arguments_and_no_stdout(
    tmp_path: Path,
) -> None:
    text = stopped_log(
        sandboxed(tmp_path), 0, CONFIG, RunArtifacts("", REFUSED, 2, False, 0.1), "k"
    )
    assert text.count("(none)") == 2
    assert text.rstrip().endswith("(empty)")


def test_a_never_started_rep_stops_the_run_with_exit_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import benchmarks.runner.__main__ as bench

    results_dir(tmp_path, monkeypatch)
    calls: list[str] = []

    def agent(case: Case, workdir: Path, artifacts: Path, config: Config) -> RunArtifacts:
        calls.append(case.id)
        return RunArtifacts("", REFUSED, 2, False, 0.1)

    monkeypatch.setattr(bench, "run_agent", agent)
    monkeypatch.setattr(bench, "build_image", lambda root: None)
    monkeypatch.setattr(bench, "judge_transport", lambda config: None)
    monkeypatch.setattr(bench, "new_stamp", lambda: "2026-11-04T00-00-00Z")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    argv = ["run", "--case", "edit-docstring", "--model", "m", "--provider", "anthropic"]
    assert main(argv) == 2
    assert calls == ["edit-docstring"]
    err = capsys.readouterr().err
    assert "invalid int value: 'abc'" in err
    results = tmp_path / "benchmarks" / "results"
    assert not list(results.glob("*.jsonl"))
    log = results / "2026-11-04T00-00-00Z" / "stopped.txt"
    assert str(log) in err
    assert "invalid int value: 'abc'" in log.read_text(encoding="utf-8")


def test_a_never_started_rep_stops_the_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import benchmarks.runner.__main__ as bench

    matrix(tmp_path, monkeypatch, HAIKU, NANO)

    async def refused(
        case: Case, config: Config, transport: Transport, reps: int, evidence: Path
    ) -> list[RepRecord]:
        raise NeverStarted(case.id, 0, REFUSED)

    monkeypatch.setattr(bench, "run_case", refused)
    assert main(["run", "--matrix", "--case", "edit-docstring"]) == 2
    assert "invalid int value: 'abc'" in capsys.readouterr().err
    assert not list((tmp_path / "benchmarks" / "results").glob("*.jsonl"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_main_args.py -q`
Expected: FAIL at collection with `ImportError: cannot import name 'NeverStarted'`.

- [ ] **Step 3: Implement**

In `run_rep`, after `keep_evidence(...)` and `parse_stdout`, when `line is None and not result.timed_out and result.exit_code == NEVER_STARTED`: write `stopped_log(case, rep, config, result, kept_ref)` to `evidence / "stopped.txt"` (UTF-8), then raise `NeverStarted(case.id, rep, result.stderr, evidence / "stopped.txt")`. The rep's evidence is already kept by then. In `main`'s single `run`, wrap `run_model` and `confirm` in `try`/`except NeverStarted`; in `run_matrix`, wrap the per-model loop. Neither handler writes or deletes anything: the log and the kept evidence are the record of why the run stopped.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest benchmarks/tests -q`
Expected: PASS.

- [ ] **Step 5: Build and commit**

```bash
bin/build
git add benchmarks/runner/__main__.py benchmarks/tests/test_main_args.py
git commit -F - <<'EOF'
feat(bench): stop the run when nare never started (#156)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 7: Baselines carry the run arguments, and reports name the variant

**Files:**
- Modify: `benchmarks/runner/report.py`
- Test: `benchmarks/tests/test_report.py`

**Interfaces:**
- Produces:
  - `BaselineMeta.nare_args: tuple[str, ...] = ()` and `BaselineMeta.nare_files: dict[str, str] = field(default_factory=dict)`, after `max_tokens`.
  - `render_variant(args: Sequence[str], files: Mapping[str, str]) -> str`.
  - `render_matrix(columns: Sequence[tuple[str, Comparison]], judge: str, variants: Mapping[str, str] | None = None) -> str`, where `variants` maps a model to its `render_variant` text.

- [ ] **Step 1: Write the failing tests**

Add `render_variant` to the import of `benchmarks/tests/test_report.py` and append:

```python
TERSE = "benchmarks/prompts/terse.md"
VARIANT = replace(
    META,
    nare_args=("--effort", "low", "--system-file", TERSE),
    nare_files={TERSE: "sha256:" + "ab" * 32},
)


def test_a_baseline_with_run_arguments_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "b.toml"
    path.write_text(dumps_baseline(VARIANT, summarize([record(case="c")])))
    assert load_baseline(path).meta == VARIANT


def test_run_arguments_are_written_as_the_spec_shows_them() -> None:
    text = dumps_baseline(VARIANT, [])
    assert f'nare_args   = ["--effort", "low", "--system-file", "{TERSE}"]\n' in text
    assert f'\n[meta.nare_files]\n"{TERSE}" = "sha256:{"ab" * 32}"\n' in text


def test_a_baseline_without_run_arguments_writes_nothing_for_them() -> None:
    text = dumps_baseline(META, [])
    assert "nare_args" not in text
    assert "nare_files" not in text


def test_every_committed_baseline_renders_byte_for_byte_as_it_is() -> None:
    from benchmarks.runner.sandbox import repo_root

    paths = sorted((repo_root() / "benchmarks" / "baselines").glob("*.toml"))
    assert paths
    for path in paths:
        baseline = load_baseline(path)
        assert (baseline.meta.nare_args, baseline.meta.nare_files) == ((), {})
        rendered = dumps_baseline(baseline.meta, list(baseline.cases.values()))
        assert rendered == path.read_text(encoding="utf-8"), path.name


def test_a_variant_reads_as_its_command_line_with_each_files_digest() -> None:
    assert render_variant([], {}) == "none"
    assert render_variant(["--effort", "low"], {}) == "--effort low"
    assert render_variant(["--system", "be terse"], {}) == "--system 'be terse'"
    digest = "sha256:0123456789ab" + "f" * 52
    assert render_variant(["--system-file", "p.md"], {"p.md": digest}) == (
        "--system-file p.md (p.md sha256:0123456789ab)"
    )


def test_the_matrix_note_names_each_models_variant_when_any_has_one() -> None:
    columns = [
        (model, compare([rated("ask", 1, 2)], baseline_of(rated("ask", 1, 2))))
        for model in ("a", "b")
    ]
    plain = render_matrix(columns, "j")
    assert render_matrix(columns, "j", {"a": "none", "b": "none"}) == plain
    note = render_matrix(columns, "j", {"a": "none", "b": "--effort low"})
    assert note.splitlines()[-1] == (
        "Judge: j, for every model. Run arguments: a: none; b: --effort low."
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_report.py -q`
Expected: FAIL at collection with `ImportError: cannot import name 'render_variant'`.

- [ ] **Step 3: Implement in `benchmarks/runner/report.py`**

Add the two `BaselineMeta` fields with a comment: the run arguments the numbers were measured under, and each run file's digest; empty is the default variant, which every baseline blessed before #156 is. In `dumps_baseline`, after the `max_tokens` line:

```python
    if meta.nare_args:
        lines.append(
            "nare_args   = [" + ", ".join(_toml_str(a) for a in meta.nare_args) + "]"
        )
    if meta.nare_files:
        lines += ["", "[meta.nare_files]"]
        lines += [
            f"{_toml_str(path)} = {_toml_str(digest)}"
            for path, digest in meta.nare_files.items()
        ]
```

`load_baseline` reads `nare_args=tuple(str(a) for a in meta_raw.get("nare_args", ()))` and `nare_files={str(k): str(v) for k, v in meta_raw.get("nare_files", {}).items()}`. `render_variant` returns `"none"` for no arguments, else `shlex.join(args)`, followed by `f" ({digests})"` when there are files, where `digests` joins `f"{path} {digest[:19]}"` (the `sha256:` prefix and 12 hex digits) with `", "`. In `render_matrix`, when `variants` has any value other than `"none"`, the note gains `" Run arguments: " + "; ".join(f"{m}: {variants.get(m, 'none')}" for m in models) + "."` after the existing sentences.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest benchmarks/tests/test_report.py -q`
Expected: PASS, including every existing round-trip and matrix test.

- [ ] **Step 5: Build and commit**

```bash
bin/build
git add benchmarks/runner/report.py benchmarks/tests/test_report.py
git commit -F - <<'EOF'
feat(bench): baselines carry the run arguments, and reports name the variant (#156)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 8: `bin/bench run -- ARGS`, checked before anything is spent

**Files:**
- Modify: `benchmarks/runner/__main__.py`
- Test: `benchmarks/tests/test_main_args.py`

**Interfaces:**
- Consumes: `check_invocation`, `nare_contract`, `ArgsError` (Task 2); `stage_inputs`, `RunArgs` (Task 3); `NeverStarted` (Task 6).
- Produces: `split_run_args(argv: Sequence[str]) -> tuple[list[str], list[str] | None]`: bench's own arguments, and what follows the first `--`, or `None` when there is no `--`.

- [ ] **Step 1: Write the failing tests**

Add `hashlib`, `dataclass` and `field` to the imports, and `split_run_args` from `benchmarks.runner.__main__`; append:

```python
STAMP = "2026-11-04T00-00-00Z"
ARGUED = """\
id = "argued"
tier = "smoke"
prompt = "Say what bar.py holds."
{args}

[[check]]
kind = "result"
status = "done"
"""
RUN = ["run", "--model", "m"]


@dataclass
class Ran:
    builds: int = 0
    reps: list[tuple[Case, Config]] = field(default_factory=list)


def argued(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: str = "") -> Ran:
    """A checkout whose one case, `argued`, has `args` (TOML), over a fake
    container in which every rep is done. No judge, no bash check."""
    import benchmarks.runner.__main__ as bench

    case = tmp_path / "benchmarks" / "cases" / "argued"
    (case / "fixture").mkdir(parents=True)
    (case / "fixture" / "bar.py").write_text("x = 1\n")
    (case / "case.toml").write_text(ARGUED.format(args=f"args = {args}" if args else ""))
    ran = Ran()

    def agent(case: Case, workdir: Path, artifacts: Path, config: Config) -> RunArtifacts:
        ran.reps.append((case, config))
        return RunArtifacts(DONE_LINE, "", 0, False, 1.0)

    def build(root: Path) -> None:
        ran.builds += 1

    monkeypatch.setattr(bench, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bench, "run_agent", agent)
    monkeypatch.setattr(bench, "git_diff", lambda w: "")
    monkeypatch.setattr(bench, "build_image", build)
    monkeypatch.setattr(bench, "judge_transport", lambda config: None)
    monkeypatch.setattr(bench, "new_stamp", lambda: STAMP)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    for var in ("NARE_PROVIDER", "NARE_BASE_URL", "NARE_BENCH_JUDGE_MODEL", "NARE_BENCH_MAX_TOKENS"):
        monkeypatch.delenv(var, raising=False)
    return ran


def results_of(tmp_path: Path) -> list[RepRecord]:
    return read_results(tmp_path / "benchmarks" / "results" / f"{STAMP}.jsonl")


def test_everything_after_the_first_double_dash_is_for_nare() -> None:
    assert split_run_args(["run", "--tier", "full", "--", "--effort", "low"]) == (
        ["run", "--tier", "full"],
        ["--effort", "low"],
    )
    assert split_run_args(["run", "--", "--", "x"]) == (["run"], ["--", "x"])
    assert split_run_args(["run"]) == (["run"], None)


def test_run_passes_its_arguments_then_the_cases_and_records_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = argued(tmp_path, monkeypatch, '["--tools", "read,list,search"]')
    assert main([*RUN, "--", "--effort", "low"]) == 0
    assert ran.builds == 1
    assert {(c.args, cfg.run_args.passed) for c, cfg in ran.reps} == {
        (("--tools", "read,list,search"), ("--effort", "low"))
    }
    lines = results_of(tmp_path)
    assert len(lines) == len(ran.reps) == 3
    assert {(tuple(r.case_args), tuple(r.nare_args)) for r in lines} == {
        (("--tools", "read,list,search"), ("--effort", "low"))
    }


def test_run_reads_a_system_file_once_and_every_line_records_its_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = argued(tmp_path, monkeypatch)
    prompts = tmp_path / "benchmarks" / "prompts"
    prompts.mkdir()
    (prompts / "terse.md").write_text("Be terse.\n")
    # Read from where bench runs, recorded from the checkout's root.
    monkeypatch.chdir(tmp_path / "benchmarks")
    assert main([*RUN, "--", "--system-file", "prompts/terse.md"]) == 0
    inputs = tmp_path / "benchmarks" / "results" / STAMP / "inputs"
    assert {cfg.run_args.passed for _, cfg in ran.reps} == {
        ("--system-file", "/in/0-terse.md")
    }
    assert {cfg.run_args.inputs for _, cfg in ran.reps} == {inputs}
    assert (inputs / "0-terse.md").read_text() == "Be terse.\n"
    digest = "sha256:" + hashlib.sha256(b"Be terse.\n").hexdigest()
    recorded = "benchmarks/prompts/terse.md"
    assert {(tuple(r.nare_args), tuple(r.nare_files.items())) for r in results_of(tmp_path)} == {
        (("--system-file", recorded), ((recorded, digest),))
    }


def test_a_double_dash_with_nothing_after_it_is_the_default_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = argued(tmp_path, monkeypatch)
    assert main([*RUN, "--"]) == 0
    assert ran.reps
    assert all(cfg.run_args == RunArgs() for _, cfg in ran.reps)


@pytest.mark.parametrize(
    ("case_args", "run_args", "named"),
    [
        ('["--tools", "read,teleport"]', [], "case argued: --tools names 'teleport'"),
        ('["--model", "x"]', [], "case argued: --model is reserved for"),
        ('["--system", "Be terse."]', [], "case argued: --system is a run argument"),
        (
            "",
            ["--schema", "a.json"],
            "run arguments: --schema names a path in the work tree",
        ),
        ("", ["--max-tok", "5"], "run arguments: unknown flag '--max-tok'"),
        (
            '["--context-window", "8000"]',
            ["--context-window", "16000"],
            "--context-window is in the run arguments and in case argued's args",
        ),
        ("", ["--system-file", "absent.md"], "--system-file absent.md: cannot read it"),
    ],
)  # fmt: skip
def test_run_refuses_arguments_before_building_anything(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case_args: str,
    run_args: list[str],
    named: str,
) -> None:
    ran = argued(tmp_path, monkeypatch, case_args)
    monkeypatch.chdir(tmp_path)
    assert main([*RUN, "--", *run_args]) == 2
    assert (ran.builds, ran.reps) == (0, [])
    assert named in capsys.readouterr().err


def test_verify_refuses_a_case_whose_args_nare_would_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = argued(tmp_path, monkeypatch, '["--no-such-flag"]')
    assert main(["verify"]) == 2
    assert ran.builds == 0
    assert "case argued: unknown flag '--no-such-flag'" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["compare", "--model", "m", "--"],
        ["bless", "--model", "m", "--", "--effort", "low"],
        ["verify", "--", "--effort", "low"],
        ["models", "--"],
        ["compare-judges", "--"],
    ],
)
def test_only_run_takes_arguments_for_nare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    ran = argued(tmp_path, monkeypatch)
    assert main(argv) == 2
    assert ran.builds == 0
    assert f"bench: {argv[0]} takes no arguments for nare" in capsys.readouterr().err


def test_the_matrix_takes_no_run_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = argued(tmp_path, monkeypatch)
    assert main(["run", "--matrix", "--", "--effort", "low"]) == 2
    assert ran.builds == 0
    assert "--matrix runs each baseline's own run arguments" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_main_args.py -q`
Expected: FAIL at collection with `ImportError: cannot import name 'split_run_args'`.

- [ ] **Step 3: Implement in `benchmarks/runner/__main__.py`**

- `main`: `argv = sys.argv[1:] if argv is None else argv`; `own, nare = split_run_args(argv)`; parse `own`.
- Directly after the no-subcommand check, before `compare-judges` and `models` are dispatched: a `--` on any command but `run` prints `f"bench: {args.command} takes no arguments for nare; only run passes them, after --, and compare and bless read them from the results"`, and on `run --matrix` prints `"bench: --matrix runs each baseline's own run arguments; drop the -- and what follows"`. Both return 2.
- After the cases are selected, for `verify` and for a single `run`: `check_invocation(nare or [], {c.id: c.args for c in cases}, *nare_contract())`; an `ArgsError` prints `f"bench: {exc}"` and returns 2. This is before `resolve_config`, so a typo is reported even without a key.
- Single `run`, after `resolve_config` and before the baseline check: `stamp = new_stamp()` (moved up from after `build_image`), then `config = replace(config, run_args=stage_inputs(nare or [], base=Path.cwd(), root=root, dest=root / "benchmarks" / "results" / stamp / "inputs"))`, an `ArgsError` printing and returning 2. The rest of the run uses that `stamp`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest benchmarks/tests -q`
Expected: PASS, including `test_a_confirmation_rerun_leaves_one_results_file` (the stamp is still taken once, now earlier).

- [ ] **Step 5: Build and commit**

```bash
bin/build
git add benchmarks/runner/__main__.py benchmarks/tests/test_main_args.py
git commit -F - <<'EOF'
feat(bench): bin/bench run -- passes arguments to nare, checked before anything is spent (#156)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 9: Results from other run arguments do not compare by default

**Files:**
- Modify: `benchmarks/runner/__main__.py`
- Test: `benchmarks/tests/test_main_args.py`

**Interfaces:**
- Consumes: `variant` (Task 2); `RunArgs` (Task 3); `BaselineMeta.nare_args`, `.nare_files`, `render_variant` (Task 7).
- Produces:
  - `args_changed(baseline: Baseline, config: Config, allow: bool) -> bool`: `False` when `allow` or when `variant(baseline.meta.nare_args, baseline.meta.nare_files) == variant(config.run_args.recorded, config.run_args.files)`; otherwise prints `f"bench: baseline was blessed with run arguments {B}; this run used {R}. Numbers are not comparable across run arguments. Pass --allow-arg-change to override."` to stderr, `B` and `R` from `render_variant`, and returns `True`.
  - `variant_note(baseline: Baseline, config: Config) -> str | None`: `None` for one variant, else `f"baseline run arguments: {B}\nresults run arguments:  {R}"`.
  - `as_recorded` also sets `run_args=RunArgs(recorded=tuple(records[0].nare_args), files=dict(records[0].nare_files))` when there are records.
  - `--allow-arg-change` on `run`, `compare` and `bless`.

- [ ] **Step 1: Write the failing tests**

Add `Any` (from `typing`), `args_changed` (from `benchmarks.runner.__main__`), `Baseline`, `BaselineMeta`, `baseline_path`, `dumps_baseline`, `load_baseline` (from `benchmarks.runner.report`) and `KEPT`, `SONNET`, `summary` (from `benchmarks.tests.test_main`) to the imports; append:

```python
DIGEST = "sha256:" + "ab" * 32


def blessed_with(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *nare_args: str,
    files: dict[str, str] | None = None,
) -> Path:
    """Model m, blessed on openai and judged by j, with run arguments `nare_args`."""
    baselines = results_dir(tmp_path, monkeypatch)
    baselines.mkdir()
    path = baseline_path(baselines, "m", "smoke")
    meta = BaselineMeta(
        "t", "abc1234", "openai", "m", "j", nare_args=nare_args, nare_files=files or {}
    )
    path.write_text(dumps_baseline(meta, [summary("edit-docstring", 1.0)]))
    return path


def ran_with(
    *nare_args: str, files: dict[str, str] | None = None, **change: Any
) -> RepRecord:
    ran = replace(
        record("edit-docstring", model="m"),
        provider="openai",
        judge_model="j",
        nare_args=list(nare_args),
        nare_files=files or {},
    )
    return replace(ran, **change)


BASE = Baseline(BaselineMeta("t", "abc1234", "anthropic", "m", "m"), {})


@pytest.mark.parametrize(
    ("blessed", "now", "changed"),
    [
        ((), (), False),
        (
            ("--effort", "low", "--context-window", "16000"),
            ("--context-window=16000", "--effort=low"),
            False,
        ),
        ((), ("--effort", "low"), True),
        (("--effort", "low"), ("--effort", "high"), True),
    ],
)
def test_a_variant_change_is_by_key_not_by_text(
    blessed: tuple[str, ...], now: tuple[str, ...], changed: bool
) -> None:
    baseline = Baseline(replace(BASE.meta, nare_args=blessed), {})
    config = replace(CONFIG, run_args=RunArgs(recorded=now))
    assert args_changed(baseline, config, False) is changed
    assert args_changed(baseline, config, True) is False


def test_a_run_file_is_compared_by_digest_not_by_path() -> None:
    meta = replace(BASE.meta, nare_args=("--system-file", "a/p.md"), nare_files={"a/p.md": "sha256:aa"})
    moved = RunArgs(recorded=("--system-file", "b/p.md"), files={"b/p.md": "sha256:aa"})
    edited = RunArgs(recorded=("--system-file", "a/p.md"), files={"a/p.md": "sha256:bb"})
    assert not args_changed(Baseline(meta, {}), replace(CONFIG, run_args=moved), False)
    assert args_changed(Baseline(meta, {}), replace(CONFIG, run_args=edited), False)


@pytest.mark.parametrize(
    ("flags", "code"),
    [([], 2), (["--allow-model-change"], 2), (["--allow-arg-change"], 0)],
)
def test_compare_refuses_results_from_another_variant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
    code: int,
) -> None:
    blessed_with(tmp_path, monkeypatch)
    write_results(tmp_path, [ran_with("--context-window", "16000")])
    assert main(["compare", "--model", "m", *flags]) == code
    captured = capsys.readouterr()
    if code == 2:
        assert (
            "baseline was blessed with run arguments none; "
            "this run used --context-window 16000"
        ) in captured.err
    else:
        assert captured.out.startswith(
            "baseline run arguments: none\n"
            "results run arguments:  --context-window 16000\n"
        )


def test_allow_arg_change_does_not_cover_a_judge_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    blessed_with(tmp_path, monkeypatch)
    write_results(tmp_path, [ran_with("--effort", "low", judge_model="other")])
    assert main(["compare", "--model", "m", "--allow-arg-change"]) == 2
    assert "baseline was blessed on provider 'openai'" in capsys.readouterr().err
    both = ["--allow-arg-change", "--allow-model-change"]
    assert main(["compare", "--model", "m", *both]) == 0


def test_results_from_before_compare_as_the_default_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blessed_with(tmp_path, monkeypatch)
    write_results(tmp_path, [ran_with()])
    assert main(["compare", "--model", "m"]) == 0


def test_run_refuses_another_variant_before_building_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = matrix(tmp_path, monkeypatch, NANO)
    single = [
        "run", "--case", "edit-docstring", "--model", "gpt-5-nano",
        "--provider", "openai", "--judge-model", SONNET,
    ]  # fmt: skip
    assert main([*single, "--", "--effort", "low"]) == 2
    assert main([*single, "--allow-model-change", "--", "--effort", "low"]) == 2
    assert ran == []
    err = capsys.readouterr().err
    assert "baseline was blessed with run arguments none; this run used --effort low" in err
    assert main([*single, "--allow-arg-change", "--", "--effort", "low"]) == 0
    assert [c.run_args.recorded for c in ran[1:]] == [("--effort", "low")]
    assert "results run arguments:  --effort low" in capsys.readouterr().out


def test_bless_writes_the_variant_the_results_were_run_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baselines = results_dir(tmp_path, monkeypatch)
    terse = "benchmarks/prompts/terse.md"
    write_results(tmp_path, [ran_with("--system-file", terse, files={terse: DIGEST})])
    assert main(["bless", "--model", "m"]) == 0
    meta = load_baseline(baseline_path(baselines, "m", "smoke")).meta
    assert (meta.nare_args, meta.nare_files) == (("--system-file", terse), {terse: DIGEST})


def test_bless_will_not_keep_cases_measured_on_another_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    baselines = results_dir(tmp_path, monkeypatch)
    baselines.mkdir()
    blessed = baseline_path(baselines, "gpt-5-nano", "smoke")
    blessed.write_text(dumps_baseline(NANO, [summary(c, 1.0) for c in KEPT]))
    before = blessed.read_text()
    ran = replace(
        record("edit-docstring", model="gpt-5-nano"),
        provider="openai",
        judge_model=SONNET,
        nare_args=["--effort", "low"],
    )
    write_results(tmp_path, [ran])
    assert main(["bless", "--model", "gpt-5-nano"]) == 2
    assert main(["bless", "--model", "gpt-5-nano", "--allow-model-change"]) == 2
    assert blessed.read_text() == before
    err = capsys.readouterr().err
    assert "multi-file-rename, read-only-question" in err
    assert "--allow-arg-change" in err
    assert main(["bless", "--model", "gpt-5-nano", "--allow-arg-change"]) == 0
    assert load_baseline(blessed).meta.nare_args == ("--effort", "low")


@pytest.mark.parametrize("flags", [[], ["--allow-arg-change"], ["--allow-model-change"]])
def test_bless_refuses_a_run_file_outside_the_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
) -> None:
    baselines = results_dir(tmp_path, monkeypatch)
    outside = "/elsewhere/terse.md"
    write_results(tmp_path, [ran_with("--system-file", outside, files={outside: DIGEST})])
    assert main(["bless", "--model", "m", *flags]) == 2
    assert "outside the checkout" in capsys.readouterr().err
    assert not baseline_path(baselines, "m", "smoke").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_main_args.py -q`
Expected: FAIL at collection with `ImportError: cannot import name 'args_changed'`.

- [ ] **Step 3: Implement in `benchmarks/runner/__main__.py`**

- `build_parser`: beside `--allow-model-change`, add `--allow-arg-change` (`store_true`, help: "compare against, or bless on top of, a baseline blessed with different run arguments (what follows -- on run)").
- `as_recorded`: carry the variant from the first record, as in Interfaces.
- Single `run`: `if baseline is not None and (model_changed(baseline, config, args.allow_model_change) or args_changed(baseline, config, args.allow_arg_change)): return 2`. Before `print(render(final))`, print `variant_note(baseline, config)` when it is not `None`.
- `compare`: the same check after `model_changed`, and the same note before `print(render(comparison))`.
- `bless`, first thing in its branch (before `baselines.mkdir`): any `nare_files` key that is an absolute path prints `f"bench: {results_file.name}: the run arguments name {path}, which is outside the checkout; a baseline must name files anyone with the checkout can read, so move it into the checkout and run again"` and returns 2, whatever the `--allow-*` flags say. After the existing kept-cases model check, a second one: `old is not None and kept and args_changed(old, config, args.allow_arg_change)` prints `f"bench: blessing would keep {', '.join(kept)} from {baseline_file.name} under these results' run arguments; run those cases too, or pass --allow-arg-change"` and returns 2. `BaselineMeta(...)` gets `nare_args=config.run_args.recorded, nare_files=dict(config.run_args.files)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest benchmarks/tests -q`
Expected: PASS, including every existing bless and compare test in `test_main.py`.

- [ ] **Step 5: Build and commit**

```bash
bin/build
git add benchmarks/runner/__main__.py benchmarks/tests/test_main_args.py
git commit -F - <<'EOF'
feat(bench): results from other run arguments do not compare by default (#156)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 10: The matrix runs each baseline's own arguments

**Files:**
- Modify: `benchmarks/runner/__main__.py` (`run_matrix`, `compare_matrix`, `baselines_table`)
- Test: `benchmarks/tests/test_main_args.py`

**Interfaces:**
- Consumes: `check_invocation`, `nare_contract`, `stage_inputs`, `ArgsError`, `args_changed`, `render_variant`, `render_matrix(..., variants=)`.
- Produces: no new names. `run_matrix` hands `run_model` a config whose `run_args` is the baseline's arguments, staged under `benchmarks/results/<stamp>.<slug>/inputs`.

- [ ] **Step 1: Write the failing tests**

Add `MATRIX_STAMP` and `write_matrix_run` (from `benchmarks.tests.test_main`) to the imports; append:

```python
TERSE_PATH = "benchmarks/prompts/terse.md"
TERSE_DIGEST = "sha256:" + hashlib.sha256(b"Be terse.\n").hexdigest()


def write_terse(tmp_path: Path, text: str = "Be terse.\n") -> None:
    prompt = tmp_path / TERSE_PATH
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(text)


def test_the_matrix_runs_each_model_with_its_baselines_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    nano = replace(
        NANO,
        nare_args=("--effort", "low", "--system-file", TERSE_PATH),
        nare_files={TERSE_PATH: TERSE_DIGEST},
    )
    ran = matrix(tmp_path, monkeypatch, HAIKU, nano)
    write_terse(tmp_path)
    assert main(["run", "--matrix", "--case", "edit-docstring"]) == 0
    by_model = {c.model: c.run_args for c in ran[1:]}
    assert by_model["claude-haiku-5-5"] == RunArgs()
    staged = by_model["gpt-5-nano"]
    assert (staged.recorded, staged.files) == (nano.nare_args, nano.nare_files)
    assert staged.passed == ("--effort", "low", "--system-file", "/in/0-terse.md")
    results = tmp_path / "benchmarks" / "results"
    assert staged.inputs == results / f"{MATRIX_STAMP}.gpt-5-nano" / "inputs"
    note = capsys.readouterr().out.splitlines()[-1]
    assert note.endswith(
        "Run arguments: claude-haiku-5-5: none; gpt-5-nano: --effort low "
        f"--system-file {TERSE_PATH} ({TERSE_PATH} {TERSE_DIGEST[:19]})."
    )


@pytest.mark.parametrize(
    ("nare_args", "files", "prompt", "named"),
    [
        (("--system-file", TERSE_PATH), {TERSE_PATH: TERSE_DIGEST}, "Be verbose.\n",
         f"{TERSE_PATH} no longer has the digest the baseline measured"),
        (("--system-file", TERSE_PATH), {TERSE_PATH: TERSE_DIGEST}, None,
         f"--system-file {TERSE_PATH}: cannot read it"),
        (("--no-such-flag",), {}, None, "unknown flag '--no-such-flag'"),
    ],
)  # fmt: skip
def test_a_baseline_whose_arguments_cannot_run_stops_the_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    nare_args: tuple[str, ...],
    files: dict[str, str],
    prompt: str | None,
    named: str,
) -> None:
    nano = replace(NANO, nare_args=nare_args, nare_files=files)
    ran = matrix(tmp_path, monkeypatch, HAIKU, nano)
    if prompt is not None:
        write_terse(tmp_path, prompt)
    assert main(["run", "--matrix"]) == 2
    assert ran == []
    err = capsys.readouterr().err
    assert "gpt-5-nano.smoke.toml" in err
    assert named in err


def test_compare_matrix_refuses_results_from_another_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = matrix(tmp_path, monkeypatch, HAIKU, NANO)
    write_matrix_run(tmp_path, HAIKU, NANO, change={"nare_args": ["--effort", "low"]})
    assert main(["compare", "--matrix"]) == 2
    assert "--allow-arg-change" in capsys.readouterr().err
    assert main(["compare", "--matrix", "--allow-arg-change"]) == 0
    assert capsys.readouterr().out.splitlines()[-1].endswith(
        "Run arguments: claude-haiku-5-5: --effort low; gpt-5-nano: none."
    )
    assert ran == []


def test_the_baselines_table_names_each_baselines_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    matrix(tmp_path, monkeypatch, HAIKU, replace(NANO, nare_args=("--effort", "low")))
    assert main(["compare", "--matrix", "--baselines"]) == 0
    assert capsys.readouterr().out.splitlines()[-1].endswith(
        "Run arguments: claude-haiku-5-5: none; gpt-5-nano: --effort low."
    )
```

`matrix()` creates `tmp_path/benchmarks` without `exist_ok`, so `write_terse` runs after it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_main_args.py -q -k "matrix or baselines_table"`
Expected: FAIL. The matrix passes no arguments (`staged.recorded == ()`), a changed or missing prompt and an unknown flag do not stop it, `compare --matrix` exits 0 without the flag, and no note names a variant.

- [ ] **Step 3: Implement in `benchmarks/runner/__main__.py`**

- `run_matrix`: take `stamp = new_stamp()` before the loop over baselines (it now names the staging directories too). In that loop, after `baseline_config`: `check_invocation(baseline.meta.nare_args, {c.id: c.args for c in cases}, *nare_contract())`; then `staged = stage_inputs(baseline.meta.nare_args, base=root, root=root, dest=root / "benchmarks" / "results" / f"{stamp}.{slugify(config.model)}" / "inputs")`. Either `ArgsError` prints `f"bench: {path.name}: {exc}"` and returns 2. Each path in `set(staged.files) | set(baseline.meta.nare_files)` whose two digests differ prints `f"bench: {path.name}: {file} no longer has the digest the baseline measured; restore it, or bless that model again on the new file"` and returns 2. Append `replace(config, run_args=staged)`. Pass `variants={config.model: render_variant(config.run_args.recorded, config.run_args.files) for _, config in configs}` to `render_matrix`.
- `compare_matrix`: after `model_changed`, `if args_changed(baseline, config, args.allow_arg_change): return 2`; collect each model's `render_variant` of `config.run_args` and pass them to `render_matrix`.
- `baselines_table`: pass each baseline's `render_variant(meta.nare_args, meta.nare_files)` by model.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest benchmarks/tests -q`
Expected: PASS, including every existing matrix test in `test_main.py` (no baseline there has arguments, so the note is unchanged).

- [ ] **Step 5: Build and commit**

```bash
bin/build
git add benchmarks/runner/__main__.py benchmarks/tests/test_main_args.py
git commit -F - <<'EOF'
feat(bench): the matrix runs each baseline's own run arguments (#156)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 11: README

**Files:**
- Modify: `benchmarks/README.md`

- [ ] **Step 1: Add the section**

Insert directly above `## What a run leaves behind`:

````markdown
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

`--system-file` and `--append-system-file` name a file on the host. The
runner reads it once when the run starts, copies it to
`results/<stamp>/inputs/`, and mounts the copy read-only at `/in` in every
rep, so editing the file during a run changes nothing, and the file never
shows in the diff the judge reads. Each results line records the path,
relative to the checkout, and the copy's SHA-256.

A rep whose nare never started (its container exited 2 with no result line,
and did not time out) is nare refusing its invocation, `--context-window abc`
for example. The run stops there with exit 2, prints that rep's stderr, and
writes no results file. Why it stopped is kept in two places: the rep's own
directory (`stdout.jsonl`, `stderr.txt`), as for any rep, and
`results/<stamp>/stopped.txt` (`<stamp>.<model>/` in a matrix), which names
the case, rep, model, exit code and the arguments nare was given, and holds
nare's stderr and stdout verbatim. The printed message names the file.

Each results line also records the case's args (`case_args`), the run
arguments (`nare_args`), each run file's digest (`nare_files`), the context
window the rep had (`context_window`: its size, and whether it came from the
flag, the backend or nare's default) and how many times nare compacted
(`compactions`). A window from the backend is the number that matters for a
compaction experiment, and only the stream knows it.

### The variant

Results compare with a baseline only when their run arguments are the same
variant: the same flags and values in any order, `--flag=value` and
`--flag value` alike, each run file counted by its digest, so the same
prompt under two paths is one variant and an edited prompt at one path is
two. No run arguments is the default variant, which every baseline blessed
before this is. A case's args are part of the case, like its prompt, and not
part of the variant.

`run` refuses a baseline blessed on another variant before it builds
anything, and `compare` refuses the same way, with exit 2 and both lists in
the message. `--allow-arg-change` overrides that, and only that: a model,
provider, judge or cap change still needs `--allow-model-change`, which does
not cover a variant change. When the override lets two variants meet, both
lists print above the table.

`bless` writes the results' run arguments and file digests into the
baseline's `[meta]`. It exits 2 rather than keep cases from a baseline
blessed on another variant, unless `--allow-arg-change`, and always exits 2
for a run file outside the checkout, since nobody else could reproduce that
baseline. A model has one blessed variant at a time.

`run --matrix` runs each model with its baseline's run arguments, after
reading each run file again: one whose digest no longer matches the
baseline exits 2 before anything is built. The matrix table's note names
each model's variant when any has one.

The runner never passes `--effort` unasked, since what a level means varies
by model. Passed as a run argument, it is part of the variant, so a level is
only ever compared with the same level on the same model.

    bin/bench run --tier full --case context-overflow -- --context-window 16000

makes `context-overflow` compact on any model, and `compactions` on each
results line says whether it did.
````

- [ ] **Step 2: Point to it from the other sections**

- In "Running it", end the paragraph that begins `` `run` and `compare` refuse, with exit 2, `` with: `` Run arguments are part of what a baseline measured too, with their own override, `--allow-arg-change`: see "Arguments to nare". ``
- In "What a run leaves behind", add as the last line of the first tree block, aligned with its comments: `    benchmarks/results/<stamp>/inputs/        the run's prompt files, as every rep read them`.
- In "Writing a case", after the checks paragraph, add: `` `args` is an optional list of flags for `nare run`, for example `["--tools", "read,list,search"]`. A path in a flag's value is relative to the fixture, so a case that passes `--schema answer.schema.json` ships that file in `fixture/`. Which flags a case may pass is under "Arguments to nare". Adding `args` to an existing case changes what its baselines measured, so bless it again. ``

- [ ] **Step 3: Check the prose**

Run: `grep -nP '\x{2014}' benchmarks/README.md`
Expected: no output.

- [ ] **Step 4: Build and commit**

```bash
bin/build
git add benchmarks/README.md
git commit -F - <<'EOF'
docs(bench): document case args, run arguments, the variant and --allow-arg-change (#156)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 12: The gate

- [ ] **Step 1: Build**

Run: `bin/build`
Expected: clean.

- [ ] **Step 2: Scope**

Run: `git diff --stat main -- src/nare benchmarks/cases benchmarks/baselines`
Expected: no output.

- [ ] **Step 3: Prose and infrastructure**

Run: `git diff main | grep -nP '\x{2014}'`
Expected: no output. Then read `git diff main` for hostnames, internal addresses or key aliases: tests use only `https://proxy.example` and placeholder keys.

- [ ] **Step 4: Acceptance criteria (spec section 14)**

Confirm each is pinned by a passing test:

| Criterion | Tests |
| --- | --- |
| A case's `args` reach nare; `verify` and `run` refuse an unknown flag or tool before building | `test_a_case_passes_its_args_as_written`, `test_arguments_reach_docker_after_the_script_and_never_inside_it`, `test_run_passes_its_arguments_then_the_cases_and_records_both`, `test_verify_refuses_a_case_whose_args_nare_would_refuse`, `test_run_refuses_arguments_before_building_anything` |
| `run -- --system-file PATH` runs every rep with it, every line records path and SHA-256, `bless` writes both | `test_run_reads_a_system_file_once_and_every_line_records_its_digest`, `test_a_run_file_is_mounted_read_only_at_in`, `test_bless_writes_the_variant_the_results_were_run_with` |
| `run`, `compare`, `bless` exit 2 across variants unless `--allow-arg-change`, which overrides only that | `test_run_refuses_another_variant_before_building_anything`, `test_compare_refuses_results_from_another_variant`, `test_allow_arg_change_does_not_cover_a_judge_change`, `test_bless_will_not_keep_cases_measured_on_another_variant`, `test_compare_matrix_refuses_results_from_another_variant` |
| Reserved flags, flags in the wrong list, a flag in both lists exit 2 naming the flag | `test_a_reserved_flag_is_refused_in_either_list_and_either_form`, `test_a_system_prompt_flag_is_a_run_argument_only`, `test_a_work_tree_path_flag_is_a_case_argument_only`, `test_a_flag_in_both_lists_is_refused_naming_the_flag_and_the_case` |
| A never-started rep stops the run with exit 2, writes no results file, and leaves `stopped.txt` with the reason beside the rep's evidence | `test_only_a_never_started_rep_stops_the_run`, `test_the_stop_log_says_why_with_the_arguments_and_both_streams`, `test_the_stop_log_says_when_there_were_no_arguments_and_no_stdout`, `test_a_never_started_rep_stops_the_run_with_exit_two`, `test_a_never_started_rep_stops_the_matrix` |
| Each line records the window and the compactions | `test_a_rep_records_its_arguments_window_and_compactions`, `test_an_errored_rep_records_them_too` |
| Existing baselines and results load unchanged, as the default variant | `test_every_committed_baseline_renders_byte_for_byte_as_it_is`, `test_a_results_line_from_before_loads_as_the_default_variant`, `test_results_from_before_compare_as_the_default_variant` |
| README documents case `args`, run arguments, the key and `--allow-arg-change`; `bin/build` passes | Task 11; Step 1 |

- [ ] **Step 5 (optional, free, needs Docker): `bin/bench verify`**

Run: `bin/bench verify --tier full`
Expected: every case `ok` or `exempt`, as before this branch. If Docker is not available here, say so in the report instead.

- [ ] **Step 6 (optional, paid, only with the developer's go-ahead): one live run**

For example `bin/bench run --case edit-docstring -- --system-file <a prompt file inside the checkout>`, at smoke reps. Report only aggregates (pass rate, token median, `context_window`, `compactions`), never a path outside the checkout, a key or the base URL. Do not bless it.

- [ ] **Step 7: Report**

Report the commits, the `bin/build` result and the Step 4 table. No push and no PR unless asked.
