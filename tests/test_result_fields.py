"""The result line's token total and final reply (#146).

`usage.total` is the one token number a caller should use: it is what
`--budget-tokens` counts, cache included. `text` is the final reply of a run
that ended `done`, so a caller does not have to scan the stream for it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider, text_reply, tool_reply
from nare.cli import main
from nare.session import Usage
from nare.transport import Reply

DOC = Path(__file__).parent.parent / "docs" / "contract.md"


def lines(out: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def cached(text: str) -> Reply:
    """A reply whose usage has every category, as a cached turn does."""
    return Reply(
        content=[{"type": "text", "text": text}],
        tool_calls=[],
        usage=Usage(input=7, output=3, cache_read=100, cache_write=20),
        stop_reason="end_turn",
    )


def run(argv: list[str], replies: list[Reply], capsys: Any) -> list[dict[str, Any]]:
    main(["run", "--yes", "--jsonl", *argv], transport=FakeProvider(replies))
    return lines(capsys.readouterr().out)


def test_the_result_total_counts_every_category_and_matches_the_budget(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = run(["go"], [cached("ok")], capsys)[-1]

    assert result["usage"]["total"] == 7 + 3 + 100 + 20
    assert result["usage"]["total"] == result["budget"]["used_tokens"]


@pytest.mark.parametrize(
    ("replies", "status"),
    [
        ([cached("ok")], "done"),
        ([tool_reply("ask", {"questions": ["which?"]})], "blocked"),
        ([text_reply("cut", stop_reason="max_tokens")], "error"),
    ],
)
def test_the_total_equals_used_tokens_on_every_result(
    replies: list[Reply], status: str, capsys: pytest.CaptureFixture[str]
) -> None:
    result = run(["go"], replies, capsys)[-1]

    assert result["status"] == status
    assert result["usage"]["total"] == result["budget"]["used_tokens"]


def test_a_budget_stop_carries_the_total_in_its_usage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    emitted = run(
        ["--budget-tokens", "1", "--root", str(tmp_path), "go"],
        [tool_reply("bash", {"command": "true"}), text_reply("never")],
        capsys,
    )

    error = next(e for e in emitted if e["type"] == "error")
    assert error["detail"]["usage"]["total"] == error["detail"]["budget"]["used_tokens"]
    assert emitted[-1]["usage"]["total"] == emitted[-1]["budget"]["used_tokens"]


def test_each_cost_event_carries_its_turn_total(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emitted = run(["go"], [cached("ok")], capsys)

    cost = next(e for e in emitted if e["type"] == "cost")
    assert cost["detail"]["total"] == 130


def test_the_session_file_keeps_its_usage_shape(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "s.json"

    run(["--session", str(path), "go"], [cached("ok")], capsys)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "total" not in saved["usage"]


def test_a_done_run_carries_its_final_reply(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = run(
        ["--root", str(tmp_path), "go"],
        [tool_reply("bash", {"command": "true"}), text_reply("all finished")],
        capsys,
    )[-1]

    assert result["status"] == "done"
    assert result["text"] == "all finished"
    assert result["output"] is None


def test_the_final_reply_is_redacted_as_events_are(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "sk-ant-api03-" + "a" * 40
    emitted = run(["go"], [text_reply(f"the key is {secret}")], capsys)

    output = next(e for e in emitted if e["type"] == "output")
    assert secret not in emitted[-1]["text"]
    assert emitted[-1]["text"] == output["text"]


@pytest.mark.parametrize(
    "replies",
    [
        [tool_reply("ask", {"questions": ["which?"]})],
        [text_reply("cut", stop_reason="max_tokens")],
    ],
)
def test_a_run_that_is_not_done_has_no_final_reply(
    replies: list[Reply], capsys: pytest.CaptureFixture[str]
) -> None:
    result = run(["go"], replies, capsys)[-1]

    assert result["status"] != "done"
    assert result["text"] is None


def test_a_schema_run_keeps_output_and_also_carries_the_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    schema = tmp_path / "answer.schema.json"
    schema.write_text(json.dumps({"type": "object"}), encoding="utf-8")

    result = run(["--schema", str(schema), "go"], [text_reply('{"ok": true}')], capsys)[
        -1
    ]

    assert result["output"] == {"ok": True}
    assert result["text"] == '{"ok": true}'


def test_the_contract_documents_both_fields() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "`usage.total`" in text
    assert (
        "`text`"
        in text.split("The last line is not an event.", 1)[1].split("\n\n", 1)[0]
    )
