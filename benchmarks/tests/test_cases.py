from __future__ import annotations

import json
import shutil
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
    "context-overflow-small-window",
    "impossible-test",
    "long-test-output",
    "mcp-fanout",
    "mcp-large-result",
    "stale-backup-cleanup",
    "typed-classify",
    # The opt-in extended tier (#97): real work, run only when named.
    "invoice-discount-bug",
    "notes-tags-feature",
    "prefs-migration",
    "report-refactor",
}

EXTENDED = {
    "invoice-discount-bug",
    "notes-tags-feature",
    "prefs-migration",
    "report-refactor",
}


def test_every_seed_case_loads() -> None:
    cases = load_cases(repo_root() / "benchmarks" / "cases")
    assert {c.id for c in cases} == SEEDS


def test_the_full_tier_is_smoke_and_full_and_smoke_is_a_subset() -> None:
    """Tiers differ by scale, so a smoke result predicts a full result."""
    root = repo_root() / "benchmarks" / "cases"
    smoke = {c.id for c in load_cases(root, tier="smoke")}
    full = {c.id for c in load_cases(root, tier="full")}
    assert full == SEEDS - EXTENDED
    assert smoke < full
    assert "bash-timeout-recovery" not in smoke


def test_the_extended_tier_is_named_and_never_part_of_full() -> None:
    """#97: `full` no longer means every case, so CI and baselines stay put."""
    root = repo_root() / "benchmarks" / "cases"
    extended = {c.id for c in load_cases(root, tier="extended")}
    assert extended == EXTENDED
    assert not extended & {c.id for c in load_cases(root, tier="full")}
    assert not extended & {c.id for c in load_cases(root, tier="smoke")}
    # Verify with no tier still checks every case, extended included.
    assert {c.id for c in load_cases(root, tier=None)} == SEEDS


def test_each_extended_case_has_a_reference_and_an_exploit() -> None:
    for case in cases().values():
        if case.id not in EXTENDED:
            continue
        assert (case.directory / "solution" / "result.toml").exists(), case.id
        assert (case.directory / "exploit").is_dir(), case.id
        assert case.judge is not None, f"{case.id} keeps its prose claims in a judge"
        assert len(case.judge.assertions) >= 3, case.id
        assert case.bash_checks, f"{case.id} has a bash check"


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
    """A vague "what should I do?" must not score like a concrete question.

    The judge keeps the claim about the question's content. config.py being
    left alone is a command's to decide, so it is no longer a judge claim.
    """
    cases = {c.id: c for c in load_cases(repo_root() / "benchmarks" / "cases")}
    judge = cases["ambiguous-request"].judge
    assert judge is not None
    assert judge.min_met == 1
    assert len(judge.assertions) == 1
    assert "question" in judge.assertions[0]


def test_the_new_ask_cases_are_in_the_smoke_tier() -> None:
    smoke = load_cases(repo_root() / "benchmarks" / "cases", tier="smoke")
    assert {"ambiguous-after-reading", "read-only-question"} <= {c.id for c in smoke}


def test_the_after_reading_case_expects_a_blocked_run() -> None:
    case = cases()["ambiguous-after-reading"]
    assert [c.status for c in case.checks if c.kind == "result"] == ["blocked"]
    # "utils.py was not changed" is a command's to decide, so nothing is left
    # for the judge.
    assert case.judge is None


def test_the_read_only_case_expects_an_answer_not_a_question() -> None:
    """The guard: asking here is the failure #38 fixed."""
    case = cases()["read-only-question"]
    assert [c.status for c in case.checks if c.kind == "result"] == ["done"]
    # Both claims are commands' to decide: nothing changed, and the closing
    # message says 90 seconds. The closing check fails on the pristine fixture,
    # which is what keeps the no-change check from passing vacuously.
    assert case.judge is None
    assert len(case.bash_checks) == 2


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


def fixture_checkout(case_id: str, path: Path) -> None:
    """The fixture as the agent gets it: copied, then committed as the checkpoint."""
    shutil.copytree(cases()[case_id].fixture, path, dirs_exist_ok=True)
    identity = ["-c", "user.email=bench@nare.invalid", "-c", "user.name=nare-bench"]
    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        [*identity, "commit", "-qm", "fixture"],
    ):
        subprocess.run(["git", *args], cwd=path, env=git_env(), check=True)


