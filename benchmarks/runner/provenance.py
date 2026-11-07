"""What produced a results line, so an old number can be compared or refused.

Pure except for two reads: the case's files, and `docker image inspect`. Every
helper here degrades to an empty value rather than failing the rep, because a
results line without a field is still a result, and the record of why a field
is empty is what `compare` reports.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from benchmarks.runner.case import Case
from benchmarks.runner.judge import JudgeError
from benchmarks.runner.sandbox import IMAGE

# The causes a rep that could not be graded can carry, as results lines write
# them. A rep with no cause was graded, or predates causes.
ERROR_CAUSES = (
    "timeout",
    "no_result_line",
    "agent_http_error",
    "sandbox_error",
    "judge_http_error",
    "judge_empty",
    "judge_unparsed",
    "judge_unanswered",
)
TRANSPORT_STOP = "transport error"


def case_hash(case: Case) -> str:
    """A digest of the case as the runner reads it: case.toml, then every file
    of the fixture by its path, so any edit to the task or its inputs changes it.

    "" when the case's files are not on disk: nothing can be said of them, so
    no comparison is refused on that account.
    """
    digest = hashlib.sha256()
    try:
        digest.update(b"case.toml\0")
        digest.update((case.directory / "case.toml").read_bytes())
        for path in sorted(p for p in case.fixture.rglob("*") if p.is_file()):
            rel = path.relative_to(case.directory).as_posix()
            digest.update(rel.encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    except OSError:
        return ""
    return "sha256:" + digest.hexdigest()


def image_id() -> str:
    """The Docker image ID the reps ran in, or "" when docker cannot say."""
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", "-f", "{{.Id}}", IMAGE],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def tool_calls_by_tool(session: Mapping[str, Any]) -> dict[str, int]:
    """Tool calls the agent made, counted by tool name, from a saved session."""
    counts: Counter[str] = Counter()
    for message in session.get("messages") or []:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                counts[str(block.get("name") or "")] += 1
    return dict(sorted(counts.items()))


def session_tool_calls(evidence: Path) -> dict[str, int] | None:
    """The tool calls in a rep's kept session.json, or None when none was saved."""
    path = evidence / "session.json"
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return tool_calls_by_tool(loaded) if isinstance(loaded, dict) else None


def judge_cause(exc: JudgeError) -> str:
    """Why the judge could not answer, typed."""
    if not exc.attempts:
        return "judge_unanswered"
    if any(a.stop_reason == TRANSPORT_STOP for a in exc.attempts):
        return "judge_http_error"
    if not exc.attempts[-1].text.strip():
        return "judge_empty"
    return "judge_unparsed"
