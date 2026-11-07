import json
import subprocess
import sys

from prefs.store import load


def test_load_fills_in_defaults(tmp_path):
    path = tmp_path / "prefs.json"
    path.write_text("{}")
    assert load(path)["theme"] == "light"


def test_show_prints_a_version_1_file(tmp_path):
    path = tmp_path / "prefs.json"
    path.write_text(json.dumps({"theme": "dark", "font_size": 14, "recent": ["a.txt"]}))
    out = subprocess.run(
        [sys.executable, "-m", "prefs", "show", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "theme: dark" in out
    assert "recent: a.txt" in out
