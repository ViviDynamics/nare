# Write the service and its tests, as the task asks.
cat > app.py <<'APPEOF'
"""A bookmarks JSON API over PostgreSQL: add, list and delete."""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

SCHEMA = """
CREATE TABLE IF NOT EXISTS bookmarks (
    id SERIAL PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT ''
)
"""


def connect():
    import psycopg  # a client library; the PostgreSQL server is set up separately

    return psycopg.connect(os.environ["DATABASE_URL"])


def add(conn, url, title):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bookmarks (url, title) VALUES (%s, %s) RETURNING id",
            (url, title),
        )
        bookmark_id = cur.fetchone()[0]
    conn.commit()
    return bookmark_id


def list_all(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id, url, title FROM bookmarks ORDER BY id")
        return [{"id": i, "url": u, "title": t} for i, u, t in cur.fetchall()]


def delete(conn, bookmark_id):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM bookmarks WHERE id = %s", (bookmark_id,))
        removed = cur.rowcount
    conn.commit()
    return removed


class Handler(BaseHTTPRequestHandler):
    conn = None

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/bookmarks":
            self._send(200, list_all(self.conn))
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/bookmarks":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self._send(201, {"id": add(self.conn, body["url"], body.get("title", ""))})

    def do_DELETE(self):
        prefix = "/bookmarks/"
        if not self.path.startswith(prefix):
            self._send(404, {"error": "not found"})
            return
        removed = delete(self.conn, int(self.path[len(prefix):]))
        self._send(200 if removed else 404, {"deleted": removed})


def main():
    Handler.conn = connect()
    with Handler.conn.cursor() as cur:
        cur.execute(SCHEMA)
    Handler.conn.commit()
    HTTPServer(("127.0.0.1", 8000), Handler).serve_forever()


if __name__ == "__main__":
    main()

APPEOF
mkdir -p tests
cat > tests/test_app.py <<'TESTEOF'
"""Tests for the bookmarks API, against an in-memory stand-in for the connection."""

from app import add, delete, list_all


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self.rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        if sql.startswith("INSERT"):
            self.store.append((len(self.store) + 1, params[0], params[1]))
            self.rows = [(len(self.store),)]
        elif sql.startswith("SELECT"):
            self.rows = list(self.store)
        elif sql.startswith("DELETE"):
            before = len(self.store)
            self.store[:] = [row for row in self.store if row[0] != params[0]]
            self.rowcount = before - len(self.store)

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self):
        self.store = []
        self.commits = 0

    def cursor(self):
        return FakeCursor(self.store)

    def commit(self):
        self.commits += 1


def test_add_list_and_delete():
    conn = FakeConnection()
    assert add(conn, "https://example.com", "Example") == 1
    assert list_all(conn) == [{"id": 1, "url": "https://example.com", "title": "Example"}]
    assert delete(conn, 1) == 1
    assert list_all(conn) == []
    assert conn.commits == 2
TESTEOF
