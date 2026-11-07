cat > notes/store.py <<'APPEOF'
"""Notes and their tags, kept in SQLite."""

import sqlite3


class NoteStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS notes "
            "(id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '')"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS note_tags "
            "(note_id INTEGER NOT NULL, tag TEXT NOT NULL, PRIMARY KEY (note_id, tag))"
        )
        self.db.commit()

    def create(self, title, body="", tags=()):
        cur = self.db.execute("INSERT INTO notes (title, body) VALUES (?, ?)", (title, body))
        note_id = cur.lastrowid
        self.db.executemany(
            "INSERT OR IGNORE INTO note_tags (note_id, tag) VALUES (?, ?)",
            [(note_id, tag) for tag in tags],
        )
        self.db.commit()
        return note_id

    def _note(self, row):
        note_id, title, body = row
        tags = [
            tag
            for (tag,) in self.db.execute(
                "SELECT tag FROM note_tags WHERE note_id = ? ORDER BY tag", (note_id,)
            )
        ]
        return {"id": note_id, "title": title, "body": body, "tags": tags}

    def get(self, note_id):
        row = self.db.execute(
            "SELECT id, title, body FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        return None if row is None else self._note(row)

    def list_notes(self, tag=None):
        if tag is None:
            rows = self.db.execute("SELECT id, title, body FROM notes ORDER BY id").fetchall()
        else:
            rows = self.db.execute(
                "SELECT n.id, n.title, n.body FROM notes n "
                "JOIN note_tags t ON t.note_id = n.id "
                "WHERE t.tag = ? ORDER BY n.id",
                (tag,),
            ).fetchall()
        return [self._note(row) for row in rows]

    def tag_counts(self):
        rows = self.db.execute(
            "SELECT tag, COUNT(*) FROM note_tags GROUP BY tag ORDER BY tag"
        ).fetchall()
        return {tag: count for tag, count in rows}
APPEOF
cat > notes/server.py <<'APPEOF'
"""HTTP server for notes: a JSON API under /api, and the page at /."""

import argparse
import json
import pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from notes.store import NoteStore

STATIC = pathlib.Path(__file__).parent / "static"


def make_server(db_path, port=0, host="127.0.0.1"):
    store = NoteStore(db_path)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body, content_type="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parts = urlparse(self.path)
            path = parts.path
            if path == "/":
                page = (STATIC / "index.html").read_bytes()
                return self._send(200, page, "text/html; charset=utf-8")
            if path == "/api/notes":
                tag = parse_qs(parts.query).get("tag", [None])[0]
                return self._send(200, store.list_notes(tag=tag))
            if path == "/api/tags":
                return self._send(200, store.tag_counts())
            if path.startswith("/api/notes/"):
                try:
                    note = store.get(int(path.rsplit("/", 1)[1]))
                except ValueError:
                    note = None
                if note is None:
                    return self._send(404, {"error": "no such note"})
                return self._send(200, note)
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if urlparse(self.path).path != "/api/notes":
                return self._send(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            if not data.get("title"):
                return self._send(400, {"error": "title is required"})
            tags = data.get("tags", [])
            if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
                return self._send(400, {"error": "tags must be a list of strings"})
            note_id = store.create(data["title"], data.get("body", ""), tags=tags)
            self._send(201, store.get(note_id))

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="notes.server")
    parser.add_argument("--db", default="notes.db")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    server = make_server(args.db, port=args.port)
    print(f"serving on http://127.0.0.1:{server.server_address[1]}")
    server.serve_forever()


if __name__ == "__main__":
    main()
APPEOF
cat > notes/static/index.html <<'APPEOF'
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Notes</title>
</head>
<body>
  <h1>Notes</h1>
  <label>Filter by tag
    <select id="filter"><option value="">all</option></select>
  </label>
  <form id="new-note">
    <input name="title" placeholder="Title" required>
    <textarea name="body" placeholder="Body"></textarea>
    <input name="tags" placeholder="Tags, comma separated">
    <button type="submit">Add</button>
  </form>
  <ul id="notes"></ul>
  <script>
    async function loadTags() {
      const counts = await (await fetch("/api/tags")).json();
      const filter = document.getElementById("filter");
      const chosen = filter.value;
      filter.innerHTML = '<option value="">all</option>';
      for (const tag of Object.keys(counts).sort()) {
        const option = document.createElement("option");
        option.value = tag;
        option.textContent = tag + " (" + counts[tag] + ")";
        filter.appendChild(option);
      }
      filter.value = chosen;
    }

    async function load() {
      const tag = document.getElementById("filter").value;
      const url = tag ? "/api/notes?tag=" + encodeURIComponent(tag) : "/api/notes";
      const notes = await (await fetch(url)).json();
      const list = document.getElementById("notes");
      list.innerHTML = "";
      for (const note of notes) {
        const item = document.createElement("li");
        item.textContent = note.title + ": " + note.body;
        if (note.tags.length) {
          item.textContent += " [tags: " + note.tags.join(", ") + "]";
        }
        list.appendChild(item);
      }
    }

    document.getElementById("filter").addEventListener("change", load);

    document.getElementById("new-note").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.target;
      const tags = form.tags.value.split(",").map((t) => t.trim()).filter(Boolean);
      await fetch("/api/notes", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({title: form.title.value, body: form.body.value, tags: tags}),
      });
      form.reset();
      await loadTags();
      load();
    });

    loadTags().then(load);
  </script>
</body>
</html>
APPEOF
cat > tests/test_store.py <<'APPEOF'
from notes.store import NoteStore


def test_create_then_get(tmp_path):
    store = NoteStore(tmp_path / "notes.db")
    note_id = store.create("milk", "two litres")
    note = store.get(note_id)
    assert note["title"] == "milk"
    assert note["body"] == "two litres"
    assert note["tags"] == []


def test_list_keeps_creation_order(tmp_path):
    store = NoteStore(tmp_path / "notes.db")
    store.create("a")
    store.create("b")
    assert [n["title"] for n in store.list_notes()] == ["a", "b"]


def test_tags_are_kept_and_filtered(tmp_path):
    store = NoteStore(tmp_path / "notes.db")
    store.create("a", tags=["work", "home"])
    store.create("b", tags=["home"])
    assert [n["title"] for n in store.list_notes(tag="home")] == ["a", "b"]
    assert [n["title"] for n in store.list_notes(tag="work")] == ["a"]
    assert store.tag_counts() == {"home": 2, "work": 1}
APPEOF
