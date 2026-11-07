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
    for path in settings["recent"]:
        print(f"recent: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
