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
    body = GOOD.replace("max_turns = 12", "max_turns = 12\nreps = 9\ntimeout = 30")
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
    write_case(
        tmp_path,
        "aaa-full-case",
        GOOD.replace('tier = "smoke"', 'tier = "full"').replace(
            'id = "fix-failing-test"', 'id = "aaa-full-case"'
        ),
    )
    assert [c.id for c in load_cases(tmp_path)] == [
        "aaa-full-case",
        "fix-failing-test",
    ]
    assert [c.id for c in load_cases(tmp_path, tier="smoke")] == ["fix-failing-test"]


def test_load_cases_on_an_empty_root_returns_nothing(tmp_path: Path) -> None:
    assert load_cases(tmp_path) == []
