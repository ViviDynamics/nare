"""Notes, kept in SQLite."""

import sqlite3


class NoteStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS notes "
            "(id INTEGER PRIMARY KEY, title TEXT NOT NULL, "
            "body TEXT NOT NULL DEFAULT '')"
        )
        self.db.commit()

    def create(self, title, body=""):
        cur = self.db.execute(
            "INSERT INTO notes (title, body) VALUES (?, ?)", (title, body)
        )
        self.db.commit()
        return cur.lastrowid

    def get(self, note_id):
        row = self.db.execute(
            "SELECT id, title, body FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        if row is None:
            return None
        return {"id": row[0], "title": row[1], "body": row[2]}

    def list_notes(self):
        rows = self.db.execute(
            "SELECT id, title, body FROM notes ORDER BY id"
        ).fetchall()
        return [{"id": r[0], "title": r[1], "body": r[2]} for r in rows]
