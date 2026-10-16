from __future__ import annotations

import copy
import json
from dataclasses import asdict

from fake_provider import FakeProvider, text_reply
from nare.compact import PREFIX, compact, estimate
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


def write_transcript(turns: int = 8, chars: int = 4000) -> list[Message]:
    """A write-heavy session: big write inputs, short results."""
    messages = [Message("user", [{"type": "text", "text": "task"}])]
    for turn in range(1, turns + 1):
        path = f"src/f{turn}.ts"
        messages += [
            Message(
                "assistant",
                [
                    {"type": "text", "text": f"writing {path}"},
                    {
                        "type": "tool_use",
                        "id": f"w{turn}",
                        "name": "write",
                        "input": {"path": path, "content": "x" * chars},
                    },
                ],
            ),
            Message(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"w{turn}",
                        "content": f"wrote {chars} characters to {path}",
                        "is_error": False,
                    }
                ],
            ),
        ]
    return messages


def test_a_write_heavy_session_compacts_below_sixty_percent() -> None:
    # On the practice run write inputs were 46% of the context and tool
    # results 7.5%, so eliding results alone could not get below 60%.
    s = new_session("go")
    s.messages = write_transcript()
    before = copy.deepcopy(s.messages)
    window = int(estimate(s) / 0.9)
    report = compact(s, window)
    assert report is not None
    assert report.after <= window * 0.6
    assert (report.elided, report.inputs) == (0, 3)
    assert s.messages[1].content[1] == {
        "type": "tool_use",
        "id": "w1",
        "name": "write",
        "input": {
            "path": "src/f1.ts",
            "content": "[elided by nare: wrote 4000 chars to src/f1.ts at turn 1. "
            "Read the file if you need it.]",
        },
    }
    for i in (3, 5):
        assert s.messages[i].content[1]["input"]["content"].startswith(PREFIX)
    # Results, assistant text and the newer writes are untouched.
    for i, message in enumerate(s.messages):
        if message.role == "user" or i > 5:
            assert message == before[i]
        else:
            assert message.content[0] == before[i].content[0]


def test_long_edit_texts_are_elided_and_short_ones_kept() -> None:
    s = new_session("go")
    s.messages = write_transcript()
    s.messages[1].content[1]["name"] = "edit"
    s.messages[1].content[1]["input"] = {"path": "a.py", "old": "z" * 3000, "new": "y"}
    s.messages[3].content[1]["name"] = "edit"
    s.messages[3].content[1]["input"] = {"path": "b.py", "old": "z", "new": "y" * 3000}
    report = compact(s, int(estimate(s) / 0.9))
    assert report is not None
    assert s.messages[1].content[1]["input"] == {
        "path": "a.py",
        "old": "[elided by nare: replaced 3000 chars in a.py at turn 1. "
        "Read the file if you need it.]",
        "new": "y",
    }
    assert s.messages[3].content[1]["input"] == {
        "path": "b.py",
        "old": "z",
        "new": "[elided by nare: wrote 3000 chars into b.py at turn 2. "
        "Read the file if you need it.]",
    }


def test_a_failed_write_keeps_its_content() -> None:
    # Its stub would claim a write that never happened.
    s = new_session("go")
    s.messages = write_transcript()
    s.messages[2].content[0]["content"] = "write was not approved"
    s.messages[2].content[0]["is_error"] = True
    report = compact(s, int(estimate(s) / 0.9))
    assert report is not None
    assert s.messages[1].content[1]["input"]["content"] == "x" * 4000
    assert s.messages[3].content[1]["input"]["content"].startswith(PREFIX)


def test_tool_results_are_elided_before_tool_inputs() -> None:
    # The first tier still runs first: an input goes only when eliding old
    # output was not enough.
    s = new_session("go")
    s.messages = write_transcript()
    for i in range(2, 13, 2):
        s.messages[i].content[0]["content"] = "r" * 4000
    report = compact(s, int(estimate(s) / 0.9))
    assert report is not None
    assert report.elided > 0
    assert report.inputs == 0
    assert all(
        block["input"]["content"] == "x" * 4000
        for message in s.messages
        for block in message.content
        if block["type"] == "tool_use"
    )


async def test_the_compaction_event_counts_elided_inputs() -> None:
    s = new_session("go")
    s.messages = write_transcript()
    window = int(estimate(s) / 0.9)
    fake = FakeProvider([text_reply("done")])
    events = [event async for event in run(s, transport=fake, context_window=window)]
    notices = [e for e in events if "compaction" in e.detail]
    assert len(notices) == 1
    assert notices[0].detail["compaction"]["inputs"] == 3
    assert notices[0].text.startswith(
        "compacted: elided 0 tool results and 3 tool inputs, ~"
    )
