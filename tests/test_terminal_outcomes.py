"""Terminal outcomes: every way a run ends names its cause without prose.

A caller branches on the result's (status, stop_reason) pair. `nare contract`
lists every stop reason with its kind, so a run that ran out of room is told
apart from one that broke, and docs/contract.md carries the same table.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, get_args

import pytest

from fake_provider import (
    Exploding,
    FakeProvider,
    text_reply,
    thinking_reply,
    tool_reply,
)
from nare.cli import main
from nare.contract import describe
from nare.loop import run
from nare.session import Status, new_session

DOC = Path(__file__).parent.parent / "docs" / "contract.md"
KINDS = {"finished", "limit", "answer", "failure"}


def stop_reason_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = describe()["stop_reasons"]
    return rows


def documented_rows() -> dict[str | None, tuple[str, list[str]]]:
    """The stop reason table in docs/contract.md, value -> (kind, statuses)."""
    text = DOC.read_text(encoding="utf-8")
    section = text.split("## Stop reasons", 1)[1].split("\n## ", 1)[0]
    table: dict[str | None, tuple[str, list[str]]] = {}
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4 or not cells[1].startswith("`"):
            continue
        values: list[str | None] = [
            None if token == "null" else token.strip("`")
            for token in re.findall(r"`[^`]+`|\bnull\b", cells[0])
        ]
        kind = cells[1].strip("`")
        statuses = re.findall(r"`([^`]+)`", cells[2])
        for value in values:
            table[value] = (kind, statuses)
    return table


def test_the_contract_lists_every_stop_reason_with_its_kind() -> None:
    rows = stop_reason_rows()
    values = [row["stop_reason"] for row in rows]

    assert len(values) == len(set(values))
    for row in rows:
        assert set(row) == {"stop_reason", "kind", "statuses"}
        assert row["kind"] in KINDS
        assert row["statuses"]
        assert set(row["statuses"]) <= set(get_args(Status)) - {"working"}
    # The kinds the issue asks to tell apart.
    kind = {row["stop_reason"]: row["kind"] for row in rows}
    assert kind["max_tokens"] == "limit"
    assert kind[None] == "failure"


def test_the_contract_subcommand_prints_the_stop_reasons(capsys: Any) -> None:
    assert main(["contract"]) == 0

    printed = json.loads(capsys.readouterr().out)
    assert printed["stop_reasons"] == stop_reason_rows()


def test_the_documented_table_matches_the_code() -> None:
    expected = {
        row["stop_reason"]: (row["kind"], row["statuses"]) for row in stop_reason_rows()
    }

    assert documented_rows() == expected


async def test_reasoning_without_an_answer_names_its_own_reason() -> None:
    session = new_session("write an essay")

    [_ async for _ in run(session, transport=FakeProvider([thinking_reply()]))]

    assert (session.status, session.stop_reason) == ("error", "no_answer")


def test_a_failed_session_write_names_its_own_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unwritable = tmp_path / "missing" / "deep" / "s.json"

    code = main(
        ["run", "--yes", "--jsonl", "--session", str(unwritable), "go"],
        transport=FakeProvider([text_reply("ok")]),
    )

    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert code == 1
    assert (result["status"], result["stop_reason"]) == ("error", "session_write")


class FailingDiscovery(FakeProvider):
    """A library transport whose window discovery raises."""

    async def context_window(self) -> int | None:
        raise RuntimeError("metadata endpoint down")


async def test_a_transport_discovery_failure_is_not_reported_as_mcp() -> None:
    session = new_session("go")

    events = [e async for e in run(session, transport=FailingDiscovery([]))]

    assert session.status == "error"
    assert session.stop_reason is None
    assert "MCP" not in (session.error or "")
    assert "metadata endpoint down" in (session.error or "")
    assert events[-1].type == "error"


# Every terminal path the CLI can reach, driven for real: the pair it ends
# with must be a row of the table, so a new path cannot ship unlisted.
SCENARIOS: dict[str, tuple[list[str], Any]] = {
    "done": ([], lambda: FakeProvider([text_reply("ok")])),
    "stop_sequence": (
        [],
        lambda: FakeProvider([text_reply("ok", stop_reason="stop_sequence")]),
    ),
    "blocked": ([], lambda: FakeProvider([tool_reply("ask", {"questions": ["?"]})])),
    "max_tokens": (
        [],
        lambda: FakeProvider([text_reply("cut", stop_reason="max_tokens")]),
    ),
    "refusal": ([], lambda: FakeProvider([text_reply("", stop_reason="refusal")])),
    "no_answer": ([], lambda: FakeProvider([thinking_reply()])),
    "max_turns": (
        ["--max-turns", "1"],
        lambda: FakeProvider(
            [tool_reply("bash", {"command": "true"}), text_reply("summary")]
        ),
    ),
    "context": (["--context-window", "1"], lambda: FakeProvider([])),
    "budget": (
        ["--budget-tokens", "1"],
        lambda: FakeProvider(
            [tool_reply("bash", {"command": "true"}), text_reply("never")]
        ),
    ),
    "provider": ([], lambda: Exploding()),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_terminal_path_ends_on_a_listed_pair(
    name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    flags, transport = SCENARIOS[name]

    main(
        ["run", "--yes", "--jsonl", "--root", str(tmp_path), *flags, "go"],
        transport=transport(),
    )

    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    listed = {row["stop_reason"]: row["statuses"] for row in stop_reason_rows()}
    assert result["stop_reason"] in listed, result
    assert result["status"] in listed[result["stop_reason"]], result


def test_a_schema_violation_ends_on_a_listed_pair(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    schema = tmp_path / "answer.schema.json"
    schema.write_text(json.dumps({"type": "object"}), encoding="utf-8")

    main(
        ["run", "--yes", "--jsonl", "--schema", str(schema), "go"],
        transport=FakeProvider([text_reply("not json"), text_reply("still not")]),
    )

    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert (result["status"], result["stop_reason"]) == ("error", "schema_violation")
    listed = {row["stop_reason"]: row["statuses"] for row in stop_reason_rows()}
    assert "error" in listed["schema_violation"]


def test_an_mcp_startup_failure_ends_on_a_listed_pair(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "servers.json"
    config.write_text(
        json.dumps({"bad": {"command": str(tmp_path / "missing-server")}}),
        encoding="utf-8",
    )

    main(
        ["run", "--yes", "--jsonl", "--mcp-config", str(config), "go"],
        transport=FakeProvider([]),
    )

    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert (result["status"], result["stop_reason"]) == ("error", "mcp")
    listed = {row["stop_reason"]: row["statuses"] for row in stop_reason_rows()}
    assert "error" in listed["mcp"]
