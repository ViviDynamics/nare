from notes.store import NoteStore


def test_create_then_get(tmp_path):
    store = NoteStore(tmp_path / "notes.db")
    note_id = store.create("milk", "two litres")
    note = store.get(note_id)
    assert note["title"] == "milk"
    assert note["body"] == "two litres"


def test_list_keeps_creation_order(tmp_path):
    store = NoteStore(tmp_path / "notes.db")
    store.create("a")
    store.create("b")
    assert [n["title"] for n in store.list_notes()] == ["a", "b"]
