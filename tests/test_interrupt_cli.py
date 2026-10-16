"""A real SIGINT must stay quiet without bypassing bash cancellation cleanup."""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROBE = Path(__file__).with_name("budget_probe.py")


def test_sigint_during_bash_exits_cleanly_and_kills_children(tmp_path: Path) -> None:
    pidfile = tmp_path / "pids"
    command = (
        "sleep 240 & first=$!; sleep 90 & second=$!; "
        f"printf '%s\\n' $$ $first $second > {shlex.quote(str(pidfile))}; wait"
    )
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
                    }
                ]
            }
        )
    )
    pids: list[int] = []
    with subprocess.Popen(
        [sys.executable, str(PROBE), str(script), "run", "--yes", "go"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as proc:
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if pidfile.exists():
                    pids = [int(pid) for pid in pidfile.read_text().split()]
                    if len(pids) == 3:
                        break
                assert proc.poll() is None, proc.communicate()
                time.sleep(0.01)
            assert len(pids) == 3, "bash did not start both sleeps"
            proc.send_signal(signal.SIGINT)
            _, stderr = proc.communicate(timeout=5)
            assert proc.returncode == 130
            for pid in pids:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.01)
                else:
                    pytest.fail(f"process {pid} survived SIGINT")
            assert "Traceback" not in stderr
            assert len(stderr.splitlines()) == 1
            assert "interrupted" in stderr.lower()
        finally:
            if pids:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(pids[0], signal.SIGKILL)
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=5)
