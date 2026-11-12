"""The deploy feed: one page per call, and each page is handed out once.

nare runs this as the next_page command tool. The arguments arrive as JSON on
stdin, {} for the first page or {"cursor": "..."} for a later one, and the page
is the output. A page that was handed out is never handed out again, so once
compaction has elided it, it is gone.

Every value comes from SHA-256 of fixed text, so the feed is the same on every
run and in every Python, and the checks can hold the answer. Reading this file
does not give the answer away: working out a build needs the hash computed.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import sys
from pathlib import Path

PAGES = 8
# Each page's text is about 10,800 characters. Timestamps, ids and numbers make it
# dense, 2.26 characters a token, so a page is about 4,800 tokens and the eight
# pages are about 0.6 of the case's 64,000-token window.
PAGE_CHARS = 10_800
STATE = Path(".feed")
SERVICES = (
    "auth",
    "billing",
    "catalog",
    "checkout",
    "gateway",
    "inventory",
    "ledger",
    "mailer",
    "notifier",
    "payments",
    "profile",
    "ratings",
    "reports",
    "scheduler",
    "search",
    "shipping",
)
# A deploy line for a build that did not go live has one of these statuses.
OTHER = ("staged", "canary", "rolled_back", "failed")
# Each service's builds that never go live, spread over the feed.
DECOYS = 3
METHODS = ("GET", "GET", "GET", "POST", "PUT", "DELETE")
PATHS = (
    "/v2/orders/{n}",
    "/v2/users/{n}/profile",
    "/v2/cart/{n}",
    "/v2/catalog/items/{n}",
    "/v2/invoices/{n}/lines",
    "/v2/shipments/{n}/events",
)
CODES = (200, 200, 200, 200, 201, 204, 304, 404, 409, 500)


def number(*parts: object) -> int:
    """A number fixed by its parts."""
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return int.from_bytes(digest[:8], "big")


def hex_of(width: int, *parts: object) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:width]


def build(service: str, n: int) -> str:
    """Build 0 of a service is the one that goes live; the others never do."""
    return hex_of(7, "build", service, n)


def cursor(page: int) -> str:
    """The cursor that hands out `page`. Page 1 needs none."""
    return hex_of(8, "cursor", page)


def live_on(page: int) -> list[str]:
    """The two services that go live on `page`."""
    order = sorted(SERVICES, key=lambda s: number("order", s))
    return order[2 * (page - 1) : 2 * page]


def deploys(page: int) -> list[tuple[float, str]]:
    """The deploy lines on `page`, each with where on the page it falls (0 to 1)."""
    events = []
    for j, service in enumerate(live_on(page)):
        at = 0.1 + 0.35 * j + (number("at", page, j) % 100) / 1000
        line = f"deploy service={service} build={build(service, 0)} status="
        events.append((at, line + "canary"))
        events.append((at + 0.12, line + "live"))
    for service in SERVICES:
        for n in range(1, DECOYS + 1):
            if number("decoy page", service, n) % PAGES + 1 != page:
                continue
            status = OTHER[number("status", service, n) % len(OTHER)]
            at = (number("decoy at", service, n) % 950) / 1000
            events.append(
                (
                    at,
                    f"deploy service={service} build={build(service, n)} "
                    f"status={status}",
                )
            )
    return events


def noise(page: int, i: int) -> str:
    """Line `i` of the traffic around the deploys."""
    kind = number("kind", page, i) % 5
    service = SERVICES[number("service", page, i) % len(SERVICES)]
    ms = 3 + number("ms", page, i) % 900
    if kind <= 1:
        method = METHODS[number("method", page, i) % len(METHODS)]
        path = PATHS[number("path", page, i) % len(PATHS)]
        path = path.format(n=10_000 + number("id", page, i) % 90_000)
        code = CODES[number("code", page, i) % len(CODES)]
        return f"http {method} {path} {code} {ms}ms req={hex_of(8, 'req', page, i)}"
    if kind == 2:
        return f"worker {service} job={hex_of(8, 'job', page, i)} finished in {ms}ms"
    if kind == 3:
        return f"health {service} ok {ms}ms"
    hits = number("hits", page, i) % 5000
    return f"cache {service} hits={hits} misses={number('misses', page, i) % 400}"


def stamp(seconds: int) -> str:
    """A time on 2 October 2026, from 09:00:00 UTC."""
    hours, rest = divmod(9 * 3600 + seconds, 3600)
    return f"2026-10-02T{hours:02d}:{rest // 60:02d}:{rest % 60:02d}Z"


def page_text(page: int) -> str:
    events = deploys(page)
    room = PAGE_CHARS - sum(len(line) + 22 for _, line in events)
    lines: list[str] = []
    while room > 0:
        lines.append(noise(page, len(lines)))
        room -= len(lines[-1]) + 22
    for at, line in sorted(events, reverse=True):
        lines.insert(int(at * len(lines)), line)
    clock = (page - 1) * 900
    body = []
    for i, line in enumerate(lines):
        clock += 1 + number("step", page, i) % 2
        body.append(f"{stamp(clock)} {line}")
    end = (
        f"next cursor: {cursor(page + 1)}"
        if page < PAGES
        else "end of feed: there is no next page."
    )
    return f"deploy feed, page {page} of {PAGES}\n" + "\n".join(body) + f"\n{end}\n"


def main() -> int:
    raw = sys.stdin.read()
    args = json.loads(raw) if raw.strip() else {}
    given = str(args.get("cursor") or "").strip()
    if given:
        pages = {cursor(k): k for k in range(2, PAGES + 1)}
        if given not in pages:
            print(
                f"unknown cursor {given!r}: pass the cursor printed at the end of "
                "the last page you got",
                file=sys.stderr,
            )
            return 1
        page = pages[given]
    else:
        page = 1
    STATE.mkdir(exist_ok=True)
    with open(STATE / "lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        handed_file = STATE / "handed.json"
        handed = json.loads(handed_file.read_text()) if handed_file.exists() else []
        if page in handed:
            print(
                f"page {page} was already handed out, and a page is handed out once: "
                "this feed cannot give it again. Continue from the cursor at the end "
                "of the last page you got.",
                file=sys.stderr,
            )
            return 1
        handed_file.write_text(json.dumps(sorted([*handed, page])))
    sys.stdout.write(page_text(page))
    return 0


if __name__ == "__main__":
    sys.exit(main())
