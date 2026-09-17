from pathlib import Path

from fake_provider import FakeProvider, text_reply, tool_reply
from nare.loop import step
from nare.session import Usage, new_session
from nare.tools import approve_all


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
