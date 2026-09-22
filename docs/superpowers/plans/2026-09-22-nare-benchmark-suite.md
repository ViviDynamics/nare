# nare Benchmark Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a container-isolated benchmark suite that measures whether a
change to nare makes real tasks succeed more often, at better quality, for
fewer tokens.

**Architecture:** A top-level `benchmarks/` tree, excluded from the wheel. Six
runner modules split into four pure ones (`config`, `case`, `grade`, `report`)
that are unit-tested with no Docker and no network, and two impure ones
(`sandbox`, `judge`) that talk to Docker and to the model. Cases are data , 
a `case.toml` plus a `fixture/` tree - and name no model, so the same case
runs against a proxy alias or a first-party model unchanged.

**Tech Stack:** Python 3.12 stdlib only (`tomllib`, `subprocess`, `shutil`,
`statistics`, `json`, `argparse`) plus `nare` itself for the judge transport.
Docker CLI via `subprocess`. pytest, ruff, mypy already in the dev group.

**Spec:** `docs/superpowers/specs/2026-09-21-nare-benchmark-suite-design.md`

## Starting point

**Branch:** `spec/nare-benchmark-suite`, branched from `main`.

Slice 1 (the harness itself) merged as PR #4, so `main` carries `src/nare`,
`tests/`, `pyproject.toml` and `bin/build`. Nothing in this plan depends on
unmerged work.

Read `.agents/skills/conventions/SKILL.md` before the first commit. Four of its
rules bind this plan: squash merges only, never push to `main`, never
force-push anything but your own unmerged branch and then only with
`--force-with-lease`, and published prose carries no em dashes.

Set a fresh clone up first, because CI runs the same check:

```bash
cp repo.env.example repo.env
.agents/skills/ci-safety/scripts/check-wiring    # prints {"ok":true}
```

Then confirm you are in the right place before Task 1:

```bash
git rev-parse --abbrev-ref HEAD          # spec/nare-benchmark-suite
ls src/nare/loop.py tests/fake_provider.py pyproject.toml bin/build
bin/build                                # must be green before you change anything
```

If `bin/build` is red on a clean checkout, stop. That is a pre-existing
failure and not this plan's to fix.

**What each phase needs:**

| Tasks | Needs |
|---|---|
| 1-8 | `uv` only. No Docker, no API key, no network, no money. |
| 9-11 | Docker. Still no API key and no money. |
| 12-15 | Docker, plus a key and a model. Task 15 step 5 spends real tokens. |

Tasks 1-8 are more than half the work and can be executed entirely offline.

## Global Constraints

- **No new dependencies, runtime or dev.** The benchmark uses the standard
  library, `nare` itself, and the `docker` CLI through `subprocess`. Nothing
  is added to `[project] dependencies` or `[dependency-groups] dev`.
- **Nothing under `src/nare/` changes.** Not one line. If a task appears to
  need a change there, stop and raise it.
- **Python `>=3.12`**, matching `requires-python`.
- **ruff**: `line-length = 88`, lint select `["E", "F", "I", "UP", "B"]`.
- **mypy strict** (`[tool.mypy] strict = true`, `python_version = "3.12"`).
  `benchmarks` is added to the checked paths, so every function needs
  annotations and no `Any` escapes without a cast.
- **pytest**: `asyncio_mode = "auto"`. Markers are `live` and `docker`;
  `addopts = "-m 'not live and not docker'"`.
- **Exit codes**: `0` clean, `1` regression or inconclusive, `2` setup or
  infrastructure failure. Matches `src/nare/cli.py`.
- **Tier defaults**: `smoke` = 3 reps, 600s per rep. `full` = 5 reps, 900s per
  rep. Re-confirmation runs 7 reps. Token tolerance band is ±10%.
- **Image tag**: `nare-bench:local`.
- **Cases carry no `model`, `provider`, or `base_url`.** `case.py` rejects
  them. This is what makes the suite portable and is tested explicitly.
- **Commit after every task.** Conventional-commit prefixes, matching the
  repo's history (`feat:`, `test:`, `fix:`, `docs:`, `chore:`).

## Deviations from the spec, and why

Three, all deliberate. Raise anything else rather than improvising.

1. **Six runner modules, not five.** The spec's section 4 lists five. This
   plan adds `config.py` so that environment resolution is unit-testable
   without argparse and `__main__.py` stays a thin adapter.
2. **Tests split per module, not one `test_runner.py`.** The repo already
   uses one test file per module (`test_loop.py`, `test_tools.py`); the suite
   follows the house style.
3. **`verify` exercises only `bash` checks.** Carried into the spec on
   2026-09-22. A `result` check needs a result line and `verify` never runs
   the agent; a case with only `result` checks is exempt, not failed.

---

### Task 1: Project wiring and configuration resolution

Creates the package skeleton, wires the toolchain, and delivers the one piece
of pure logic every later task depends on: turning flags and environment
variables into a resolved `Config`.

**Files:**
- Create: `benchmarks/__init__.py`
- Create: `benchmarks/runner/__init__.py`
- Create: `benchmarks/tests/__init__.py`
- Create: `benchmarks/runner/config.py`
- Create: `benchmarks/tests/test_config.py`
- Create: `bin/bench`
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Modify: `bin/build`

**Interfaces:**
- Consumes: nothing.
- Produces: `Config` (frozen dataclass with fields `model: str`,
  `provider: str`, `base_url: str | None`, `judge_model: str`,
  `api_key: str`), `ConfigError(Exception)`, and
  `resolve_config(*, model: str | None, provider: str | None, base_url: str | None, judge_model: str | None, env: Mapping[str, str]) -> Config`.

- [ ] **Step 1: Create the package skeleton**

```bash
mkdir -p benchmarks/runner benchmarks/tests benchmarks/cases benchmarks/baselines
touch benchmarks/__init__.py benchmarks/runner/__init__.py benchmarks/tests/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `benchmarks/tests/test_config.py`:

```python
from __future__ import annotations

import pytest

from benchmarks.runner.config import ConfigError, resolve_config


def test_flags_win_over_environment() -> None:
    config = resolve_config(
        model="claude-haiku",
        provider=None,
        base_url=None,
        judge_model=None,
        env={"NARE_MODEL": "ignored", "ANTHROPIC_API_KEY": "k"},
    )
    assert config.model == "claude-haiku"
    assert config.provider == "anthropic"
    assert config.base_url is None


def test_environment_supplies_what_flags_omit() -> None:
    config = resolve_config(
        model=None,
        provider=None,
        base_url=None,
        judge_model=None,
        env={
            "NARE_MODEL": "gpt-5-nano",
            "NARE_PROVIDER": "anthropic",
            "NARE_BASE_URL": "https://proxy.example/v1",
            "ANTHROPIC_API_KEY": "k",
        },
    )
    assert config.model == "gpt-5-nano"
    assert config.base_url == "https://proxy.example/v1"


def test_judge_model_defaults_to_the_model_under_test() -> None:
    config = resolve_config(
        model="claude-haiku",
        provider=None,
        base_url=None,
        judge_model=None,
        env={"ANTHROPIC_API_KEY": "k"},
    )
    assert config.judge_model == "claude-haiku"


def test_judge_model_can_be_set_by_environment() -> None:
    config = resolve_config(
        model="claude-haiku",
        provider=None,
        base_url=None,
        judge_model=None,
        env={"ANTHROPIC_API_KEY": "k", "NARE_BENCH_JUDGE_MODEL": "gpt-5-nano"},
    )
    assert config.judge_model == "gpt-5-nano"


def test_missing_model_is_refused() -> None:
    with pytest.raises(ConfigError, match="model"):
        resolve_config(
            model=None,
            provider=None,
            base_url=None,
            judge_model=None,
            env={"ANTHROPIC_API_KEY": "k"},
        )


def test_missing_api_key_is_refused() -> None:
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        resolve_config(
            model="claude-haiku",
            provider=None,
            base_url=None,
            judge_model=None,
            env={},
        )
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest benchmarks/tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmarks.runner.config'`

- [ ] **Step 4: Write the implementation**

Create `benchmarks/runner/config.py`:

```python
"""Everything the run supplies and a case deliberately does not.

Cases name a task; the environment names the model. Reusing nare's own
variable names means an operator who can already run `nare run` against their
endpoint can run the benchmark with no further configuration.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


class ConfigError(Exception):
    """Configuration that cannot produce a run. Always exit 2."""


@dataclass(frozen=True)
class Config:
    model: str
    provider: str
    base_url: str | None
    judge_model: str
    api_key: str


def resolve_config(
    *,
    model: str | None,
    provider: str | None,
    base_url: str | None,
    judge_model: str | None,
    env: Mapping[str, str],
) -> Config:
    """Flags win, environment fills the gaps, and two values are mandatory."""
    resolved_model = model or env.get("NARE_MODEL")
    if not resolved_model:
        raise ConfigError("no model: pass --model or set NARE_MODEL")
    api_key = env.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ConfigError("ANTHROPIC_API_KEY is not set")
    return Config(
        model=resolved_model,
        provider=provider or env.get("NARE_PROVIDER") or "anthropic",
        base_url=base_url or env.get("NARE_BASE_URL") or None,
        # The judge defaults to the model under test because that is the one
        # model the operator is known to have access to.
        judge_model=(
            judge_model or env.get("NARE_BENCH_JUDGE_MODEL") or resolved_model
        ),
        api_key=api_key,
    )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest benchmarks/tests/test_config.py -v`
Expected: PASS, 6 tests

- [ ] **Step 6: Wire the toolchain**

In `pyproject.toml`, replace the `[tool.pytest.ini_options]` block with:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests", "benchmarks/tests"]
markers = [
  "live: hits the real Anthropic API; needs ANTHROPIC_API_KEY",
  "docker: needs a working docker daemon",
]
addopts = "-m 'not live and not docker'"
```

In `.gitignore`, append:

```
benchmarks/results/
```

In `bin/build`, replace the three tool lines so `benchmarks` is held to the
same bar as `src`:

```bash
uv run ruff format --check src tests benchmarks
uv run ruff check src tests benchmarks
uv run mypy src tests benchmarks
```

- [ ] **Step 7: Create the wrapper script**

Create `bin/bench`:

```bash
#!/usr/bin/env bash
# The benchmark entry point. Matches bin/build's idiom so one habit covers both.
set -euo pipefail
exec uv run python -m benchmarks.runner "$@"
```

Then: `chmod +x bin/bench`

- [ ] **Step 8: Verify the whole toolchain passes**

Run: `bin/build`
Expected: ruff format, ruff check, mypy, and pytest all pass. The existing
`tests/` suite is unaffected and `benchmarks/tests/test_config.py` runs.

- [ ] **Step 9: Commit**

```bash
git add benchmarks bin/bench bin/build pyproject.toml .gitignore
git commit -m "feat: add the benchmark package skeleton and config resolution"
```

---

### Task 2: Case loading and validation

Cases are data. This task makes a directory of TOML into typed objects and
rejects every malformed shape the spec names - including the model fields
that would break portability.

**Files:**
- Create: `benchmarks/runner/case.py`
- Create: `benchmarks/tests/test_case.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Check`, `Judge`, `Case`, `TierDefaults`, `TIER_DEFAULTS`,
  `CaseError(Exception)`, `load_case(directory: Path) -> Case`, and
  `load_cases(root: Path, tier: str | None = None) -> list[Case]`.
  `Case` exposes `reps` and `timeout` as resolved ints (tier default applied
  at load time, so no later module repeats the fallback).

- [ ] **Step 1: Write the failing test**

Create `benchmarks/tests/test_case.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.runner.case import CaseError, load_case, load_cases

GOOD = """
id = "fix-failing-test"
tier = "smoke"
prompt = "test_parse fails. Fix bar.py so the suite passes."
max_turns = 12

[[check]]
kind = "bash"
cmd = "pytest -q"

[[check]]
kind = "result"
status = "done"
max_turns = 10

[judge]
assertions = ["The fix is in bar.py.", "test_parse was not weakened."]
min_met = 2
"""


def write_case(root: Path, name: str, body: str) -> Path:
    directory = root / name
    (directory / "fixture").mkdir(parents=True)
    (directory / "fixture" / "bar.py").write_text("x = 1\n")
    (directory / "case.toml").write_text(body)
    return directory


def test_loads_a_well_formed_case(tmp_path: Path) -> None:
    case = load_case(write_case(tmp_path, "fix-failing-test", GOOD))
    assert case.id == "fix-failing-test"
    assert case.tier == "smoke"
    assert case.max_turns == 12
    assert len(case.checks) == 2
    assert case.checks[0].kind == "bash"
    assert case.checks[0].cmd == "pytest -q"
    assert case.checks[1].status == "done"
    assert case.checks[1].max_turns == 10
    assert case.judge is not None
    assert case.judge.min_met == 2
    assert case.fixture.is_dir()


def test_tier_defaults_are_resolved_at_load(tmp_path: Path) -> None:
    case = load_case(write_case(tmp_path, "fix-failing-test", GOOD))
    assert case.reps == 3
    assert case.timeout == 600


def test_case_overrides_beat_tier_defaults(tmp_path: Path) -> None:
    body = GOOD.replace('max_turns = 12', 'max_turns = 12\nreps = 9\ntimeout = 30')
    case = load_case(write_case(tmp_path, "fix-failing-test", body))
    assert case.reps == 9
    assert case.timeout == 30


def test_full_tier_has_its_own_defaults(tmp_path: Path) -> None:
    body = GOOD.replace('tier = "smoke"', 'tier = "full"')
    case = load_case(write_case(tmp_path, "fix-failing-test", body))
    assert case.reps == 5
    assert case.timeout == 900


@pytest.mark.parametrize(
    "mutation, message",
    [
        ('id = "fix-failing-test"', "does not match its directory"),
        ('tier = "smoke"', "tier"),
        ('prompt = "test_parse fails. Fix bar.py so the suite passes."', "prompt"),
    ],
)
def test_missing_required_fields_are_refused(
    tmp_path: Path, mutation: str, message: str
) -> None:
    body = GOOD.replace(mutation, "")
    with pytest.raises(CaseError, match=message):
        load_case(write_case(tmp_path, "fix-failing-test", body))


def test_mismatched_id_is_refused(tmp_path: Path) -> None:
    body = GOOD.replace('id = "fix-failing-test"', 'id = "something-else"')
    with pytest.raises(CaseError, match="does not match its directory"):
        load_case(write_case(tmp_path, "fix-failing-test", body))


