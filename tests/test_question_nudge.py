"""A headless text reply that asks a question in prose is nudged toward ask
once (issue 117). A text reply ends the run as finished, so a question in one
is never answered; the nudge gives the model one turn to call ask instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider, text_reply, tool_reply
from nare.cli import DEFAULT_SYSTEM, main
from nare.events import Event
from nare.loop import QUESTION_NUDGE, asks_in_prose, run
from nare.session import Session, dumps, loads, new_session
from nare.tools import Policy, approve_all
from nare.transport import Transport


def policy_for(tmp_path: Path, tools: frozenset[str]) -> Policy:
    return Policy(tools=tools, root=tmp_path)


async def drive(
    session: Session, transport: Transport, policy: Policy, **kwargs: Any
) -> list[Event]:
    return [
        event
        async for event in run(
            session, transport=transport, approve=approve_all, policy=policy, **kwargs
        )
    ]


@pytest.mark.parametrize(
    "text, asks",
    [
        ("Which value should it be, 60 or 120?", True),
        ("Want me to also add tests?", True),
        ("**Which file?**", True),
        ("Done. Which one do you want?\nI can wait.", True),
        ("Done.", False),
        ("Set `x?` in the config. Done.", False),
        ("Run this:\n```\nif a?\n```\nAll done.", False),
        ("Run this:\n~~~\nif a?\n~~~\nAll done.", False),
        ("Run this:\n```python\nif a?", False),
        ("The pattern is ``x?`` here. Done.", False),
        ("See https://example.com/search?q=1 for details.", False),
        ("Details are at https://example.com/faq?", False),
        ("[docs](https://example.com/search?)", False),
        ("", False),
    ],
)
def test_a_sentence_ending_question_outside_code_is_a_question(
    text: str, asks: bool
) -> None:
    assert asks_in_prose(text) is asks


async def test_a_prose_question_is_nudged_once_and_then_the_answer_ends_done(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(
        [text_reply("Which value should it be, 60 or 120?"), text_reply("Set 60.")]
    )
    session = new_session("raise the timeout")
    policy = policy_for(tmp_path, frozenset({"read", "ask"}))

    events = await drive(session, fake, policy, question_nudge=True)

    assert session.status == "done"
    assert session.output is None
    assert len(fake.calls) == 2
    assert QUESTION_NUDGE in str(fake.calls[1][0][-1])
    assert session.ask_nudged is True
    assert any(
        e.type == "progress" and e.detail == {"question_nudge": {"asked": True}}
        for e in events
    )


async def test_the_nudge_leads_to_ask_when_the_model_calls_it(tmp_path: Path) -> None:
    fake = FakeProvider(
        [
            text_reply("Which value should it be, 60 or 120?"),
            tool_reply("ask", {"questions": ["60 or 120? Unanswered, 60."]}),
        ]
    )
    session = new_session("raise the timeout")
    policy = policy_for(tmp_path, frozenset({"read", "ask"}))

    await drive(session, fake, policy, question_nudge=True)

    assert session.status == "blocked"
    assert session.questions == ["60 or 120? Unanswered, 60."]


async def test_no_nudge_unless_the_headless_flag_is_on(tmp_path: Path) -> None:
    fake = FakeProvider([text_reply("Which value, 60 or 120?")])
    session = new_session("raise the timeout")

    await drive(session, fake, policy_for(tmp_path, frozenset({"read", "ask"})))

    assert session.status == "done"
    assert len(fake.calls) == 1
    assert session.ask_nudged is False


async def test_a_question_in_code_is_not_nudged(tmp_path: Path) -> None:
    fake = FakeProvider([text_reply("The regex `a?` matches an optional a. Done.")])
    session = new_session("explain")

    await drive(
        session,
        fake,
        policy_for(tmp_path, frozenset({"read", "ask"})),
        question_nudge=True,
    )

    assert session.status == "done"
    assert len(fake.calls) == 1


async def test_no_nudge_after_a_write_has_succeeded(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    fake = FakeProvider(
        [
            tool_reply("write", {"path": str(target), "content": "x"}),
            text_reply("Wrote out.txt. Shall I add tests?"),
        ]
    )
    session = new_session("write it")
    policy = policy_for(tmp_path, frozenset({"write", "ask"}))

    await drive(session, fake, policy, question_nudge=True)

    assert target.exists()
    assert session.status == "done"
    assert len(fake.calls) == 2
    assert session.ask_nudged is False


async def test_a_failed_write_does_not_count_as_a_change(tmp_path: Path) -> None:
    fake = FakeProvider(
        [
            tool_reply("write", {"path": "/no/such/dir/x.txt", "content": "x"}),
            text_reply("The write failed. Which path should I use?"),
            text_reply("Stopped."),
        ]
    )
    session = new_session("write it")
    policy = policy_for(tmp_path, frozenset({"write", "ask"}))

    await drive(session, fake, policy, question_nudge=True)

    assert session.ask_nudged is True
    assert session.status == "done"


async def test_a_nudged_session_is_not_nudged_again(tmp_path: Path) -> None:
    fake = FakeProvider(
        [text_reply("Which one, A or B?"), text_reply("Still unsure, which?")]
    )
    session = new_session("go")
    session.ask_nudged = True

    await drive(
        session,
        fake,
        policy_for(tmp_path, frozenset({"read", "ask"})),
        question_nudge=True,
    )

    assert len(fake.calls) == 1
    assert session.status == "done"


async def test_no_nudge_without_the_ask_tool(tmp_path: Path) -> None:
    fake = FakeProvider([text_reply("Which one, A or B?")])
    session = new_session("go")

    await drive(
        session,
        fake,
        policy_for(tmp_path, frozenset({"read"})),
        question_nudge=True,
    )

    assert len(fake.calls) == 1
    assert session.status == "done"


def test_ask_nudged_survives_a_save_and_load() -> None:
    session = new_session("go")
    session.ask_nudged = True

    assert loads(dumps(session)).ask_nudged is True


def test_a_headless_run_nudges_and_the_default_prompt_says_so(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert "call ask on its own" in DEFAULT_SYSTEM
    fake = FakeProvider(
        [text_reply("Which value should it be, 60 or 120?"), text_reply("Set 60.")]
    )

    code = main(["run", "--yes", "--jsonl", "loop"], transport=fake)

    assert code == 0
    assert len(fake.calls) == 2
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["status"] == "done"


async def test_a_prose_question_on_the_turn_limit_summary_is_not_nudged(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(
        [
            tool_reply("read", {"path": "NOTES.txt"}),
            text_reply("Which one, A or B?"),
        ]
    )
    (tmp_path / "NOTES.txt").write_text("x\n", encoding="utf-8")
    session = new_session("go")

    await drive(
        session,
        fake,
        policy_for(tmp_path, frozenset({"read", "ask"})),
        question_nudge=True,
        max_turns=1,
    )

    assert len(fake.calls) == 2
    assert session.ask_nudged is False
    assert session.stop_reason == "max_turns"
