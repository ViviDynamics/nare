"""The preferences file: how it is read and written.

Version 1 is the flat layout every install has today. It has no "version" key.
"""

import json
from pathlib import Path

DEFAULTS = {"theme": "light", "font_size": 12}


def load(path):
    """The settings in path, as a dict: theme, font_size and recent (paths)."""
    data = json.loads(Path(path).read_text())
    return {
        "theme": data.get("theme", DEFAULTS["theme"]),
        "font_size": data.get("font_size", DEFAULTS["font_size"]),
        "recent": list(data.get("recent", [])),
    }


def save(settings, path):
    """Write settings to path, in the version 1 layout."""
    data = {
        "theme": settings["theme"],
        "font_size": settings["font_size"],
        "recent": list(settings["recent"]),
    }
    Path(path).write_text(json.dumps(data, indent=2) + "\n")
