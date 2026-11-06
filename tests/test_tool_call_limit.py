"""A cap on the tool calls one invocation runs (#147).

`--max-tool-calls N` bounds how much a session explores, which `--max-turns`
does only loosely: one turn can hold many calls. At the cap a call is answered
with an error telling the model to answer, and not run. A model that calls
again after that ends the run `error` with `stop_reason` `tool_calls`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider, calls_reply, text_reply, tool_reply
from nare.cli import main
from nare.contract import describe
from nare.loop import run
from nare.session import Message, Usage, loads, new_session, reopen
from nare.tools import Policy
from nare.transport import Reply, ToolCall

DOC = Path(__file__).parent.parent / "docs" / "contract.md"


def lines(out: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def writes(root: Path, *names: str) -> Reply:
    return calls_reply(
        *(("write", {"path": str(root / name), "content": name}) for name in names)
    )


def results(fake: FakeProvider, turn: int) -> list[dict[str, Any]]:
    """The tool results the model was sent before its turn `turn` (from 0)."""
    messages, _ = fake.calls[turn]
    return messages[-1].content


def test_a_call_past_the_cap_is_answered_and_not_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeProvider(
        [writes(tmp_path, "a", "b", "c", "d"), text_reply("three written")]
    )

    code = main(
        ["run", "--yes", "--jsonl", "--root", str(tmp_path)]
        + ["--max-tool-calls", "3", "go"],
        transport=fake,
    )

    result = lines(capsys.readouterr().out)[-1]
    assert [p.name for p in sorted(tmp_path.iterdir())] == ["a", "b", "c"]
    sent = results(fake, 1)
    assert [r["is_error"] for r in sent] == [False, False, False, True]
    assert "tool call limit" in sent[3]["content"]
    assert "answer" in sent[3]["content"]
    # The model answered after the refusal, so the run finished.
    assert code == 0
    assert (result["status"], result["stop_reason"]) == ("done", "end_turn")
    assert result["budget"]["tool_calls"] == 3
    assert result["budget"]["used_tool_calls"] == 3


def test_a_model_that_keeps_calling_ends_with_tool_calls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeProvider([writes(tmp_path, "a", "b"), writes(tmp_path, "c")])

    code = main(
        ["run", "--yes", "--jsonl", "--root", str(tmp_path)]
        + ["--max-tool-calls", "1", "go"],
        transport=fake,
    )

    emitted = lines(capsys.readouterr().out)
    result = emitted[-1]
    assert code == 1
    assert (result["status"], result["stop_reason"]) == ("error", "tool_calls")
    assert "1" in result["error"]
    assert any(e["type"] == "error" for e in emitted[:-1])
    assert [p.name for p in tmp_path.iterdir()] == ["a"]
    assert result["budget"]["used_tool_calls"] == 1
    assert len(fake.calls) == 2


async def test_the_cap_counts_per_invocation() -> None:
    session = new_session("go")
    first = FakeProvider(
        [tool_reply("ask", {"questions": ["?"]}, call_id="q1")]
    )  # an ask runs, and counts
    [_ async for _ in run(session, transport=first, max_tool_calls=1, policy=Policy())]
    assert session.tool_calls == 1

    reopen(session, "carry on")
    second = FakeProvider(
        [tool_reply("bash", {"command": "true"}, call_id="b1"), text_reply("done")]
    )
    [_ async for _ in run(session, transport=second, max_tool_calls=1, policy=Policy())]

    # The resume got its own allowance: its one call ran.
    assert session.status == "done"
    assert results(second, 1)[0]["is_error"] is False
    assert session.tool_calls == 2


def test_the_lifetime_count_is_saved(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "s.json"

    main(
        ["run", "--yes", "--jsonl", "--root", str(tmp_path), "--session", str(path)]
        + ["go"],
        transport=FakeProvider([writes(tmp_path, "a", "b"), text_reply("ok")]),
    )

    capsys.readouterr()
    assert loads(path.read_text(encoding="utf-8")).tool_calls == 2


def test_a_session_without_the_count_loads(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    main(
        ["run", "--yes", "--session", str(path), "go"],
        transport=FakeProvider([text_reply("ok")]),
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["tool_calls"]

    assert loads(json.dumps(raw)).tool_calls == 0


class MCPModel:
    """Calls the stdio test server's echo twice, then answers."""

    def __init__(self) -> None:
        self.messages: list[list[Message]] = []

    async def context_window(self) -> int | None:
        return None

    async def turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> Reply:
        self.messages.append(list(messages))
        if len(self.messages) == 1:
            calls = [
                ToolCall(f"e{n}", "local__echo", {"text": f"hi {n}"}) for n in (1, 2)
            ]
            return Reply(
                [
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.args}
                    for c in calls
                ],
                calls,
                Usage(),
                "tool_use",
            )
        return Reply([{"type": "text", "text": "finished"}], [], Usage(), "end_turn")


def test_mcp_calls_count_against_the_cap(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "local": {
                    "command": sys.executable,
                    "args": [
                        str(Path(__file__).with_name("mcp_server.py")),
                        str(tmp_path / "pid"),
                    ],
                    "timeout": 5,
                }
            }
        )
    )
    model = MCPModel()

    code = main(
        ["run", "--yes", "--jsonl", "--mcp-config", str(config)]
        + ["--max-tool-calls", "1", "go"],
        transport=model,
    )

    result = lines(capsys.readouterr().out)[-1]
    assert code == 0
    sent = model.messages[1][-1].content
    assert [r["is_error"] for r in sent] == [False, True]
    assert result["budget"]["used_tool_calls"] == 1


@pytest.mark.parametrize("value", ["0", "-1", "two", "1.5"])
def test_an_invalid_cap_never_starts(
    value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    try:
        code = main(["run", "--yes", "--jsonl", "--max-tool-calls", value, "go"])
    except SystemExit as exc:  # argparse refuses what is not an integer
        code = int(exc.code or 0)

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "--max-tool-calls" in captured.err


async def test_the_library_refuses_an_invalid_cap() -> None:
    with pytest.raises(ValueError, match="max_tool_calls"):
        [
            _
            async for _ in run(
                new_session("go"), transport=FakeProvider([]), max_tool_calls=0
            )
        ]


def test_without_a_cap_the_budget_reports_calls_used_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(
        ["run", "--yes", "--jsonl", "--root", str(tmp_path), "go"],
        transport=FakeProvider([writes(tmp_path, "a", "b"), text_reply("ok")]),
    )

    budget = lines(capsys.readouterr().out)[-1]["budget"]
    assert "tool_calls" not in budget
    assert budget["used_tool_calls"] == 2


def test_the_contract_lists_the_flag_and_the_stop_reason() -> None:
    described = describe()
    kinds = {row["stop_reason"]: row["kind"] for row in described["stop_reasons"]}

    assert kinds["tool_calls"] == "limit"
    assert "--max-tool-calls" in described["flags"]
    assert described["tool_call_limit"] == {
        "flag": "--max-tool-calls",
        "per": "invocation",
        "stop_reason": "tool_calls",
        "budget_keys": ["tool_calls", "used_tool_calls"],
    }
    text = DOC.read_text(encoding="utf-8")
    assert "`used_tool_calls`" in text