def test_unknown_tier_is_refused(tmp_path: Path) -> None:
    body = GOOD.replace('tier = "smoke"', 'tier = "enormous"')
    with pytest.raises(CaseError, match="tier"):
        load_case(write_case(tmp_path, "fix-failing-test", body))


def test_a_case_with_no_checks_is_refused(tmp_path: Path) -> None:
    body = GOOD.split("[[check]]")[0]
    with pytest.raises(CaseError, match="at least one check"):
        load_case(write_case(tmp_path, "fix-failing-test", body))


def test_unknown_check_kind_is_refused(tmp_path: Path) -> None:
    body = GOOD.replace('kind = "bash"', 'kind = "file"')
    with pytest.raises(CaseError, match="unknown check kind"):
        load_case(write_case(tmp_path, "fix-failing-test", body))


def test_bash_check_without_cmd_is_refused(tmp_path: Path) -> None:
    body = GOOD.replace('cmd = "pytest -q"', "")
    with pytest.raises(CaseError, match="cmd"):
        load_case(write_case(tmp_path, "fix-failing-test", body))


def test_result_check_with_no_assertion_is_refused(tmp_path: Path) -> None:
    body = GOOD.replace('status = "done"\nmax_turns = 10', "")
    with pytest.raises(CaseError, match="status"):
        load_case(write_case(tmp_path, "fix-failing-test", body))


@pytest.mark.parametrize("field", ["model", "provider", "base_url"])
def test_environment_fields_are_refused(tmp_path: Path, field: str) -> None:
    """The portability guarantee, enforced rather than documented."""
    body = GOOD + f'\n{field} = "claude-haiku"\n'
    with pytest.raises(CaseError, match=field):
        load_case(write_case(tmp_path, "fix-failing-test", body))


def test_empty_assertions_list_is_refused(tmp_path: Path) -> None:
    body = GOOD.replace(
        'assertions = ["The fix is in bar.py.", "test_parse was not weakened."]',
        "assertions = []",
    )
    with pytest.raises(CaseError, match="assertions"):
        load_case(write_case(tmp_path, "fix-failing-test", body))


def test_min_met_above_assertion_count_is_refused(tmp_path: Path) -> None:
    body = GOOD.replace("min_met = 2", "min_met = 5")
    with pytest.raises(CaseError, match="min_met"):
        load_case(write_case(tmp_path, "fix-failing-test", body))


def test_a_case_without_a_fixture_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "fix-failing-test"
    directory.mkdir()
    (directory / "case.toml").write_text(GOOD)
    with pytest.raises(CaseError, match="fixture"):
        load_case(directory)


def test_load_cases_filters_by_tier_and_sorts(tmp_path: Path) -> None:
    write_case(tmp_path, "fix-failing-test", GOOD)
    write_case(tmp_path, "aaa-full-case", GOOD.replace('tier = "smoke"', 'tier = "full"')
               .replace('id = "fix-failing-test"', 'id = "aaa-full-case"'))
    assert [c.id for c in load_cases(tmp_path)] == [
        "aaa-full-case",
        "fix-failing-test",
    ]
    assert [c.id for c in load_cases(tmp_path, tier="smoke")] == ["fix-failing-test"]


def test_load_cases_on_an_empty_root_returns_nothing(tmp_path: Path) -> None:
    assert load_cases(tmp_path) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest benchmarks/tests/test_case.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmarks.runner.case'`

- [ ] **Step 3: Write the implementation**

Create `benchmarks/runner/case.py`:

```python
"""Loading and validating cases. Pure: no Docker, no network, no clock.

A case is data. It describes a task and how to check it, and says nothing
about where it runs -- the model, the provider and the endpoint are the run's
business. `load_case` enforces that rather than trusting it.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Tier = Literal["smoke", "full"]
CheckKind = Literal["bash", "result"]

# Fields that would pin a case to one operator's environment. Rejected by
# name so the failure explains itself instead of being silently ignored.
_ENVIRONMENT_FIELDS = ("model", "provider", "base_url")


class CaseError(Exception):
    """A case that cannot be run. Always exit 2; never a task failure."""


@dataclass(frozen=True)
class TierDefaults:
    reps: int
    timeout: int


TIER_DEFAULTS: dict[str, TierDefaults] = {
    "smoke": TierDefaults(reps=3, timeout=600),
    "full": TierDefaults(reps=5, timeout=900),
}


@dataclass(frozen=True)
class Check:
    kind: CheckKind
    cmd: str | None = None
    status: str | None = None
    max_turns: int | None = None


@dataclass(frozen=True)
class Judge:
    assertions: tuple[str, ...]
    min_met: int | None = None


@dataclass(frozen=True)
class Case:
    id: str
    tier: Tier
    prompt: str
    directory: Path
    max_turns: int
    reps: int
    timeout: int
    checks: tuple[Check, ...]
    judge: Judge | None

    @property
    def fixture(self) -> Path:
        return self.directory / "fixture"

    @property
    def bash_checks(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.kind == "bash")


def _require_str(raw: dict[str, Any], key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise CaseError(f"{where}: {key} is required and must be a non-empty string")
    return value


def _load_check(raw: dict[str, Any], where: str) -> Check:
    kind = raw.get("kind")
    if kind == "bash":
        cmd = raw.get("cmd")
        if not isinstance(cmd, str) or not cmd:
            raise CaseError(f"{where}: a bash check needs a non-empty cmd")
        return Check(kind="bash", cmd=cmd)
    if kind == "result":
        status = raw.get("status")
        max_turns = raw.get("max_turns")
        if status is None and max_turns is None:
            raise CaseError(
                f"{where}: a result check needs status, max_turns, or both"
            )
        if status is not None and not isinstance(status, str):
            raise CaseError(f"{where}: result check status must be a string")
        if max_turns is not None and not isinstance(max_turns, int):
            raise CaseError(f"{where}: result check max_turns must be an integer")
        return Check(kind="result", status=status, max_turns=max_turns)
    raise CaseError(f"{where}: unknown check kind {kind!r}; expected bash or result")


def _load_judge(raw: dict[str, Any], where: str) -> Judge:
    assertions = raw.get("assertions")
    if not isinstance(assertions, list) or not assertions:
        raise CaseError(f"{where}: judge assertions must be a non-empty list")
    if not all(isinstance(a, str) and a for a in assertions):
        raise CaseError(f"{where}: every judge assertion must be a non-empty string")
    min_met = raw.get("min_met")
    if min_met is not None:
        if not isinstance(min_met, int) or min_met < 1:
            raise CaseError(f"{where}: min_met must be a positive integer")
        if min_met > len(assertions):
            raise CaseError(
                f"{where}: min_met {min_met} exceeds {len(assertions)} assertions"
            )
    return Judge(assertions=tuple(assertions), min_met=min_met)


def load_case(directory: Path) -> Case:
    """Read and validate one case directory."""
    path = directory / "case.toml"
    where = str(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CaseError(f"{where}: no case.toml") from exc
    except tomllib.TOMLDecodeError as exc:
        raise CaseError(f"{where}: malformed TOML: {exc}") from exc

    for field in _ENVIRONMENT_FIELDS:
        if field in raw:
            raise CaseError(
                f"{where}: {field} belongs to the run, not the case. "
                "Pass it with --model/--provider/--base-url or the NARE_* "
                "variables so the case stays portable."
            )

    case_id = _require_str(raw, "id", where)
    if case_id != directory.name:
        raise CaseError(
            f"{where}: id {case_id!r} does not match its directory "
            f"{directory.name!r}; the directory name is the identity"
        )

    tier = _require_str(raw, "tier", where)
    if tier not in TIER_DEFAULTS:
        raise CaseError(
            f"{where}: tier {tier!r} is not one of {', '.join(TIER_DEFAULTS)}"
        )
    defaults = TIER_DEFAULTS[tier]

    prompt = _require_str(raw, "prompt", where)

    raw_checks = raw.get("check")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise CaseError(f"{where}: a case needs at least one check")
    checks = tuple(_load_check(c, where) for c in raw_checks)

    raw_judge = raw.get("judge")
    judge = _load_judge(raw_judge, where) if raw_judge is not None else None

    if not (directory / "fixture").is_dir():
        raise CaseError(f"{where}: no fixture/ directory beside case.toml")

    return Case(
        id=case_id,
        tier=tier,  # type: ignore[arg-type]
        prompt=prompt,
        directory=directory,
        max_turns=int(raw.get("max_turns", 12)),
        # Resolved here so no later module repeats the fallback.
        reps=int(raw.get("reps", defaults.reps)),
        timeout=int(raw.get("timeout", defaults.timeout)),
        checks=checks,
        judge=judge,
    )


def load_cases(root: Path, tier: str | None = None) -> list[Case]:
    """Load every case under `root`, sorted by id, optionally filtered by tier."""
    if not root.is_dir():
        return []
    cases = [
        load_case(entry)
        for entry in sorted(root.iterdir())
        if entry.is_dir() and (entry / "case.toml").exists()
    ]
    if tier is not None:
        cases = [c for c in cases if c.tier == tier]
    return cases
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest benchmarks/tests/test_case.py -v`
Expected: PASS

- [ ] **Step 5: Verify types and lint**

Run: `uv run ruff format benchmarks && uv run ruff check benchmarks && uv run mypy benchmarks`
Expected: all clean

- [ ] **Step 6: Commit**

```bash
git add benchmarks/runner/case.py benchmarks/tests/test_case.py
git commit -m "feat: add case loading, with environment fields refused"
```

---

### Task 3: Parsing nare's output stream

Turns the JSONL on a run's stdout into the one thing grading needs: the
terminal `result` line, or `None` when there is no such line.

**Files:**
- Create: `benchmarks/runner/grade.py`
- Create: `benchmarks/tests/test_grade.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ResultLine` (frozen dataclass: `status: str`, `turns: int`,
  `usage: dict[str, int]`, `stop_reason: str | None`, `error: str | None`)
  and `parse_stdout(text: str) -> ResultLine | None`.

- [ ] **Step 1: Write the failing test**

Create `benchmarks/tests/test_grade.py`:

```python
from __future__ import annotations

import json

from benchmarks.runner.grade import parse_stdout

EVENT = json.dumps({"type": "progress", "text": "working", "detail": {}})
RESULT = json.dumps(
    {
        "type": "result",
        "session_id": "abc",
        "status": "done",
        "questions": [],
        "usage": {"input": 100, "output": 20, "cache_read": 0, "cache_write": 0},
        "stop_reason": "end_turn",
        "turns": 4,
        "error": None,
    }
)


def test_reads_the_result_line_past_the_events() -> None:
    line = parse_stdout(f"{EVENT}\n{EVENT}\n{RESULT}\n")
    assert line is not None
    assert line.status == "done"
    assert line.turns == 4
    assert line.usage["input"] == 100
    assert line.stop_reason == "end_turn"
    assert line.error is None


def test_no_result_line_returns_none() -> None:
    """A run that never started emits no result line. That is an error, not
    a failure, and the caller needs to be able to tell."""
    assert parse_stdout(f"{EVENT}\n{EVENT}\n") is None


def test_empty_output_returns_none() -> None:
    assert parse_stdout("") is None


def test_non_json_noise_is_ignored() -> None:
    line = parse_stdout(f"warning: something\n{RESULT}\n")
    assert line is not None
    assert line.status == "done"


def test_a_truncated_final_line_returns_none() -> None:
    assert parse_stdout(f"{EVENT}\n{RESULT[:40]}") is None


def test_the_last_result_line_wins() -> None:
    second = RESULT.replace('"status": "done"', '"status": "blocked"')
    line = parse_stdout(f"{RESULT}\n{second}\n")
    assert line is not None
    assert line.status == "blocked"


def test_an_error_result_is_carried_through() -> None:
    raw = RESULT.replace('"status": "done"', '"status": "error"').replace(
        '"error": null', '"error": "stopped after 12 turns"'
    )
    line = parse_stdout(raw)
    assert line is not None
    assert line.status == "error"
    assert line.error == "stopped after 12 turns"


def test_missing_usage_fields_default_to_zero() -> None:
    raw = json.dumps({"type": "result", "status": "done", "turns": 1, "usage": {}})
    line = parse_stdout(raw)
    assert line is not None
    assert line.usage == {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest benchmarks/tests/test_grade.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmarks.runner.grade'`

- [ ] **Step 3: Write the implementation**

Create `benchmarks/runner/grade.py`:

```python
"""Grading. Pure: it is handed what happened and decides what it means.

Nothing here runs a container or calls a model. `sandbox` produces exit codes
and stdout, `judge` produces booleans, and this module turns both into a
verdict -- which is what makes the whole grading policy unit-testable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

_USAGE_FIELDS = ("input", "output", "cache_read", "cache_write")


@dataclass(frozen=True)
class ResultLine:
    """nare's terminal `result` line, which is state rather than an event."""

    status: str
    turns: int
    usage: dict[str, int]
    stop_reason: str | None
    error: str | None


def _usage_from(raw: Any) -> dict[str, int]:
    source = raw if isinstance(raw, dict) else {}
    return {field: int(source.get(field, 0)) for field in _USAGE_FIELDS}


def parse_stdout(text: str) -> ResultLine | None:
    """Return the last `result` line, or None if the run emitted none.

    None is load-bearing: nare emits no result line when a run never started,
    which the caller turns into an `error` outcome rather than a `fail`.
    """
    found: ResultLine | None = None
    for raw_line in text.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            # Noise on stdout, or a line cut off by a kill. Neither is a
            # result line, and neither should crash the run.
            continue
        if not isinstance(payload, dict) or payload.get("type") != "result":
            continue
        found = ResultLine(
            status=str(payload.get("status", "")),
            turns=int(payload.get("turns", 0)),
            usage=_usage_from(payload.get("usage")),
            stop_reason=payload.get("stop_reason"),
            error=payload.get("error"),
        )
    return found
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest benchmarks/tests/test_grade.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add benchmarks/runner/grade.py benchmarks/tests/test_grade.py
git commit -m "feat: parse nare's result line out of a run's stdout"
```

---

### Task 4: Check grading and the rep outcome

The heart of the grading policy: what passes, what fails, and - the rule that
makes the numbers trustworthy - what is neither.

**Files:**
- Modify: `benchmarks/runner/grade.py`
- Modify: `benchmarks/tests/test_grade.py`

