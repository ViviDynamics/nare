"""Exercise the shipped CLI adapter in a fresh process, with an offline rail."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

PROBE = Path(__file__).with_name("budget_probe.py")


def text(text: str = "done", **kwargs: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], **kwargs}


def read(path: Path, call_id: str, partial: str | None = None) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if partial is not None:
        content.append({"type": "text", "text": partial})
    content.append(
        {
            "type": "tool_use",
            "id": call_id,
            "name": "read",
            "input": {"path": str(path)},
        }
    )
    return {
        "content": content,
        "tool_calls": [{"id": call_id, "name": "read", "args": {"path": str(path)}}],
        "stop_reason": "tool_use",
    }


def invoke(
    tmp_path: Path,
    replies: list[dict[str, Any]],
    *args: str,
    env: dict[str, str] | None = None,
    measure_input: bool = False,
) -> tuple[
    subprocess.CompletedProcess[str], list[dict[str, Any]], list[dict[str, Any]]
]:
    script = tmp_path / "script.json"
    script.write_text(json.dumps({"replies": replies, "measure_input": measure_input}))
    calls = script.with_suffix(".calls.json")
    calls.unlink(missing_ok=True)
    process_env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("NARE_BUDGET_", "NARE_PRICE_", "NARE_CONTEXT_"))
    }
    process_env.update(env or {})
    proc = subprocess.run(
        [sys.executable, str(PROBE), str(script), "run", "--yes", "--jsonl", *args],
        capture_output=True,
        text=True,
        env=process_env,
    )
    return (
        proc,
        [json.loads(line) for line in proc.stdout.splitlines()],
        json.loads(calls.read_text()) if calls.exists() else [],
    )


def test_max_tokens_probe_and_cumulative_stop(tmp_path: Path) -> None:
    source = tmp_path / "review.py"
    source.write_text("print('review evidence')")
    replies = [read(source, "c1"), read(source, "c2"), text()]
    baseline, lines, calls = invoke(tmp_path, replies, "go", "--max-tokens", "5")
    assert baseline.returncode == 0
    assert lines[-1]["usage"]["input"] == 30
    assert lines[-1]["usage"]["output"] == 15
    assert [c["max_tokens"] for c in calls] == [5, 5, 5]
    session = tmp_path / "session.json"
    stopped, lines, calls = invoke(
        tmp_path,
        replies,
        "go",
        "--max-tokens",
        "5",
        "--budget-tokens",
        "25",
        "--session",
        str(session),
    )
    assert stopped.returncode == 1
    assert len(calls) == 2
    result = lines[-1]
    assert (result["status"], result["stop_reason"], result["turns"]) == (
        "error",
        "budget",
        2,
    )
    assert result["usage"]["input"] == 20
    assert result["usage"]["output"] == 10
    assert result["budget"]["tokens"] == 25
    error = next(e for e in lines if e["type"] == "error")
    assert error["detail"]["budget"]["used_tokens"] == 30
    assert error["detail"]["budget"]["tokens"] == 25
    saved = json.loads(session.read_text())
    assert saved["usage"] == result["usage"]
    assert saved["budget"]["tokens"] == 25
    assert [
        b["tool_use_id"]
        for m in saved["messages"]
        for b in m["content"]
        if b["type"] == "tool_result"
    ] == ["c1", "c2"]
    assert len([e for e in lines if e["type"] == "cost"]) == 2


def test_resume_refuses_exhausted_limit_and_continues_with_larger_limit(
    tmp_path: Path,
) -> None:
    source = tmp_path / "code.py"
    source.write_text("hello")
    path = tmp_path / "s.json"
    proc, _, _ = invoke(
        tmp_path,
        [read(source, "c1")],
        "go",
        "--budget-tokens",
        "15",
        "--session",
        str(path),
    )
    assert proc.returncode == 1
    for budget_args in [[], ["--budget-tokens", "15"], ["--budget-tokens", "10"]]:
        proc, lines, calls = invoke(
            tmp_path, [text()], "--resume", str(path), *budget_args
        )
        assert proc.returncode == 1
        assert calls == []
        assert lines[-1]["stop_reason"] == "budget"
        assert lines[-1]["turns"] == 1
    proc, lines, calls = invoke(
        tmp_path, [text()], "--resume", str(path), "--budget-tokens", "50"
    )
    assert proc.returncode == 0
    assert len(calls) == 1
    assert lines[-1]["usage"]["input"] == 20
    assert lines[-1]["usage"]["output"] == 10
    assert lines[-1]["budget"]["tokens"] == 50
    assert json.loads(path.read_text())["turns"] == 2


def test_done_wins_on_crossing_turn_and_keeps_actual_usage(tmp_path: Path) -> None:
    proc, lines, calls = invoke(
        tmp_path, [text('{"findings": ["confirmed"]}')], "go", "--budget-tokens", "1"
    )
    assert proc.returncode == 0
    assert len(calls) == 1
    assert lines[-1]["status"] == "done"
    assert lines[-1]["usage"]["input"] == 10
    assert lines[-1]["usage"]["output"] == 5
    assert lines[-1]["budget"]["used_tokens"] == 15
    assert lines[-1]["budget"]["tokens"] == 1
    assert not any(e["type"] == "error" for e in lines)


def test_valid_structured_partial_findings_survive_budget_and_resume_refusal(
    tmp_path: Path,
) -> None:
    schema = tmp_path / "schema.json"
    schema.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {
                    "findings": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["findings"],
                "additionalProperties": False,
            }
        )
    )
    source = tmp_path / "code.py"
    source.write_text("evidence")
    session = tmp_path / "s.json"
    partial = '{"findings": ["confirmed finding"]}'
    proc, lines, _ = invoke(
        tmp_path,
        [read(source, "c1", partial)],
        "go",
        "--budget-tokens",
        "15",
        "--schema",
        str(schema),
        "--session",
        str(session),
    )
    assert proc.returncode == 1
    assert lines[-1]["output"] == {"findings": ["confirmed finding"]}
    assert json.loads(session.read_text())["output"] == lines[-1]["output"]
    proc, lines, calls = invoke(tmp_path, [text()], "--resume", str(session))
    assert proc.returncode == 1
    assert calls == []
    assert lines[-1]["output"] == {"findings": ["confirmed finding"]}


def test_invalid_partial_is_not_promoted_to_structured_output(tmp_path: Path) -> None:
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object","required":["findings"]}')
    source = tmp_path / "code.py"
    source.write_text("evidence")
    proc, lines, _ = invoke(
        tmp_path,
        [read(source, "c1", '{"wrong": 1}')],
        "go",
        "--budget-tokens",
        "15",
        "--schema",
        str(schema),
    )
    assert proc.returncode == 1
    assert lines[-1]["output"] is None
    assert any(e["text"] == '{"wrong": 1}' for e in lines if e["type"] == "progress")


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "nan", "inf", "garbage"])
def test_invalid_token_budgets_never_start(tmp_path: Path, value: str) -> None:
    proc, lines, calls = invoke(tmp_path, [text()], "go", f"--budget-tokens={value}")
    assert proc.returncode == 2
    assert lines == []
    assert calls == []
    assert "budget" in proc.stderr


def test_budget_flag_overrides_environment(tmp_path: Path) -> None:
    source = tmp_path / "code.py"
    source.write_text("evidence")
    replies = [read(source, "c1"), text()]
    proc, lines, calls = invoke(
        tmp_path, replies, "go", env={"NARE_BUDGET_TOKENS": "15"}
    )
    assert proc.returncode == 1
    assert len(calls) == 1
    assert lines[-1]["stop_reason"] == "budget"
    proc, lines, calls = invoke(
        tmp_path,
        replies,
        "go",
        "--budget-tokens",
        "50",
        env={"NARE_BUDGET_TOKENS": "bad"},
    )
    assert proc.returncode == 0
    assert len(calls) == 2
    assert lines[-1]["budget"]["tokens"] == 50


def test_cache_categories_count_once(tmp_path: Path) -> None:
    source = tmp_path / "code.py"
    source.write_text("evidence")
    reply = read(source, "c1")
    reply["usage"] = {"input": 3, "output": 2, "cache_read": 7, "cache_write": 4}
    proc, lines, calls = invoke(
        tmp_path, [reply, text()], "go", "--budget-tokens", "16"
    )
    assert proc.returncode == 1
    assert len(calls) == 1
    assert lines[-1]["budget"]["used_tokens"] == 16


def test_provider_failure_is_distinct_from_budget(tmp_path: Path) -> None:
    proc, lines, calls = invoke(
        tmp_path, [{"error": "offline provider failed"}], "go", "--budget-tokens", "20"
    )
    assert proc.returncode == 1
    assert len(calls) == 1
    assert lines[-1]["stop_reason"] is None
    assert "provider failed" in lines[-1]["error"]


def test_blocked_wins_on_crossing_turn(tmp_path: Path) -> None:
    reply = {
        "content": [
            {
                "type": "tool_use",
                "id": "ask1",
                "name": "ask",
                "input": {"questions": ["which file?"]},
            }
        ],
        "tool_calls": [
            {"id": "ask1", "name": "ask", "args": {"questions": ["which file?"]}}
        ],
        "stop_reason": "tool_use",
    }
    proc, lines, _ = invoke(tmp_path, [reply], "go", "--budget-tokens", "1")
    assert proc.returncode == 0
    assert lines[-1]["status"] == "blocked"
    assert lines[-1]["budget"]["used_tokens"] == 15


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "bad"])
def test_invalid_windows_never_start(tmp_path: Path, value: str) -> None:
    proc, lines, calls = invoke(tmp_path, [text()], "go", f"--context-window={value}")
    assert proc.returncode == 2
    assert lines == []
    assert calls == []


def test_window_env_and_flag_precedence(tmp_path: Path) -> None:
    proc, lines, calls = invoke(
        tmp_path, [text()], "go", env={"NARE_CONTEXT_WINDOW": "1"}
    )
    assert proc.returncode == 1
    assert calls == []
    assert lines[-1]["stop_reason"] == "context"
    proc, lines, calls = invoke(
        tmp_path,
        [text()],
        "go",
        "--context-window",
        "1000",
        env={"NARE_CONTEXT_WINDOW": "bad"},
    )
    assert proc.returncode == 0
    assert len(calls) == 1
    assert lines[0]["detail"]["context_window"]["tokens"] == 1000


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "bad"])
def test_invalid_usd_budgets_never_start(tmp_path: Path, value: str) -> None:
    proc, lines, calls = invoke(tmp_path, [text()], "go", f"--budget-usd={value}")
    assert proc.returncode == 2
    assert lines == []
    assert calls == []


@pytest.mark.parametrize(
    "name",
    [
        "NARE_PRICE_IN",
        "NARE_PRICE_OUT",
        "NARE_PRICE_CACHE_READ",
        "NARE_PRICE_CACHE_WRITE",
    ],
)
@pytest.mark.parametrize("value", ["-1", "nan", "inf", "bad"])
def test_invalid_prices_never_start(tmp_path: Path, name: str, value: str) -> None:
    proc, lines, calls = invoke(tmp_path, [text()], "go", env={name: value})
    assert proc.returncode == 2
    assert lines == []
    assert calls == []
    assert name in proc.stderr


def test_usd_budget_unknown_stops_after_first_complete_tool_turn(
    tmp_path: Path,
) -> None:
    source = tmp_path / "code.py"
    source.write_text("evidence")
    proc, lines, calls = invoke(
        tmp_path, [read(source, "c1"), text()], "go", "--budget-usd", "1"
    )
    assert proc.returncode == 1
    assert len(calls) == 1
    assert lines[-1]["stop_reason"] == "budget"
    assert lines[-1]["usage"]["cost"] is None
    assert "NARE_PRICE_IN" in lines[-1]["error"]
    assert lines[-1]["budget"]["usd"] == 1


def test_priced_budget_persists_and_resume_can_raise_usd_limit(tmp_path: Path) -> None:
    source = tmp_path / "code.py"
    source.write_text("evidence")
    session = tmp_path / "s.json"
    env = {
        "NARE_PRICE_IN": "1000000",
        "NARE_PRICE_OUT": "1000000",
        "NARE_BUDGET_USD": "15",
    }
    proc, lines, calls = invoke(
        tmp_path, [read(source, "c1"), text()], "go", "--session", str(session), env=env
    )
    assert proc.returncode == 1
    assert len(calls) == 1
    assert lines[-1]["usage"]["cost"] == 15
    proc, lines, calls = invoke(tmp_path, [text()], "--resume", str(session), env=env)
    assert proc.returncode == 1
    assert calls == []
    proc, lines, calls = invoke(
        tmp_path, [text()], "--resume", str(session), "--budget-usd", "50", env=env
    )
    assert proc.returncode == 0
    assert len(calls) == 1
    assert lines[-1]["usage"]["cost"] == 30
    assert lines[-1]["budget"]["usd"] == 50


def test_done_wins_even_when_usd_cost_is_unknown(tmp_path: Path) -> None:
    proc, lines, _ = invoke(tmp_path, [text()], "go", "--budget-usd", "1")
    assert proc.returncode == 0
    assert lines[-1]["status"] == "done"
    assert lines[-1]["usage"]["cost"] is None


def test_usd_flag_overrides_invalid_environment(tmp_path: Path) -> None:
    proc, lines, _ = invoke(
        tmp_path, [text()], "go", "--budget-usd", "1", env={"NARE_BUDGET_USD": "bad"}
    )
    assert proc.returncode == 0
    assert lines[-1]["budget"]["usd"] == 1


def test_schema_valid_done_crosses_budget_after_two_tool_turns(tmp_path: Path) -> None:
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object","required":["findings"]}')
    source = tmp_path / "code.py"
    source.write_text("evidence")
    proc, lines, calls = invoke(
        tmp_path,
        [read(source, "c1"), read(source, "c2"), text('{"findings":["confirmed"]}')],
        "go",
        "--budget-tokens",
        "31",
        "--schema",
        str(schema),
    )
    assert proc.returncode == 0
    assert len(calls) == 3
    assert lines[-1]["status"] == "done"
    assert lines[-1]["output"] == {"findings": ["confirmed"]}
    assert lines[-1]["budget"]["used_tokens"] == 45
    assert lines[-1]["budget"]["tokens"] == 31


def test_exact_budget_boundary_stops_before_next_call(tmp_path: Path) -> None:
    source = tmp_path / "code.py"
    source.write_text("evidence")
    proc, lines, calls = invoke(
        tmp_path,
        [read(source, "c1"), read(source, "c2"), text()],
        "go",
        "--budget-tokens",
        "30",
    )
    assert proc.returncode == 1
    assert len(calls) == 2
    assert lines[-1]["budget"]["used_tokens"] == 30


def test_eight_large_tool_results_compact_in_actual_cli(tmp_path: Path) -> None:
    source = tmp_path / "evidence.txt"
    source.write_text("evidence " * 4000)  # read caps each result at 30000 chars
    session = tmp_path / "s.json"
    replies = [read(source, f"c{i}") for i in range(8)] + [
        text("all eight reads finished")
    ]
    proc, lines, calls = invoke(
        tmp_path,
        replies,
        "go",
        "--context-window",
        "32000",
        "--session",
        str(session),
        measure_input=True,
    )
    assert proc.returncode == 0
    assert len(calls) == 9
    assert lines[-1]["status"] == "done"
    assert any("compaction" in event.get("detail", {}) for event in lines)
    saved = json.loads(session.read_text())
    results = [
        block
        for message in saved["messages"]
        for block in message["content"]
        if block["type"] == "tool_result"
    ]
    assert len(results) == 8
    assert any(block["content"].startswith("[elided by nare:") for block in results)
    assert sum(len(block["content"]) for block in results) < 128000
    assert max(call["input_chars"] for call in calls) < 128000