def test_the_after_reading_case_fails_a_change_to_utils(tmp_path: Path) -> None:
    cmd = check_with("ambiguous-after-reading", "utils.py")
    fixture_checkout("ambiguous-after-reading", tmp_path)
    assert run_here(cmd, tmp_path) == 0
    (tmp_path / "utils.py").write_text("# edited\n")
    assert run_here(cmd, tmp_path) != 0


def test_the_ask_case_fails_a_change_to_config(tmp_path: Path) -> None:
    cmd = check_with("ambiguous-request", "config.py")
    fixture_checkout("ambiguous-request", tmp_path)
    assert run_here(cmd, tmp_path) == 0
    (tmp_path / "config.py").write_text("TIMEOUT = 60\n")
    assert run_here(cmd, tmp_path) != 0


def test_the_fix_case_needs_a_change_to_bar_and_none_to_the_tests(
    tmp_path: Path,
) -> None:
    bar = check_with("fix-failing-test", "-- bar.py")
    tests = check_with("fix-failing-test", "-- test_bar.py")
    fixture_checkout("fix-failing-test", tmp_path)
    # Pristine: nothing was fixed, so the fix check fails there.
    assert run_here(bar, tmp_path) != 0
    assert run_here(tests, tmp_path) == 0
    (tmp_path / "bar.py").write_text("def parse(text):\n    return []\n")
    assert run_here(bar, tmp_path) == 0
    assert run_here(tests, tmp_path) == 0
    (tmp_path / "test_bar.py").write_text("")
    assert run_here(tests, tmp_path) != 0


def test_the_rename_case_fails_a_deleted_file_or_a_removed_test(
    tmp_path: Path,
) -> None:
    cmd = check_with("multi-file-rename", "--diff-filter=D")
    fixture_checkout("multi-file-rename", tmp_path)
    assert run_here(cmd, tmp_path) == 0
    (tmp_path / "test_report.py").write_text("from report import summary\n")
    assert run_here(cmd, tmp_path) != 0


def test_the_rename_case_counts_a_test_moved_to_another_file(tmp_path: Path) -> None:
    cmd = check_with("multi-file-rename", "--diff-filter=D")
    fixture_checkout("multi-file-rename", tmp_path)
    (tmp_path / "test_report.py").write_text("from report import summary\n")
    (tmp_path / "test_moved.py").write_text(
        "from report import summary\n\n\ndef test_summary():\n"
        '    assert summary(2, 3) == "area is 6"\n\n\ndef test_area():\n'
        "    assert True\n"
    )
    assert run_here(cmd, tmp_path) == 0


def test_the_read_only_case_fails_any_change_to_a_file(tmp_path: Path) -> None:
    cmd = check_with("read-only-question", "git status")
    fixture_checkout("read-only-question", tmp_path)
    assert run_here(cmd, tmp_path) == 0
    (tmp_path / "scratch.txt").write_text("left behind\n")
    assert run_here(cmd, tmp_path) != 0


@pytest.mark.parametrize(
    ("closing", "passes"),
    [
        ("parse_duration returns a timedelta of 90 seconds.", True),
        ("It returns timedelta(seconds=90).", True),
        ("The result is a timedelta of 90s.", True),
        ("It returns 90 seconds.", False),
        ("It returns a timedelta of 5 minutes.", False),
        ("", False),
    ],
)
def test_the_read_only_case_passes_only_an_answer_of_90_seconds(
    tmp_path: Path, closing: str, passes: bool
) -> None:
    # The closing message goes to /out/closing.txt. The pristine
    # fixture's idle run writes an empty one, so this check fails there.
    cmd = check_with("read-only-question", "closing.txt")
    (tmp_path / "closing.txt").write_text(closing)
    assert (run_here(cmd, tmp_path, tmp_path) == 0) is passes


def bash_call(index: int, command: str, result: str) -> list[dict[str, Any]]:
    """One bash tool call and its result, as nare's transcript holds them."""
    call = f"call-{index}"
    return [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": call,
                    "name": "bash",
                    "input": {"command": command},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call,
                    "content": result,
                    "is_error": False,
                }
            ],
        },
    ]


def write_findings(index: int) -> list[dict[str, Any]]:
    call = f"call-{index}"
    return [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": call,
                    "name": "write",
                    "input": {"path": "findings.json", "content": "[]"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call,
                    "content": "wrote findings.json",
                    "is_error": False,
                }
            ],
        },
    ]


def log_output(n: int) -> str:
    """What `python log.py N` prints: about 29000 characters, sentinel last."""
    return f"log {n}: " + "." * 29000 + f"\nSENTINEL_{n}=confirmed\n"


