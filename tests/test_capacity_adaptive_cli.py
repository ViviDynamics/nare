"""The adaptive capacity flags, the result fields and the contract (issue 289)."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from nare.capacity import LocalStore, SharedStore
from nare.cli import build_parser, capacity_for_run, main, validate_run_args
from nare.contract import describe
from nare.transport import declared_concurrency
from nare.transport.openai import OpenAITransport
from test_capacity_queue import Marked, answered, refusal, rows


class Declared(Marked):
    """A transport whose provider declares a route limit of two."""

    def declared_concurrency(self) -> int | None:
        return 2


def adaptive(*extra: str) -> list[str]:
    return [
        "run",
        "--yes",
        "--jsonl",
        "--capacity-queue",
        "--capacity-adaptive",
        *extra,
    ]


def test_adaptive_is_off_by_default() -> None:
    args = build_parser().parse_args(["run", "go"])
    assert args.capacity_adaptive is False
    assert args.capacity_ceiling is None
    assert args.capacity_shared_dir is None


def test_the_environment_turns_adaptive_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("NARE_CAPACITY_ADAPTIVE", "1")
    monkeypatch.setenv("NARE_CAPACITY_CEILING", "6")
    monkeypatch.setenv("NARE_CAPACITY_SHARED_DIR", str(tmp_path))
    args = build_parser().parse_args(["run", "go"])
    assert args.capacity_adaptive is True
    assert args.capacity_ceiling == 6
    assert args.capacity_shared_dir == str(tmp_path)


@pytest.mark.parametrize(
    "argv",
    [
        ["--capacity-adaptive"],
        ["--capacity-adaptive", "--capacity-queue", "--capacity-ceiling", "0"],
        [
            "--capacity-adaptive",
            "--capacity-queue",
            "--capacity-floor",
            "3",
            "--capacity-ceiling",
            "2",
        ],
        ["--capacity-adaptive", "--capacity-queue", "--capacity-quiet-seconds", "-1"],
    ],
)
def test_an_invalid_adaptive_option_exits_two(argv: list[str]) -> None:
    assert main(["run", "--yes", "--jsonl", *argv, "go"], transport=Marked([])) == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["--capacity-queue", "--capacity-ceiling", "4"],
        ["--capacity-queue", "--capacity-floor", "1"],
        ["--capacity-queue", "--capacity-quiet-seconds", "30"],
        ["--capacity-queue", "--capacity-shared-dir", "/tmp/x"],
    ],
)
def test_adaptive_only_flags_need_adaptive(argv: list[str]) -> None:
    assert main(["run", "--yes", "--jsonl", *argv, "go"], transport=Marked([])) == 2


def test_the_result_carries_the_limit_after_a_refusal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = Marked([[refusal(retry_after="0")], [answered()]])
    assert main(adaptive("go"), transport=fake) == 0
    out = rows(capsys.readouterr().out)
    result = out[-1]
    assert result["status"] == "done"
    assert result["capacity_refusals"] == 1
    assert result["capacity_limit"] == 4
    assert result["capacity_slot_wait_seconds"] == 0.0
    cut = [
        row["detail"]["capacity_limit"]
        for row in out
        if row.get("type") == "progress" and "capacity_limit" in row.get("detail", {})
    ]
    assert {"limit": 4, "previous": 8, "reason": "refusal"} in cut


def test_the_result_reports_no_limit_without_adaptive(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = Marked([[answered()]])
    assert main(["run", "--yes", "--jsonl", "go"], transport=fake) == 0
    result = rows(capsys.readouterr().out)[-1]
    assert result["capacity_limit"] is None
    assert result["capacity_slot_wait_seconds"] == 0


def test_a_shared_directory_carries_the_limit_to_the_next_process(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    shared = str(tmp_path / "shared")
    first = Marked([[refusal(retry_after="0")], [answered()]])
    assert main(adaptive("--capacity-shared-dir", shared, "go"), transport=first) == 0
    capsys.readouterr()
    # A later invocation, with no memory of the first, starts at the cut limit:
    # the quiet period has not passed.
    second = Marked([[answered()]])
    assert main(adaptive("--capacity-shared-dir", shared, "go"), transport=second) == 0
    assert rows(capsys.readouterr().out)[-1]["capacity_limit"] == 4


def test_a_declared_limit_starts_the_run_below_the_ceiling(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(adaptive("go"), transport=Declared([[answered()]])) == 0
    out = rows(capsys.readouterr().out)
    assert out[-1]["capacity_limit"] == 2
    notes = [
        row
        for row in out
        if row.get("type") == "progress"
        and row.get("detail", {}).get("capacity_limit", {}).get("reason") == "declared"
    ]
    assert len(notes) == 1


def test_capacity_for_run_attaches_a_local_limiter_by_default(tmp_path: Path) -> None:
    args = _validated(["--capacity-queue", "--capacity-adaptive", "go"])
    queue = capacity_for_run(args, Marked([]))
    assert queue is not None
    assert queue.limiter is not None
    assert isinstance(queue.limiter._store, LocalStore)


def test_capacity_for_run_uses_a_shared_store_when_a_directory_is_named(
    tmp_path: Path,
) -> None:
    args = _validated(
        [
            "--capacity-queue",
            "--capacity-adaptive",
            "--capacity-shared-dir",
            str(tmp_path),
            "go",
        ]
    )
    queue = capacity_for_run(args, Marked([]))
    assert queue is not None and queue.limiter is not None
    assert isinstance(queue.limiter._store, SharedStore)


def test_capacity_for_run_without_adaptive_attaches_nothing() -> None:
    args = _validated(["--capacity-queue", "go"])
    queue = capacity_for_run(args, Marked([]))
    assert queue is not None
    assert queue.limiter is None
    assert capacity_for_run(_validated(["go"]), Marked([])) is None


def test_the_openai_rail_declares_no_limit() -> None:
    transport = OpenAITransport(model="m", api_key="test-key")
    assert declared_concurrency(transport) is None
    assert declared_concurrency(Declared([])) == 2
    assert declared_concurrency(object()) is None


def test_the_contract_advertises_adaptive_capacity() -> None:
    entry = describe()["capacity_adaptive"]
    assert entry["flags"] == {
        "enable": "--capacity-adaptive",
        "ceiling": "--capacity-ceiling",
        "floor": "--capacity-floor",
        "quiet_seconds": "--capacity-quiet-seconds",
        "shared_dir": "--capacity-shared-dir",
    }
    assert entry["default"]["enable"] is False
    assert entry["default"]["shared_dir"] is None
    assert entry["providers"] == ["openai"]
    assert entry["event_detail"] == "capacity_limit"
    flags = describe()["flags"]
    assert flags["--capacity-adaptive"] == {"takes_value": False, "choices": None}
    assert flags["--capacity-shared-dir"]["takes_value"] is True


def _validated(argv: list[str]) -> argparse.Namespace:
    """A run's arguments as main() leaves them: parsed, then validated."""
    args = build_parser().parse_args(["run", *argv])
    validate_run_args(args)
    return args
