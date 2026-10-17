from __future__ import annotations

import copy
import json
from dataclasses import asdict
from typing import Any

from fake_provider import FakeProvider, text_reply
from nare.compact import PREFIX, compact, context_chars, estimate
from nare.loop import run
from nare.session import Message, new_session
from nare.transport.anthropic import messages_from


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
                    # Signed, so it is sent and counts toward the estimate, as
                    # every thinking block did when these numbers were set.
                    {"type": "thinking", "thinking": "protected", "signature": "s"},
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


THINKING = ("thinking", "redacted_thinking")


def without_thinking(message: Message) -> Message:
    return Message(
        message.role, [b for b in message.content if b["type"] not in THINKING]
    )


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
    # The thinking after the first edit goes; everything else is kept.
    for i in [0, 1, 3, 5, 6, 7, 8, 9, 10, 11, 12]:
        expected = before[i] if i < 2 else without_thinking(before[i])
        assert s.messages[i] == expected
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


def thinking_transcript(*lead: dict[str, Any]) -> list[Message]:
    """Ten short replies, each opening with copies of the `lead` blocks."""
    messages = [Message("user", [{"type": "text", "text": "task"}])]
    for turn in range(1, 11):
        messages += [
            Message(
                "assistant",
                [*copy.deepcopy(lead), {"type": "text", "text": f"step {turn}"}],
            ),
            Message("user", [{"type": "text", "text": "go on"}]),
        ]
    return messages


def test_the_estimate_skips_blocks_that_are_not_sent_back() -> None:
    # Uncalibrated, the estimate is the saved session's size, and it counted
    # unsigned thinking that messages_from() no longer sends and compaction
    # cannot elide: a 10-turn session whose request was about 590 tokens was
    # estimated at 8235.
    s = new_session("go")
    s.messages = thinking_transcript(
        {"type": "thinking", "thinking": "t" * 3000, "signature": ""},
        {"type": "thinking", "thinking": "t" * 3000},
        {"type": "text", "text": ""},
    )
    bare = new_session("go")
    bare.messages = thinking_transcript()
    assert s.last_input_tokens == 0
    assert estimate(s) == estimate(bare)


def test_signed_thinking_still_counts_toward_the_estimate() -> None:
    # Signed thinking goes back to the model, so it still fills the window.
    s = new_session("go")
    s.messages = thinking_transcript(
        {"type": "thinking", "thinking": "t" * 3000, "signature": "sig"}
    )
    bare = new_session("go")
    bare.messages = thinking_transcript()
    assert estimate(s) - estimate(bare) > 10 * 3000 / 4


async def test_unsent_thinking_does_not_stop_a_run_for_context() -> None:
    # The 8235-token estimate stopped a run with a 4000-token window with
    # stop_reason=context and 0 model calls, though its request fit.
    s = new_session("go")
    s.messages = thinking_transcript(
        {"type": "thinking", "thinking": "t" * 3000, "signature": ""}
    )
    fake = FakeProvider([text_reply("done")])
    _ = [event async for event in run(s, transport=fake, context_window=4000)]
    assert s.stop_reason != "context"
    assert len(fake.calls) == 1


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


def test_tier_two_stops_once_the_estimate_is_exactly_sixty_percent() -> None:
    # The stop is `<= window * 0.6`: a session already at 60% loses nothing
    # more. With `<` in its place every other test still passed.
    window = 10000
    assert window * 0.6 == 6000  # exact in floating point
    s = new_session("go")
    s.messages = write_transcript()
    # The session as tier 2 should leave it: the first three writes stubbed.
    elided = copy.deepcopy(s)
    for turn in (1, 2, 3):
        elided.messages[2 * turn - 1].content[1]["input"]["content"] = (
            f"{PREFIX} wrote 4000 chars to src/f{turn}.ts at turn {turn}. "
            "Read the file if you need it.]"
        )
    # Pad the task so that session is 24000 chars, an uncalibrated estimate of
    # exactly 24000 / 4 = 6000. Three writes, not two, so that the session
    # starts above the 80% that sets compaction off.
    pad = "p" * (24000 - context_chars(elided.messages))
    for session in (s, elided):
        session.messages[0].content[0]["text"] += pad
    assert estimate(elided) == window * 0.6
    report = compact(s, window)
    assert report is not None
    assert (report.elided, report.inputs) == (0, 3)
    assert s.messages == elided.messages
    assert s.messages[7].content[1]["input"]["content"] == "x" * 4000


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


