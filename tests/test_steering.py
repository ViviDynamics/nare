from pathlib import Path

from fake_provider import FakeProvider, text_reply, tool_reply
from nare.loop import run
from nare.session import Message, new_session
from nare.tools import Policy, approve_all
from nare.transport import anthropic, openai


async def test_text_queued_mid_run_follows_the_tool_results(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    session = new_session("write a file")
    fake = FakeProvider(
        [
            tool_reply("write", {"path": str(target), "content": "hi"}),
            text_reply("done"),
        ]
    )
    # Called once before each model call: nothing before the first, then one
    # message, as if the person typed it while the write ran.
    calls = {"n": 0}

    def inbox() -> list[str]:
        calls["n"] += 1
        return ["stop and summarise"] if calls["n"] == 2 else []

    _ = [
        event
        async for event in run(
            session,
            transport=fake,
            approve=approve_all,
            policy=Policy(),
            inbox=inbox,
        )
    ]
    next_request, _tools = fake.calls[1]
    last = next_request[-1]
    assert last.role == "user"
    assert [b["type"] for b in last.content] == ["tool_result", "text"]
    assert last.content[-1]["text"] == "stop and summarise"
    # Saved like any other message: the transcript keeps it.
    assert any(
        block.get("text") == "stop and summarise"
        for message in session.messages
        for block in message.content
    )


async def test_a_run_without_an_inbox_sends_nothing_extra(tmp_path: Path) -> None:
    session = new_session("say hi")
    fake = FakeProvider([text_reply("hi")])
    _ = [event async for event in run(session, transport=fake, policy=Policy())]
    assert [m.role for m in session.messages] == ["user", "assistant"]


def queued_after_a_tool_result() -> list[Message]:
    return [
        Message("user", [{"type": "text", "text": "write a file"}]),
        Message(
            "assistant",
            [{"type": "tool_use", "id": "t1", "name": "write", "input": {}}],
        ),
        Message(
            "user",
            [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
                {"type": "text", "text": "stop and summarise"},
            ],
        ),
    ]


def test_anthropic_sends_the_queued_text_after_the_tool_results() -> None:
    sent = anthropic.messages_from(queued_after_a_tool_result())
    assert [b["type"] for b in sent[-1]["content"]] == ["tool_result", "text"]
    assert sent[-1]["content"][-1]["text"] == "stop and summarise"


def test_openai_sends_the_queued_text_as_a_user_message_after_the_tool_messages() -> (
    None
):
    sent = openai.messages_from(queued_after_a_tool_result())
    assert [m["role"] for m in sent][-2:] == ["tool", "user"]
    assert sent[-1]["content"] == "stop and summarise"
