"""Read evidence: which bytes of which file version every read returned."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from nare.tools import MAX_TOOL_OUTPUT, Policy, dispatch
from nare.transport import ToolCall


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def evidence_of(
    root: Path, tool: str, args: dict[str, Any], **policy: Any
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    result = await dispatch(
        ToolCall("c1", tool, args),
        Policy(frozenset({tool}), root, **policy),
        evidence=records.append,
    )
    return result, records


@pytest.mark.asyncio
async def test_a_read_records_its_path_digest_size_and_range(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    data = b"one\ntwo\nthree\n"
    (tmp_path / "src" / "a.txt").write_bytes(data)

    result, records = await evidence_of(tmp_path, "read", {"path": "src/a.txt"})

    assert result["content"] == "one\ntwo\nthree"
    assert records == [
        {
            "tool": "read",
            "tool_use_id": "c1",
            "path": "src/a.txt",
            "sha256": sha(data),
            "size": len(data),
            "lines": {"first": 1, "last": 3},
            "bytes": {"start": 0, "end": len(data)},
            "truncated": False,
        }
    ]


@pytest.mark.asyncio
async def test_an_offset_and_limit_record_the_bytes_of_those_lines(
    tmp_path: Path,
) -> None:
    data = "ä\r\nb\r\nc\r\nd".encode()
    (tmp_path / "crlf.txt").write_bytes(data)

    result, [record] = await evidence_of(
        tmp_path, "read", {"path": "crlf.txt", "offset": 1, "limit": 2}
    )

    assert result["content"] == "b\nc"
    assert record["lines"] == {"first": 2, "last": 3}
    start = len("ä\r\n".encode())
    assert record["bytes"] == {"start": start, "end": start + len(b"b\r\nc\r\n")}
    assert data[record["bytes"]["start"] : record["bytes"]["end"]] == b"b\r\nc\r\n"
    assert record["sha256"] == sha(data) and record["size"] == len(data)


@pytest.mark.asyncio
async def test_the_last_line_without_a_newline_ends_at_the_end_of_the_file(
    tmp_path: Path,
) -> None:
    data = b"a\nb"
    (tmp_path / "f.txt").write_bytes(data)

    _, [record] = await evidence_of(tmp_path, "read", {"path": "f.txt", "offset": 1})

    assert record["lines"] == {"first": 2, "last": 2}
    assert record["bytes"] == {"start": 2, "end": 3}


@pytest.mark.asyncio
async def test_a_truncated_read_records_the_range_returned_not_requested(
    tmp_path: Path,
) -> None:
    line = "é" * 999  # 999 characters, 1998 bytes, 1000 with its newline
    data = (line + "\n").encode() * 100
    (tmp_path / "big.txt").write_bytes(data)

    result, [record] = await evidence_of(
        tmp_path, "read", {"path": "big.txt", "limit": 100}
    )

    shown = result["content"].split("\n[truncated at")[0]
    assert len(shown) == MAX_TOOL_OUTPUT
    assert record["truncated"] is True
    # 30 whole lines (30,000 characters with their newlines) fill the cap
    # exactly, so line 30 is the last one returned, and with its newline.
    assert record["lines"] == {"first": 1, "last": 30}
    assert record["bytes"] == {"start": 0, "end": 30 * len((line + "\n").encode())}
    assert data[: record["bytes"]["end"]].decode() == shown


@pytest.mark.asyncio
async def test_a_read_cut_mid_line_ends_at_the_last_character_returned(
    tmp_path: Path,
) -> None:
    data = ("x" * 20_000 + "\n" + "é" * 20_000 + "\n").encode()
    (tmp_path / "wide.txt").write_bytes(data)

    result, [record] = await evidence_of(tmp_path, "read", {"path": "wide.txt"})

    shown = result["content"].split("\n[truncated at")[0]
    assert record["truncated"] is True
    assert record["lines"] == {"first": 1, "last": 2}
    assert data[: record["bytes"]["end"]].decode() == shown


@pytest.mark.asyncio
async def test_a_read_that_returns_nothing_records_no_range(tmp_path: Path) -> None:
    data = b"only\n"
    (tmp_path / "f.txt").write_bytes(data)

    result, [record] = await evidence_of(
        tmp_path, "read", {"path": "f.txt", "offset": 5}
    )

    assert result["content"] == ""
    assert record["lines"] is None and record["bytes"] is None
    assert record["sha256"] == sha(data) and record["truncated"] is False


@pytest.mark.asyncio
async def test_an_image_read_records_the_whole_image(tmp_path: Path) -> None:
    Image.new("RGB", (4, 4), "blue").save(tmp_path / "a.png", format="PNG")
    data = (tmp_path / "a.png").read_bytes()

    _, [record] = await evidence_of(
        tmp_path, "read", {"path": "a.png"}, image_input=True
    )

    assert record["sha256"] == sha(data) and record["size"] == len(data)
    assert record["lines"] is None
    assert record["bytes"] == {"start": 0, "end": len(data)}
    assert record["truncated"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,args",
    [
        ("read", {"path": "missing.txt"}),
        ("read", {"path": "../outside.txt"}),
        ("list", {"path": "missing"}),
        ("search", {"pattern": "("}),
    ],
)
async def test_a_failed_call_records_no_evidence(
    tmp_path: Path, tool: str, args: dict[str, Any]
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("secret\n", encoding="utf-8")

    result, records = await evidence_of(root, tool, args)

    assert result["is_error"] is True
    assert records == []


@pytest.mark.asyncio
async def test_a_list_records_the_directory_and_the_entries_returned(
    tmp_path: Path,
) -> None:
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "a.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "d" / "b.bin").write_bytes(b"\0\1")
    (tmp_path / "d" / "c").mkdir()

    _, [first] = await evidence_of(tmp_path, "list", {"path": "d", "limit": 2})
    _, [whole] = await evidence_of(tmp_path, "list", {"path": "d"})

    assert first == {
        "tool": "list",
        "tool_use_id": "c1",
        "path": "d",
        "entries": [
            {"name": "a.txt", "kind": "file", "size": 5, "binary": False},
            {"name": "b.bin", "kind": "file", "size": 2, "binary": True},
        ],
        "more": True,
    }
    assert whole["more"] is False
    assert whole["entries"][-1] == {
        "name": "c",
        "kind": "dir",
        "size": None,
        "binary": False,
    }


@pytest.mark.asyncio
async def test_a_search_records_each_file_digest_and_matched_lines(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    a = b"port = 1\nhost = x\nport = 2\n"
    b = b"no match\nport = 3\n"
    (tmp_path / "src" / "a.py").write_bytes(a)
    (tmp_path / "src" / "b.py").write_bytes(b)
    (tmp_path / "src" / "c.py").write_bytes(b"nothing here\n")

    _, [record] = await evidence_of(
        tmp_path, "search", {"path": "src", "glob": "*.py", "pattern": "port"}
    )

    assert record == {
        "tool": "search",
        "tool_use_id": "c1",
        "path": "src",
        "glob": "*.py",
        "pattern": "port",
        "files": [
            {"path": "src/a.py", "sha256": sha(a), "size": len(a), "lines": [1, 3]},
            {"path": "src/b.py", "sha256": sha(b), "size": len(b), "lines": [2]},
        ],
        "more": False,
    }


@pytest.mark.asyncio
async def test_a_paged_search_records_only_the_lines_on_its_page(
    tmp_path: Path,
) -> None:
    data = b"hit\nhit\nhit\n"
    (tmp_path / "a.txt").write_bytes(data)

    _, [record] = await evidence_of(tmp_path, "search", {"pattern": "hit", "limit": 2})

    assert record["files"] == [
        {"path": "a.txt", "sha256": sha(data), "size": len(data), "lines": [1, 2]}
    ]
    assert record["more"] is True


@pytest.mark.asyncio
async def test_a_glob_only_search_records_paths_and_opens_no_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("y\n", encoding="utf-8")

    _, [record] = await evidence_of(tmp_path, "search", {"glob": "*.py"})

    assert record["path"] == "."
    assert record["glob"] == "*.py" and record["pattern"] is None
    assert record["files"] == [{"path": "a.py"}]
