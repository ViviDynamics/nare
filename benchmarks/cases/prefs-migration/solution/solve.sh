cat > prefs/store.py <<'APPEOF'
"""The preferences file: how it is read and written.

Version 2 is what is written. Version 1, the flat layout with no "version" key,
is read and upgraded in memory; the next save writes version 2.
"""

import json
from pathlib import Path

DEFAULTS = {"theme": "light", "font_size": 12}
VERSION = 3


def load(path):
    """The settings in path, as a dict: theme, font_size and recent.

    recent is a list of {"path": ..., "opened": ...}, opened being an ISO time
    or None.
    """
    data = json.loads(Path(path).read_text())
    version = data.get("version", 1)
    if version == 1:
        display = {key: data[key] for key in ("theme", "font_size") if key in data}
        recent = [{"path": path_, "opened": None} for path_ in data.get("recent", [])]
    elif version == VERSION:
        display = data.get("display", {})
        recent = [
            {"path": item["path"], "opened": item.get("opened")}
            for item in data.get("recent", [])
        ]
    else:
        raise ValueError(f"unknown settings version {version!r}")
    return {
        "theme": display.get("theme", DEFAULTS["theme"]),
        "font_size": display.get("font_size", DEFAULTS["font_size"]),
        "recent": recent,
    }


def save(settings, path):
    """Write settings to path, in version 2."""
    data = {
        "version": VERSION,
        "display": {
            "theme": settings["theme"],
            "font_size": settings["font_size"],
        },
        "recent": [
            {"path": item["path"], "opened": item.get("opened")}
            for item in settings["recent"]
        ],
    }
    Path(path).write_text(json.dumps(data, indent=2) + "\n")
APPEOF
cat > prefs/__main__.py <<'APPEOF'
"""python -m prefs show PATH: print the settings in a file."""

import sys

from prefs.store import load


def main(argv):
    if len(argv) != 2 or argv[0] != "show":
        print("usage: python -m prefs show PATH", file=sys.stderr)
        return 2
    settings = load(argv[1])
    print(f"theme: {settings['theme']}")
    print(f"font size: {settings['font_size']}")
    for item in settings["recent"]:
        print(f"recent: {item['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
APPEOF