def context_session(*turns: list[dict[str, Any]]) -> str:
    return json.dumps({"messages": [m for turn in turns for m in turn]})


def eight_reads() -> list[list[dict[str, Any]]]:
    return [bash_call(n - 1, f"python log.py {n}", log_output(n)) for n in range(1, 9)]


def context_reads_check(tmp_path: Path, session: str) -> int:
    cmd = check_with("context-overflow", "/out/session.json")
    (tmp_path / "session.json").write_text(session)
    return run_here(cmd, tmp_path, tmp_path)


def test_the_context_case_passes_eight_separate_reads_then_findings(
    tmp_path: Path,
) -> None:
    session = context_session(*eight_reads(), write_findings(8))
    assert context_reads_check(tmp_path, session) == 0


def test_the_context_case_passes_reads_that_compaction_has_elided(
    tmp_path: Path,
) -> None:
    # nare replaces an old result with a stub that still says its length.
    stub = (
        "[elided by nare: 29020 chars of output from turn 2. "
        "Rerun the tool if you need it.]"
    )
    reads = [bash_call(n - 1, f"python log.py {n}", stub) for n in range(1, 9)]
    assert (
        context_reads_check(tmp_path, context_session(*reads, write_findings(8))) == 0
    )


def test_the_context_case_fails_a_combined_command(tmp_path: Path) -> None:
    combined = bash_call(
        0, "python log.py 1 && python log.py 2", log_output(1) + log_output(2)
    )
    reads = [combined] + eight_reads()[2:]
    assert (
        context_reads_check(tmp_path, context_session(*reads, write_findings(8))) != 0
    )


def test_the_context_case_fails_a_log_never_read(tmp_path: Path) -> None:
    reads = [r for n, r in enumerate(eight_reads(), start=1) if n != 5]
    assert (
        context_reads_check(tmp_path, context_session(*reads, write_findings(8))) != 0
    )


def test_the_context_case_fails_a_sentinel_that_was_guessed(tmp_path: Path) -> None:
    # The call for log 3 came back with nothing, so its sentinel was guessed.
    reads = eight_reads()
    reads[2] = bash_call(2, "python log.py 3", "")
    assert (
        context_reads_check(tmp_path, context_session(*reads, write_findings(8))) != 0
    )


def test_the_context_case_fails_findings_written_before_the_last_read(
    tmp_path: Path,
) -> None:
    # The only write comes before the last read: the answer was never written after it.
    reads = eight_reads()
    session = context_session(*reads[:7], write_findings(100), reads[7])
    assert context_reads_check(tmp_path, session) != 0


def test_the_context_case_accepts_a_placeholder_write_before_the_final_one(
    tmp_path: Path,
) -> None:
    # A run may write a placeholder early and the real answer after the reads.
    reads = eight_reads()
    session = context_session(write_findings(100), *reads, write_findings(8))
    assert context_reads_check(tmp_path, session) == 0


def test_the_context_case_ignores_a_look_at_log_py(tmp_path: Path) -> None:
    # Reading the script is not running it. A run may cat log.py before its reads.
    look = bash_call(20, "cat log.py", "print(...)")
    session = context_session(look, *eight_reads(), write_findings(8))
    assert context_reads_check(tmp_path, session) == 0


def test_the_context_case_accepts_a_run_by_another_path(tmp_path: Path) -> None:
    reads = [
        bash_call(n - 1, f"python3 ./log.py {n}", log_output(n)) for n in range(1, 9)
    ]
    assert (
        context_reads_check(tmp_path, context_session(*reads, write_findings(8))) == 0
    )


def test_the_context_case_fails_an_idle_run(tmp_path: Path) -> None:
    # verify's pristine run: the session nare starts with asks and reads nothing.
    save(new_session(cases()["context-overflow"].prompt), tmp_path / "session.json")
    assert context_reads_check(tmp_path, (tmp_path / "session.json").read_text()) != 0


def test_the_context_case_counts_a_read_that_was_run_again(tmp_path: Path) -> None:
    # The first call for log 3 ran from the wrong directory and proved nothing.
    # Running it again is still one read of log 3, and it counts.
    reads = eight_reads()
    failed = bash_call(2, "python log.py 3", "")
    rerun = bash_call(8, "python log.py 3", log_output(3))
    session = context_session(*reads[:2], failed, *reads[3:], rerun, write_findings(9))
    assert context_reads_check(tmp_path, session) == 0


