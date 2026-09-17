from pathlib import Path

import nare
from fake_provider import Exploding, FakeProvider, text_reply, tool_reply
from nare.loop import MAX_TURNS_DEFAULT, run, step
from nare.session import (
    Session,
    Status,
    Usage,
    append_user_text,
    dumps,
    loads,
    new_session,
)
from nare.tools import approve_all
from nare.transport import Transport


async def test_a_reply_without_tool_calls_finishes_the_session() -> None:
    session = new_session("say hi")
    await step(session, FakeProvider([text_reply("all done")]), approve_all)
    assert session.status == "done"
    assert session.stop_reason == "end_turn"
    assert session.turns == 1


async def test_usage_accumulates_across_steps() -> None:
    session = new_session("go")
    fake = FakeProvider([text_reply("one"), text_reply("two")])
    await step(session, fake, approve_all)
    session.status = "working"
    await step(session, fake, approve_all)
    assert session.usage == Usage(input=20, output=10)
    assert session.turns == 2


async def test_the_assistant_reply_lands_in_the_transcript() -> None:
    session = new_session("go")
    await step(session, FakeProvider([text_reply("all done")]), approve_all)
    assert session.messages[-1].role == "assistant"
    assert session.messages[-1].content == [{"type": "text", "text": "all done"}]


async def test_a_tool_call_runs_and_appends_a_user_result(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    session = new_session("write a file")
    fake = FakeProvider([tool_reply("write", {"path": str(target), "content": "hi"})])
    await step(session, fake, approve_all)
    assert target.read_text() == "hi"
    assert session.status == "working"
    assert session.messages[-1].role == "user"
    assert session.messages[-1].content[0]["type"] == "tool_result"


async def test_ask_blocks_the_session_with_questions() -> None:
    session = new_session("do the thing")
    fake = FakeProvider([tool_reply("ask", {"questions": ["which file?"]})])
    await step(session, fake, approve_all)
    assert session.status == "blocked"
    assert session.questions == ["which file?"]
    # The tool_result is still appended, so a resume picks up from a
    # well-formed transcript rather than a dangling tool_use.
    assert session.messages[-1].content[0]["type"] == "tool_result"


async def test_a_failing_tool_keeps_the_session_working() -> None:
    session = new_session("read a missing file")
    fake = FakeProvider([tool_reply("read", {"path": "/nope/nope.py"})])
    await step(session, fake, approve_all)
    assert session.status == "working"
    assert session.messages[-1].content[0]["is_error"] is True


async def test_step_emits_progress_and_cost_events() -> None:
    session = new_session("go")
    await step(session, FakeProvider([text_reply("all done")]), approve_all)
    kinds = [e.type for e in session.events]
    assert kinds == ["progress", "cost", "output"]
    assert session.events[0].text == "all done"
    assert session.events[1].detail == {
        "input": 10,
        "output": 5,
        "cache_read": 0,
        "cache_write": 0,
    }
    assert session.events[2].text == "all done"


async def test_step_emits_a_tool_use_event(tmp_path: Path) -> None:
    session = new_session("go")
    fake = FakeProvider([tool_reply("bash", {"command": "echo hi"})])
    await step(session, fake, approve_all)
    # The full order is a contract, not an accident: task 13 asserts a golden
    # JSONL stream, and the downstream adapter is built against this sequence.
    # Note there is no `progress` here — this reply carries no text block.
    assert [e.type for e in session.events] == ["cost", "tool_use"]
    tool_events = [e for e in session.events if e.type == "tool_use"]
    assert len(tool_events) == 1
    assert tool_events[0].text == "bash"
    assert tool_events[0].detail == {"command": "echo hi"}


async def test_thinking_blocks_become_thinking_events() -> None:
    from nare.transport import Reply

    session = new_session("go")
    reply = Reply(
        content=[
            {"type": "thinking", "thinking": "let me consider"},
            {"type": "text", "text": "done"},
        ],
        tool_calls=[],
        usage=Usage(input=1, output=1),
        stop_reason="end_turn",
    )
    await step(session, FakeProvider([reply]), approve_all)
    assert [e.type for e in session.events][:2] == ["thinking", "progress"]


async def drain(
    session: Session, transport: Transport, *, max_turns: int = MAX_TURNS_DEFAULT
) -> list[str]:
    return [
        e.type async for e in run(session, transport=transport, max_turns=max_turns)
    ]


async def test_run_drives_to_done_and_yields_every_event(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    session = new_session("write then finish")
    fake = FakeProvider(
        [
            tool_reply("write", {"path": str(target), "content": "hi"}),
            text_reply("finished"),
        ]
    )
    kinds = await drain(session, fake)
    assert kinds == ["cost", "tool_use", "progress", "cost", "output"]
    assert session.status == "done"
    assert session.turns == 2
    assert target.read_text() == "hi"


async def test_run_stops_at_blocked() -> None:
    session = new_session("go")
    fake = FakeProvider([tool_reply("ask", {"questions": ["which one?"]})])
    await drain(session, fake)
    assert session.status == "blocked"
    assert session.questions == ["which one?"]


async def test_max_turns_ends_the_run_honestly(tmp_path: Path) -> None:
    session = new_session("loop forever")
    fake = FakeProvider([tool_reply("bash", {"command": "true"}) for _ in range(5)])
    kinds = await drain(session, fake, max_turns=2)
    assert session.status == "error"
    assert session.stop_reason == "max_turns"
    assert session.turns == 2
    assert "error" in kinds


async def test_a_transport_failure_becomes_status_error() -> None:
    session = new_session("go")
    kinds = await drain(session, Exploding())
    assert session.status == "error"
    assert session.error is not None
    assert "connection reset" in session.error
    assert kinds == ["error"]


async def test_events_are_drained_not_hoarded() -> None:
    session = new_session("go")
    await drain(session, FakeProvider([text_reply("done")]))
    assert session.events == []


async def test_a_resumed_session_runs_identically(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    first = new_session("write then finish")
    await drain(
        first, FakeProvider([tool_reply("ask", {"questions": ["which path?"]})])
    )
    assert first.status == "blocked"

    revived = loads(dumps(first))
    assert revived == first

    append_user_text(revived, f"use {target}")
    working: Status = "working"
    revived.status = working
    fake = FakeProvider(
        [
            tool_reply("write", {"path": str(target), "content": "hi"}),
            text_reply("finished"),
        ]
    )
    await drain(revived, fake)
    assert revived.status == "done"
    assert revived.turns == 3
    assert target.read_text() == "hi"
    # The revived transcript is what the transport actually saw.
    assert fake.calls[0][0][0].role == "user"


def test_the_public_api_is_the_documented_surface() -> None:
    assert set(nare.__all__) == {
        "Event",
        "Message",
        "Reply",
        "Session",
        "ToolCall",
        "Transport",
        "Usage",
        "approve_all",
        "dumps",
        "loads",
        "make_transport",
        "new_session",
        "run",
        "step",
    }
    for name in nare.__all__:
        assert hasattr(nare, name), name
