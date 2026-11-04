"""Every tool call is matched to how it ended, from the event stream alone."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from fake_provider import FakeProvider, calls_reply, text_reply, tool_reply
from nare.cli import main
from nare.events import Event
from nare.loop import ASK_NOT_ALONE, ASK_NOT_DELIVERED, step
from nare.session import Session, new_session
from nare.tools import Policy, approve_all


def results(session: Session) -> list[Event]:
    return [e for e in session.events if "tool_result" in e.detail]


def test_an_event_without_a_call_has_the_four_wire_fields() -> None:
    assert set(Event("progress", "working").wire()) == {
        "timestamp",
        "type",
        "text",
        "detail",
    }


def test_an_event_of_a_call_adds_its_id_to_the_wire() -> None:
    wire = Event("tool_use", "bash", {"command": "ls"}, tool_use_id="c1").wire()
    assert wire["tool_use_id"] == "c1"
    assert wire["detail"] == {"command": "ls"}
    assert set(wire) == {"timestamp", "type", "text", "detail", "tool_use_id"}


def test_the_id_is_redacted_like_every_event_field() -> None:
    key = "sk-ant-api03-" + "A" * 40
    assert Event("tool_use", "bash", tool_use_id=key).tool_use_id == "[redacted]"
    assert asdict(Event("progress", "x"))["tool_use_id"] is None


async def test_tool_use_carries_the_call_id_outside_the_arguments() -> None:
    session = new_session("go")
    # An argument named like the id cannot shadow it: the id is not in detail.
    args = {"command": "true", "tool_use_id": "not-the-id"}
    fake = FakeProvider([tool_reply("bash", args, call_id="toolu_9")])
    await step(session, fake, approve_all, Policy())
    [use] = [e for e in session.events if e.type == "tool_use"]
    assert use.tool_use_id == "toolu_9"
    assert use.detail == args


async def test_each_built_in_call_gets_one_result_event(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("one\n")
    session = new_session("go")
    fake = FakeProvider(
        [
            calls_reply(
                ("bash", {"command": "printf hello"}),
                ("read", {"path": str(target)}),
                ("write", {"path": str(tmp_path / "b.txt"), "content": "hi"}),
                ("edit", {"path": str(target), "old": "absent", "new": "x"}),
            )
        ]
    )
    await step(session, fake, approve_all, Policy())

    transcript = {r["tool_use_id"]: r for r in session.messages[-1].content}
    events = results(session)
    assert [e.tool_use_id for e in events] == ["call_1", "call_2", "call_3", "call_4"]
    for event in events:
        assert event.type == "progress"
        record = event.detail["tool_result"]
        saved = transcript[event.tool_use_id]
        assert record == {
            "tool_use_id": event.tool_use_id,
            "is_error": saved["is_error"],
            "chars": len(saved["content"]),
        }
    assert [e.detail["tool"] for e in events] == ["bash", "read", "write", "edit"]
    # The failed edit says so; the others did not fail.
    assert [e.detail["tool_result"]["is_error"] for e in events] == [
        False,
        False,
        False,
        True,
    ]
    assert events[0].detail["tool_result"]["chars"] == len("hello")


async def test_a_result_event_carries_no_content(tmp_path: Path) -> None:
    secret = tmp_path / "s.txt"
    secret.write_text("the content itself\n")
    session = new_session("go")
    fake = FakeProvider([tool_reply("read", {"path": str(secret)})])
    await step(session, fake, approve_all, Policy())
    [event] = results(session)
    assert "the content itself" not in json.dumps(event.wire())


async def test_an_ask_beside_other_calls_has_its_result_event() -> None:
    session = new_session("go")
    fake = FakeProvider(
        [calls_reply(("bash", {"command": "true"}), ("ask", {"questions": ["why?"]}))]
    )
    await step(session, fake, approve_all, Policy())
    ask = results(session)[1]
    assert ask.tool_use_id == "call_2"
    assert ask.detail["tool_result"] == {
        "tool_use_id": "call_2",
        "is_error": True,
        "chars": len(ASK_NOT_ALONE),
    }


async def test_a_truncated_turn_reports_each_call_not_run() -> None:
    session = new_session("go")
    fake = FakeProvider(
        [
            calls_reply(
                ("bash", {"command": "true"}),
                ("write", {"path": "x", "content": "cut"}),
                stop_reason="max_tokens",
            )
        ]
    )
    await step(session, fake, approve_all, Policy())
    events = results(session)
    assert [e.tool_use_id for e in events] == ["call_1", "call_2"]
    assert all(e.detail["tool_result"]["is_error"] for e in events)


async def test_an_ask_rewritten_as_not_delivered_is_reported_as_written() -> None:
    session = new_session("go")
    fake = FakeProvider(
        [calls_reply(("ask", {"questions": ["which?"]}), ("ask", {"questions": []}))]
    )
    await step(session, fake, approve_all, Policy())
    good, bad = results(session)
    assert good.detail["tool_result"] == {
        "tool_use_id": "call_1",
        "is_error": True,
        "chars": len(ASK_NOT_DELIVERED),
    }
    assert bad.detail["tool_result"]["is_error"] is True


async def test_a_blocking_ask_has_its_result_event() -> None:
    session = new_session("go")
    fake = FakeProvider([tool_reply("ask", {"questions": ["which?"]})])
    await step(session, fake, approve_all, Policy())
    assert session.status == "blocked"
    [event] = results(session)
    assert event.detail["tool_result"]["is_error"] is False


async def test_evidence_events_carry_the_call_id(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("one\n")
    session = new_session("go")
    fake = FakeProvider([tool_reply("read", {"path": "a.txt"}, call_id="r1")])
    await step(session, fake, approve_all, Policy(root=tmp_path))
    [evidence] = [e for e in session.events if "evidence" in e.detail]
    assert evidence.tool_use_id == "r1"
    assert evidence.detail["evidence"]["tool_use_id"] == "r1"


async def test_image_events_carry_the_call_id(tmp_path: Path) -> None:
    Image.new("RGB", (4, 4), "blue").save(tmp_path / "p.png", format="PNG")
    session = new_session("go")
    fake = FakeProvider([tool_reply("read", {"path": "p.png"}, call_id="i1")])
    policy = Policy(frozenset({"read"}), tmp_path, image_input=True)
    await step(session, fake, approve_all, policy)
    [image] = [e for e in session.events if "image" in e.detail]
    assert image.tool_use_id == "i1"


def test_every_jsonl_tool_use_matches_exactly_one_result_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "f.txt"
    target.write_text("abc\n")
    fake = FakeProvider(
        [
            calls_reply(
                ("read", {"path": "f.txt"}),
                ("edit", {"path": "f.txt", "old": "zzz", "new": "y"}),
            ),
            tool_reply("bash", {"command": "true"}, call_id="call_3"),
            text_reply("done"),
        ]
    )
    code = main(
        ["run", "--yes", "--jsonl", "--root", str(tmp_path), "go"], transport=fake
    )
    assert code == 0
    lines: list[dict[str, Any]] = [
        json.loads(line) for line in capsys.readouterr().out.splitlines()
    ]
    events = [line for line in lines if line.get("type") != "result"]
    uses = [e["tool_use_id"] for e in events if e["type"] == "tool_use"]
    ends = Counter(
        e["tool_use_id"] for e in events if "tool_result" in e.get("detail", {})
    )
    assert uses == ["call_1", "call_2", "call_3"]
    assert ends == Counter(uses)
    failed = [
        e
        for e in events
        if "tool_result" in e.get("detail", {}) and e["detail"]["tool_result"]["is_error"]
    ]
    assert [e["detail"]["tool"] for e in failed] == ["edit"]
    # A line that belongs to no call keeps the four fields.
    cost = next(e for e in events if e["type"] == "cost")
    assert "tool_use_id" not in cost