def test_an_edit_with_both_texts_long_counts_as_one_input() -> None:
    # `inputs` counts calls, not fields, as the event's "N tool inputs" says.
    # Counting fields passed every other test, since none had both texts long.
    # With three turns only turn 1 is outside the protected tail, and a
    # 100-token window never reaches 60%, so the walk always gets to it.
    s = new_session("go")
    s.messages = write_transcript(turns=3)
    s.messages[1].content[1]["name"] = "edit"
    s.messages[1].content[1]["input"] = {
        "path": "a.py",
        "old": "z" * 3000,
        "new": "y" * 3000,
    }
    report = compact(s, 100)
    assert report is not None
    assert (report.elided, report.inputs) == (0, 1)
    assert s.messages[1].content[1]["input"] == {
        "path": "a.py",
        "old": "[elided by nare: replaced 3000 chars in a.py at turn 1. "
        "Read the file if you need it.]",
        "new": "[elided by nare: wrote 3000 chars into a.py at turn 1. "
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


def test_the_newest_writes_are_kept_and_a_second_pass_elides_nothing() -> None:
    # A window too small to ever reach 60% walks the whole history, so only the
    # protected tail and the already-stubbed skip stop it: without the first the
    # newest writes are stubbed, without the second a second pass re-stubs the
    # stubs with a wrong "wrote 88 chars" text and counts them again.
    s = new_session("go")
    s.messages = write_transcript()
    report = compact(s, 100)
    assert report is not None
    assert report.inputs == 6
    for i in (13, 15):
        assert s.messages[i].content[1]["input"]["content"] == "x" * 4000
    assert compact(s, 100) is None


def test_compaction_drops_the_thinking_sent_after_its_first_edit() -> None:
    # On Claude Fable 5.1 and the 5.5 models a signed thinking block is valid
    # only while everything sent before it is unchanged, and a stale one is a
    # 400. Removing thinking from the end of the history is valid, so the
    # blocks from the first edited message on go, from the session, since
    # putting one back later is invalid too. The ones before it stay.
    s = new_session("go")
    s.messages = transcript()
    redacted = {"type": "redacted_thinking", "data": "opaque"}
    unsigned = {"type": "thinking", "thinking": "proxy", "signature": ""}
    for i in (1, 5, 11):
        s.messages[i].content.insert(0, dict(redacted))
    s.messages[7].content.insert(0, dict(unsigned))
    before = copy.deepcopy(s.messages)
    report = compact(s, 8000)
    assert report is not None
    assert report.elided == 2  # messages 2 and 4, so the first edit is 2
    # Signed in 3, 5, 7, 9 and 11, redacted in 5 and 11.
    assert report.thinking == 7
    assert s.messages[1] == before[1]
    # Unsigned thinking is never sent, so it stays as a record.
    assert [b for b in s.messages[7].content if b["type"] in THINKING] == [unsigned]
    sent, was_sent = messages_from(s.messages), messages_from(before)
    assert sent[:2] == was_sent[:2]
    assert not [b for m in sent[2:] for b in m["content"] if b["type"] in THINKING]


def test_elision_stops_once_the_thinking_it_drops_is_enough() -> None:
    # The thinking goes at the edit, not after the pass, so the estimate sees
    # it: here one elision and the thinking behind it reach 60%, and the
    # second old result is kept.
    s = new_session("go")
    s.messages = transcript()
    for message in s.messages[3::2]:
        message.content[1]["thinking"] = "t" * 6000
    kept = copy.deepcopy(s.messages[4])
    report = compact(s, 14000)
    assert report is not None
    assert (report.elided, report.thinking) == (1, 5)
    assert report.after <= 14000 * 0.6
    assert s.messages[4] == kept


def test_an_elided_input_drops_the_thinking_of_its_own_turn_on() -> None:
    s = new_session("go")
    s.messages = write_transcript()
    for message in s.messages[1::2]:
        message.content.insert(
            0, {"type": "thinking", "thinking": "plan", "signature": "sig"}
        )
    s.messages[2].content[0]["is_error"] = True  # w1 keeps its input
    before = copy.deepcopy(s.messages)
    report = compact(s, int(estimate(s) / 0.9))
    assert report is not None
    assert (report.elided, report.inputs) == (0, 3)
    assert s.messages[3].content[1]["input"]["content"].startswith(PREFIX)
    assert s.messages[1] == before[1]
    assert report.thinking == 7  # assistant turns 3 to 15
    assert not [b for m in s.messages[3:] for b in m.content if b["type"] in THINKING]


def test_a_compaction_with_no_thinking_after_it_reports_none() -> None:
    s = new_session("go")
    s.messages = write_transcript()
    report = compact(s, int(estimate(s) / 0.9))
    assert report is not None
    assert report.thinking == 0


async def test_the_compaction_event_counts_dropped_thinking() -> None:
    s = new_session("go")
    s.messages = transcript()
    fake = FakeProvider([text_reply("done")])
    events = [event async for event in run(s, transport=fake, context_window=8000)]
    notices = [e for e in events if "compaction" in e.detail]
    assert len(notices) == 1
    assert notices[0].detail["compaction"]["thinking"] == 5
    assert notices[0].text.startswith(
        "compacted: elided 2 tool results, dropped 5 thinking blocks, ~"
    )
