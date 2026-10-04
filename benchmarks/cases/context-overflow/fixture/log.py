"""Eight tool outputs together exceed a 32000 token context estimate."""

import sys

section = int(sys.argv[1])
if not 1 <= section <= 8:
    raise SystemExit("section must be 1 through 8")
print(f"log {section}: " + "." * 29000)
print(f"SENTINEL_{section}=confirmed")
