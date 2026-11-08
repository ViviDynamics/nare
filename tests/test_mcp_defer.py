"""MCP tool definitions deferred behind a search step (issue #242).

A fake stdio MCP server with many tools stands in for a real one, so every test
runs offline. The model is a scripted FakeProvider that records each request.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider, text_reply, tool_reply
from nare.cli import main
from nare.contract import describe
from nare.loop import prefix_changes, run
from nare.mcp import parse_servers
from nare.session import Message, Session, dumps, loads, new_session, reopen
from nare.tools import Policy, approve_all

SERVER = str(Path(__file__).with_name("mcp_server.py"))
ECHO = "local__echo"
SEARCH, LOAD, CALL = "mcp_search", "mcp_load", "mcp_call"


def servers(tmp_path: Path, count: int = 40) -> dict[str, Any]:
    pid = tmp_path / "pid"
    return parse_servers(
        {
            "local": {
                "command": sys.executable,
                "args": [SERVER, str(pid), "--many", str(count)],
                "timeout": 5,
            }
        }
    )


def names(tools: list[dict[str, Any]]) -> list[str]:
    return [t["name"] for t in tools]


def results_of(session: Session) -> list[dict[str, Any]]:
    """Every tool_result block in the transcript, in order."""
    return [
        block
        for message in session.messages
        for block in message.content
        if block.get("type") == "tool_result"
    ]


def result_for(session: Session, call_id: str) -> dict[str, Any]:
    [found] = [b for b in results_of(session) if b["tool_use_id"] == call_id]
    return found


async def drive(
    session: Session,
    fake: FakeProvider,
    *,
    mcp: dict[str, Any],
    defer: bool,
    approve: Any = approve_all,
    policy: Policy | None = None,
) -> list[Any]:
    return [
        event
        async for event in run(
            session,
            transport=fake,
            approve=approve,
            policy=policy,
            mcp_servers=mcp,
            mcp_allow_all=policy is None,
            mcp_defer=defer,
        )
    ]


def recorder(seen: list[str]) -> Any:
    """An approver that records each tool it is asked about, and approves it."""

    def approve(tool: str, args: dict[str, Any]) -> bool:
        seen.append(tool)
        return True

    return approve


def offered(fake: FakeProvider, turn: int) -> list[dict[str, Any]]:
    return fake.calls[turn][1]


# -- Off is unchanged ---------------------------------------------------------


async def test_off_offers_every_mcp_definition_and_no_meta_tools(
    tmp_path: Path,
) -> None:
    fake = FakeProvider([text_reply("done")])
    session = new_session("go")

    await drive(session, fake, mcp=servers(tmp_path), defer=False)

    sent = names(offered(fake, 0))
    assert "local__filler_001" in sent
    assert ECHO in sent
    assert not {SEARCH, LOAD, CALL} & set(sent)
    recorded = session.policy
    assert "mcp_defer" not in recorded
    assert "mcp_loaded" not in json.loads(dumps(session))
    assert session.mcp_loaded == {}


async def test_off_has_the_same_request_and_policy_record_as_before(
    tmp_path: Path,
) -> None:
    # Off is the default: a run that never names the setting sends the same
    # tools and writes the same record as a run that passes False.
    default = FakeProvider([text_reply("done")])
    explicit = FakeProvider([text_reply("done")])
    first, second = new_session("go"), new_session("go")
    mcp = servers(tmp_path)

    await drive(first, default, mcp=mcp, defer=False)
    async for _ in run(second, transport=explicit, mcp_servers=mcp, mcp_allow_all=True):
        pass

    assert names(offered(default, 0)) == names(offered(explicit, 0))
    assert first.policy == second.policy


# -- Deferral sends no MCP definitions ---------------------------------------


async def test_deferral_offers_built_ins_and_the_three_meta_tools_only(
    tmp_path: Path,
) -> None:
    fake = FakeProvider([text_reply("done")])
    session = new_session("go")

    await drive(session, fake, mcp=servers(tmp_path), defer=True)

    sent = names(offered(fake, 0))
    assert not any(name.startswith("local__") for name in sent)
    assert sent[-3:] == [SEARCH, LOAD, CALL]
    assert "read" in sent
    assert session.policy["mcp_defer"] is True


async def test_deferral_shrinks_the_request_by_most_of_the_definitions(
    tmp_path: Path,
) -> None:
    mcp = servers(tmp_path)
    off, on = FakeProvider([text_reply("a")]), FakeProvider([text_reply("b")])

    await drive(new_session("go"), off, mcp=mcp, defer=False)
    await drive(new_session("go"), on, mcp=mcp, defer=True)

    before = len(json.dumps(offered(off, 0)))
    after = len(json.dumps(offered(on, 0)))
    assert after < before * 0.2, (before, after)


async def test_deferral_with_no_mcp_tools_is_the_same_request_as_off() -> None:
    off, on = FakeProvider([text_reply("a")]), FakeProvider([text_reply("b")])
    await drive(new_session("go"), off, mcp={}, defer=False)
    await drive(new_session("go"), on, mcp={}, defer=True)

    assert offered(on, 0) == offered(off, 0)


# -- Search -------------------------------------------------------------------


async def test_search_finds_by_description_and_loads_nothing(tmp_path: Path) -> None:
    fake = FakeProvider(
        [
            tool_reply(SEARCH, {"query": "echo"}),
            text_reply("found it"),
        ]
    )
    session = new_session("go")

    await drive(session, fake, mcp=servers(tmp_path), defer=True)

    found = result_for(session, "call_1")
    assert found["is_error"] is False
    assert ECHO in found["content"]
    assert "Echo the given text back" in found["content"]
    assert "input_schema" not in found["content"]
    assert session.mcp_loaded == {}


async def test_search_caps_its_matches_and_says_so(tmp_path: Path) -> None:
    fake = FakeProvider([tool_reply(SEARCH, {"query": ""}), text_reply("ok")])
    session = new_session("go")

    await drive(session, fake, mcp=servers(tmp_path, 60), defer=True)

    content = result_for(session, "call_1")["content"]
    listed = [line for line in content.splitlines() if line.startswith("local__")]
    assert len(listed) == 20
    assert "more match" in content


async def test_search_with_no_match_is_an_error_that_names_the_query(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(
        [tool_reply(SEARCH, {"query": "zzz-nothing"}), text_reply("ok")]
    )
    session = new_session("go")

    await drive(session, fake, mcp=servers(tmp_path), defer=True)

    found = result_for(session, "call_1")
    assert found["is_error"] is True
    assert "zzz-nothing" in found["content"]


# -- Load then call in one run ------------------------------------------------


async def test_load_then_call_in_one_run_reaches_the_server(tmp_path: Path) -> None:
    fake = FakeProvider(
        [
            tool_reply(LOAD, {"names": [ECHO]}, call_id="c_load"),
            tool_reply(
                CALL,
                {"name": ECHO, "arguments": {"text": "pineapple"}},
                call_id="c_call",
            ),
            text_reply("the word was pineapple"),
        ]
    )
    session = new_session("go")
    approvals: list[tuple[str, dict[str, Any]]] = []

    def record(tool: str, args: dict[str, Any]) -> bool:
        approvals.append((tool, args))
        return True

    events = await drive(
        session, fake, mcp=servers(tmp_path), defer=True, approve=record
    )

    loaded = result_for(session, "c_load")
    assert loaded["is_error"] is False
    definition = json.loads(loaded["content"])["tools"][0]
    assert definition["name"] == ECHO
    assert definition["input_schema"]["required"] == ["text"]

    called = result_for(session, "c_call")
    assert called == {
        "type": "tool_result",
        "tool_use_id": "c_call",
        "content": "pineapple",
        "is_error": False,
    }
    assert approvals == [(ECHO, {"text": "pineapple"})]
    assert session.status == "done"
    assert set(session.mcp_loaded) == {ECHO}
    assert any(e.detail.get("mcp_defer") == {"loaded": [ECHO]} for e in events)


async def test_load_reports_names_it_does_not_know(tmp_path: Path) -> None:
    fake = FakeProvider(
        [tool_reply(LOAD, {"names": [ECHO, "local__nope"]}), text_reply("ok")]
    )
    session = new_session("go")

    await drive(session, fake, mcp=servers(tmp_path), defer=True)

    found = result_for(session, "call_1")
    assert found["is_error"] is False
    body = json.loads(found["content"])
    assert [t["name"] for t in body["tools"]] == [ECHO]
    assert body["not_found"] == ["local__nope"]


async def test_load_refuses_more_than_eight_names(tmp_path: Path) -> None:
    many = [f"local__filler_{n:03d}" for n in range(1, 10)]
    fake = FakeProvider([tool_reply(LOAD, {"names": many}), text_reply("ok")])
    session = new_session("go")

    await drive(session, fake, mcp=servers(tmp_path), defer=True)

    found = result_for(session, "call_1")
    assert found["is_error"] is True
    assert "at most 8" in found["content"]
    assert session.mcp_loaded == {}


# -- Refusals never reach the server or approval ------------------------------


async def test_call_before_load_is_refused_and_never_approved(tmp_path: Path) -> None:
    fake = FakeProvider(
        [
            tool_reply(CALL, {"name": ECHO, "arguments": {"text": "x"}}),
            text_reply("ok"),
        ]
    )
    session = new_session("go")
    approvals: list[str] = []

    await drive(
        session,
        fake,
        mcp=servers(tmp_path),
        defer=True,
        approve=recorder(approvals),
    )

    found = result_for(session, "call_1")
    assert found["is_error"] is True
    assert "mcp_load" in found["content"]
    assert approvals == []


async def test_direct_call_by_the_deferred_name_is_refused_with_the_way_in(
    tmp_path: Path,
) -> None:
    fake = FakeProvider([tool_reply(ECHO, {"text": "x"}), text_reply("ok")])
    session = new_session("go")
    approvals: list[str] = []

    await drive(
        session,
        fake,
        mcp=servers(tmp_path),
        defer=True,
        approve=recorder(approvals),
    )

    found = result_for(session, "call_1")
    assert found["is_error"] is True
    assert "mcp_load" in found["content"]
    assert approvals == []


async def test_call_of_an_unknown_name_is_refused(tmp_path: Path) -> None:
    fake = FakeProvider(
        [
            tool_reply(CALL, {"name": "local__missing", "arguments": {}}),
            text_reply("ok"),
        ]
    )
    session = new_session("go")

    await drive(session, fake, mcp=servers(tmp_path), defer=True)

    found = result_for(session, "call_1")
    assert found["is_error"] is True
    assert "not a deferred MCP tool" in found["content"]


async def test_call_without_a_name_is_refused(tmp_path: Path) -> None:
    fake = FakeProvider([tool_reply(CALL, {"arguments": {}}), text_reply("ok")])
    session = new_session("go")

    await drive(session, fake, mcp=servers(tmp_path), defer=True)

    assert result_for(session, "call_1")["is_error"] is True


async def test_a_tool_the_allowlist_excludes_is_not_searchable_or_callable(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(
        [
            tool_reply(SEARCH, {"query": "echo"}, call_id="c_search"),
            tool_reply(
                CALL, {"name": ECHO, "arguments": {"text": "x"}}, call_id="c_call"
            ),
            text_reply("ok"),
        ]
    )
    session = new_session("go")
    policy = Policy(
        tools=frozenset({"read", "local__filler_001"}),
        pending=frozenset({"local__filler_001"}),
    )
    approvals: list[str] = []

    await drive(
        session,
        fake,
        mcp=servers(tmp_path),
        defer=True,
        policy=policy,
        approve=recorder(approvals),
    )

    assert "local__echo" not in result_for(session, "c_search")["content"]
    assert result_for(session, "c_call")["is_error"] is True
    assert approvals == []
    sent = names(offered(fake, 0))
    assert "local__filler_001" not in sent


async def test_a_denied_deferred_call_comes_back_as_an_error(tmp_path: Path) -> None:
    fake = FakeProvider(
        [
            tool_reply(LOAD, {"names": [ECHO]}, call_id="c_load"),
            tool_reply(
                CALL, {"name": ECHO, "arguments": {"text": "x"}}, call_id="c_call"
            ),
            text_reply("ok"),
        ]
    )
    session = new_session("go")

    await drive(
        session,
        fake,
        mcp=servers(tmp_path),
        defer=True,
        approve=lambda tool, args: False,
    )

    denied = result_for(session, "c_call")
    assert denied["is_error"] is True
    assert f"{ECHO} was not approved" in denied["content"]


# -- Cache stability ----------------------------------------------------------


async def test_the_tools_array_is_byte_equal_across_every_turn(tmp_path: Path) -> None:
    fake = FakeProvider(
        [
            tool_reply(SEARCH, {"query": "echo"}, call_id="c1"),
            tool_reply(LOAD, {"names": [ECHO]}, call_id="c2"),
            tool_reply(CALL, {"name": ECHO, "arguments": {"text": "x"}}, call_id="c3"),
            text_reply("done"),
        ]
    )

    await drive(new_session("go"), fake, mcp=servers(tmp_path), defer=True)

    arrays = [json.dumps(tools, sort_keys=False) for _, tools in fake.calls]
    assert len(arrays) == 4
    assert len(set(arrays)) == 1


async def test_each_request_extends_the_last_one_without_rewriting_it(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(
        [
            tool_reply(LOAD, {"names": [ECHO]}, call_id="c2"),
            tool_reply(CALL, {"name": ECHO, "arguments": {"text": "x"}}, call_id="c3"),
            text_reply("done"),
        ]
    )

    await drive(new_session("go"), fake, mcp=servers(tmp_path), defer=True)

    for (before, _), (after, _) in zip(fake.calls, fake.calls[1:], strict=False):
        assert [asdict(m) for m in after[: len(before)]] == [asdict(m) for m in before]


# -- Resume -------------------------------------------------------------------


async def test_a_resume_restores_the_loaded_set_and_calls_without_loading(
    tmp_path: Path,
) -> None:
    mcp = servers(tmp_path)
    first = FakeProvider(
        [tool_reply(LOAD, {"names": [ECHO]}, call_id="c1"), text_reply("loaded")]
    )
    session = new_session("go")
    await drive(session, first, mcp=mcp, defer=True)
    saved = loads(dumps(session))
    assert set(saved.mcp_loaded) == {ECHO}

    second = FakeProvider(
        [
            tool_reply(
                CALL, {"name": ECHO, "arguments": {"text": "again"}}, call_id="c2"
            ),
            text_reply("again"),
        ]
    )
    reopen(saved, "call it now")
    events = await drive(saved, second, mcp=mcp, defer=True)

    assert result_for(saved, "c2")["content"] == "again"
    assert result_for(saved, "c2")["is_error"] is False
    assert offered(second, 0) == offered(first, 0)
    assert not any("MCP tool definitions changed" in e.text for e in events)


async def test_a_changed_definition_on_resume_drops_only_that_entry(
    tmp_path: Path,
) -> None:
    mcp = servers(tmp_path)
    session = new_session("go")
    await drive(
        session,
        FakeProvider(
            [tool_reply(LOAD, {"names": [ECHO]}, call_id="c1"), text_reply("loaded")]
        ),
        mcp=mcp,
        defer=True,
    )
    session.mcp_loaded = {ECHO: "0" * 64}
    reopen(session, "again")
    fake = FakeProvider(
        [
            tool_reply(CALL, {"name": ECHO, "arguments": {"text": "x"}}, call_id="c2"),
            text_reply("ok"),
        ]
    )

    events = await drive(session, fake, mcp=mcp, defer=True)

    assert session.mcp_loaded == {}
    refused = result_for(session, "c2")
    assert refused["is_error"] is True
    assert "mcp_load" in refused["content"]
    [dropped] = [e for e in events if e.detail.get("mcp_defer", {}).get("dropped")]
    assert dropped.detail["mcp_defer"]["dropped"] == [ECHO]


async def test_a_changed_deferred_definition_keeps_signed_thinking(
    tmp_path: Path,
) -> None:
    # The tools array is the same, so the signed thinking made against it stays.
    mcp = servers(tmp_path)
    session = new_session("go")
    await drive(
        session,
        FakeProvider(
            [tool_reply(LOAD, {"names": [ECHO]}, call_id="c1"), text_reply("loaded")]
        ),
        mcp=mcp,
        defer=True,
    )
    session.messages.insert(
        1,
        Message(
            "assistant",
            [{"type": "thinking", "thinking": "signed", "signature": "sig"}],
        ),
    )
    session.mcp_loaded = {ECHO: "0" * 64}
    reopen(session, "again")
    events = await drive(session, FakeProvider([text_reply("ok")]), mcp=mcp, defer=True)

    assert not any(e.detail.get("tools", {}).get("thinking_dropped") for e in events)
    kept = [
        b
        for m in session.messages
        for b in m.content
        if b.get("type") == "thinking" and b.get("signature")
    ]
    assert len(kept) == 1


def test_turning_deferral_on_or_off_is_a_prefix_change(tmp_path: Path) -> None:
    from nare.mcp import ExternalTool

    async def call(args: dict[str, Any]) -> tuple[str, bool]:
        return "ok", False

    tool = ExternalTool(
        schema={"name": ECHO, "input_schema": {"type": "object"}}, call=call
    )
    names_ = frozenset({ECHO, "read"})
    off = Policy(tools=names_, external={ECHO: tool}).recorded()
    on = Policy(tools=names_, external={ECHO: tool}, defer_mcp=True).recorded()

    assert prefix_changes(off, on) == ["MCP tool deferral turned on"]
    assert prefix_changes(on, off) == ["MCP tool deferral turned off"]
    assert prefix_changes(on, on) == []


async def test_turning_deferral_off_on_resume_drops_signed_thinking(
    tmp_path: Path,
) -> None:
    mcp = servers(tmp_path)
    session = new_session("go")
    await drive(
        session,
        FakeProvider(
            [tool_reply(LOAD, {"names": [ECHO]}, call_id="c1"), text_reply("loaded")]
        ),
        mcp=mcp,
        defer=True,
    )
    session.messages.insert(
        1,
        Message(
            "assistant",
            [{"type": "thinking", "thinking": "signed", "signature": "sig"}],
        ),
    )
    reopen(session, "now without deferral")
    events = await drive(
        session, FakeProvider([text_reply("ok")]), mcp=mcp, defer=False
    )

    [change] = [e for e in events if e.detail.get("tools")]
    assert change.detail["tools"]["changed"] == ["MCP tool deferral turned off"]
    assert change.detail["tools"]["thinking_dropped"] == 1
    assert session.mcp_loaded == {}


# -- Contract and CLI ---------------------------------------------------------


def test_cli_flag_reaches_the_run(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "local": {
                    "command": sys.executable,
                    "args": [SERVER, str(tmp_path / "pid"), "--many", "40"],
                    "timeout": 5,
                }
            }
        )
    )
    fake = FakeProvider([text_reply("ok")])

    code = main(
        ["run", "go", "--yes", "--mcp-config", str(path), "--mcp-defer"],
        transport=fake,
    )

    assert code == 0
    sent = names(offered(fake, 0))
    assert SEARCH in sent and not any(n.startswith("local__") for n in sent)


def test_contract_describes_deferral() -> None:
    block = describe()["mcp_deferral"]

    assert block["flag"] == "--mcp-defer"
    assert block["default"] is False
    assert block["tools"] == [SEARCH, LOAD, CALL]
    assert block["session_field"] == "mcp_loaded"
    assert block["event_detail"] == "mcp_defer"


def test_the_documented_contract_describes_deferral() -> None:
    text = (Path(__file__).parent.parent / "docs" / "contract.md").read_text(
        encoding="utf-8"
    )
    for name in ("--mcp-defer", SEARCH, LOAD, CALL, "mcp_loaded", "mcp_defer"):
        assert name in text, name


def test_a_loaded_session_round_trips_through_the_file(tmp_path: Path) -> None:
    session = new_session("go")
    session.mcp_loaded = {ECHO: "a" * 64}

    assert loads(dumps(session)).mcp_loaded == {ECHO: "a" * 64}


def test_an_empty_loaded_set_is_not_written(tmp_path: Path) -> None:
    session = new_session("go")
    assert "mcp_loaded" not in json.loads(dumps(session))


def test_a_loaded_set_of_the_wrong_shape_is_refused(tmp_path: Path) -> None:
    session = new_session("go")
    raw = json.loads(dumps(session))
    raw["mcp_loaded"] = ["not", "a", "map"]
    with pytest.raises(ValueError):
        loads(json.dumps(raw))


async def test_a_caller_tool_named_like_a_meta_tool_is_refused_up_front() -> None:
    from nare.caller_tools import Tool

    shadow = Tool(
        name="mcp_load",
        description="x",
        input_schema={"type": "object"},
        call=lambda args: "ok",
    )
    fake = FakeProvider([text_reply("never sent")])

    with pytest.raises(ValueError, match="reserved while MCP tool deferral"):
        async for _ in run(
            new_session("go"),
            transport=fake,
            tools=[shadow],
            mcp_defer=True,
        ):
            pass
    assert fake.calls == []
