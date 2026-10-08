import json
import os
from pathlib import Path

from nare.session import Message, Session, dumps, new_session
from nare.tui.attach import Watcher

TICK = [1_000_000_000]


def write(path: Path, text: str) -> None:
    # A distinct mtime per write, so a test never depends on clock granularity.
    path.write_text(text)
    TICK[0] += 1_000_000_000
    os.utime(path, ns=(TICK[0], TICK[0]))


def grow(s: Session) -> None:
    s.messages.append(Message("assistant", [{"type": "text", "text": "ok"}]))
    s.turns += 1


def test_missing_then_present(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    watcher = Watcher(path)
    assert watcher.poll() is None
    assert watcher.problem == f"waiting for {path}"
    s = new_session("go")
    write(path, dumps(s))
    update = watcher.poll()
    assert update is not None and update.redraw is True
    assert update.session.id == s.id
    assert watcher.problem is None
    assert watcher.poll() is None  # unchanged file


def test_a_longer_transcript_appends(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    s = new_session("go")
    write(path, dumps(s))
    watcher = Watcher(path)
    watcher.poll()
    grow(s)
    write(path, dumps(s))
    update = watcher.poll()
    assert update is not None and update.redraw is False
    assert len(update.session.messages) == 2


def test_a_merged_last_message_redraws(tmp_path: Path) -> None:
    # Conductor's resume merges feedback into the trailing user message, then
    # the run grows the list. Appending would never draw the feedback.
    path = tmp_path / "s.json"
    s = new_session("go")
    write(path, dumps(s))
    watcher = Watcher(path)
    watcher.poll()
    s.messages[-1].content.append({"type": "text", "text": "use bar.py"})
    grow(s)
    write(path, dumps(s))
    update = watcher.poll()
    assert update is not None and update.redraw is True


def test_a_different_session_redraws(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    write(path, dumps(new_session("one")))
    watcher = Watcher(path)
    watcher.poll()
    other = new_session("two")
    grow(other)
    write(path, dumps(other))
    update = watcher.poll()
    assert update is not None and update.redraw is True


def test_malformed_keeps_the_last_good_session_and_retries(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    s = new_session("go")
    write(path, dumps(s))
    watcher = Watcher(path)
    watcher.poll()
    write(path, "{not json")
    assert watcher.poll() is None
    assert watcher.problem is not None and str(path) in watcher.problem
    assert watcher.session is not None and watcher.session.id == s.id
    assert watcher.fatal is False
    grow(s)
    write(path, dumps(s))
    assert watcher.poll() is not None
    assert watcher.problem is None


def test_a_version_mismatch_stops_polling(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    raw = json.loads(dumps(new_session("go")))
    raw["contract"] = 99
    write(path, json.dumps(raw))
    watcher = Watcher(path)
    assert watcher.poll() is None
    assert watcher.fatal is True
    assert watcher.problem is not None and "contract 99" in watcher.problem
    write(path, dumps(new_session("go")))
    assert watcher.poll() is None  # permanent: shown once, never re-read
