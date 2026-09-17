import pytest

from nare.events import Event
from nare.session import (
    SESSION_VERSION,
    Message,
    Usage,
    append_user_text,
    dumps,
    loads,
    new_session,
)


def test_usage_adds_every_field() -> None:
    total = Usage(1, 2, 3, 4) + Usage(10, 20, 30, 40)
    assert total == Usage(11, 22, 33, 44)


def test_new_session_starts_with_one_user_message() -> None:
    s = new_session("fix the bug")
    assert s.status == "working"
    assert s.turns == 0
    assert s.messages == [
        Message(role="user", content=[{"type": "text", "text": "fix the bug"}])
    ]
    assert s.id


def test_append_user_text_merges_into_a_trailing_user_message() -> None:
    s = new_session("first")
    append_user_text(s, "second")
    assert len(s.messages) == 1
    assert s.messages[0].content == [
        {"type": "text", "text": "first"},
        {"type": "text", "text": "second"},
    ]


def test_append_user_text_starts_a_new_turn_after_an_assistant_message() -> None:
    s = new_session("first")
    s.messages.append(
        Message(role="assistant", content=[{"type": "text", "text": "ok"}])
    )
    append_user_text(s, "second")
    assert len(s.messages) == 3
    assert s.messages[-1].role == "user"


def test_round_trip_preserves_the_session() -> None:
    s = new_session("go")
    s.usage = Usage(5, 6, 7, 8)
    s.turns = 2
    s.status = "blocked"
    s.questions = ["which file?"]
    s.stop_reason = "tool_use"
    back = loads(dumps(s))
    assert back == s


def test_events_do_not_survive_serialization() -> None:
    s = new_session("go")
    s.events.append(Event("progress", "thinking about it"))
    assert '"events"' not in dumps(s)
    assert loads(dumps(s)).events == []


def test_loads_rejects_a_foreign_version() -> None:
    raw = dumps(new_session("go")).replace(
        f'"version": {SESSION_VERSION}', '"version": 99'
    )
    with pytest.raises(ValueError, match="version 99"):
        loads(raw)


def test_round_trip_is_equal_even_with_events_pending() -> None:
    s = new_session("go")
    s.events.append(Event("progress", "drained by run(), never persisted"))
    # events is compare=False precisely because dumps() drops it: __eq__ and
    # the persistence contract have to agree, or a resumed session compares
    # unequal to the one it was written from.
    assert loads(dumps(s)) == s
    assert loads(dumps(s)).events == []
