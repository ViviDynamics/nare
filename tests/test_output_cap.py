"""The output cap keeps what the model needs: the end of a long command's
output, stderr beside a long stdout, and where a read stopped.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from nare.mcp import ExternalTool
from nare.tools import MAX_TOOL_OUTPUT, Policy, dispatch, read_file, run_bash
from nare.transport import ToolCall


def body(result: str) -> str:
    """A bash result without its status line."""
    return result.split("\n", 1)[1]


async def test_a_long_run_keeps_the_failure_line_printed_last() -> None:
    out = await run_bash(
        "python3 -c \"print('passed ' * 5000)\"; echo 'FAILED test_the_one_that_broke'"
    )

    assert out.startswith("exit 0\npassed passed")
    assert out.rstrip().endswith("FAILED test_the_one_that_broke")
    assert len(body(out)) <= MAX_TOOL_OUTPUT


async def test_the_marker_says_how_much_was_cut_and_how_to_see_it() -> None:
    out = await run_bash("python3 -c \"print('y' * 40000)\"")

    marker = re.search(r"\[\.\.\. ([\d,]+) characters of stdout cut here", out)
    assert marker is not None
    kept = len(body(out)) - len(marker.group(0))
    assert int(marker.group(1).replace(",", "")) > 40000 - MAX_TOOL_OUTPUT
    assert kept < MAX_TOOL_OUTPUT
    assert "redirect" in out and "offset" in out


async def test_stderr_survives_a_long_stdout() -> None:
    out = await run_bash(
        "python3 -c \"print('o' * 50000)\"; echo 'error: the real problem' >&2; exit 1"
    )

    assert out.startswith("exit 1\n")
    assert "error: the real problem" in out
    assert len(body(out)) <= MAX_TOOL_OUTPUT


async def test_stdout_survives_a_long_stderr() -> None:
    out = await run_bash(
        "echo 'the answer is 42'; "
        "python3 -c \"import sys; sys.stderr.write('e' * 50000)\""
    )

    assert "the answer is 42" in out
    assert len(body(out)) <= MAX_TOOL_OUTPUT


async def test_two_long_streams_each_keep_both_ends() -> None:
    out = await run_bash(
        "python3 -c \"print('OUT-START' + 'o' * 40000 + 'OUT-END')\"; "
        'python3 -c "import sys; '
        "sys.stderr.write('ERR-START' + 'e' * 40000 + 'ERR-END')\""
    )

    for edge in ("OUT-START", "OUT-END", "ERR-START", "ERR-END"):
        assert edge in out
    assert "characters of stdout cut here" in out
    assert "characters of stderr cut here" in out
    assert len(body(out)) <= MAX_TOOL_OUTPUT


async def test_output_under_the_cap_is_unchanged() -> None:
    assert await run_bash("echo out; echo err >&2") == "exit 0\nout\nerr"


async def test_an_mcp_result_keeps_its_head_and_tail() -> None:
    async def call(args: dict[str, Any]) -> tuple[str, bool]:
        return "HEAD" + "m" * 50000 + "TAIL", False

    tool = ExternalTool({"name": "srv__big", "input_schema": {"type": "object"}}, call)
    policy = Policy(tools=frozenset({"srv__big"}), external={"srv__big": tool})

    result = await dispatch(ToolCall("c1", "srv__big", {}), policy)

    assert result["content"].startswith("HEAD")
    assert result["content"].endswith("TAIL")
    assert "characters of the result cut here" in result["content"]
    assert len(result["content"]) <= MAX_TOOL_OUTPUT


def test_a_default_read_of_a_long_file_says_where_to_continue(
    tmp_path: Path,
) -> None:
    target = tmp_path / "long.txt"
    target.write_text("".join(f"line {n}\n" for n in range(1, 3001)))

    out = read_file(str(target))

    assert out.splitlines()[1999] == "line 2000"
    assert out.endswith("\n[lines 1 to 2000 of 3000; continue with offset=2000]")
    assert read_file(str(target), offset=2000).startswith("line 2001\n")


def test_a_read_that_reaches_the_end_has_no_notice(tmp_path: Path) -> None:
    target = tmp_path / "long.txt"
    target.write_text("".join(f"line {n}\n" for n in range(1, 3001)))

    out = read_file(str(target), offset=2000)

    assert out.endswith("line 3000")


def test_a_read_cut_by_the_cap_says_where_to_continue(tmp_path: Path) -> None:
    target = tmp_path / "wide.txt"
    target.write_text("".join(f"{n:04d}" + "w" * 995 + "\n" for n in range(1, 101)))

    out = read_file(str(target), limit=100)

    # 30 whole lines of 1,000 characters with their newlines fill the cap.
    assert out.endswith(
        f"\n[truncated at {MAX_TOOL_OUTPUT} chars: lines 1 to 30 of 100; "
        "continue with offset=30]"
    )
    assert read_file(str(target), offset=30, limit=1).startswith("0031")


def test_a_read_cut_partway_through_a_line_says_so(tmp_path: Path) -> None:
    target = tmp_path / "bundle.min.js"
    target.write_text("x" * (MAX_TOOL_OUTPUT * 2) + "\nnext\n")

    out = read_file(str(target))

    assert out.endswith(
        f"\n[truncated at {MAX_TOOL_OUTPUT} chars, partway through line 1: "
        "lines 1 to 1 of 2; continue with offset=1]"
    )


@pytest.mark.asyncio
async def test_evidence_for_a_read_with_a_notice_matches_the_bytes_returned(
    tmp_path: Path,
) -> None:
    data = "".join(f"line {n}\n" for n in range(1, 11)).encode()
    (tmp_path / "f.txt").write_bytes(data)
    records: list[dict[str, Any]] = []

    result = await dispatch(
        ToolCall("c1", "read", {"path": "f.txt", "offset": 2, "limit": 3}),
        Policy(frozenset({"read"}), tmp_path),
        evidence=records.append,
    )

    shown, notice = result["content"].rsplit("\n", 1)
    assert notice == "[lines 3 to 5 of 10; continue with offset=5]"
    [record] = records
    assert record["lines"] == {"first": 3, "last": 5}
    assert record["truncated"] is False
    start, end = record["bytes"]["start"], record["bytes"]["end"]
    assert data[start:end].decode().rstrip("\n") == shown
