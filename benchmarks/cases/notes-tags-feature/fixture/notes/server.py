"""HTTP server for notes: a JSON API under /api, and the page at /."""

import argparse
import json
import pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

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
            path = urlparse(self.path).path
            if path == "/":
                page = (STATIC / "index.html").read_bytes()
                return self._send(200, page, "text/html; charset=utf-8")
            if path == "/api/notes":
                return self._send(200, store.list_notes())
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
            note_id = store.create(data["title"], data.get("body", ""))
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