**Interfaces:**
- Consumes: `Check`, `Judge` from `benchmarks.runner.case`; `ResultLine` from
  Task 3.
- Produces: `Outcome = Literal["pass", "fail", "error"]`, `CheckResult`
  (frozen: `label: str`, `passed: bool`, `detail: str`),
  `grade_result_check(check, line) -> CheckResult`,
  `grade_bash_check(check, exit_code, output) -> CheckResult`, and
  `rep_outcome(checks: Sequence[CheckResult], judge: Judge | None, met: list[bool] | None) -> Outcome`.

- [ ] **Step 1: Write the failing test**

Append to `benchmarks/tests/test_grade.py`:

```python
from benchmarks.runner.case import Check, Judge
from benchmarks.runner.grade import (
    CheckResult,
    ResultLine,
    grade_bash_check,
    grade_result_check,
    rep_outcome,
)

DONE = ResultLine(
    status="done",
    turns=4,
    usage={"input": 1, "output": 1, "cache_read": 0, "cache_write": 0},
    stop_reason="end_turn",
    error=None,
)


def test_result_check_matches_status() -> None:
    result = grade_result_check(Check(kind="result", status="done"), DONE)
    assert result.passed


def test_result_check_rejects_a_different_status() -> None:
    result = grade_result_check(Check(kind="result", status="blocked"), DONE)
    assert not result.passed
    assert "done" in result.detail


def test_result_check_enforces_a_turn_ceiling() -> None:
    assert grade_result_check(Check(kind="result", max_turns=4), DONE).passed
    assert not grade_result_check(Check(kind="result", max_turns=3), DONE).passed


def test_result_check_requires_both_when_both_are_given() -> None:
    check = Check(kind="result", status="done", max_turns=3)
    assert not grade_result_check(check, DONE).passed


def test_bash_check_passes_on_exit_zero() -> None:
    assert grade_bash_check(Check(kind="bash", cmd="pytest -q"), 0, "ok").passed


def test_bash_check_fails_on_nonzero_and_keeps_the_output() -> None:
    result = grade_bash_check(Check(kind="bash", cmd="pytest -q"), 1, "E   assert 0")
    assert not result.passed
    assert "assert 0" in result.detail


def test_rep_passes_when_checks_pass_and_there_is_no_judge() -> None:
    assert rep_outcome([CheckResult("bash: x", True, "")], None, None) == "pass"


def test_rep_fails_when_any_check_fails() -> None:
    checks = [CheckResult("a", True, ""), CheckResult("b", False, "")]
    assert rep_outcome(checks, None, None) == "fail"


def test_a_failed_check_is_not_rescued_by_a_perfect_judge() -> None:
    """Programmatic correctness gates. The judge can only ever make it worse."""
    judge = Judge(assertions=("a", "b"), min_met=2)
    assert rep_outcome([CheckResult("x", False, "")], judge, [True, True]) == "fail"


def test_judge_gate_can_fail_a_rep_whose_checks_passed() -> None:
    judge = Judge(assertions=("a", "b"), min_met=2)
    passed = [CheckResult("x", True, "")]
    assert rep_outcome(passed, judge, [True, False]) == "fail"
    assert rep_outcome(passed, judge, [True, True]) == "pass"


def test_a_judge_without_min_met_reports_but_does_not_gate() -> None:
    judge = Judge(assertions=("a", "b"), min_met=None)
    assert rep_outcome([CheckResult("x", True, "")], judge, [False, False]) == "pass"


def test_an_unanswerable_judge_is_an_error_only_when_it_gates() -> None:
    """A judge that cannot answer must not look like a bad answer."""
    gating = Judge(assertions=("a",), min_met=1)
    reporting = Judge(assertions=("a",), min_met=None)
    passed = [CheckResult("x", True, "")]
    assert rep_outcome(passed, gating, None) == "error"
    assert rep_outcome(passed, reporting, None) == "pass"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest benchmarks/tests/test_grade.py -v`
Expected: FAIL with `ImportError: cannot import name 'CheckResult'`

- [ ] **Step 3: Write the implementation**

Append to `benchmarks/runner/grade.py` (and add
`from collections.abc import Sequence`, `from typing import Literal`, and
`from benchmarks.runner.case import Check, Judge` to the imports):

```python
Outcome = Literal["pass", "fail", "error"]


@dataclass(frozen=True)
class CheckResult:
    label: str
    passed: bool
    detail: str


def grade_result_check(check: Check, line: ResultLine) -> CheckResult:
    """Every condition a result check names must hold, not just one."""
    failures: list[str] = []
    if check.status is not None and line.status != check.status:
        failures.append(f"status {line.status!r}, wanted {check.status!r}")
    if check.max_turns is not None and line.turns > check.max_turns:
        failures.append(f"took {line.turns} turns, wanted at most {check.max_turns}")
    wanted = " and ".join(
        part
        for part in (
            f"status={check.status}" if check.status is not None else "",
            f"max_turns={check.max_turns}" if check.max_turns is not None else "",
        )
        if part
    )
    return CheckResult(
        label=f"result: {wanted}",
        passed=not failures,
        detail="; ".join(failures),
    )


def grade_bash_check(check: Check, exit_code: int, output: str) -> CheckResult:
    return CheckResult(
        label=f"bash: {check.cmd}",
        passed=exit_code == 0,
        detail="" if exit_code == 0 else f"exit {exit_code}\n{output}".strip(),
    )


def rep_outcome(
    checks: Sequence[CheckResult],
    judge: Judge | None,
    met: list[bool] | None,
) -> Outcome:
    """Decide one repetition.

    `error` means the repetition could not be judged at all, and is counted in
    neither the numerator nor the denominator. It is returned here only when a
    gating judge produced no answer; every other `error` -- a dead container, a
    missing result line -- is decided by the caller before grading starts.
    """
    if judge is not None and judge.min_met is not None and met is None:
        return "error"
    if not all(check.passed for check in checks):
        return "fail"
    if judge is not None and judge.min_met is not None:
        assert met is not None  # narrowed by the guard above
        if sum(met) < judge.min_met:
            return "fail"
    return "pass"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest benchmarks/tests/test_grade.py -v`
Expected: PASS

- [ ] **Step 5: Verify types**

Run: `uv run mypy benchmarks`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add benchmarks/runner/grade.py benchmarks/tests/test_grade.py
git commit -m "feat: grade checks and decide a repetition's outcome"
```

---

### Task 5: Aggregating repetitions into a case summary

Many noisy repetitions become one number per case. Medians, not means, and an
`error` that never quietly becomes a pass.

**Files:**
- Create: `benchmarks/runner/report.py`
- Create: `benchmarks/tests/test_report.py`

**Interfaces:**
- Consumes: `Outcome`, `CheckResult` from `benchmarks.runner.grade`.
- Produces: `RepRecord`, `CaseSummary`, `median_int(values) -> int | None`,
  and `summarize(records: Sequence[RepRecord]) -> list[CaseSummary]`.
  `RepRecord` fields: `case: str`, `rep: int`, `outcome: Outcome`,
  `checks: tuple[CheckResult, ...]`, `judge_met: int | None`,
  `judge_why: str`, `status: str | None`, `stop_reason: str | None`,
  `turns: int | None`, `usage: dict[str, int]`, `duration_s: float`,
  `model: str`. `CaseSummary` fields: `case: str`, `passes: int`,
  `fails: int`, `errors: int`, `pass_rate: float | None`,
  `inconclusive: bool`, `tokens_median: int | None`,
  `turns_median: int | None`, `judge_met_median: int | None`.

- [ ] **Step 1: Write the failing test**

Create `benchmarks/tests/test_report.py`:

```python
from __future__ import annotations

from benchmarks.runner.grade import Outcome
from benchmarks.runner.report import RepRecord, median_int, summarize


def record(
    case: str = "c",
    rep: int = 0,
    outcome: Outcome = "pass",
    tokens: int = 1000,
    turns: int = 4,
    judge_met: int | None = 2,
) -> RepRecord:
    return RepRecord(
        case=case,
        rep=rep,
        outcome=outcome,
        checks=(),
        judge_met=judge_met,
        judge_why="",
        status="done",
        stop_reason="end_turn",
        turns=turns,
        usage={"input": tokens, "output": 0, "cache_read": 0, "cache_write": 0},
        duration_s=1.0,
        model="claude-haiku",
    )


def test_median_of_an_empty_sequence_is_none() -> None:
    assert median_int([]) is None


def test_median_rounds_to_an_int() -> None:
    assert median_int([1, 2]) == 2
    assert median_int([10, 20, 31]) == 20


def test_pass_rate_counts_passes_over_passes_plus_fails() -> None:
    records = [record(outcome="pass"), record(outcome="pass"), record(outcome="fail")]
    summary = summarize(records)[0]
    assert (summary.passes, summary.fails, summary.errors) == (2, 1, 0)
    assert summary.pass_rate == 2 / 3


def test_errors_are_excluded_from_the_denominator() -> None:
    """An infrastructure failure must never read as a task failure."""
    records = [record(outcome="pass"), record(outcome="error")]
    summary = summarize(records)[0]
    assert summary.pass_rate == 1.0
    assert summary.errors == 1
    assert not summary.inconclusive


def test_more_than_half_errors_is_inconclusive() -> None:
    records = [record(outcome="pass"), record(outcome="error"), record(outcome="error")]
    summary = summarize(records)[0]
    assert summary.inconclusive
    assert summary.pass_rate is None


def test_all_errors_is_inconclusive_not_a_zero_pass_rate() -> None:
    summary = summarize([record(outcome="error")])[0]
    assert summary.inconclusive
    assert summary.pass_rate is None


def test_medians_ignore_errored_reps() -> None:
    records = [
        record(outcome="pass", tokens=1000, turns=4),
        record(outcome="fail", tokens=2000, turns=6),
        record(outcome="error", tokens=999_999, turns=999),
    ]
    summary = summarize(records)[0]
    assert summary.tokens_median == 1500
    assert summary.turns_median == 5


def test_one_runaway_rep_cannot_swing_the_median() -> None:
    records = [
        record(tokens=1000),
        record(tokens=1100),
        record(tokens=90_000),
    ]
    assert summarize(records)[0].tokens_median == 1100


def test_tokens_median_sums_input_and_output() -> None:
    rec = record(tokens=1000)
    rec = RepRecord(**{**rec.__dict__, "usage": {**rec.usage, "output": 500}})
    assert summarize([rec])[0].tokens_median == 1500


def test_judge_median_is_none_when_no_rep_was_judged() -> None:
    assert summarize([record(judge_met=None)])[0].judge_met_median is None


