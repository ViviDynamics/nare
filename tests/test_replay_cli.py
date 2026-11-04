"""`nare run --provider fake --replay FILE`, the way a caller's CI runs it: a
real subprocess, with no key and no network.

The fixtures are the ones docs/replay/ publishes, so the examples a caller
copies are the ones exercised here and cannot rot.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider
from nare.cli import main
from nare.session import Usage
from nare.transport import Reply, ToolCall

FIXTURES = Path(__file__).resolve().parent.parent / "docs" / "replay"


def keyless_env() -> dict[str, str]:
    """The environment of a public runner with no secrets."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.endswith("_API_KEY") and not key.startswith("NARE_")
    }


def nare(
    *argv: str, cwd: Path
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]]]:
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from nare.cli import main; raise SystemExit(main())",
            *argv,
        ],
        capture_output=True,
        text=True,
        env=keyless_env(),
        cwd=cwd,
        timeout=30,
    )
    lines = [json.loads(line) for line in proc.stdout.splitlines() if line]
    return proc, lines


def project(tmp_path: Path) -> Path:
    """The run's root. Sessions are written beside it, never in it, so a
    listing is the same however many runs share the directory.
    """
    root = tmp_path / "project"
    if not root.exists():
        root.mkdir()
        (root / "notes.txt").write_text("hi\n", encoding="utf-8")
    return root


def replay(
    fixture: str, tmp_path: Path, *extra: str
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]], dict[str, Any]]:
    root = project(tmp_path)
    session = tmp_path / "session.json"
    proc, lines = nare(
        "run",
        "--yes",
        "--jsonl",
        "--provider",
        "fake",
        "--replay",
        str(FIXTURES / fixture),
        "--root",
        str(root),
        "--session",
        str(session),
        *extra,
        "tidy up the project",
        cwd=root,
    )
    saved = json.loads(session.read_text(encoding="utf-8")) if session.exists() else {}
    return proc, lines, saved


def test_every_published_fixture_is_exercised() -> None:
    assert sorted(p.name for p in FIXTURES.glob("*.json")) == [
        "blocked.json",
        "done.json",
        "error.json",
        "truncated.json",
    ]


def test_done_runs_its_tools_and_exits_0(tmp_path: Path) -> None:
    proc, lines, saved = replay("done.json", tmp_path)

    assert proc.returncode == 0, proc.stderr
    result = lines[-1]
    assert result["type"] == "result"
    assert result["status"] == "done"
    assert result["stop_reason"] == "end_turn"
    assert result["turns"] == 2
    assert result["usage"]["input"] == 300
    assert result["usage"]["output"] == 30
    # The list tool really ran against the root.
    tool_events = [e for e in lines[:-1] if e["type"] == "tool_use"]
    assert tool_events and "list" in tool_events[0]["text"]
    assert saved["status"] == "done"
    results = [
        block
        for message in saved["messages"]
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]
    assert "notes.txt" in json.dumps(results)


