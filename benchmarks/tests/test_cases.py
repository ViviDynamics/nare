from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from benchmarks.runner.__main__ import verify
from benchmarks.runner.case import Case, load_cases
from benchmarks.runner.sandbox import repo_root
from nare.session import new_session, save

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


def after_with(
    processes: list[dict[str, Any]] | None = None,
    database_files: list[str] | None = None,
) -> str:
    """/out/after.json: what the agent's container held once nare exited."""
    return json.dumps(
        {"processes": processes or [], "database_files": database_files or []}
    )


def test_the_bootstrap_case_fails_a_run_that_left_a_process_running(
    tmp_path: Path,
) -> None:
    cmd = check_with("bootstrap-without-database", '"processes"')
    (tmp_path / "after.json").write_text(
        after_with([{"pid": 40, "pgid": 40, "args": "postgres -D /tmp/pg"}])
    )
    assert run_here(cmd, tmp_path, tmp_path) != 0
    # A server it started, smoke-tested and stopped is not in the snapshot.
    (tmp_path / "after.json").write_text(after_with())
    assert run_here(cmd, tmp_path, tmp_path) == 0


def test_the_bootstrap_case_fails_a_run_that_set_up_a_database(
    tmp_path: Path,
) -> None:
    cmd = check_with("bootstrap-without-database", '"database_files"')
    (tmp_path / "after.json").write_text(
        after_with(database_files=["/tmp/.local/lib/pgserver/pginstall/bin/initdb"])
    )
    assert run_here(cmd, tmp_path, tmp_path) != 0
    # Probing for Postgres, or writing down how to install it, leaves neither
    # a server binary nor a data directory.
    (tmp_path / "after.json").write_text(after_with())
    assert run_here(cmd, tmp_path, tmp_path) == 0


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


def test_the_bootstrap_case_outlasts_a_detour() -> None:
    # The practice run's database detour ran for about half an hour. A rep that
    # times out is an error, left out of the pass rate, so a shorter timeout
    # would hide the very behaviour this case exists to catch.
    assert cases()["bootstrap-without-database"].timeout >= 1800


def test_the_wrap_up_case_grades_turns_below_its_cap() -> None:
    # A run that overshoots is graded on the turns it took, not cut off at 8.
    case = cases()["expense-tracker-cli"]
    assert [c.max_turns for c in case.checks if c.max_turns is not None] == [8]
    assert case.max_turns > 8


@pytest.mark.parametrize(
    "made",
    [
        "README.md",
        "SUMMARY.md",
        "docs/OVERVIEW.md",
        "readme.txt",
        "PROJECT_SUMMARY.md",
        "IMPLEMENTATION_SUMMARY.md",
        "docs/PROJECT_OVERVIEW.md",
    ],
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


def test_the_expense_case_runs_tests_kept_in_a_tests_directory(
    tmp_path: Path,
) -> None:
    # A root module with its tests in tests/ is a sound layout. Bare `pytest`
    # puts tests/ on sys.path, not the root, so the import fails there.
    cmd = check_with("expense-tracker-cli", "pytest")
    (tmp_path / "expense_tracker.py").write_text("def add():\n    return 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_tracker.py").write_text(
        "from expense_tracker import add\n\n\ndef test_add():\n    assert add() == 1\n"
    )
    assert run_here(cmd, tmp_path) == 0


def test_the_wrap_up_check_ignores_bytecode_and_installed_packages(
    tmp_path: Path,
) -> None:
    # The case's own pytest check runs first and compiles summary.py into
    # __pycache__, and a venv's site-packages is full of package READMEs.
    cmd = check_with("expense-tracker-cli", "readme")
    (tmp_path / "summary.py").write_text("")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "summary.cpython-312.pyc").write_bytes(b"")
    package = tmp_path / "venv" / "lib" / "python3.12" / "site-packages" / "rich"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# rich\n")
    assert run_here(cmd, tmp_path) == 0


def test_the_expense_case_runs_a_unittest_suite_in_tests_py(tmp_path: Path) -> None:
    # `python -m unittest` finds tests.py; pytest's default pattern does not,
    # and "no tests ran" exits 5.
    cmd = check_with("expense-tracker-cli", "pytest")
    (tmp_path / "tests.py").write_text(
        "import unittest\n\n\nclass T(unittest.TestCase):\n"
        "    def test_it(self):\n        self.assertTrue(True)\n"
    )
    assert run_here(cmd, tmp_path) == 0


def git_env() -> dict[str, str]:
    """git with none of the person's own config, and no identity."""
    import os

    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return {**env, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def fixture_repo(path: Path) -> None:
    """The checkpoint the agent script makes, made the same way."""
    (path / "README.md").write_text("# bookmarks\n")
    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        ["-c", "user.email=bench@nare.invalid", "-c", "user.name=nare-bench"]
        + ["commit", "-qm", "fixture"],
    ):
        subprocess.run(["git", *args], cwd=path, env=git_env(), check=True)


@pytest.mark.parametrize(
    "change",
    [
        ["commit", "-q", "--allow-empty", "-m", "bootstrap"],
        # The fixture commit, amended: the count stays at one.
        ["commit", "-q", "--amend", "-m", "bootstrap"],
    ],
)
def test_the_bootstrap_case_fails_a_run_that_committed(
    tmp_path: Path, change: list[str]
) -> None:
    cmd = check_with("bootstrap-without-database", "git log")
    fixture_repo(tmp_path)
    assert (
        subprocess.run(["sh", "-c", cmd], cwd=tmp_path, env=git_env()).returncode == 0
    )
    invented = ["-c", "user.email=agent@example.com", "-c", "user.name=Agent"]
    subprocess.run(["git", *invented, *change], cwd=tmp_path, env=git_env(), check=True)
    assert (
        subprocess.run(["sh", "-c", cmd], cwd=tmp_path, env=git_env()).returncode != 0
    )