def test_summaries_are_sorted_by_case() -> None:
    records = [record(case="zeta"), record(case="alpha")]
    assert [s.case for s in summarize(records)] == ["alpha", "zeta"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest benchmarks/tests/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmarks.runner.report'`

- [ ] **Step 3: Write the implementation**

Create `benchmarks/runner/report.py`:

```python
"""Aggregation, baselines, and comparison. Pure: arithmetic and policy only."""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from benchmarks.runner.grade import CheckResult, Outcome


@dataclass(frozen=True)
class RepRecord:
    case: str
    rep: int
    outcome: Outcome
    checks: tuple[CheckResult, ...]
    judge_met: int | None
    judge_why: str
    status: str | None
    stop_reason: str | None
    turns: int | None
    usage: dict[str, int] = field(default_factory=dict)
    duration_s: float = 0.0
    model: str = ""


@dataclass(frozen=True)
class CaseSummary:
    case: str
    passes: int
    fails: int
    errors: int
    pass_rate: float | None
    inconclusive: bool
    tokens_median: int | None
    turns_median: int | None
    judge_met_median: int | None


def median_int(values: Sequence[int]) -> int | None:
    if not values:
        return None
    return int(round(statistics.median(values)))


def summarize(records: Sequence[RepRecord]) -> list[CaseSummary]:
    """Collapse repetitions into one summary per case.

    Errors are excluded from the pass rate rather than counted against it: a
    dead container says nothing about whether the task is achievable. When
    errors take the majority the case is inconclusive and has no pass rate at
    all, so it can never be silently scored.
    """
    grouped: dict[str, list[RepRecord]] = defaultdict(list)
    for rec in records:
        grouped[rec.case].append(rec)

    summaries: list[CaseSummary] = []
    for case in sorted(grouped):
        reps = grouped[case]
        passes = sum(1 for r in reps if r.outcome == "pass")
        fails = sum(1 for r in reps if r.outcome == "fail")
        errors = sum(1 for r in reps if r.outcome == "error")
        judged = [r for r in reps if r.outcome != "error"]
        inconclusive = errors > len(reps) / 2
        summaries.append(
            CaseSummary(
                case=case,
                passes=passes,
                fails=fails,
                errors=errors,
                pass_rate=(
                    None
                    if inconclusive or not (passes + fails)
                    else passes / (passes + fails)
                ),
                inconclusive=inconclusive,
                tokens_median=median_int(
                    [r.usage.get("input", 0) + r.usage.get("output", 0) for r in judged]
                ),
                turns_median=median_int(
                    [r.turns for r in judged if r.turns is not None]
                ),
                judge_met_median=median_int(
                    [r.judge_met for r in judged if r.judge_met is not None]
                ),
            )
        )
    return summaries
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest benchmarks/tests/test_report.py -v`
Expected: PASS, 11 tests

- [ ] **Step 5: Commit**

```bash
git add benchmarks/runner/report.py benchmarks/tests/test_report.py
git commit -m "feat: aggregate repetitions into per-case summaries"
```

---

### Task 6: Baseline reading, writing, and naming

Baselines are keyed by model so contributors' numbers coexist. TOML is written
by hand - the standard library reads TOML but does not write it, and this
shape is too small to justify a dependency.

**Files:**
- Modify: `benchmarks/runner/report.py`
- Modify: `benchmarks/tests/test_report.py`

**Interfaces:**
- Consumes: `CaseSummary` from Task 5.
- Produces: `BaselineMeta` (frozen: `blessed: str`, `commit: str`,
  `provider: str`, `model: str`, `judge_model: str`), `Baseline` (frozen:
  `meta: BaselineMeta`, `cases: dict[str, CaseSummary]`), `slugify(model) -> str`,
  `baseline_path(root: Path, model: str, tier: str) -> Path`,
  `load_baseline(path: Path) -> Baseline`, and
  `dumps_baseline(meta: BaselineMeta, summaries: Sequence[CaseSummary]) -> str`.

- [ ] **Step 1: Write the failing test**

Append to `benchmarks/tests/test_report.py`:

```python
from pathlib import Path

import pytest

from benchmarks.runner.report import (
    BaselineMeta,
    baseline_path,
    dumps_baseline,
    load_baseline,
    slugify,
)

META = BaselineMeta(
    blessed="2026-09-22T14:02:00Z",
    commit="237a678",
    provider="anthropic",
    model="claude-haiku",
    judge_model="claude-haiku",
)


def test_slugify_makes_a_model_name_filesystem_safe() -> None:
    assert slugify("claude-haiku") == "claude-haiku"
    assert slugify("ada/qwen3-14b") == "ada-qwen3-14b"
    assert slugify("spark/glm-5.3-flash") == "spark-glm-5-3-flash"


def test_baseline_path_keys_by_model_and_tier(tmp_path: Path) -> None:
    assert baseline_path(tmp_path, "ada/qwen3-14b", "smoke") == (
        tmp_path / "ada-qwen3-14b.smoke.toml"
    )


def test_a_blessed_baseline_round_trips(tmp_path: Path) -> None:
    summaries = summarize([record(case="c", tokens=1000, turns=4, judge_met=3)])
    path = tmp_path / "claude-haiku.smoke.toml"
    path.write_text(dumps_baseline(META, summaries))

    baseline = load_baseline(path)
    assert baseline.meta == META
    assert baseline.cases["c"].pass_rate == 1.0
    assert baseline.cases["c"].tokens_median == 1000
    assert baseline.cases["c"].judge_met_median == 3


def test_inconclusive_cases_are_not_blessed(tmp_path: Path) -> None:
    """A baseline is a claim about what should happen. 'We don't know' isn't one."""
    summaries = summarize(
        [record(case="c", outcome="error"), record(case="c", outcome="error")]
    )
    path = tmp_path / "b.toml"
    path.write_text(dumps_baseline(META, summaries))
    assert load_baseline(path).cases == {}


def test_loading_a_missing_baseline_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_baseline(tmp_path / "absent.toml")


def test_a_model_name_with_quotes_survives_the_round_trip(tmp_path: Path) -> None:
    meta = BaselineMeta(
        blessed=META.blessed,
        commit=META.commit,
        provider="anthropic",
        model='weird"name',
        judge_model="claude-haiku",
    )
    path = tmp_path / "b.toml"
    path.write_text(dumps_baseline(meta, summarize([record(case="c")])))
    assert load_baseline(path).meta.model == 'weird"name'
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest benchmarks/tests/test_report.py -v`
Expected: FAIL with `ImportError: cannot import name 'BaselineMeta'`

- [ ] **Step 3: Write the implementation**

Append to `benchmarks/runner/report.py` (adding `import json`, `import re`,
`import tomllib`, and `from pathlib import Path` to the imports):

```python
@dataclass(frozen=True)
class BaselineMeta:
    blessed: str
    commit: str
    provider: str
    model: str
    judge_model: str


@dataclass(frozen=True)
class Baseline:
    meta: BaselineMeta
    cases: dict[str, CaseSummary]


def slugify(model: str) -> str:
    """Model names reach the filesystem, and they contain slashes and dots."""
    return re.sub(r"[^a-zA-Z0-9]+", "-", model).strip("-").lower()


def baseline_path(root: Path, model: str, tier: str) -> Path:
    return root / f"{slugify(model)}.{tier}.toml"


def _toml_str(value: str) -> str:
    # TOML basic strings and JSON strings share an escape syntax for every
    # character these values can contain, so json.dumps is a correct encoder
    # and a dependency-free one.
    return json.dumps(value)


def dumps_baseline(meta: BaselineMeta, summaries: Sequence[CaseSummary]) -> str:
    """Render a baseline file.

    Inconclusive cases are omitted: a baseline states what should happen, and
    "we could not tell" is not such a statement.
    """
    lines = [
        "# Blessed benchmark numbers. Reviewed like any other committed file.",
        "",
        "[meta]",
        f"blessed     = {_toml_str(meta.blessed)}",
        f"commit      = {_toml_str(meta.commit)}",
        f"provider    = {_toml_str(meta.provider)}",
        f"model       = {_toml_str(meta.model)}",
        f"judge_model = {_toml_str(meta.judge_model)}",
    ]
    for summary in summaries:
        if summary.inconclusive or summary.pass_rate is None:
            continue
        lines += [
            "",
            f"[case.{summary.case}]",
            f"pass_rate        = {summary.pass_rate}",
            f"reps             = {summary.passes + summary.fails}",
        ]
        for key, value in (
            ("tokens_median", summary.tokens_median),
            ("turns_median", summary.turns_median),
            ("judge_met_median", summary.judge_met_median),
        ):
            if value is not None:
                lines.append(f"{key:<16} = {value}")
    return "\n".join(lines) + "\n"


def load_baseline(path: Path) -> Baseline:
    raw: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    meta_raw = raw.get("meta", {})
    meta = BaselineMeta(
        blessed=str(meta_raw.get("blessed", "")),
        commit=str(meta_raw.get("commit", "")),
        provider=str(meta_raw.get("provider", "")),
        model=str(meta_raw.get("model", "")),
        judge_model=str(meta_raw.get("judge_model", "")),
    )
    cases: dict[str, CaseSummary] = {}
    for name, body in raw.get("case", {}).items():
        reps = int(body.get("reps", 0))
        rate = float(body["pass_rate"])
        passes = int(round(rate * reps))
        cases[name] = CaseSummary(
            case=name,
            passes=passes,
            fails=reps - passes,
            errors=0,
            pass_rate=rate,
            inconclusive=False,
            tokens_median=body.get("tokens_median"),
            turns_median=body.get("turns_median"),
            judge_met_median=body.get("judge_met_median"),
        )
    return Baseline(meta=meta, cases=cases)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest benchmarks/tests/test_report.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add benchmarks/runner/report.py benchmarks/tests/test_report.py
git commit -m "feat: read, write, and name baselines per model and tier"
```

---

### Task 7: Comparison and the rendered table

The regression policy, in one pure function. Everything the run finally prints
and every exit code it returns is decided here and nowhere else.

**Files:**
- Modify: `benchmarks/runner/report.py`
- Modify: `benchmarks/tests/test_report.py`

**Interfaces:**
- Consumes: `CaseSummary`, `Baseline` from Tasks 5 and 6.
- Produces: `Verdict = Literal["ok", "improved", "suspect", "regression", "inconclusive", "new", "missing"]`,
  `CaseDelta` (frozen: `case: str`, `verdict: Verdict`, `summary: CaseSummary | None`,
  `baseline: CaseSummary | None`, `token_delta: float | None`, `notes: tuple[str, ...]`),
  `Comparison` (frozen: `deltas: tuple[CaseDelta, ...]`, `exit_code: int`),
  `compare(summaries, baseline, *, confirmed: bool = False, token_band: float = 0.10, strict_tokens: bool = False) -> Comparison`,
  and `render(comparison: Comparison) -> str`.

- [ ] **Step 1: Write the failing test**

Append to `benchmarks/tests/test_report.py`:

```python
from benchmarks.runner.report import Baseline, CaseSummary, compare, render


def summary(
    case: str = "c",
    pass_rate: float | None = 1.0,
    tokens: int | None = 1000,
    judge: int | None = 3,
    inconclusive: bool = False,
) -> CaseSummary:
    return CaseSummary(
        case=case,
        passes=3,
        fails=0,
        errors=0,
        pass_rate=pass_rate,
        inconclusive=inconclusive,
        tokens_median=tokens,
        turns_median=4,
        judge_met_median=judge,
    )


def baseline_of(*summaries: CaseSummary) -> Baseline:
    return Baseline(meta=META, cases={s.case: s for s in summaries})


def test_matching_numbers_are_ok_and_exit_zero() -> None:
    comparison = compare([summary()], baseline_of(summary()))
    assert comparison.deltas[0].verdict == "ok"
    assert comparison.exit_code == 0


def test_a_pass_rate_drop_is_suspect_before_confirmation() -> None:
    comparison = compare([summary(pass_rate=0.67)], baseline_of(summary()))
    assert comparison.deltas[0].verdict == "suspect"
    assert comparison.exit_code == 0


def test_a_confirmed_pass_rate_drop_is_a_regression() -> None:
    comparison = compare(
        [summary(pass_rate=0.67)], baseline_of(summary()), confirmed=True
    )
    assert comparison.deltas[0].verdict == "regression"
    assert comparison.exit_code == 1


def test_a_higher_pass_rate_is_an_improvement_not_a_failure() -> None:
    comparison = compare([summary(pass_rate=1.0)], baseline_of(summary(pass_rate=0.5)))
    assert comparison.deltas[0].verdict == "improved"
    assert comparison.exit_code == 0


def test_tokens_inside_the_band_do_not_register() -> None:
    comparison = compare([summary(tokens=1030)], baseline_of(summary(tokens=1000)))
    assert comparison.deltas[0].verdict == "ok"
    assert comparison.exit_code == 0


def test_tokens_above_the_band_warn_but_exit_zero() -> None:
    comparison = compare([summary(tokens=1150)], baseline_of(summary(tokens=1000)))
    assert comparison.exit_code == 0
    assert any("token" in note for note in comparison.deltas[0].notes)


def test_tokens_above_the_band_fail_under_strict() -> None:
    comparison = compare(
        [summary(tokens=1150)], baseline_of(summary(tokens=1000)), strict_tokens=True
    )
    assert comparison.exit_code == 1


def test_tokens_below_the_band_are_never_a_failure() -> None:
    comparison = compare(
        [summary(tokens=500)], baseline_of(summary(tokens=1000)), strict_tokens=True
    )
    assert comparison.exit_code == 0


def test_a_judge_drop_warns() -> None:
    comparison = compare([summary(judge=1)], baseline_of(summary(judge=3)))
    assert comparison.exit_code == 0
    assert any("judge" in note for note in comparison.deltas[0].notes)


def test_an_inconclusive_case_exits_one() -> None:
    comparison = compare(
        [summary(pass_rate=None, inconclusive=True)], baseline_of(summary())
    )
    assert comparison.deltas[0].verdict == "inconclusive"
    assert comparison.exit_code == 1


def test_a_case_absent_from_the_baseline_is_new_and_never_fails() -> None:
    comparison = compare([summary(case="fresh")], baseline_of(summary(case="old")))
    verdicts = {d.case: d.verdict for d in comparison.deltas}
    assert verdicts["fresh"] == "new"
    assert comparison.exit_code == 0


def test_a_case_absent_from_the_run_is_missing_and_never_fails() -> None:
    comparison = compare([], baseline_of(summary(case="old")))
    assert comparison.deltas[0].verdict == "missing"
    assert comparison.exit_code == 0


def test_render_names_the_regression() -> None:
    comparison = compare(
        [summary(pass_rate=0.67)], baseline_of(summary()), confirmed=True
    )
    text = render(comparison)
    assert "REGRESSION" in text
    assert "c" in text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest benchmarks/tests/test_report.py -v`
Expected: FAIL with `ImportError: cannot import name 'compare'`

- [ ] **Step 3: Write the implementation**

Append to `benchmarks/runner/report.py` (adding `Literal` to the `typing`
import):

```python
Verdict = Literal[
    "ok", "improved", "suspect", "regression", "inconclusive", "new", "missing"
]


@dataclass(frozen=True)
class CaseDelta:
    case: str
    verdict: Verdict
    summary: CaseSummary | None
    baseline: CaseSummary | None
    token_delta: float | None
    notes: tuple[str, ...]


@dataclass(frozen=True)
class Comparison:
    deltas: tuple[CaseDelta, ...]
    exit_code: int


def compare(
    summaries: Sequence[CaseSummary],
    baseline: Baseline,
    *,
    confirmed: bool = False,
    token_band: float = 0.10,
    strict_tokens: bool = False,
) -> Comparison:
    """Diff a run against a baseline.

    `confirmed` is the difference between "this looks wrong" and "this is
    wrong". A pass-rate drop is `suspect` on the first pass and becomes a
    `regression` only after the caller has re-run the case at higher reps, so
    ordinary sampling noise never turns the build red on its own.
    """
    by_case = {s.case: s for s in summaries}
    deltas: list[CaseDelta] = []
    failed = False

    for name in sorted(set(by_case) | set(baseline.cases)):
        current = by_case.get(name)
        before = baseline.cases.get(name)
        notes: list[str] = []
        token_delta: float | None = None

        if current is None:
            deltas.append(CaseDelta(name, "missing", None, before, None, ()))
            continue
        if current.inconclusive:
            failed = True
            deltas.append(
                CaseDelta(
                    name,
                    "inconclusive",
                    current,
                    before,
                    None,
                    (f"{current.errors} of {current.passes + current.fails + current.errors} reps errored",),
                )
            )
            continue
        if before is None:
            deltas.append(CaseDelta(name, "new", current, None, None, ()))
            continue

        verdict: Verdict = "ok"
        if (
            current.pass_rate is not None
            and before.pass_rate is not None
        ):
            if current.pass_rate < before.pass_rate:
                verdict = "regression" if confirmed else "suspect"
                if confirmed:
                    failed = True
            elif current.pass_rate > before.pass_rate:
                verdict = "improved"

        if current.tokens_median and before.tokens_median:
            token_delta = (
                current.tokens_median - before.tokens_median
            ) / before.tokens_median
            if token_delta > token_band:
                notes.append(f"tokens +{token_delta:.0%}, outside the band")
                if strict_tokens:
                    failed = True

        if (
            current.judge_met_median is not None
            and before.judge_met_median is not None
            and current.judge_met_median < before.judge_met_median
        ):
            notes.append(
                f"judge {before.judge_met_median} -> {current.judge_met_median}"
            )

        deltas.append(
            CaseDelta(name, verdict, current, before, token_delta, tuple(notes))
        )

    return Comparison(deltas=tuple(deltas), exit_code=1 if failed else 0)


def _rate(summary: CaseSummary | None) -> str:
    if summary is None or summary.pass_rate is None:
        return "-"
    return f"{summary.passes}/{summary.passes + summary.fails}"


def render(comparison: Comparison) -> str:
    lines = [f"{'case':<24} {'pass':>7} {'tokens':>10} {'judge':>6}  verdict", "-" * 70]
    for delta in comparison.deltas:
        tokens = "-"
        if delta.summary and delta.summary.tokens_median is not None:
            tokens = f"{delta.summary.tokens_median:,}"
            if delta.token_delta is not None:
                tokens += f" {delta.token_delta:+.0%}"
        judge = "-"
        if delta.summary and delta.summary.judge_met_median is not None:
            judge = str(delta.summary.judge_met_median)
        lines.append(
            f"{delta.case:<24} {_rate(delta.summary):>7} {tokens:>10} "
            f"{judge:>6}  {delta.verdict}"
        )
        for note in delta.notes:
            lines.append(f"{'':<24} {note}")
    lines.append("-" * 70)

    bad = [d for d in comparison.deltas if d.verdict in ("regression", "inconclusive")]
    for delta in bad:
        lines.append(
            f"{delta.verdict.upper()}: {delta.case} "
            f"{_rate(delta.baseline)} -> {_rate(delta.summary)}"
        )
    if not bad:
        lines.append("no regressions")
    return "\n".join(lines)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest benchmarks/tests/test_report.py -v`
Expected: PASS

- [ ] **Step 5: Verify types and lint**

Run: `uv run ruff format benchmarks && uv run ruff check benchmarks && uv run mypy benchmarks`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add benchmarks/runner/report.py benchmarks/tests/test_report.py
git commit -m "feat: compare a run against its baseline and render the table"
```

---

### Task 8: The assertion-scoring judge

Scores a checklist of binary claims through nare's own transport. No second
API client, no new dependency, and portable to every provider nare reaches.

**Files:**
- Create: `benchmarks/runner/judge.py`
- Create: `benchmarks/tests/test_judge.py`

**Interfaces:**
- Consumes: `Case`, `Judge` from `benchmarks.runner.case`; `Transport`,
  `Reply`, `make_transport` from `nare.transport`; `Message`, `Usage` from
  `nare.session`.
- Produces: `JudgeError(Exception)`, `build_prompt(case, diff, final_text) -> str`,
  `parse_reply(text: str, expected: int) -> list[bool]`,
  `async score(case, diff, final_text, *, transport) -> tuple[list[bool], str]`,
  and `judge_transport(config) -> Transport`.

- [ ] **Step 1: Write the failing test**

Create `benchmarks/tests/test_judge.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from benchmarks.runner.case import Case, Check, Judge
from benchmarks.runner.judge import JudgeError, build_prompt, parse_reply, score
from nare.session import Message, Usage
from nare.transport import Reply

CASE = Case(
    id="fix-failing-test",
    tier="smoke",
    prompt="Fix bar.py so the suite passes.",
    directory=Path("/nowhere"),
    max_turns=12,
    reps=3,
    timeout=600,
    checks=(Check(kind="bash", cmd="pytest -q"),),
    judge=Judge(assertions=("The fix is in bar.py.", "The test was not weakened."),
                min_met=2),
)


class FakeJudge:
    """A scripted transport, in the FakeProvider idiom the repo already uses."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[list[Message]] = []

    async def turn(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> Reply:
        self.calls.append(messages)
        assert tools == [], "the judge asks for no tools"
        return Reply(
            content=[{"type": "text", "text": self.text}],
            tool_calls=[],
            usage=Usage(input=10, output=5),
            stop_reason="end_turn",
        )


def test_prompt_carries_the_task_the_diff_and_the_assertions() -> None:
    prompt = build_prompt(CASE, "diff --git a/bar.py", "I fixed it.")
    assert "Fix bar.py so the suite passes." in prompt
    assert "diff --git a/bar.py" in prompt
    assert "The fix is in bar.py." in prompt
    assert "The test was not weakened." in prompt
    assert "I fixed it." in prompt


def test_parses_a_clean_json_reply() -> None:
    assert parse_reply('{"met": [true, false], "why": "second failed"}', 2) == [
        True,
        False,
    ]


def test_parses_json_wrapped_in_prose() -> None:
    """Small models pad their answers. That is not a judge failure."""
    reply = 'Here is my assessment:\n{"met": [true, true], "why": "ok"}\nDone.'
    assert parse_reply(reply, 2) == [True, True]


def test_parses_json_in_a_fenced_block() -> None:
    reply = '```json\n{"met": [false, true], "why": "x"}\n```'
    assert parse_reply(reply, 2) == [False, True]


def test_a_wrong_length_array_is_a_judge_failure() -> None:
    with pytest.raises(JudgeError, match="2"):
        parse_reply('{"met": [true], "why": "x"}', 2)


def test_unparseable_output_is_a_judge_failure() -> None:
    with pytest.raises(JudgeError):
        parse_reply("I think it looks fine, honestly.", 2)


def test_non_boolean_entries_are_a_judge_failure() -> None:
    with pytest.raises(JudgeError):
        parse_reply('{"met": ["yes", "no"], "why": "x"}', 2)


async def test_score_returns_the_booleans_and_the_reason() -> None:
    transport = FakeJudge('{"met": [true, false], "why": "the test was edited"}')
    met, why = await score(CASE, "a diff", "final text", transport=transport)
    assert met == [True, False]
    assert why == "the test was edited"
    assert len(transport.calls) == 1


async def test_score_raises_on_an_unusable_reply() -> None:
    with pytest.raises(JudgeError):
        await score(CASE, "d", "f", transport=FakeJudge("no json here"))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest benchmarks/tests/test_judge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmarks.runner.judge'`

- [ ] **Step 3: Write the implementation**

Create `benchmarks/runner/judge.py`:

```python
"""Scoring a case's assertions.

The judge answers a checklist of binary, objectively checkable claims about
the diff, not a holistic rating. Every model available to score is a small
one, and a small model is far better at "is this true, yes or no" than at
rating quality on a scale -- which also makes the score stable run to run.

It runs through nare's own transport, so the benchmark adds no second client,
no second credential path, and reaches every provider nare reaches.
"""

from __future__ import annotations

import json
import re
from typing import Any

from benchmarks.runner.case import Case
from benchmarks.runner.config import Config
from nare.session import Message
from nare.transport import Transport, make_transport

JUDGE_SYSTEM = """\
You check whether specific claims about a code change are true.

You are given a task, the diff that an agent produced for it, and a numbered
list of claims. For each claim, decide whether it is true of the diff.

Reply with JSON only, in exactly this shape:
{"met": [true, false, ...], "why": "one short sentence"}

The "met" array must have exactly one boolean per claim, in the same order.
Judge only what the diff shows. Do not reward effort or intent.\
"""

# Judging is a short answer; this bounds a runaway reply rather than the task.
JUDGE_MAX_TOKENS = 1024


class JudgeError(Exception):
    """The judge could not answer. Never a zero -- see grade.rep_outcome."""


def judge_transport(config: Config) -> Transport:
    return make_transport(
        config.provider,
        model=config.judge_model,
        base_url=config.base_url,
        api_key=config.api_key,
        max_tokens=JUDGE_MAX_TOKENS,
        system=JUDGE_SYSTEM,
    )


def build_prompt(case: Case, diff: str, final_text: str) -> str:
    assert case.judge is not None, "build_prompt is only called for judged cases"
    claims = "\n".join(
        f"{i}. {text}" for i, text in enumerate(case.judge.assertions, start=1)
    )
    return (
        f"## Task given to the agent\n\n{case.prompt}\n\n"
        f"## Diff the agent produced\n\n```diff\n{diff}\n```\n\n"
        f"## The agent's closing message\n\n{final_text}\n\n"
        f"## Claims to check\n\n{claims}\n"
    )


def _extract_json(text: str) -> dict[str, Any]:
    """Small models pad their answers with prose and fences. Dig the object out."""
    candidates = [text]
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    candidates = fenced + candidates
    braced = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if braced:
        candidates.append(braced.group(0))
    for candidate in candidates:
        try:
            payload = json.loads(candidate.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise JudgeError(f"no JSON object in the judge's reply: {text[:200]!r}")


def parse_reply(text: str, expected: int) -> list[bool]:
    payload = _extract_json(text)
    met = payload.get("met")
    if not isinstance(met, list) or len(met) != expected:
        raise JudgeError(
            f"judge returned {len(met) if isinstance(met, list) else 'no'} "
            f"verdicts, expected {expected}"
        )
    if not all(isinstance(value, bool) for value in met):
        raise JudgeError(f"judge returned non-boolean verdicts: {met!r}")
    return [bool(value) for value in met]


async def score(
    case: Case, diff: str, final_text: str, *, transport: Transport
) -> tuple[list[bool], str]:
    assert case.judge is not None, "score is only called for judged cases"
    reply = await transport.turn(
        [Message(role="user", content=[{"type": "text", "text": build_prompt(case, diff, final_text)}])],
        [],
    )
    text = "\n".join(
        block.get("text", "") for block in reply.content if block.get("type") == "text"
    )
    payload = _extract_json(text)
    met = parse_reply(text, len(case.judge.assertions))
    return met, str(payload.get("why", ""))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest benchmarks/tests/test_judge.py -v`
Expected: PASS, 9 tests

- [ ] **Step 5: Commit**

```bash
git add benchmarks/runner/judge.py benchmarks/tests/test_judge.py
git commit -m "feat: score case assertions through nare's transport"
```

---

### Task 9: The container image and preflight

The image installs the working tree's nare, so the suite measures local
changes. Preflight fails before anything is spent.

**Files:**
- Create: `benchmarks/Dockerfile`
- Create: `benchmarks/Dockerfile.dockerignore`
- Create: `benchmarks/runner/sandbox.py`
- Create: `benchmarks/tests/test_sandbox.py`

**Interfaces:**
- Consumes: `Config` from `benchmarks.runner.config`.
- Produces: `IMAGE = "nare-bench:local"`, `SandboxError(Exception)`,
  `preflight(config: Config, *, which=shutil.which) -> None`,
  `build_image(repo_root: Path) -> None`, and `repo_root() -> Path`.

- [ ] **Step 1: Write the Dockerfile**

Create `benchmarks/Dockerfile`:

```dockerfile
# The benchmark sandbox. Installs the WORKING TREE's nare, not a release, so
# the suite measures the change under test.
FROM python:3.12-slim

# git for the fixture checkpoint the judge diffs against; ripgrep because the
# tool set has no grep tool and cases are expected to reach for `rg`.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ripgrep \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pytest

WORKDIR /src
# pyproject references all three, so a build without them fails.
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN pip install --no-cache-dir .

# The container runs as the invoking user so that files written into the bind
# mount are owned by them and `git diff` on the host does not trip over
# dubious ownership. That user has no home directory, and git and pip both
# need a writable one.
ENV HOME=/tmp
WORKDIR /work
```

- [ ] **Step 2: Write the ignore file**

Create `benchmarks/Dockerfile.dockerignore`:

```
.git
.venv
.mypy_cache
.pytest_cache
.ruff_cache
dist
benchmarks/results
**/__pycache__
.env
```

Note: the build context is the repository root, so this must be the
per-Dockerfile ignore file. A `benchmarks/.dockerignore` would never be read , 
Docker looks for `.dockerignore` at the context root.

- [ ] **Step 3: Write the failing test**

Create `benchmarks/tests/test_sandbox.py`:

```python
from __future__ import annotations

import pytest

from benchmarks.runner.config import Config
from benchmarks.runner.sandbox import IMAGE, SandboxError, preflight, repo_root

CONFIG = Config(
    model="claude-haiku",
    provider="anthropic",
    base_url=None,
    judge_model="claude-haiku",
    api_key="k",
)


def test_preflight_passes_when_docker_is_present() -> None:
    preflight(CONFIG, which=lambda name: "/usr/bin/docker")


def test_preflight_refuses_without_docker() -> None:
    with pytest.raises(SandboxError, match="docker"):
        preflight(CONFIG, which=lambda name: None)


def test_repo_root_holds_the_project_files() -> None:
    root = repo_root()
    assert (root / "pyproject.toml").is_file()
    assert (root / "benchmarks" / "Dockerfile").is_file()


def test_the_image_tag_is_local() -> None:
    assert IMAGE == "nare-bench:local"


@pytest.mark.docker
def test_the_image_builds_and_carries_nare() -> None:
    import subprocess

    from benchmarks.runner.sandbox import build_image

    build_image(repo_root())
    proc = subprocess.run(
        ["docker", "run", "--rm", IMAGE, "nare", "run", "--help"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0
    assert "--jsonl" in proc.stdout
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `uv run pytest benchmarks/tests/test_sandbox.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmarks.runner.sandbox'`

- [ ] **Step 5: Write the implementation**

Create `benchmarks/runner/sandbox.py`:

```python
"""Docker. The only module that runs the agent, and the only one that can
damage something if it is wrong.

Containment covers the filesystem and the process tree, not the network: the
container must reach the model endpoint, so `--network none` is not available.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from benchmarks.runner.config import Config

IMAGE = "nare-bench:local"


class SandboxError(Exception):
    """The sandbox could not be prepared or run. Always exit 2."""


def repo_root() -> Path:
    """This file is benchmarks/runner/sandbox.py, so the root is two up."""
    return Path(__file__).resolve().parent.parent.parent


def preflight(
    config: Config, *, which: Callable[[str], str | None] = shutil.which
) -> None:
    """Fail before anything is spent. Config already proved the key and model."""
    if which("docker") is None:
        raise SandboxError(
            "docker is not on PATH; the benchmark runs every repetition in a "
            "container"
        )


def build_image(root: Path) -> None:
    proc = subprocess.run(
        [
            "docker", "build",
            "-t", IMAGE,
            "-f", str(root / "benchmarks" / "Dockerfile"),
            str(root),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SandboxError(f"docker build failed:\n{proc.stderr.strip()}")
```

- [ ] **Step 6: Run the unit tests**

Run: `uv run pytest benchmarks/tests/test_sandbox.py -v`
Expected: PASS, 4 tests (the `docker` test is deselected)

- [ ] **Step 7: Run the Docker test explicitly**

Run: `uv run pytest benchmarks/tests/test_sandbox.py -v -m docker`
Expected: PASS. The first build takes a few minutes; later builds hit the
layer cache.

- [ ] **Step 8: Commit**

```bash
git add benchmarks/Dockerfile benchmarks/Dockerfile.dockerignore \
        benchmarks/runner/sandbox.py benchmarks/tests/test_sandbox.py
git commit -m "feat: add the benchmark container image and preflight"
```

---

### Task 10: Running one repetition in the sandbox

Copy the fixture, checkpoint it in git, run the agent under a timeout, and
hand back everything grading needs.

**Files:**
- Modify: `benchmarks/runner/sandbox.py`
- Modify: `benchmarks/tests/test_sandbox.py`

**Interfaces:**
- Consumes: `Case` from `benchmarks.runner.case`; `Config`, `IMAGE`.
- Produces: `RunArtifacts` (frozen: `stdout: str`, `stderr: str`,
  `exit_code: int`, `timed_out: bool`, `duration_s: float`),
  `sandbox(case) -> AbstractContextManager[tuple[Path, Path]]` yielding
  `(workdir, artifacts_dir)`, `run_agent(case, workdir, artifacts, config) -> RunArtifacts`,
  `run_check(workdir, cmd, timeout) -> tuple[int, str]`, and
  `git_diff(workdir) -> str`.

- [ ] **Step 1: Write the failing test**

Append to `benchmarks/tests/test_sandbox.py`:

```python
from pathlib import Path

from benchmarks.runner.case import Case, Check, Judge


def make_case(tmp_path: Path, prompt: str = "Create done.txt containing ok.") -> Case:
    directory = tmp_path / "demo"
    (directory / "fixture").mkdir(parents=True)
    (directory / "fixture" / "bar.py").write_text("def foo():\n    return 1\n")
    return Case(
        id="demo",
        tier="smoke",
        prompt=prompt,
        directory=directory,
        max_turns=4,
        reps=1,
        timeout=120,
        checks=(Check(kind="bash", cmd="test -f done.txt"),),
        judge=None,
    )


def test_sandbox_copies_the_fixture_and_cleans_up(tmp_path: Path) -> None:
    from benchmarks.runner.sandbox import sandbox

    case = make_case(tmp_path)
    with sandbox(case) as (workdir, artifacts):
        assert (workdir / "bar.py").read_text().startswith("def foo")
        assert workdir != case.fixture, "the fixture itself is never graded"
        assert artifacts.is_dir()
        kept = workdir
    assert not kept.exists()


@pytest.mark.docker
def test_run_check_reports_the_exit_code(tmp_path: Path) -> None:
    from benchmarks.runner.sandbox import run_check, sandbox

    with sandbox(make_case(tmp_path)) as (workdir, _):
        assert run_check(workdir, "test -f bar.py", 60)[0] == 0
        code, output = run_check(workdir, "test -f absent.txt", 60)
        assert code != 0


@pytest.mark.docker
def test_run_check_writes_as_the_invoking_user(tmp_path: Path) -> None:
    """Root-owned files in the bind mount would break git diff on the host."""
    import os

    from benchmarks.runner.sandbox import run_check, sandbox

    with sandbox(make_case(tmp_path)) as (workdir, _):
        assert run_check(workdir, "touch made.txt", 60)[0] == 0
        assert (workdir / "made.txt").stat().st_uid == os.getuid()


@pytest.mark.docker
def test_git_diff_shows_the_agents_change_only(tmp_path: Path) -> None:
    from benchmarks.runner.sandbox import git_checkpoint, git_diff, sandbox

    with sandbox(make_case(tmp_path)) as (workdir, _):
        git_checkpoint(workdir)
        assert git_diff(workdir) == ""
        (workdir / "bar.py").write_text("def foo():\n    return 2\n")
        diff = git_diff(workdir)
        assert "return 2" in diff
        assert "bar.py" in diff


@pytest.mark.docker
def test_a_hanging_command_is_killed_at_the_timeout(tmp_path: Path) -> None:
    from benchmarks.runner.sandbox import run_check, sandbox

    with sandbox(make_case(tmp_path)) as (workdir, _):
        code, _ = run_check(workdir, "sleep 30", 3)
        assert code != 0


@pytest.mark.live
@pytest.mark.docker
def test_run_agent_end_to_end(tmp_path: Path) -> None:
    """The one test that spends money. Deselected twice over by default."""
    import os

    from benchmarks.runner.config import resolve_config
    from benchmarks.runner.sandbox import run_agent, sandbox

    config = resolve_config(
        model=None, provider=None, base_url=None, judge_model=None, env=os.environ
    )
    case = make_case(tmp_path)
    with sandbox(case) as (workdir, artifacts):
        result = run_agent(case, workdir, artifacts, config)
    assert not result.timed_out
    assert '"type": "result"' in result.stdout or '"type":"result"' in result.stdout
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest benchmarks/tests/test_sandbox.py -v`
Expected: FAIL with `ImportError: cannot import name 'sandbox'`

- [ ] **Step 3: Write the implementation**

Append to `benchmarks/runner/sandbox.py` (adding `import os`, `import shutil`,
`import tempfile`, `import time`, `import uuid`,
`from collections.abc import Iterator`, `from contextlib import contextmanager`,
`from dataclasses import dataclass`, and
`from benchmarks.runner.case import Case` to the imports):

```python
# The agent's script. Values arrive as environment variables rather than
# interpolated text: a prompt is arbitrary user data, and splicing it into a
# shell command is how quoting bugs become arbitrary execution.
_AGENT_SCRIPT = """\
set -e
git init -q -b main
git add -A
git -c user.email=bench@nare.invalid -c user.name=nare-bench commit -qm fixture
exec nare run --yes --jsonl --session /out/session.json \
     --max-turns "$BENCH_MAX_TURNS" --model "$BENCH_MODEL" -- "$BENCH_PROMPT"
"""


@dataclass(frozen=True)
class RunArtifacts:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    duration_s: float


@contextmanager
def sandbox(case: Case) -> Iterator[tuple[Path, Path]]:
    """A throwaway copy of the fixture, plus a directory outside the graded tree.

    The session is written to the artifacts directory rather than the work
    directory so that it never shows up in the diff the judge reads, and still
    survives long enough to debug a failed repetition.
    """
    holder = Path(tempfile.mkdtemp(prefix=f"nare-bench-{case.id}-"))
    workdir = holder / "work"
    artifacts = holder / "out"
    try:
        shutil.copytree(case.fixture, workdir)
        artifacts.mkdir()
        yield workdir, artifacts
    finally:
        shutil.rmtree(holder, ignore_errors=True)


def _docker_run(
    workdir: Path,
    argv: list[str],
    timeout: int,
    *,
    artifacts: Path | None = None,
    env: dict[str, str] | None = None,
    passthrough: tuple[str, ...] = (),
) -> tuple[int, str, str, bool]:
    name = f"nare-bench-{uuid.uuid4().hex[:12]}"
    command = [
        "docker", "run", "--rm", "--name", name,
        # As the invoking user, so files in the bind mount are owned by them
        # and the host's git does not refuse the repository as dubious.
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-v", f"{workdir}:/work",
        "-w", "/work",
    ]
    if artifacts is not None:
        command += ["-v", f"{artifacts}:/out"]
    for key, value in (env or {}).items():
        command += ["-e", f"{key}={value}"]
    for key in passthrough:
        if os.environ.get(key):
            # Passed by name so the value never reaches argv, which is
            # world-readable through ps and /proc.
            command += ["-e", key]
    command += [IMAGE, *argv]

    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout
        )
        return proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        # subprocess's timeout kills the docker CLI, not the container it
        # started. Without this the container outlives the repetition.
        subprocess.run(["docker", "kill", name], capture_output=True)
        out = exc.stdout or b""
        err = exc.stderr or b""
        return (
            124,
            out.decode(errors="replace") if isinstance(out, bytes) else out,
            err.decode(errors="replace") if isinstance(err, bytes) else err,
            True,
        )


def run_agent(
    case: Case, workdir: Path, artifacts: Path, config: Config
) -> RunArtifacts:
    """Run one repetition of the agent against a prepared sandbox.

    `--effort` is deliberately never passed: it renders as
    thinking.budget_tokens, which some models accept and others reject
    outright, and a flag whose availability varies by model has no place in a
    controlled measurement.
    """
    started = time.monotonic()
    code, out, err, timed_out = _docker_run(
        workdir,
        ["sh", "-c", _AGENT_SCRIPT],
        case.timeout,
        artifacts=artifacts,
        env={
            "BENCH_PROMPT": case.prompt,
            "BENCH_MODEL": config.model,
            "BENCH_MAX_TURNS": str(case.max_turns),
        },
        passthrough=("ANTHROPIC_API_KEY", "NARE_BASE_URL", "NARE_PROVIDER"),
    )
    return RunArtifacts(
        stdout=out,
        stderr=err,
        exit_code=code,
        timed_out=timed_out,
        duration_s=time.monotonic() - started,
    )


def run_check(workdir: Path, cmd: str, timeout: int) -> tuple[int, str]:
    """Grade one bash check in a second container over the same directory."""
    code, out, err, _ = _docker_run(workdir, ["sh", "-c", cmd], timeout)
    return code, (out + err).strip()


def git_checkpoint(workdir: Path) -> None:
    """Used by `verify`, which needs a checkpoint without running the agent."""
    _docker_run(
        workdir,
        [
            "sh", "-c",
            "git init -q -b main && git add -A && "
            "git -c user.email=bench@nare.invalid -c user.name=nare-bench "
            "commit -qm fixture",
        ],
        60,
    )


def git_diff(workdir: Path) -> str:
    """Read the agent's change on the host: the bind mount put .git here."""
    proc = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    return proc.stdout
```

- [ ] **Step 4: Run the unit tests**

Run: `uv run pytest benchmarks/tests/test_sandbox.py -v`
Expected: PASS (Docker and live tests deselected)

- [ ] **Step 5: Run the Docker tests**

Run: `uv run pytest benchmarks/tests/test_sandbox.py -v -m docker`
Expected: PASS. If `test_run_check_writes_as_the_invoking_user` fails, the
`--user` flag is not taking effect and `git_diff` on the host will break next.

- [ ] **Step 6: Verify types and lint**

Run: `uv run ruff format benchmarks && uv run ruff check benchmarks && uv run mypy benchmarks`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add benchmarks/runner/sandbox.py benchmarks/tests/test_sandbox.py
git commit -m "feat: run one repetition in a throwaway container"
```

---

### Task 11: The CLI and `bench verify`

`verify` is the cheapest end-to-end path - Docker but no API key and no money
,  so it is the first subcommand wired, and the one CI can run on every PR.

**Files:**
- Create: `benchmarks/runner/__main__.py`
- Create: `benchmarks/tests/test_main.py`

**Interfaces:**
- Consumes: everything from Tasks 1, 2, 9, 10.
- Produces: `build_parser() -> argparse.ArgumentParser`,
  `verify(cases, *, run_check) -> tuple[str, int]`, and
  `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Write the failing test**

Create `benchmarks/tests/test_main.py`:

```python
from __future__ import annotations

from pathlib import Path

from benchmarks.runner.__main__ import build_parser, main, verify
from benchmarks.runner.case import Case, Check


def case_with(*checks: Check, case_id: str = "demo") -> Case:
    return Case(
        id=case_id,
        tier="smoke",
        prompt="do a thing",
        directory=Path("/nowhere"),
        max_turns=4,
        reps=3,
        timeout=60,
        checks=checks,
        judge=None,
    )


def test_parser_offers_the_four_subcommands() -> None:
    parser = build_parser()
    for command in ("run", "compare", "bless", "verify"):
        assert parser.parse_args([command]).command == command


def test_run_accepts_the_configuration_flags() -> None:
    args = build_parser().parse_args(
        ["run", "--tier", "smoke", "--model", "claude-haiku", "--strict-tokens"]
    )
    assert args.tier == "smoke"
    assert args.model == "claude-haiku"
    assert args.strict_tokens


def test_verify_passes_when_a_check_fails_on_the_pristine_fixture() -> None:
    case = case_with(Check(kind="bash", cmd="test -f done.txt"))
    text, code = verify([case], run_check=lambda w, c, t: (1, ""))
    assert code == 0
    assert "demo" in text


def test_verify_fails_a_case_whose_checks_already_pass() -> None:
    """The worst benchmark bug: a check that is green before the agent runs."""
    case = case_with(Check(kind="bash", cmd="true"))
    text, code = verify([case], run_check=lambda w, c, t: (0, ""))
    assert code == 1
    assert "already passes" in text


def test_verify_exempts_a_case_with_only_result_checks() -> None:
    """verify never runs the agent, so there is no result line to check."""
    case = case_with(Check(kind="result", status="blocked"))
    text, code = verify([case], run_check=lambda w, c, t: (0, ""))
    assert code == 0
    assert "exempt" in text


def test_verify_needs_only_one_failing_check_of_several() -> None:
    case = case_with(
        Check(kind="bash", cmd="true"),
        Check(kind="bash", cmd="test -f done.txt"),
    )
    codes = iter([0, 1])
    _, code = verify([case], run_check=lambda w, c, t: (next(codes), ""))
    assert code == 0


def test_main_without_a_subcommand_exits_two() -> None:
    assert main([]) == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest benchmarks/tests/test_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmarks.runner.__main__'`

- [ ] **Step 3: Write the implementation**

Create `benchmarks/runner/__main__.py`:

```python
"""`bin/bench` -- a thin adapter over the runner modules.

Everything decidable is decided in the pure modules; this file parses
arguments, orders the work, and prints.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from benchmarks.runner.case import Case, CaseError, load_cases
from benchmarks.runner.config import ConfigError, resolve_config
from benchmarks.runner.sandbox import (
    SandboxError,
    build_image,
    git_checkpoint,
    repo_root,
    run_check,
    sandbox,
)

CheckRunner = Callable[[Path, str, int], tuple[int, str]]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bench", description="Measure whether a change to nare helped."
    )
    sub = parser.add_subparsers(dest="command")

    for name, help_text in (
        ("run", "run a tier and compare it against the baseline"),
        ("compare", "compare the latest results against the baseline"),
        ("bless", "write the latest results to the baseline"),
        ("verify", "check that each case's checks fail on its pristine fixture"),
    ):
        child = sub.add_parser(name, help=help_text)
        child.add_argument(
            "--tier", choices=["smoke", "full"], default="smoke", help="which tier"
        )
        child.add_argument("--case", action="append", help="limit to this case id")
        if name == "verify":
            continue
        child.add_argument("--model", help="model under test (env: NARE_MODEL)")
        child.add_argument("--provider", help="provider (env: NARE_PROVIDER)")
        child.add_argument("--base-url", help="endpoint (env: NARE_BASE_URL)")
        child.add_argument(
            "--judge-model", help="judge (env: NARE_BENCH_JUDGE_MODEL)"
        )
        child.add_argument(
            "--strict-tokens",
            action="store_true",
            help="fail the run when tokens rise beyond the tolerance band",
        )
        child.add_argument(
            "--allow-model-change",
            action="store_true",
            help="compare against a baseline blessed on a different model",
        )
    return parser


def _select(root: Path, tier: str, only: Sequence[str] | None) -> list[Case]:
    cases = load_cases(root / "benchmarks" / "cases", tier=tier)
    if only:
        wanted = set(only)
        cases = [c for c in cases if c.id in wanted]
    return cases


def verify(cases: Sequence[Case], *, run_check: CheckRunner) -> tuple[str, int]:
    """Prove each case's checks measure something the agent has to do.

    Only `bash` checks are exercised: a `result` check reads nare's result
    line, and verify never runs the agent. A case whose checks are all
    `result` kind is exempt rather than failed.
    """
    lines: list[str] = []
    failed = False
    for case in cases:
        if not case.bash_checks:
            lines.append(f"{case.id:<24} exempt (no bash checks to verify)")
            continue
        with sandbox(case) as (workdir, _):
            git_checkpoint(workdir)
            outcomes = [
                run_check(workdir, check.cmd or "", 120)[0]
                for check in case.bash_checks
            ]
        if any(code != 0 for code in outcomes):
            lines.append(f"{case.id:<24} ok (fails on the pristine fixture)")
        else:
            failed = True
            lines.append(
                f"{case.id:<24} BROKEN: every bash check already passes before "
                "the agent runs, so this case measures nothing"
            )
    return "\n".join(lines), 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command is None:
        print("bench needs a subcommand: run, compare, bless, or verify",
              file=sys.stderr)
        return 2

    root = repo_root()
    try:
        cases = _select(root, args.tier, args.case)
    except CaseError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2
    if not cases:
        print(f"bench: no cases in tier {args.tier}", file=sys.stderr)
        return 2

    if args.command == "verify":
        try:
            build_image(root)
        except SandboxError as exc:
            print(f"bench: {exc}", file=sys.stderr)
            return 2
        text, code = verify(cases, run_check=run_check)
        print(text)
        return code

    try:
        resolve_config(
            model=args.model,
            provider=args.provider,
            base_url=args.base_url,
            judge_model=args.judge_model,
            env=os.environ,
        )
    except ConfigError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2

    print(f"bench: {args.command} is not wired yet", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest benchmarks/tests/test_main.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
git add benchmarks/runner/__main__.py benchmarks/tests/test_main.py
git commit -m "feat: add the bench CLI and the verify subcommand"
```

---

### Task 12: `bench run`

The full loop: repetitions, grading, the judge, the results file, and the
automatic re-confirmation that keeps three repetitions affordable.

**Files:**
- Modify: `benchmarks/runner/__main__.py`
- Modify: `benchmarks/tests/test_main.py`

**Interfaces:**
- Consumes: everything from Tasks 1-11.
- Produces: `run_rep(case, config, transport) -> RepRecord`,
  `run_case(case, config, transport, reps) -> list[RepRecord]`, and
  `write_results(root, records) -> Path`.

- [ ] **Step 1: Write the failing test**

Append to `benchmarks/tests/test_main.py`:

```python
import json

from benchmarks.runner.report import RepRecord, summarize
from benchmarks.runner.__main__ import CONFIRM_REPS, write_results


def test_results_are_written_as_one_json_line_per_rep(tmp_path: Path) -> None:
    records = [
        RepRecord(
            case="demo",
            rep=0,
            outcome="pass",
            checks=(),
            judge_met=2,
            judge_why="fine",
            status="done",
            stop_reason="end_turn",
            turns=4,
            usage={"input": 10, "output": 5, "cache_read": 0, "cache_write": 0},
            duration_s=1.5,
            model="claude-haiku",
        )
    ]
    path = write_results(tmp_path, records)
    assert path.parent == tmp_path / "benchmarks" / "results"
    payload = json.loads(path.read_text().splitlines()[0])
    assert payload["case"] == "demo"
    assert payload["outcome"] == "pass"
    assert payload["usage"]["input"] == 10
    assert payload["model"] == "claude-haiku"


def test_results_are_gitignored(tmp_path: Path) -> None:
    from benchmarks.runner.sandbox import repo_root

    assert "benchmarks/results/" in (repo_root() / ".gitignore").read_text()


def test_confirmation_uses_seven_reps() -> None:
    assert CONFIRM_REPS == 7
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest benchmarks/tests/test_main.py -v`
Expected: FAIL with `ImportError: cannot import name 'CONFIRM_REPS'`

- [ ] **Step 3: Write the implementation**

Add to `benchmarks/runner/__main__.py` (importing `asyncio`, `json`,
`datetime`, `subprocess`, the grading helpers, the judge, and the report
helpers):

```python
CONFIRM_REPS = 7
TOKEN_BAND = 0.10


def _commit() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    )
    return proc.stdout.strip() or "unknown"


async def run_rep(
    case: Case, config: Config, transport: Transport, rep: int
) -> RepRecord:
    """One repetition, from a clean fixture to a graded record."""
    empty = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    with sandbox(case) as (workdir, artifacts):
        result = run_agent(case, workdir, artifacts, config)
        line = parse_stdout(result.stdout)
        if line is None:
            # No result line means the run never produced one: a crash, a
            # timeout, a Docker fault. That is an error, never a failure.
            return RepRecord(
                case=case.id, rep=rep, outcome="error", checks=(),
                judge_met=None,
                judge_why="timed out" if result.timed_out else "no result line",
                status=None, stop_reason=None, turns=None, usage=empty,
                duration_s=result.duration_s, model=config.model,
            )

        graded = [
            grade_result_check(check, line)
            for check in case.checks
            if check.kind == "result"
        ]
        for check in case.bash_checks:
            code, output = run_check(workdir, check.cmd or "", 120)
            graded.append(grade_bash_check(check, code, output))

        met: list[bool] | None = None
        why = ""
        if case.judge is not None:
            try:
                met, why = await score(
                    case, git_diff(workdir), line_text(result.stdout),
                    transport=transport,
                )
            except JudgeError as exc:
                why = f"judge failed: {exc}"

    return RepRecord(
        case=case.id, rep=rep,
        outcome=rep_outcome(graded, case.judge, met),
        checks=tuple(graded),
        judge_met=None if met is None else sum(met),
        judge_why=why,
        status=line.status, stop_reason=line.stop_reason, turns=line.turns,
        usage=line.usage, duration_s=result.duration_s, model=config.model,
    )


async def run_case(
    case: Case, config: Config, transport: Transport, reps: int
) -> list[RepRecord]:
    # Sequential on purpose: repetitions share one rate limit and one Docker
    # daemon, and a benchmark that saturates either measures the machine.
    return [await run_rep(case, config, transport, rep) for rep in range(reps)]


def write_results(root: Path, records: Sequence[RepRecord]) -> Path:
    directory = root / "benchmarks" / "results"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    path = directory / f"{stamp}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for rec in records:
            payload = asdict(rec)
            payload["checks"] = [asdict(c) for c in rec.checks]
            handle.write(json.dumps(payload) + "\n")
    return path
```

And replace the `not wired yet` branch for `run`:

```python
    if args.command == "run":
        try:
            build_image(root)
        except SandboxError as exc:
            print(f"bench: {exc}", file=sys.stderr)
            return 2
        transport = judge_transport(config)
        records: list[RepRecord] = []
        for case in cases:
            records += asyncio.run(run_case(case, config, transport, case.reps))

        path = write_results(root, records)
        print(f"results: {path}")

        baseline_file = baseline_path(
            root / "benchmarks" / "baselines", config.model, args.tier
        )
        if not baseline_file.exists():
            print(render_first_run(summarize(records)))
            print(f"\nno baseline yet; bless one with: bin/bench bless --tier {args.tier}")
            return 0

        baseline = load_baseline(baseline_file)
        if not args.allow_model_change and (
            baseline.meta.model != config.model
            or baseline.meta.judge_model != config.judge_model
        ):
            print(
                f"bench: baseline was blessed on model {baseline.meta.model!r} / "
                f"judge {baseline.meta.judge_model!r}; this run used "
                f"{config.model!r} / {config.judge_model!r}. Numbers are not "
                "comparable across models. Pass --allow-model-change to override.",
                file=sys.stderr,
            )
            return 2

        first = compare(summarize(records), baseline, token_band=TOKEN_BAND,
                        strict_tokens=args.strict_tokens)
        suspects = [d.case for d in first.deltas if d.verdict == "suspect"]
        if suspects:
            print(f"re-confirming {', '.join(suspects)} at {CONFIRM_REPS} reps...")
            for case in [c for c in cases if c.id in suspects]:
                extra = asyncio.run(run_case(case, config, transport, CONFIRM_REPS))
                records = [r for r in records if r.case != case.id] + extra
            write_results(root, records)

        final = compare(summarize(records), baseline, confirmed=True,
                        token_band=TOKEN_BAND, strict_tokens=args.strict_tokens)
        print(render(final))
        return final.exit_code
```

Add the two small helpers this branch needs:

```python
def line_text(stdout: str) -> str:
    """The final assistant text, which nare emits as the `output` event."""
    for raw in reversed(stdout.splitlines()):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("type") == "output":
            return str(payload.get("text", ""))
    return ""


def render_first_run(summaries: Sequence[CaseSummary]) -> str:
    return render(Comparison(
        deltas=tuple(
            CaseDelta(s.case, "new", s, None, None, ()) for s in summaries
        ),
        exit_code=0,
    ))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest benchmarks/tests/test_main.py -v`
Expected: PASS

- [ ] **Step 5: Verify types and lint**

Run: `uv run ruff format benchmarks && uv run ruff check benchmarks && uv run mypy benchmarks`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add benchmarks/runner/__main__.py benchmarks/tests/test_main.py
git commit -m "feat: run a tier, grade every repetition, and re-confirm suspects"
```

---

### Task 13: `bench compare` and `bench bless`

Re-compare a finished run without paying for it again, and promote a run's
numbers to the baseline as a reviewable diff.

**Files:**
- Modify: `benchmarks/runner/__main__.py`
- Modify: `benchmarks/tests/test_main.py`

**Interfaces:**
- Consumes: `write_results` from Task 12; `load_baseline`, `dumps_baseline`,
  `baseline_path` from Task 6.
- Produces: `latest_results(root) -> Path | None` and
  `read_results(path) -> list[RepRecord]`.

- [ ] **Step 1: Write the failing test**

Append to `benchmarks/tests/test_main.py`:

```python
from benchmarks.runner.__main__ import latest_results, read_results


def test_results_round_trip(tmp_path: Path) -> None:
    records = [
        RepRecord(
            case="demo", rep=0, outcome="pass", checks=(), judge_met=2,
            judge_why="", status="done", stop_reason="end_turn", turns=4,
            usage={"input": 10, "output": 5, "cache_read": 0, "cache_write": 0},
            duration_s=1.0, model="claude-haiku",
        )
    ]
    path = write_results(tmp_path, records)
    assert [r.case for r in read_results(path)] == ["demo"]
    assert read_results(path)[0].outcome == "pass"


def test_latest_results_picks_the_newest(tmp_path: Path) -> None:
    directory = tmp_path / "benchmarks" / "results"
    directory.mkdir(parents=True)
    (directory / "2026-09-20T00-00-00Z.jsonl").write_text("")
    (directory / "2026-09-22T00-00-00Z.jsonl").write_text("")
    newest = latest_results(tmp_path)
    assert newest is not None
    assert newest.name == "2026-09-22T00-00-00Z.jsonl"


def test_latest_results_is_none_when_nothing_has_run(tmp_path: Path) -> None:
    assert latest_results(tmp_path) is None


def test_blessing_writes_a_baseline_keyed_by_model(tmp_path: Path) -> None:
    from benchmarks.runner.report import baseline_path, load_baseline

    records = [
        RepRecord(
            case="demo", rep=n, outcome="pass", checks=(), judge_met=2,
            judge_why="", status="done", stop_reason="end_turn", turns=4,
            usage={"input": 1000, "output": 0, "cache_read": 0, "cache_write": 0},
            duration_s=1.0, model="ada/qwen3-14b",
        )
        for n in range(3)
    ]
    baselines = tmp_path / "baselines"
    baselines.mkdir()
    path = baseline_path(baselines, "ada/qwen3-14b", "smoke")
    path.write_text(
        dumps_baseline(
            BaselineMeta("2026-09-22T00:00:00Z", "abc1234", "anthropic",
                         "ada/qwen3-14b", "ada/qwen3-14b"),
            summarize(records),
        )
    )
    assert path.name == "ada-qwen3-14b.smoke.toml"
    assert load_baseline(path).cases["demo"].pass_rate == 1.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest benchmarks/tests/test_main.py -v`
Expected: FAIL with `ImportError: cannot import name 'latest_results'`

- [ ] **Step 3: Write the implementation**

Add to `benchmarks/runner/__main__.py`:

```python
def latest_results(root: Path) -> Path | None:
    directory = root / "benchmarks" / "results"
    if not directory.is_dir():
        return None
    files = sorted(directory.glob("*.jsonl"))
    return files[-1] if files else None


def read_results(path: Path) -> list[RepRecord]:
    records: list[RepRecord] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        payload = json.loads(raw)
        payload["checks"] = tuple(
            CheckResult(**check) for check in payload.get("checks", [])
        )
        records.append(RepRecord(**payload))
    return records
```

And replace the remaining `not wired yet` branch:

```python
    results_file = latest_results(root)
    if results_file is None:
        print("bench: no results yet; run `bin/bench run` first", file=sys.stderr)
        return 2
    summaries = summarize(read_results(results_file))
    baselines = root / "benchmarks" / "baselines"
    baseline_file = baseline_path(baselines, config.model, args.tier)

    if args.command == "bless":
        baselines.mkdir(parents=True, exist_ok=True)
        meta = BaselineMeta(
            blessed=datetime.now(UTC).isoformat(timespec="seconds"),
            commit=_commit(),
            provider=config.provider,
            model=config.model,
            judge_model=config.judge_model,
        )
        baseline_file.write_text(dumps_baseline(meta, summaries), encoding="utf-8")
        print(f"blessed {baseline_file} from {results_file.name}")
        return 0

    if not baseline_file.exists():
        print(f"bench: no baseline at {baseline_file}", file=sys.stderr)
        return 2
    baseline = load_baseline(baseline_file)
    if not args.allow_model_change and (
        baseline.meta.model != config.model
        or baseline.meta.judge_model != config.judge_model
    ):
        print(
            f"bench: baseline was blessed on model {baseline.meta.model!r} / "
            f"judge {baseline.meta.judge_model!r}; this run used "
            f"{config.model!r} / {config.judge_model!r}. Numbers are not "
            "comparable across models. Pass --allow-model-change to override.",
            file=sys.stderr,
        )
        return 2
    comparison = compare(summaries, baseline, confirmed=True,
                         token_band=TOKEN_BAND, strict_tokens=args.strict_tokens)
    print(render(comparison))
    return comparison.exit_code
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest benchmarks/tests/test_main.py -v`
Expected: PASS

- [ ] **Step 5: Run the whole build**

Run: `bin/build`
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add benchmarks/runner/__main__.py benchmarks/tests/test_main.py
git commit -m "feat: add bench compare and bench bless"
```

---

### Task 14: The five seed cases

Chosen to hit nare's own seams rather than generic coding. Two of them measure
things nothing else in the repo measures.

**Files:**
- Create: `benchmarks/cases/edit-docstring/{case.toml,fixture/bar.py}`
- Create: `benchmarks/cases/fix-failing-test/{case.toml,fixture/bar.py,fixture/test_bar.py}`
- Create: `benchmarks/cases/multi-file-rename/{case.toml,fixture/*.py}`
- Create: `benchmarks/cases/ambiguous-request/{case.toml,fixture/config.py}`
- Create: `benchmarks/cases/bash-timeout-recovery/{case.toml,fixture/*}`
- Create: `benchmarks/tests/test_cases.py`

**Interfaces:**
- Consumes: `load_cases` from Task 2.
- Produces: the case corpus. No new code.

- [ ] **Step 1: Write the failing test**

Create `benchmarks/tests/test_cases.py`:

```python
from __future__ import annotations

from benchmarks.runner.case import load_cases
from benchmarks.runner.sandbox import repo_root

SEEDS = {
    "ambiguous-request",
    "bash-timeout-recovery",
    "edit-docstring",
    "fix-failing-test",
    "multi-file-rename",
}


def test_every_seed_case_loads() -> None:
    cases = load_cases(repo_root() / "benchmarks" / "cases")
    assert {c.id for c in cases} == SEEDS


def test_the_smoke_tier_is_the_whole_seed_set() -> None:
    cases = load_cases(repo_root() / "benchmarks" / "cases", tier="smoke")
    assert len(cases) == 5


def test_no_case_names_a_model() -> None:
    """The portability guarantee, checked against the real corpus."""
    root = repo_root() / "benchmarks" / "cases"
    for path in root.glob("*/case.toml"):
        body = path.read_text()
        for field in ("model", "provider", "base_url"):
            assert f"{field} =" not in body, f"{path} names {field}"


def test_the_ask_case_expects_a_blocked_run() -> None:
    cases = {c.id: c for c in load_cases(repo_root() / "benchmarks" / "cases")}
    statuses = [c.status for c in cases["ambiguous-request"].checks]
    assert "blocked" in statuses
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest benchmarks/tests/test_cases.py -v`
Expected: FAIL - the case directories do not exist

- [ ] **Step 3: Write `edit-docstring`**

`benchmarks/cases/edit-docstring/fixture/bar.py`:

```python
def foo(values):
    total = 0
    for value in values:
        if value > 0:
            total += value
    return total
```

`benchmarks/cases/edit-docstring/case.toml`:

```toml
id = "edit-docstring"
tier = "smoke"
prompt = "Add a docstring to foo() in bar.py."
max_turns = 6

[[check]]
kind = "bash"
cmd = "python -c \"import bar; assert bar.foo.__doc__, 'no docstring'\""

[[check]]
kind = "result"
status = "done"

[judge]
assertions = [
  "bar.py gained a docstring on foo().",
  "The docstring describes what foo does, rather than only restating its name.",
  "The behaviour of foo() is unchanged.",
]
min_met = 3
```

- [ ] **Step 4: Write `fix-failing-test`**

`benchmarks/cases/fix-failing-test/fixture/bar.py`:

```python
def parse(text):
    return [int(part) for part in text.split(",")]
```

`benchmarks/cases/fix-failing-test/fixture/test_bar.py`:

```python
from bar import parse


def test_parse_handles_spaces():
    assert parse("1, 2, 3") == [1, 2, 3]


def test_parse_handles_an_empty_string():
    assert parse("") == []
```

`benchmarks/cases/fix-failing-test/case.toml`:

```toml
id = "fix-failing-test"
tier = "smoke"
prompt = "The test suite fails. Fix bar.py so that `pytest` passes."
max_turns = 12

[[check]]
kind = "bash"
cmd = "pytest -q"

[[check]]
kind = "result"
status = "done"

[judge]
assertions = [
  "The fix is in bar.py.",
  "test_bar.py was not modified, deleted, or skipped.",
  "parse() still returns a list of integers for well-formed input.",
]
min_met = 3
```

- [ ] **Step 5: Write `multi-file-rename`**

Create three files under `benchmarks/cases/multi-file-rename/fixture/`:

`shape.py`:
```python
def calc_area(width, height):
    return width * height
```

`report.py`:
```python
from shape import calc_area


def summary(width, height):
    return f"area is {calc_area(width, height)}"
```

`test_report.py`:
```python
from report import summary
from shape import calc_area


def test_area():
    assert calc_area(2, 3) == 6


def test_summary():
    assert summary(2, 3) == "area is 6"
```

`benchmarks/cases/multi-file-rename/case.toml`:

```toml
id = "multi-file-rename"
tier = "smoke"
prompt = """Rename the function calc_area to rectangle_area everywhere it \
appears, and make sure the test suite still passes."""
max_turns = 15

[[check]]
kind = "bash"
cmd = "! rg -q 'calc_area' . && rg -q 'rectangle_area' shape.py && pytest -q"

[[check]]
kind = "result"
status = "done"

[judge]
assertions = [
  "Every occurrence of calc_area was renamed, across all three files.",
  "No file was deleted and no test was removed.",
]
min_met = 2
```

- [ ] **Step 6: Write `ambiguous-request`**

`benchmarks/cases/ambiguous-request/fixture/config.py`:

```python
TIMEOUT = 30
RETRIES = 3
```

`benchmarks/cases/ambiguous-request/case.toml`:

```toml
id = "ambiguous-request"
tier = "smoke"
# The capability nare exists for, and the one tests/ can only exercise against
# scripted replies. A correct run ends blocked with a question, not done.
prompt = "Increase the timeout."
max_turns = 6

[[check]]
kind = "result"
status = "blocked"
```

- [ ] **Step 7: Write `bash-timeout-recovery`**

`benchmarks/cases/bash-timeout-recovery/fixture/slow.sh`:

```bash
#!/usr/bin/env bash
sleep 600
```

`benchmarks/cases/bash-timeout-recovery/fixture/bar.py`:

```python
def greet(name):
    return "hello " + name
```

`benchmarks/cases/bash-timeout-recovery/case.toml`:

```toml
id = "bash-timeout-recovery"
tier = "smoke"
prompt = """Run ./slow.sh with a timeout of 5 seconds to see what it does. It \
will hang -- do not wait for it. Then change greet() in bar.py to return an \
uppercase greeting, and write what happened with slow.sh to notes.txt."""
max_turns = 12

[[check]]
kind = "bash"
cmd = "python -c \"import bar; assert bar.greet('sam') == 'HELLO SAM'\" && test -s notes.txt"

[[check]]
kind = "result"
status = "done"

[judge]
assertions = [
  "notes.txt records that slow.sh hung or timed out.",
  "greet() returns an uppercase greeting.",
]
min_met = 2
```

- [ ] **Step 8: Run the tests**

Run: `uv run pytest benchmarks/tests/test_cases.py -v`
Expected: PASS, 4 tests

- [ ] **Step 9: Verify the cases actually measure something**

Run: `bin/bench verify`
Expected: exit 0. `ambiguous-request` reports `exempt (no bash checks to
verify)`; the other four report `ok (fails on the pristine fixture)`. If any
case reports `BROKEN`, its check passes before the agent runs and the check is
wrong - fix the check, not the message.

- [ ] **Step 10: Commit**

```bash
git add benchmarks/cases benchmarks/tests/test_cases.py
git commit -m "feat: add the five seed benchmark cases"
```

---

### Task 15: CI, documentation, and the first real run

Wires the free CI job, documents the suite, and takes the first measurement.

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/architecture.md`
- Create: `benchmarks/README.md`

**Interfaces:**
- Consumes: everything.
- Produces: nothing importable.

- [ ] **Step 1: Add the CI job**

`.github/workflows/ci.yml` now sets `permissions: contents: read` and runs on
pushes to `main` plus every pull request. The job below needs nothing beyond
`contents: read`, so the existing block covers it. Add it beside `build`:

```yaml
  cases:
    name: benchmark cases measure something
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.12"
          enable-cache: true
      - run: uv sync
      # Needs Docker, but no API key and no money: it only proves that each
      # case's checks fail before an agent touches the fixture.
      - run: bin/bench verify
```

- [ ] **Step 2: Write the benchmark README**

Create `benchmarks/README.md`:

```markdown
# benchmarks

Measures whether a change to nare made the harness better: whether real tasks
get done, at what quality, for how many tokens.

This is development tooling. It is not installed with nare - the wheel
contains only `src/nare` - so running it means working from a checkout.

## Running it

Docker is required; every repetition runs in a throwaway container.

    export ANTHROPIC_API_KEY=...        # or your proxy's key
    export NARE_MODEL=claude-haiku
    export NARE_BASE_URL=https://...    # only if you use a proxy

    bin/bench verify                    # free: are the cases well-formed?
    bin/bench run --tier smoke          # measure
    bin/bench bless --tier smoke        # promote the numbers to a baseline

`run` compares against `baselines/<model>.<tier>.toml` and exits 1 on a
confirmed regression. A case whose pass rate drops is re-run at seven
repetitions before it is called one, because sampling cannot be pinned and
three repetitions are not conclusive on their own.

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

Run `bin/bench verify` on any case you add. It proves the checks fail on the
pristine fixture, which is the difference between a case that measures
something and a case that is green no matter what the agent does.

## Containment

The container bounds the filesystem and the process tree. It does not bound
the network - the agent's endpoint has to be reachable, so `--network none` is
not available. Treat a benchmark run like any other unattended execution of
model-generated shell commands.
```

- [ ] **Step 3: Note the suite in the architecture doc**

Append to `docs/architecture.md`:

```markdown
## Measuring it

`tests/` proves the harness honours its contract given a scripted model.
`benchmarks/` asks the other question - whether real tasks get done, at what
quality, for how many tokens - by running the real thing in a container. It is
development tooling, excluded from the wheel, and it is where a change to a
tool description or the turn budget is shown to have helped. See
[benchmarks/README.md](../benchmarks/README.md) and ADR 0007.
```

- [ ] **Step 4: Run the whole build**

Run: `bin/build && bin/bench verify`
Expected: both green

- [ ] **Step 5: Take the first measurement**

Run: `bin/bench run --tier smoke`
Expected: five cases run, a results file is written, and - because no baseline
exists yet - every case reports `new` and the run exits 0 with a hint to
bless.

Read the table before blessing. A case at 0/3 measures nothing and should be
simplified or moved to the `full` tier; a case at 3/3 is a fine regression
detector. This calibration pass is expected, not a failure.

- [ ] **Step 6: Bless the first baseline and commit**

```bash
bin/bench bless --tier smoke
git add .github/workflows/ci.yml benchmarks/README.md \
        benchmarks/baselines docs/architecture.md
git commit -m "feat: wire the benchmark into CI and bless the first baseline"
```

---

## Self-Review

**Spec coverage.** Walked every section of the spec against the tasks:

| Spec section | Tasks |
|---|---|
| 1-3 (question, levers, decisions) | Constraints and module docstrings throughout |
| 4 (layout, what is committed, why not shipped) | 1 |
| 5 (case format, assertion judging, tier defaults) | 2, 8 |
| 6 (configuration, preflight, one rep, git init, `-e` key, judge via transport) | 1, 9, 10, 8 |
| 7 (outcome enum, medians, baseline, comparison rules) | 4, 5, 6, 7 |
| 8 (failure handling, `bench verify`) | 4, 10, 11 |
| 9 (testing the runner) | every task; `docker` marker in 1, 9, 10 |
| 10 (seed cases) | 14 |
| 11 (changes to existing files) | 1, 15 |
| 13 (acceptance criteria) | all twelve criteria map to a test; see below |

Every acceptance criterion has a home: 1 → Task 11 + 14 step 9; 2 → Task 12 +
15 step 5; 3 → Task 5 (`test_more_than_half_errors_is_inconclusive`) + Task 12;
4 → Task 3 (`test_an_error_result_is_carried_through`) + Task 12's `line is
None` branch; 5 → Task 12/13 model-mismatch branch; 6 → Task 7
(`test_a_confirmed_pass_rate_drop_is_a_regression`) + Task 12's re-confirm
loop; 7 → Task 7's three token tests; 8 → Task 13
(`test_blessing_writes_a_baseline_keyed_by_model`); 9 → Task 1 step 8; 10 →
Task 1 (`packages = ["src/nare"]` is unchanged); 11 → Task 2 and Task 14
(`test_no_case_names_a_model`); 12 → Task 1's config tests.

**Placeholder scan.** No `TBD`, no "add error handling", no "similar to Task
N". Every code step carries the actual code; every test step carries the
actual assertions.

**Type consistency.** Checked the names that cross task boundaries:
`Case.bash_checks` (defined Task 2, used Tasks 11 and 12), `CheckResult`
(Task 4, used 5, 12, 13), `RepRecord` (Task 5, used 12, 13), `CaseSummary`
(Task 5, used 6, 7), `Comparison`/`CaseDelta` (Task 7, used 12),
`RunArtifacts` (Task 10, used 12), `judge_transport` (Task 8, used 12),
`run_check` signature `(Path, str, int) -> tuple[int, str]` (Task 10, matched
by the `CheckRunner` alias in Task 11). All consistent.

**One gap found and closed during review:** Task 12 needs the final assistant
text for the judge, which is not on the result line - it is the `output`
event. `line_text()` was added to Task 12 step 3 to read it.