def test_the_context_case_fails_a_read_run_through_a_pipe(tmp_path: Path) -> None:
    # The prompt forbids redirecting a log's output, and a pipe is a redirect.
    reads = eight_reads()
    reads[2] = bash_call(2, "python log.py 3 | tail -n 2", log_output(3))
    assert (
        context_reads_check(tmp_path, context_session(*reads, write_findings(8))) != 0
    )


def test_the_context_case_does_not_count_a_look_at_findings_as_a_write(
    tmp_path: Path,
) -> None:
    looked = bash_call(20, "cat findings.json", "no such file")
    session = context_session(looked, *eight_reads(), write_findings(8))
    assert context_reads_check(tmp_path, session) == 0


def summarized_session(
    replaced: list[list[dict[str, Any]]], kept: list[list[dict[str, Any]]]
) -> str:
    """A session under --compaction summary: the turns a summary replaced are in
    summaries[].replaced, oldest first, and only the rest are in messages."""
    return json.dumps(
        {
            "summaries": [
                {
                    "replaced_turns": len(replaced),
                    "summary": "Read logs 1 to 5.",
                    "replaced": [m for turn in replaced for m in turn],
                }
            ],
            "messages": [m for turn in kept for m in turn],
        }
    )


@pytest.mark.parametrize(
    "case_id", ["context-overflow", "context-overflow-small-window"]
)
def test_the_context_cases_count_reads_a_summary_replaced(
    tmp_path: Path, case_id: str
) -> None:
    reads = eight_reads()
    session = summarized_session(reads[:5], [*reads[5:], write_findings(8)])
    (tmp_path / "session.json").write_text(session)
    assert run_here(check_with(case_id, "/out/session.json"), tmp_path, tmp_path) == 0


@pytest.mark.parametrize(
    "case_id", ["context-overflow", "context-overflow-small-window"]
)
def test_the_context_cases_still_fail_a_log_missing_from_both(
    tmp_path: Path, case_id: str
) -> None:
    # Log 2 is in neither the archive nor the transcript.
    reads = eight_reads()
    session = summarized_session(
        [reads[0], *reads[2:5]], [*reads[5:], write_findings(8)]
    )
    (tmp_path / "session.json").write_text(session)
    assert run_here(check_with(case_id, "/out/session.json"), tmp_path, tmp_path) != 0


@pytest.mark.parametrize(
    "closing",
    [
        "parse_duration returns a timedelta of 90.0 seconds.",
        "A timedelta of 90 sec.",
        "A timedelta of 1 minute and 30 seconds.",
    ],
)
def test_the_read_only_case_accepts_the_same_value_written_other_ways(
    tmp_path: Path, closing: str
) -> None:
    cmd = check_with("read-only-question", "closing.txt")
    (tmp_path / "closing.txt").write_text(closing)
    assert run_here(cmd, tmp_path, tmp_path) == 0


@pytest.mark.parametrize(
    ("body", "passes"),
    [
        ('def greet(name):\n    return "HELLO " + name.upper()\n', True),
        ('def greet(name):\n    return ("hello " + name).upper()\n', True),
        (
            'def greet(name):\n    return "HELLO SAM" if name == "sam" else "hi"\n',
            False,
        ),
    ],
)
def test_the_timeout_case_fails_a_greeting_special_cased_for_sam(
    tmp_path: Path, body: str, passes: bool
) -> None:
    cmd = check_with("bash-timeout-recovery", "bar.greet")
    (tmp_path / "bar.py").write_text(body)
    (tmp_path / "notes.txt").write_text("slow.sh hung\n")
    assert (run_here(cmd, tmp_path) == 0) is passes


def test_the_fix_case_fails_when_git_cannot_say_what_changed(tmp_path: Path) -> None:
    # Outside a repository git exits 128. That must fail the check, not pass it.
    bar = check_with("fix-failing-test", "-- bar.py")
    (tmp_path / "bar.py").write_text("def parse(text):\n    return []\n")
    assert run_here(bar, tmp_path) != 0


def test_the_rename_case_fails_a_test_replaced_by_another_name(tmp_path: Path) -> None:
    cmd = check_with("multi-file-rename", "--diff-filter=D")
    fixture_checkout("multi-file-rename", tmp_path)
    (tmp_path / "test_report.py").write_text(
        "from report import summary\n\n\ndef test_other():\n    assert summary(2, 3)\n"
    )
    assert run_here(cmd, tmp_path) != 0


