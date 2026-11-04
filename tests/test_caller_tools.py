"""Caller-registered tools: the in-process Tool, and its path through run, step
and the command line.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider
from nare import Event, Policy, Tool, ToolError, new_session, run, step
from nare.cli import main
from nare.session import Session, Usage
from nare.tools import TOOLS
from nare.transport import Reply, ToolCall

LOOKUP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"id": {"type": "integer"}},
    "required": ["id"],
}


def call_reply(name: str, args: dict[str, Any], call_id: str = "c1") -> Reply:
    return Reply(
        [{"type": "tool_use", "id": call_id, "name": name, "input": args}],
        [ToolCall(call_id, name, args)],
        Usage(),
        "tool_use",
    )


def final_reply(text: str = "finished") -> Reply:
    return Reply([{"type": "text", "text": text}], [], Usage(), "end_turn")


def lookup(args: dict[str, Any]) -> str:
    return f"record {args['id']}"


def tool(call: Any = lookup, name: str = "lookup") -> Tool:
    return Tool(
        name=name,
        description="Fetch one record by id.",
        input_schema=LOOKUP_SCHEMA,
        call=call,
    )


def result_of(session: Session) -> dict[str, Any]:
    """The last tool_result block the model was sent."""
    for message in reversed(session.messages):
        for block in message.content:
            if block.get("type") == "tool_result":
                return block
    raise AssertionError("no tool_result in the transcript")


async def collect(stream: Any) -> list[Event]:
    return [event async for event in stream]


# Task 1: declarations are checked before a run starts.


@pytest.mark.parametrize(
    "name",
    ["", "bad name", "a__b", "x" * 65, "read", "bash", "dotted.name"],
)
def test_tool_rejects_a_bad_name(name: str) -> None:
    with pytest.raises(ValueError):
        tool(name=name)


def test_tool_rejects_an_empty_description() -> None:
    with pytest.raises(ValueError):
        Tool(name="lookup", description="  ", input_schema=LOOKUP_SCHEMA, call=lookup)


@pytest.mark.parametrize("schema", [{"type": "array"}, {}, "object"])
def test_tool_rejects_a_schema_that_is_not_an_object(schema: Any) -> None:
    with pytest.raises(ValueError):
        Tool(name="lookup", description="x", input_schema=schema, call=lookup)


def test_tool_rejects_a_call_that_is_not_callable() -> None:
    not_callable: Any = "no"
    with pytest.raises(ValueError):
        Tool(
            name="lookup",
            description="x",
            input_schema=LOOKUP_SCHEMA,
            call=not_callable,
        )


async def test_duplicate_tool_names_are_refused_before_a_model_call() -> None:
    model = FakeProvider([final_reply()])
    with pytest.raises(ValueError, match="lookup"):
        await collect(run(new_session("go"), transport=model, tools=[tool(), tool()]))
    assert model.calls == []


# Task 2: dispatch.


async def test_model_calls_a_caller_tool_and_the_events_match_a_builtin() -> None:
    model = FakeProvider([call_reply("lookup", {"id": 42}), final_reply()])
    session = new_session("look up 42")
    events = await collect(run(session, transport=model, tools=[tool()]))

    assert session.status == "done"
    block = result_of(session)
    assert block["content"] == "record 42"
    assert block["is_error"] is False
    assert [e.text for e in events if e.type == "tool_use"] == ["lookup"]
    results = [
        e
        for e in events
        if e.type == "progress" and e.detail.get("tool_result") is not None
    ]
    assert len(results) == 1
    assert results[0].text == "caller result: lookup"
    assert results[0].tool_use_id == "c1"
    assert results[0].detail["tool_result"]["is_error"] is False


async def test_arguments_are_checked_before_approval() -> None:
    asked: list[str] = []

    def approve(name: str, args: dict[str, Any]) -> bool:
        asked.append(name)
        return True

    calls: list[Any] = []

    def spy(args: dict[str, Any]) -> str:
        calls.append(args)
        return "never"

    model = FakeProvider(
        [call_reply("lookup", {"id": "not a number", "extra": 1}), final_reply()]
    )
    session = new_session("go")
    await collect(run(session, transport=model, approve=approve, tools=[tool(spy)]))

    block = result_of(session)
    assert block["is_error"] is True
    assert "id" in block["content"]
    assert asked == []
    assert calls == []


async def test_a_denied_caller_tool_never_runs() -> None:
    calls: list[Any] = []

    def spy(args: dict[str, Any]) -> str:
        calls.append(args)
        return "ran"

    model = FakeProvider([call_reply("lookup", {"id": 1}), final_reply()])
    session = new_session("go")
    await collect(
        run(
            session,
            transport=model,
            approve=lambda name, args: "not today",
            tools=[tool(spy)],
        )
    )
    block = result_of(session)
    assert block["is_error"] is True
    assert block["content"] == "lookup was not approved: not today"
    assert calls == []


async def test_failures_are_ordinary_results_and_the_run_continues() -> None:
    def expected(args: dict[str, Any]) -> str:
        raise ToolError("no such record")

    def unexpected(args: dict[str, Any]) -> str:
        raise KeyError("k")

    model = FakeProvider(
        [
            call_reply("lookup", {"id": 1}, "a"),
            call_reply("other", {"id": 1}, "b"),
            final_reply(),
        ]
    )
    session = new_session("go")
    await collect(
        run(
            session,
            transport=model,
            tools=[tool(expected), tool(unexpected, name="other")],
        )
    )
    assert session.status == "done"
    results = [
        block
        for message in session.messages
        for block in message.content
        if block.get("type") == "tool_result"
    ]
    assert [(r["content"], r["is_error"]) for r in results] == [
        ("no such record", True),
        ("KeyError: 'k'", True),
    ]


async def test_async_and_structured_results() -> None:
    async def fetch(args: dict[str, Any]) -> dict[str, Any]:
        return {"id": args["id"], "ok": True}

    model = FakeProvider([call_reply("lookup", {"id": 7}), final_reply()])
    session = new_session("go")
    await collect(run(session, transport=model, tools=[tool(fetch)]))
    assert json.loads(result_of(session)["content"]) == {"id": 7, "ok": True}


# Task 3: allowlists.


async def test_an_allowlist_that_excludes_a_caller_tool_keeps_it_out() -> None:
    ran: list[Any] = []

    def spy(args: dict[str, Any]) -> str:
        ran.append(args)
        return "ran"

    model = FakeProvider([call_reply("lookup", {"id": 1}), final_reply()])
    session = new_session("go")
    await collect(
        run(
            session,
            transport=model,
            policy=Policy(tools=frozenset({"read"})),
            tools=[tool(spy)],
        )
    )
    offered = [s["name"] for s in model.calls[0][1]]
    assert "lookup" not in offered
    assert "read" in offered
    block = result_of(session)
    assert block["is_error"] is True
    assert "not allowed" in block["content"]
    assert ran == []


async def test_an_allowlist_naming_a_caller_tool_offers_it() -> None:
    model = FakeProvider([final_reply()])
    policy = Policy(tools=frozenset(TOOLS) | {"lookup"}, pending=frozenset({"lookup"}))
    session = new_session("go")
    await collect(run(session, transport=model, policy=policy, tools=[tool()]))
    offered = [s["name"] for s in model.calls[0][1]]
    assert "lookup" in offered


async def test_no_policy_offers_every_registered_caller_tool() -> None:
    model = FakeProvider([final_reply()])
    await collect(run(new_session("go"), transport=model, tools=[tool()]))
    assert "lookup" in [s["name"] for s in model.calls[0][1]]


async def test_step_offers_and_runs_a_caller_tool() -> None:
    model = FakeProvider([call_reply("lookup", {"id": 9})])
    policy = Policy(tools=frozenset(TOOLS) | {"lookup"}, pending=frozenset({"lookup"}))
    session = new_session("go")

    async def approve_all(name: str, args: dict[str, Any]) -> bool:
        return True

    await step(session, model, approve_all, policy, tools=[tool()])
    assert result_of(session)["content"] == "record 9"


async def test_step_without_the_allowlist_does_not_offer_it() -> None:
    model = FakeProvider([final_reply()])
    session = new_session("go")
    await step(session, model, lambda name, args: True, Policy(), tools=[tool()])
    assert "lookup" not in [s["name"] for s in model.calls[0][1]]


# Task 5: the command line.


def write_command(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "tool.py"
    script.write_text(body, encoding="utf-8")
    return script


def command_file(
    tmp_path: Path, command: list[str], timeout: float | None = None
) -> Path:
    entry: dict[str, Any] = {
        "description": "Fetch one record by id.",
        "input_schema": LOOKUP_SCHEMA,
        "command": command,
    }
    if timeout is not None:
        entry["timeout"] = timeout
    path = tmp_path / "tools.json"
    path.write_text(json.dumps({"lookup": entry}), encoding="utf-8")
    return path


def test_cli_command_tool_returns_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = write_command(
        tmp_path,
        "import json, sys\nargs = json.load(sys.stdin)\nprint('record', args['id'])\n",
    )
    path = command_file(tmp_path, [sys.executable, str(script)])
    model = FakeProvider([call_reply("lookup", {"id": 42}), final_reply()])
    code = main(
        ["run", "go", "--yes", "--jsonl", "--command-tools", str(path)],
        transport=model,
    )
    assert code == 0
    block = model.calls[1][0][-1].content[0]
    assert block["content"].strip() == "record 42"
    assert block["is_error"] is False


def test_cli_command_tool_failure_is_an_error_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = write_command(
        tmp_path,
        "import sys\nsys.stderr.write('no such record\\n')\nsys.exit(3)\n",
    )
    path = command_file(tmp_path, [sys.executable, str(script)])
    model = FakeProvider([call_reply("lookup", {"id": 1}), final_reply()])
    code = main(
        ["run", "go", "--yes", "--jsonl", "--command-tools", str(path)],
        transport=model,
    )
    assert code == 0
    block = model.calls[1][0][-1].content[0]
    assert block["is_error"] is True
    assert "no such record" in block["content"]
    assert "3" in block["content"]


def test_cli_command_tool_timeout_is_an_error_result(tmp_path: Path) -> None:
    script = write_command(tmp_path, "import time\ntime.sleep(30)\n")
    path = command_file(tmp_path, [sys.executable, str(script)], timeout=0.5)
    model = FakeProvider([call_reply("lookup", {"id": 1}), final_reply()])
    code = main(["run", "go", "--yes", "--command-tools", str(path)], transport=model)
    assert code == 0
    block = model.calls[1][0][-1].content[0]
    assert block["is_error"] is True
    assert "timed out" in block["content"]


def test_cli_command_tool_obeys_the_tools_allowlist(tmp_path: Path) -> None:
    script = write_command(tmp_path, "print('x')\n")
    path = command_file(tmp_path, [sys.executable, str(script)])
    model = FakeProvider([final_reply()])
    code = main(
        ["run", "go", "--yes", "--command-tools", str(path), "--tools", "read"],
        transport=model,
    )
    assert code == 0
    assert [s["name"] for s in model.calls[0][1]] == ["read"]


def test_cli_command_tool_named_in_tools_is_offered(tmp_path: Path) -> None:
    script = write_command(tmp_path, "print('x')\n")
    path = command_file(tmp_path, [sys.executable, str(script)])
    model = FakeProvider([final_reply()])
    code = main(
        [
            "run",
            "go",
            "--yes",
            "--command-tools",
            str(path),
            "--tools",
            "read,lookup",
        ],
        transport=model,
    )
    assert code == 0
    assert sorted(s["name"] for s in model.calls[0][1]) == ["lookup", "read"]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"lookup": {"description": "x", "input_schema": LOOKUP_SCHEMA}},
        {
            "lookup": {
                "description": "x",
                "input_schema": LOOKUP_SCHEMA,
                "command": "tool",
            }
        },
        {"lookup": {"description": "x", "input_schema": LOOKUP_SCHEMA, "command": []}},
        {"read": {"description": "x", "input_schema": LOOKUP_SCHEMA, "command": ["x"]}},
        {
            "lookup": {
                "description": "x",
                "input_schema": LOOKUP_SCHEMA,
                "command": ["x"],
                "shell": True,
            }
        },
        {
            "lookup": {
                "description": "x",
                "input_schema": LOOKUP_SCHEMA,
                "command": ["x"],
                "timeout": 0,
            }
        },
    ],
)
def test_cli_invalid_command_tools_never_start(
    tmp_path: Path, payload: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "tools.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    model = FakeProvider([final_reply()])
    code = main(
        ["run", "go", "--yes", "--jsonl", "--command-tools", str(path)],
        transport=model,
    )
    assert code == 2
    assert model.calls == []
    assert capsys.readouterr().out == ""


def test_contract_describes_caller_tools() -> None:
    from nare.contract import describe

    section = describe()["caller_tools"]
    assert section["flag"] == "--command-tools"
    assert section["in_process"] == "nare.Tool"
    assert section["before"] == "approval"
    assert section["default_timeout"] == 30.0


def test_the_documented_contract_describes_caller_tools() -> None:
    doc = Path(__file__).parent.parent / "docs" / "contract.md"
    text = doc.read_text(encoding="utf-8")

    assert "## Caller tools" in text
    assert "--command-tools" in text
    assert "as `tools=` to `nare.run`" in text


def test_tool_rejects_a_schema_nare_cannot_enforce() -> None:
    # Refused at registration, so a run never meets it at the first call.
    with pytest.raises(ValueError, match="lookup"):
        Tool(
            name="lookup",
            description="x",
            input_schema={"type": "object", "properties": []},
            call=lookup,
        )


async def test_a_step_keeps_names_the_policy_allows_as_pending() -> None:
    # A name allowlisted but not registered stays allowed beside step's tools.
    model = FakeProvider([final_reply()])
    policy = Policy(
        tools=frozenset(TOOLS) | {"srv__later", "lookup"},
        pending=frozenset({"srv__later", "lookup"}),
    )
    await step(new_session("go"), model, lambda n, a: True, policy, tools=[tool()])
    assert "lookup" in [s["name"] for s in model.calls[0][1]]


async def test_a_cancelled_command_tool_is_killed(tmp_path: Path) -> None:
    import asyncio
    import os

    from nare.caller_tools import command_tools

    pid_file = tmp_path / "pid"
    script = write_command(
        tmp_path,
        "import os, time, pathlib, sys\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(30)\n",
    )
    (lookup_tool,) = command_tools(
        {
            "lookup": {
                "description": "x",
                "input_schema": LOOKUP_SCHEMA,
                "command": [sys.executable, str(script)],
                "timeout": 60,
            }
        }
    )
    task = asyncio.create_task(lookup_tool.call({"id": 1}))
    for _ in range(200):
        if pid_file.exists():
            break
        await asyncio.sleep(0.02)
    pid = int(pid_file.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(200):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the cancelled command is still running")
