"""Run deadline and stop signals: a stopped run still saves and reports.

A caller bounds a run by wall clock (`--timeout`) or stops it with SIGTERM or
SIGINT. Either way nare kills what the run started, saves the session at its
last completed turn, emits an error event and writes a result line, so the
caller branches on `stop_reason` instead of synthesising an outcome.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider, text_reply
from nare.cli import main
from nare.contract import describe
from nare.session import Message, loads
from nare.transport import Reply

PROBE = Path(__file__).with_name("budget_probe.py")


def lines(out: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.splitlines() if line.strip()]


class Slow(FakeProvider):
    """A model call that outlasts any deadline a test sets."""

    async def turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> Reply:
        await asyncio.sleep(30)
        raise AssertionError("the deadline should have stopped this call")


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "soon"])
def test_an_invalid_timeout_never_starts(
    value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    try:
        code = main(["run", "--yes", "--jsonl", "--timeout", value, "go"])
    except SystemExit as exc:  # argparse refuses what is not a number
        code = int(exc.code or 0)

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "--timeout" in captured.err


def test_a_model_call_past_the_deadline_ends_with_a_timeout_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "s.json"
    started = time.monotonic()

    code = main(
        ["run", "--yes", "--jsonl", "--timeout", "0.5", "--session", str(path), "go"],
        transport=Slow([]),
    )

    elapsed = time.monotonic() - started
    emitted = lines(capsys.readouterr().out)
    result = emitted[-1]
    assert code == 1
    assert elapsed < 10
    assert result["type"] == "result"
    assert (result["status"], result["stop_reason"]) == ("error", "timeout")
    assert "0.5" in result["error"]
    assert emitted[-2]["type"] == "error"
    assert emitted[-2]["detail"]["stop"] == {
        "reason": "timeout",
        "timeout": 0.5,
        "process_groups": [],
    }
    saved = loads(path.read_text(encoding="utf-8"))
    assert (saved.status, saved.stop_reason) == ("error", "timeout")

    # The saved session is resumable: the next invocation finishes it.
    code = main(
        ["run", "--yes", "--jsonl", "--resume", str(path), "carry on"],
        transport=FakeProvider([text_reply("finished")]),
    )
    assert code == 0
    assert lines(capsys.readouterr().out)[-1]["status"] == "done"


def test_a_run_inside_its_deadline_is_unchanged(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        ["run", "--yes", "--jsonl", "--timeout", "30", "go"],
        transport=FakeProvider([text_reply("quick")]),
    )

    result = lines(capsys.readouterr().out)[-1]
    assert code == 0
    assert (result["status"], result["stop_reason"]) == ("done", "end_turn")


def _bash_script(tmp_path: Path, command: str, *, then: list[Any]) -> Path:
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            {
                "replies": [
                    {
                        "content": [],
                        "tool_calls": [
                            {
                                "id": "bash_1",
                                "name": "bash",
                                "args": {"command": command},
                            }
                        ],
                        "stop_reason": "tool_use",
                    },
                    *then,
                ]
            }
        )
    )
    return script


def _wait_for_pids(pidfile: Path, count: int, proc: subprocess.Popen[str]) -> list[int]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if pidfile.exists():
            pids = [int(pid) for pid in pidfile.read_text().split()]
            if len(pids) == count:
                return pids
        assert proc.poll() is None, proc.communicate()
        time.sleep(0.01)
    raise AssertionError("bash did not start its children")


def _assert_gone(pids: list[int]) -> None:
    for pid in pids:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        else:
            pytest.fail(f"process {pid} survived the stop")


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_a_signal_during_bash_writes_a_result_and_leaves_nothing_running(
    signum: signal.Signals, tmp_path: Path
) -> None:
    pidfile = tmp_path / "pids"
    session = tmp_path / "s.json"
    command = (
        "sleep 240 & first=$!; sleep 90 & second=$!; "
        f"printf '%s\\n' $$ $first $second > {shlex.quote(str(pidfile))}; wait"
    )
    script = _bash_script(tmp_path, command, then=[])
    pids: list[int] = []
    with subprocess.Popen(
        [
            sys.executable,
            str(PROBE),
            str(script),
            "run",
            "--yes",
            "--jsonl",
            "--session",
            str(session),
            "go",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as proc:
        try:
            pids = _wait_for_pids(pidfile, 3, proc)
            proc.send_signal(signum)
            out, stderr = proc.communicate(timeout=10)
            assert proc.returncode == 1, stderr
            _assert_gone(pids)
            emitted = lines(out)
            result = emitted[-1]
            assert result["type"] == "result"
            assert (result["status"], result["stop_reason"]) == (
                "error",
                "interrupted",
            )
            assert signum.name in result["error"]
            stop = next(e for e in emitted if e["type"] == "error")["detail"]["stop"]
            assert stop["reason"] == "interrupted"
            assert stop["signal"] == signum.name
            assert "Traceback" not in stderr
            saved = loads(session.read_text(encoding="utf-8"))
            assert saved.stop_reason == "interrupted"
            # The interrupted call is answered, so a resume is accepted.
            assert saved.messages[-1].content[0]["tool_use_id"] == "bash_1"
        finally:
            if pids:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(pids[0], signal.SIGKILL)
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=5)


def test_a_timeout_kills_what_an_earlier_call_left_running(tmp_path: Path) -> None:
    pidfile = tmp_path / "pids"
    # The call returns while its background sleep runs on: the group is
    # recorded as left running, then the next model call outlasts the deadline.
    command = (
        f"sleep 240 >/dev/null 2>&1 & printf '%s\\n' $! > {shlex.quote(str(pidfile))}"
    )
    script = _bash_script(tmp_path, command, then=[{"content": [], "delay": 60}])
    pids: list[int] = []
    try:
        done = subprocess.run(
            [
                sys.executable,
                str(PROBE),
                str(script),
                "run",
                "--yes",
                "--jsonl",
                "--timeout",
                "2",
                "go",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        pids = [int(pid) for pid in pidfile.read_text().split()]
        assert done.returncode == 1, done.stderr
        emitted = lines(done.stdout)
        assert emitted[-1]["stop_reason"] == "timeout"
        stop = next(e for e in emitted if e["type"] == "error")["detail"]["stop"]
        assert len(stop["process_groups"]) == 1
        _assert_gone(pids)
    finally:
        for pid in pids:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def test_the_contract_lists_the_deadline_and_its_stop_reasons() -> None:
    described = describe()
    kinds = {row["stop_reason"]: row["kind"] for row in described["stop_reasons"]}

    assert kinds["timeout"] == "limit"
    assert kinds["interrupted"] == "stopped"
    assert described["deadline"] == {
        "flag": "--timeout",
        "signals": ["SIGINT", "SIGTERM"],
        "stop_reasons": {"timeout": "timeout", "signal": "interrupted"},
        "exit": 1,
    }