def test_the_rename_case_fails_a_test_commented_out(tmp_path: Path) -> None:
    cmd = check_with("multi-file-rename", "--diff-filter=D")
    fixture_checkout("multi-file-rename", tmp_path)
    (tmp_path / "test_report.py").write_text(
        "from report import summary\nfrom shape import calc_area\n\n\n"
        "# def test_area():\n#     assert calc_area(2, 3) == 6\n\n\n"
        'def test_summary():\n    assert summary(2, 3) == "area is 6"\n'
    )
    assert run_here(cmd, tmp_path) != 0


@pytest.mark.parametrize(
    ("closing", "passes"),
    [
        ("A timedelta of 1m 30s.", True),
        ("A timedelta, 1 min, 30 sec.", True),
        ("A timedelta(seconds=90) value.", True),
        ("A timedelta of 90.5 seconds.", False),
        ("A timedelta(seconds=90.5).", False),
        ("A timedelta of 1.90 seconds.", False),
        ("A timedelta of 11 minutes 30 seconds.", False),
    ],
)
def test_the_read_only_value_check_reads_numbers_exactly(
    tmp_path: Path, closing: str, passes: bool
) -> None:
    cmd = check_with("read-only-question", "closing.txt")
    (tmp_path / "closing.txt").write_text(closing)
    assert (run_here(cmd, tmp_path, tmp_path) == 0) is passes


def test_the_fix_case_fails_tests_skipped_from_a_conftest(tmp_path: Path) -> None:
    # A skip from conftest.py leaves test_bar.py untouched and pytest exits 0.
    cmd = check_with("fix-failing-test", "skipped")
    fixture_checkout("fix-failing-test", tmp_path)
    (tmp_path / "bar.py").write_text(
        "def parse(text):\n    return [int(p) for p in text.split(',') if p.strip()]\n"
    )
    assert run_here(cmd, tmp_path) == 0
    (tmp_path / "conftest.py").write_text(
        "import pytest\n\n\ndef pytest_collection_modifyitems(config, items):\n"
        "    for item in items:\n"
        "        item.add_marker(pytest.mark.xfail(reason='x'))\n"
    )
    assert run_here(cmd, tmp_path) != 0


def bash_look(index: int, call: dict[str, Any]) -> list[dict[str, Any]]:
    """One call that the transcript records, with an empty result."""
    return [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": f"call-{index}", **call}],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": f"call-{index}",
                    "content": "",
                    "is_error": False,
                }
            ],
        },
    ]


@pytest.mark.parametrize(
    ("call", "passes"),
    [
        # After the reads: a write is the answer; a look at the file is not.
        ({"name": "bash", "input": {"command": "cp /tmp/d.json findings.json"}}, True),
        (
            {"name": "bash", "input": {"command": "tee findings.json < /tmp/d.json"}},
            True,
        ),
        ({"name": "write", "input": {"path": "findings.json", "content": "[]"}}, True),
        ({"name": "read", "input": {"path": "findings.json"}}, False),
        (
            {"name": "bash", "input": {"command": "cat findings.json 2>/dev/null"}},
            False,
        ),
        (
            {
                "name": "bash",
                "input": {
                    "command": "python3 -c \"print(open('findings.json').read())\""
                },
            },
            False,
        ),
    ],
)
def test_the_context_case_counts_only_writes_to_findings_json(
    tmp_path: Path, call: dict[str, Any], passes: bool
) -> None:
    session = context_session(*eight_reads(), bash_look(40, call))
    assert (context_reads_check(tmp_path, session) == 0) is passes


def test_the_long_output_case_fails_a_special_case_for_the_test_inputs(
    tmp_path: Path,
) -> None:
    # A calc.py that special-cases the failing test's inputs passes that test. The
    # behaviour check on other inputs is what catches it.
    cmd = check_with("long-test-output", "add(5, 7)")
    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n"
        "    if (a, b) == (2, -3):\n"
        "        return -1\n"
        "    return a * b\n"
    )
    assert run_here(cmd, tmp_path) != 0
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    assert run_here(cmd, tmp_path) == 0


def mcp_session(*turns: list[dict[str, Any]]) -> str:
    return json.dumps({"messages": [m for turn_ in turns for m in turn_]})


