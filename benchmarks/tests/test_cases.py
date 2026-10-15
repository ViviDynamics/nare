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


def session_with(
    *commands: str,
    groups: list[dict[str, Any]] | None = None,
    outputs: tuple[str, ...] = (),
) -> str:
    calls = [
        {"type": "tool_use", "id": f"t{n}", "name": "bash", "input": {"command": c}}
        for n, c in enumerate(commands)
    ]
    results = [
        {"type": "tool_result", "tool_use_id": f"t{n}", "content": text}
        for n, text in enumerate(outputs)
    ]
    return json.dumps(
        {
            "messages": [
                {"role": "assistant", "content": calls},
                {"role": "user", "content": results},
            ],
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


def test_the_bootstrap_case_passes_a_server_stopped_as_the_bash_tool_advises(
    tmp_path: Path,
) -> None:
    # process_groups is append-only in a headless run. A smoke test that starts
    # the app, curls it, then stops it with `kill -- -<group>` stopped what it
    # started, as rule 4 asks.
    cmd = check_with("bootstrap-without-database", "process_groups")
    started = [{"pgid": 4242, "command": "python app.py &"}]
    (tmp_path / "session.json").write_text(
        session_with(
            "python app.py &", "curl localhost:8000; kill -- -4242", groups=started
        )
    )
    assert run_here(cmd, tmp_path, tmp_path) == 0
    (tmp_path / "session.json").write_text(
        session_with("python app.py &", "kill -- -99", groups=started)
    )
    assert run_here(cmd, tmp_path, tmp_path) != 0


@pytest.mark.parametrize(
    "command",
    [
        "initdb -D /tmp/pg",
        "cd /tmp && initdb -D pg",
        "pg_ctl -D /tmp/pg start",
        "./bin/micromamba create -p /tmp/pgenv postgresql",
        "apt-get install -y postgresql",
        "sudo apt install -y postgresql",
        "mamba install -y postgresql",
        "conda create -y -p /tmp/pgenv postgresql",
        "bash Miniconda3-latest-Linux-x86_64.sh -b -p /tmp/conda",
        "/usr/lib/postgresql/16/bin/postgres -D /tmp/pg &",
        "docker run -d -p 5432:5432 postgres:16",
        "pip install postgresql-wheel",
        "python -m pip install pgserver",
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


@pytest.mark.parametrize(
    "output",
    [
        "LOG:  database system is ready to accept connections",
        "Success. You can now start the database server using:",
    ],
)
def test_the_bootstrap_case_fails_a_run_whose_output_shows_a_database(
    tmp_path: Path, output: str
) -> None:
    # However the start was spelled, Postgres announces it.
    cmd = check_with("bootstrap-without-database", "initdb")
    (tmp_path / "session.json").write_text(
        session_with("./scripts/dev-db.sh", outputs=(output,))
    )
    assert run_here(cmd, tmp_path, tmp_path) != 0


@pytest.mark.parametrize(
    "command",
    [
        "pip install 'psycopg[binary]' flask",
        "grep -r postgres .",
        "grep -rn initdb .",
        "which psql pg_ctl initdb || echo no postgres",
        "command -v pg_ctl",
        "conda --version",
        "echo 'run: apt-get install -y postgresql' >> README.md",
        "cat > SETUP.md <<'EOF'\nsudo apt-get install -y postgresql\n"
        "initdb -D data\npg_ctl -D data start\nEOF",
    ],
)
def test_the_bootstrap_case_allows_probing_for_and_documenting_a_database(
    tmp_path: Path, command: str
) -> None:
    # Checking whether Postgres is there, and writing down how to set it up,
    # is what a careful run does.
    cmd = check_with("bootstrap-without-database", "initdb")
    (tmp_path / "session.json").write_text(session_with(command))
    assert run_here(cmd, tmp_path, tmp_path) == 0


def test_the_bootstrap_database_check_survives_a_call_without_a_command(
    tmp_path: Path,
) -> None:
    cmd = check_with("bootstrap-without-database", "initdb")
    call = {"type": "tool_use", "id": "t0", "name": "bash", "input": {"command": None}}
    session = {"messages": [{"role": "assistant", "content": [call]}]}
    (tmp_path / "session.json").write_text(json.dumps(session))
    assert run_here(cmd, tmp_path, tmp_path) == 0


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
