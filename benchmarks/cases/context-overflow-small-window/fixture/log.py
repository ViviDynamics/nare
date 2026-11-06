"""Eight tool outputs together exceed a 32000 token context estimate.

The text is hex, not dots: a tokenizer squeezes a run of dots into a few tokens,
so a dotted log never grows the context the way its length says it should. Each
log here costs about its characters divided by four, as the estimate assumes.
"""

import hashlib
import sys

section = int(sys.argv[1])
if not 1 <= section <= 8:
    raise SystemExit("section must be 1 through 8")
digest = ""
seed = f"log-{section}".encode()
while len(digest) < 29000:
    seed = hashlib.sha256(seed).digest()
    digest += seed.hex()
print(f"log {section}: " + digest[:29000])
print(f"SENTINEL_{section}=confirmed")