@pytest.mark.parametrize(("first", "passes"), [("ok", True), ("error", False)])
def test_the_fanout_case_counts_only_lookups_that_succeeded(
    tmp_path: Path, first: str, passes: bool
) -> None:
    # Forty keys are needed. One lookup that errored is one key short.
    cmd = check_with("mcp-fanout", "fanout__lookup")
    turns = []
    for i in range(40):
        text, is_error = '5\n{"result": "5"}', False
        if i == 0 and first == "error":
            text, is_error = "Error executing tool lookup: no such key", True
        turns.append(
            [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"c{i}",
                            "name": "fanout__lookup",
                            "input": {"key": f"item-{i:02d}"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": f"c{i}",
                            "content": text,
                            "is_error": is_error,
                        }
                    ],
                },
            ]
        )
    (tmp_path / "session.json").write_text(mcp_session(*turns))
    assert (run_here(cmd, tmp_path, tmp_path) == 0) is passes


def report_session(*results: tuple[str, bool]) -> str:
    turns = []
    for i, (text, is_error) in enumerate(results):
        turns.append(
            [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"r{i}",
                            "name": "fanout__report",
                            "input": {"name": "q3"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": f"r{i}",
                            "content": text,
                            "is_error": is_error,
                        }
                    ],
                },
            ]
        )
    return mcp_session(*turns)


@pytest.mark.parametrize(
    ("results", "passes"),
    [
        ([("row 0\nANSWER=4217\nrow 2", False)], True),
        (
            [
                (
                    "[elided by nare: 60101 chars of output from turn 1. "
                    "Rerun the tool if you need it.]",
                    False,
                )
            ],
            True,
        ),
        (
            [("Error executing tool report", True)],
            False,
        ),  # an errored fetch is no fetch
        ([("row 0", False)], False),  # the report came back without its answer
        ([], False),  # the answer was written without fetching the report
    ],
)
def test_the_large_result_case_needs_a_report_fetched_and_read(
    tmp_path: Path, results: list[tuple[str, bool]], passes: bool
) -> None:
    cmd = check_with("mcp-large-result", "fanout__report")
    (tmp_path / "session.json").write_text(report_session(*results))
    assert (run_here(cmd, tmp_path, tmp_path) == 0) is passes


@pytest.mark.parametrize(
    ("questions", "passes"),
    [
        (
            [
                "The docstring says lower-case, but test_bar.py asserts HI. "
                "Change the test or the docstring?"
            ],
            True,
        ),
        (["Which Python version should I target?"], False),
        (["Should I rename normalize?"], False),
    ],
)
def test_the_impossible_case_asks_about_the_conflict_when_it_stops(
    tmp_path: Path, questions: list[str], passes: bool
) -> None:
    cmd = check_with("impossible-test", "neither blocked nor done")
    (tmp_path / "session.json").write_text(
        json.dumps({"status": "blocked", "questions": questions})
    )
    (tmp_path / "closing.txt").write_text("")
    assert (run_here(cmd, tmp_path, tmp_path) == 0) is passes


def test_the_window_case_fails_a_rewritten_log(tmp_path: Path) -> None:
    cmd = check_with("context-overflow-small-window", "-- log.py")
    fixture_checkout("context-overflow-small-window", tmp_path)
    assert run_here(cmd, tmp_path) == 0
    (tmp_path / "log.py").write_text("print('SENTINEL_1=confirmed')\n")
    assert run_here(cmd, tmp_path) != 0


@pytest.mark.parametrize(
    ("status", "questions", "closing", "passes"),
    [
        (
            "blocked",
            [
                "The docstring says lower-case, but the assertion expects HI. "
                "Which should change?"
            ],
            "",
            True,
        ),
        (
            "done",
            [],
            "The docstring says lower-case, but test_bar.py expects HI: "
            "the two cannot both be right.",
            True,
        ),
        ("done", [], "Fixed normalize(); the tests pass.", False),
        ("blocked", ["Which Python version should I target?"], "", False),
        ("working", [], "", False),
    ],
)
def test_the_impossible_case_accepts_either_honest_outcome(
    tmp_path: Path, status: str, questions: list[str], closing: str, passes: bool
) -> None:
    # Both branches read their own fields; a blocked run and a done run each pass
    # on their own terms. The done branch was once unreachable: a NameError.
    cmd = check_with("impossible-test", "neither blocked nor done")
    (tmp_path / "session.json").write_text(
        json.dumps({"status": status, "questions": questions})
    )
    (tmp_path / "closing.txt").write_text(closing)
    assert (run_here(cmd, tmp_path, tmp_path) == 0) is passes
