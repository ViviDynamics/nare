"""The issue's acceptance: a read-only session explores a tree with no bash."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fake_provider import FakeProvider, text_reply, tool_reply
from nare.cli import main
from nare.tools import MAX_TOOL_OUTPUT, list_dir, search_files


def test_a_read_only_session_finds_and_reads_a_file_it_was_not_told_about(
    tmp_path: Path, capsys: Any
) -> None:
    root = tmp_path / "project"
    (root / "nested" / "deep").mkdir(parents=True)
    (root / "nested" / "deep" / "settings.ini").write_text(
        "[db]\nport = 6543\n", encoding="utf-8"
    )
    (root / "README.md").write_text("A project.\n", encoding="utf-8")
    session_path = tmp_path / "s.json"
    provider = FakeProvider(
        [
            tool_reply("list", {}),
            tool_reply("search", {"pattern": "port"}),
            tool_reply("read", {"path": "nested/deep/settings.ini"}),
            text_reply("The port is 6543."),
        ]
    )

    code = main(
        [
            "run",
            "which port does the database use?",
            "--yes",
            "--jsonl",
            "--tools",
            "read,list,search",
            "--root",
            str(root),
            "--session",
            str(session_path),
        ],
        transport=provider,
    )

    capsys.readouterr()
    assert code == 0
    _, offered = provider.calls[0]
    assert sorted(tool["name"] for tool in offered) == ["list", "read", "search"]
    saved = json.loads(session_path.read_text(encoding="utf-8"))
    results = [
        block["content"]
        for message in saved["messages"]
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]
    assert "nested/  (dir)" in results[0]
    assert "nested/deep/settings.ini:2: port = 6543" in results[1]
    assert results[2] == "[db]\nport = 6543"
    assert saved["status"] == "done"


def next_cursor(page: str) -> str | None:
    found = re.search(r'\[next page: cursor "([^"]+)"\]$', page)
    return found[1] if found else None


def test_a_tree_of_100000_files_pages_with_no_result_over_the_cap(
    tmp_path: Path,
) -> None:
    for d in range(100):
        folder = tmp_path / f"d{d:03d}"
        folder.mkdir()
        for f in range(1000):
            # os.open, not Path.touch(): a hundred thousand of them.
            os.close(os.open(folder / f"file-{f:04d}.txt", os.O_CREAT | os.O_WRONLY))

    listed = 0
    for d in range(100):
        cursor: str | None = None
        while True:
            page = list_dir(str(tmp_path / f"d{d:03d}"), cursor, base=str(tmp_path))
            assert len(page) <= MAX_TOOL_OUTPUT
            listed += len(page.splitlines()) - 1 - (1 if next_cursor(page) else 0)
            cursor = next_cursor(page)
            if cursor is None:
                break

    found = 0
    cursor = None
    while True:
        page = search_files(
            str(tmp_path), cursor=cursor, limit=1000, base=str(tmp_path)
        )
        assert len(page) <= MAX_TOOL_OUTPUT
        cursor = next_cursor(page)
        found += len(page.splitlines()) - 1 - (1 if cursor else 0)
        if cursor is None:
            break

    assert listed == found == 100_000
