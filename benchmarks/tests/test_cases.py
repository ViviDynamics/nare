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
