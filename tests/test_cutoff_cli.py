"""--max-cutoffs on nare run: the budget, its result fields, and its refusals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider, text_reply
from nare.cli import main
from nare.contract import describe
from nare.loop import CUTOFFS_DEFAULT
from nare.session import Usage
from nare.transport import Reply


def lines(out: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def repeating(stop_reason: str = "repetition") -> Reply:
    return Reply(
        content=[{"type": "text", "text": '{"a":[' + " | 1" * 800}],
        tool_calls=[],
        usage=Usage(input=10, output=4000),
        stop_reason=stop_reason,  # type: ignore[arg-type]
    )


def run(
    argv: list[str], replies: list[Reply], capsys: Any
) -> tuple[int, list[dict[str, Any]]]:
    code = main(["run", "--yes", "--jsonl", *argv], transport=FakeProvider(replies))
    return code, lines(capsys.readouterr().out)


def test_a_cut_off_reply_is_corrected_by_default(capsys: Any) -> None:
    code, out = run(
        ["go"],
        [text_reply("half", stop_reason="max_tokens"), text_reply("full answer")],
        capsys,
    )

    result = out[-1]
    assert code == 0
    assert result["status"] == "done"
    assert result["cutoffs"] == 1
    assert result["budget"]["cutoffs"] == CUTOFFS_DEFAULT == 2
    assert result["budget"]["used_cutoffs"] == 1
    assert any(
        line.get("detail", {}).get("cutoff", {}).get("kind") == "max_tokens"
        for line in out
        if line.get("type") == "progress"
    )


def test_zero_cut_offs_ends_the_run_at_the_first_cut(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out = run(
        ["go", "--max-cutoffs", "0"],
        [text_reply("half", stop_reason="max_tokens"), text_reply("never")],
        capsys,
    )

    result = out[-1]
    assert code == 1
    assert (result["status"], result["stop_reason"]) == ("error", "max_tokens")
    assert result["budget"]["cutoffs"] == 0
    assert result["cutoffs"] == 1


def test_a_spent_budget_ends_with_repetition_as_the_stop_reason(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out = run(
        ["go", "--max-cutoffs", "1"],
        [repeating(), repeating()],
        capsys,
    )

    result = out[-1]
    assert code == 1
    assert (result["status"], result["stop_reason"]) == ("error", "repetition")
    assert result["cutoffs"] == 2
    assert result["budget"]["used_cutoffs"] == 2


def test_the_session_file_carries_the_cut_off_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    session_path = tmp_path / "s.json"

    run(
        ["go", "--session", str(session_path)],
        [text_reply("half", stop_reason="max_tokens"), text_reply("full")],
        capsys,
    )

    assert json.loads(session_path.read_text(encoding="utf-8"))["cutoffs"] == 1


def test_a_negative_budget_is_refused_before_anything_starts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        ["run", "--yes", "--jsonl", "go", "--max-cutoffs", "-1"],
        transport=FakeProvider([]),
    )

    assert code == 2
    assert capsys.readouterr().out == ""


def test_the_contract_lists_the_flag() -> None:
    assert describe()["flags"]["--max-cutoffs"] == {
        "takes_value": True,
        "choices": None,
    }