def test_blocked_carries_the_questions_and_exits_0(tmp_path: Path) -> None:
    proc, lines, saved = replay("blocked.json", tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert lines[-1]["status"] == "blocked"
    assert lines[-1]["questions"] == ["Which config file should change, dev or prod?"]
    assert saved["questions"] == lines[-1]["questions"]


def test_error_is_a_transport_failure_and_exits_1(tmp_path: Path) -> None:
    proc, lines, saved = replay("error.json", tmp_path)

    assert proc.returncode == 1, proc.stderr
    result = lines[-1]
    assert result["status"] == "error"
    assert result["stop_reason"] is None
    assert result["error"] == "RuntimeError: connection reset"
    assert result["turns"] == 1
    assert lines[-2] == {**lines[-2], "type": "error", "text": result["error"]}
    assert saved["status"] == "error"


def test_truncated_is_unfinished_and_exits_1(tmp_path: Path) -> None:
    proc, lines, saved = replay("truncated.json", tmp_path)

    assert proc.returncode == 1, proc.stderr
    result = lines[-1]
    assert result["status"] == "error"
    assert result["stop_reason"] == "max_tokens"
    assert "cut off at max_tokens" in result["error"]
    assert saved["stop_reason"] == "max_tokens"


def test_streaming_a_script_sends_the_text_live(tmp_path: Path) -> None:
    proc, lines, _ = replay("done.json", tmp_path, "--stream")

    assert proc.returncode == 0, proc.stderr
    # The loop breaks streamed text into its own chunks; the words arrive.
    progress = "".join(e["text"] for e in lines if e["type"] == "progress")
    assert "Looking at the project first." in progress


def test_running_past_the_script_ends_the_run_in_error(tmp_path: Path) -> None:
    script = tmp_path / "short.json"
    script.write_text(
        json.dumps({"replies": [{"tool_calls": [{"name": "list"}]}]}),
        encoding="utf-8",
    )
    proc, lines = nare(
        "run", "--yes", "--jsonl", "--provider", "fake", "--replay", str(script),
        "--root", str(tmp_path), "go", cwd=tmp_path,
    )  # fmt: skip

    assert proc.returncode == 1
    assert lines[-1]["status"] == "error"
    assert "no reply for turn 2" in lines[-1]["error"]


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--provider", "fake"], "--provider fake requires --replay"),
        (["--replay", "x.json"], "--replay requires --provider fake"),
        (["--provider", "fake", "--replay", "missing.json"], "missing.json"),
    ],
)
def test_a_mismatched_or_missing_script_never_starts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    message: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NARE_PROVIDER", raising=False)
    assert main(["run", "--yes", "--jsonl", *argv, "go"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err


def test_a_malformed_script_never_starts_and_names_the_reply(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = tmp_path / "bad.json"
    script.write_text(
        json.dumps({"replies": [{"text": "ok"}, {"stop_reason": "finished"}]}),
        encoding="utf-8",
    )
    code = main(
        ["run", "--yes", "--jsonl", "--provider", "fake", "--replay", str(script), "go"]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "reply 2: stop_reason" in captured.err


def _without_timestamps(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in line.items() if k != "timestamp"} for line in lines]


def test_a_replayed_run_matches_one_with_the_transport_injected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The same two replies, once from the published fixture through a real
    # subprocess and once handed straight to main(): the events, the result
    # line and the saved session must agree, or --replay is testing a guess.
    proc, replayed, replayed_session = replay("done.json", tmp_path)
    assert proc.returncode == 0, proc.stderr

    injected = FakeProvider(
        [
            Reply(
                content=[
                    {"type": "text", "text": "Looking at the project first."},
                    {
                        "type": "tool_use",
                        "id": "call_1_1",
                        "name": "list",
                        "input": {"path": "."},
                    },
                ],
                tool_calls=[ToolCall(id="call_1_1", name="list", args={"path": "."})],
                usage=Usage(input=120, output=18),
                stop_reason="tool_use",
            ),
            Reply(
                content=[
                    {
                        "type": "text",
                        "text": "The project root is listed. Nothing needed changing.",
                    }
                ],
                tool_calls=[],
                usage=Usage(input=180, output=12),
                stop_reason="end_turn",
            ),
        ]
    )
    root = project(tmp_path)
    monkeypatch.chdir(root)
    session = tmp_path / "injected.json"
    code = main(
        ["run", "--yes", "--jsonl", "--root", str(root), "--session",
         str(session), "tidy up the project"],
        transport=injected,
    )  # fmt: skip
    assert code == 0
    direct = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    assert _without_timestamps(replayed[:-1]) == _without_timestamps(direct[:-1])
    replayed_result = {**replayed[-1], "session_id": None}
    assert replayed_result == {**direct[-1], "session_id": None}
    injected_session = json.loads(session.read_text(encoding="utf-8"))
    assert {**replayed_session, "id": None} == {**injected_session, "id": None}
