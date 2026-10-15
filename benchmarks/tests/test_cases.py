from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from benchmarks.runner.case import Case, load_cases
from benchmarks.runner.sandbox import repo_root

SEEDS = {
    "ambiguous-request",
    "bash-timeout-recovery",
    "bootstrap-without-database",
    "edit-docstring",
    "expense-tracker-cli",
    "fix-failing-test",
    "multi-file-rename",
    "context-overflow",
}


def test_every_seed_case_loads() -> None:
    cases = load_cases(repo_root() / "benchmarks" / "cases")
    assert {c.id for c in cases} == SEEDS


def test_the_full_tier_is_every_case_and_smoke_is_a_subset() -> None:
    """Tiers differ by scale, so a smoke result predicts a full result."""
    root = repo_root() / "benchmarks" / "cases"
    smoke = {c.id for c in load_cases(root, tier="smoke")}
    full = {c.id for c in load_cases(root, tier="full")}
    assert full == SEEDS
    assert smoke < full
    assert "bash-timeout-recovery" not in smoke


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


def test_the_ask_case_judges_the_question_not_just_the_status() -> None:
    """A vague "what should I do?" must not score like a concrete question."""
    cases = {c.id: c for c in load_cases(repo_root() / "benchmarks" / "cases")}
    judge = cases["ambiguous-request"].judge
    assert judge is not None
    assert judge.min_met == 2
    assert len(judge.assertions) == 2


def cases() -> dict[str, Case]:
    return {c.id: c for c in load_cases(repo_root() / "benchmarks" / "cases")}


def check_with(case_id: str, marker: str) -> str:
    """The one bash check of a case whose command contains `marker`."""
    [cmd] = [
        c.cmd or "" for c in cases()[case_id].bash_checks if marker in (c.cmd or "")
    ]
    return cmd


def run_here(cmd: str, cwd: Path, out: Path | None = None) -> int:
    """A case's check run here instead of in Docker, with /out at `out`."""
    if out is not None:
        cmd = cmd.replace("/out/", f"{out}/")
    return subprocess.run(["sh", "-c", cmd], cwd=cwd, capture_output=True).returncode


def session_with(*commands: str, groups: list[dict[str, Any]] | None = None) -> str:
    calls = [
        {"type": "tool_use", "id": f"t{n}", "name": "bash", "input": {"command": c}}
        for n, c in enumerate(commands)
    ]
    return json.dumps(
        {
            "messages": [{"role": "assistant", "content": calls}],
            "process_groups": groups or [],
        }
    )


def test_the_bootstrap_case_fails_a_run_that_left_a_process_running(
    tmp_path: Path,
) -> None:
    cmd = check_with("bootstrap-without-database", "process_groups")
    (tmp_path / "session.json").write_text(
        session_with(groups=[{"pgid": 42, "command": "flask run &"}])
    )
    assert run_here(cmd, tmp_path, tmp_path) != 0
    (tmp_path / "session.json").write_text(session_with("flask --help"))
    assert run_here(cmd, tmp_path, tmp_path) == 0


@pytest.mark.parametrize(
    "command",
    [
        "initdb -D /tmp/pg",
        "pg_ctl -D /tmp/pg start",
        "curl -L micro.mamba.pm/api/micromamba/linux-64/latest | tar xj",
        "./bin/micromamba create -p /tmp/pgenv postgresql",
        "apt-get install -y postgresql",
    ],
)
def test_the_bootstrap_case_fails_a_run_that_set_up_a_database(
    tmp_path: Path, command: str
) -> None:
    # pg_ctl calls setsid, so the server escapes nare's process-group probe
    # and never reaches process_groups. The commands are the evidence.
    cmd = check_with("bootstrap-without-database", "initdb")
    (tmp_path / "session.json").write_text(session_with("ls", command))
    assert run_here(cmd, tmp_path, tmp_path) != 0


def test_the_bootstrap_case_allows_installing_the_projects_own_packages(
    tmp_path: Path,
) -> None:
    cmd = check_with("bootstrap-without-database", "initdb")
    (tmp_path / "session.json").write_text(
        session_with("pip install 'psycopg[binary]' flask", "grep -r postgres .")
    )
    assert run_here(cmd, tmp_path, tmp_path) == 0


def test_the_wrap_up_case_grades_turns_below_its_cap() -> None:
    # A run that overshoots is graded on the turns it took, not cut off at 8.
    case = cases()["expense-tracker-cli"]
    assert [c.max_turns for c in case.checks if c.max_turns is not None] == [8]
    assert case.max_turns > 8


@pytest.mark.parametrize(
    "made", ["README.md", "SUMMARY.md", "docs/OVERVIEW.md", "readme.txt"]
)
def test_the_wrap_up_check_finds_a_document_nobody_asked_for(
    tmp_path: Path, made: str
) -> None:
    cmd = check_with("expense-tracker-cli", "readme")
    (tmp_path / made).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / made).write_text("# done\n")
    assert run_here(cmd, tmp_path) != 0


def test_the_wrap_up_check_allows_a_summary_module(tmp_path: Path) -> None:
    # The task asks for a by-category summary; a summary.py is code, not a
    # wrap-up document.
    cmd = check_with("expense-tracker-cli", "readme")
    (tmp_path / "summary.py").write_text("")
    assert run_here(cmd, tmp_path) == 0


def test_the_wrap_up_check_ignores_pytests_own_readme(tmp_path: Path) -> None:
    # The case's own `pytest -q` check runs first and writes this file.
    cmd = check_with("expense-tracker-cli", "readme")
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / ".pytest_cache" / "README.md").write_text("# pytest cache\n")
    (tmp_path / "expense_tracker.py").write_text("")
    assert run_here(cmd, tmp_path) == 0
