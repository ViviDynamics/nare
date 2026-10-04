from __future__ import annotations

import copy
import json
from dataclasses import asdict

from fake_provider import FakeProvider, text_reply
from nare.compact import compact, estimate
from nare.loop import run
from nare.session import Message, new_session


def transcript() -> list[Message]:
    messages = [Message("user", [{"type": "text", "text": "task"}])]
    for turn in range(1, 7):
        name = "ask" if turn == 3 else "read"
        messages += [
            Message(
                "assistant",
                [
                    {
                        "type": "tool_use",
                        "id": f"c{turn}",
                        "name": name,
                        "input": {"path": "a"},
                    },
                    {"type": "thinking", "thinking": "protected"},
                ],
            ),
            Message(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"c{turn}",
                        "content": "x" * 4000,
                        "is_error": turn == 4,
                    }
                ],
            ),
        ]
    return messages


def test_oldest_elision_preserves_protected_blocks_and_pairs() -> None:
    s = new_session("go")
    s.messages = transcript()
    before = copy.deepcopy(s.messages)
    report = compact(s, 8000)
    assert report is not None
    assert report.elided == 2
    assert report.before > 6400
    assert report.after < report.before
    assert s.messages[2].content[0]["content"].startswith("[elided by nare:")
    assert s.messages[4].content[0]["content"].startswith("[elided by nare:")
    for i in [0, 1, 3, 5, 6, 7, 8, 9, 10, 11, 12]:
        assert s.messages[i] == before[i]
    assert [
        b["tool_use_id"]
        for m in s.messages
        for b in m.content
        if b["type"] == "tool_result"
    ] == [f"c{n}" for n in range(1, 7)]
    assert compact(s, 8000) is None  # no repeated stubbing


def test_below_threshold_leaves_transcript_intact() -> None:
    s = new_session("go")
    s.messages = transcript()
    before = copy.deepcopy(s.messages)
    assert compact(s, 10000) is None
    assert s.messages == before


def test_stops_eliding_once_below_sixty_percent() -> None:
    s = new_session("go")
    s.messages = transcript()
    # Remove the protected large ask/error outputs, so one elision suffices.
    s.messages[6].content[0]["content"] = "question"
    s.messages[8].content[0]["content"] = "error"
    s.messages[2].content[0]["content"] = "x" * 8000
    before_second = copy.deepcopy(s.messages[4])
    report = compact(s, 6600)
    assert report is not None
    assert report.elided == 1
    assert report.after <= 3960
    assert s.messages[4] == before_second


def test_measured_estimate_accounts_for_appended_content_and_elision() -> None:
    s = new_session("go")
    s.messages = transcript()
    s.last_input_tokens = 10000
    s.last_input_messages = len(s.messages)
    s.last_input_chars = len(json.dumps([asdict(m) for m in s.messages]))
    assert estimate(s) == 10000
    s.messages.append(Message("assistant", [{"type": "text", "text": "new"}]))
    assert estimate(s) > 10000
    report = compact(s, 12000)
    assert report is not None
    assert report.after < report.before


async def test_context_stop_without_call_when_protected_task_overflows() -> None:
    s = new_session("x" * 1000)
    fake = FakeProvider([text_reply("must not be called")])
    events = [event async for event in run(s, transport=fake, context_window=100)]
    assert fake.calls == []
    assert s.status == "error"
    assert s.stop_reason == "context"
    assert any(e.type == "error" for e in events)


async def test_resume_compacts_before_first_call() -> None:
    s = new_session("go")
    s.messages = transcript()
    fake = FakeProvider([text_reply("done")])
    events = [event async for event in run(s, transport=fake, context_window=8000)]
    assert s.status == "done"
    assert len(fake.calls) == 1
    assert fake.calls[0][0][2].content[0]["content"].startswith("[elided by nare:")
    assert events[0].detail["context_window"] == {"tokens": 8000, "source": "flag"}
    assert any("compaction" in e.detail for e in events)
    assert s.last_input_tokens == 10
