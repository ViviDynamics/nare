"""Read evidence: which bytes of which file version every read returned."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from fake_provider import FakeProvider, text_reply, tool_reply
from nare.cli import main
from nare.session import dumps, loads, new_session
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

    assert result["content"] == "b\nc\n[lines 2 to 3 of 4; continue with offset=3]"
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


def run_cli(root: Path, session: Path, replies: list[Any], capsys: Any) -> list[Any]:
    code = main(
        [
            "run",
            "where is the port?",
            "--yes",
            "--jsonl",
            "--tools",
            "read,list,search",
            "--root",
            str(root),
            "--session",
            str(session),
        ],
        transport=FakeProvider(replies),
    )
    assert code == 0
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_the_jsonl_stream_alone_lists_every_path_digest_and_range(
    tmp_path: Path, capsys: Any
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    data = b"[db]\nport = 6543\n"
    (root / "settings.ini").write_bytes(data)
    session = tmp_path / "s.json"

    lines = run_cli(
        root,
        session,
        [
            tool_reply("list", {}),
            tool_reply("search", {"pattern": "port"}),
            tool_reply("read", {"path": "settings.ini", "offset": 1}),
            text_reply("6543"),
        ],
        capsys,
    )

    records = [
        line["detail"]["evidence"]
        for line in lines
        if line.get("type") == "progress" and "evidence" in line.get("detail", {})
    ]
    assert [r["tool"] for r in records] == ["list", "search", "read"]
    read = records[2]
    assert (read["path"], read["sha256"], read["lines"], read["bytes"]) == (
        "settings.ini",
        sha(data),
        {"first": 2, "last": 2},
        {"start": 5, "end": len(data)},
    )
    assert records[1]["files"][0]["sha256"] == sha(data)
    saved = json.loads(session.read_text(encoding="utf-8"))
    assert saved["evidence"] == records


def test_a_file_changed_between_two_reads_yields_two_digests(
    tmp_path: Path, capsys: Any
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "a.txt"
    target.write_bytes(b"before\n")
    session = tmp_path / "s.json"

    first = run_cli(
        root,
        session,
        [tool_reply("read", {"path": "a.txt"}), text_reply("ok")],
        capsys,
    )
    target.write_bytes(b"after\n")
    code = main(
        [
            "run",
            "again",
            "--yes",
            "--jsonl",
            "--tools",
            "read",
            "--root",
            str(root),
            "--resume",
            str(session),
        ],
        transport=FakeProvider(
            [tool_reply("read", {"path": "a.txt"}), text_reply("ok")]
        ),
    )
    assert code == 0
    capsys.readouterr()

    saved = json.loads(session.read_text(encoding="utf-8"))
    digests = [r["sha256"] for r in saved["evidence"]]
    assert digests == [sha(b"before\n"), sha(b"after\n")]
    assert any("evidence" in line.get("detail", {}) for line in first)


def test_a_session_from_before_evidence_loads_with_none() -> None:
    s = new_session("hi")
    raw = json.loads(dumps(s))
    raw.pop("evidence", None)

    assert loads(json.dumps(raw)).evidence == []


def test_the_session_keeps_evidence_across_a_round_trip() -> None:
    s = new_session("hi")
    s.evidence.append({"tool": "read", "path": "a.txt", "sha256": "0" * 64})

    assert loads(dumps(s)).evidence == s.evidence
