# 5. A serializable Session advanced by an explicit step()

Status: Accepted (2026-09-09)

## Context

Conductor relays feedback mid-session up to five times per card, and today
rebuilds context by hand across those cycles in every adapter.

## Decision

A plain dataclass `Session`, advanced by `async def step()`, exposed as
`async for event in run(...)`. Serialized with `asdict()` and reconstructed
from `json.loads()`, with a version field on the file.

## Consequences

Resumable by construction, so `--resume` costs nothing extra and
`relay_feedback` becomes "resume with one more user message" — a whole slice-2
requirement falling out of slice 1 for free. Deterministic under test: the loop
is a pure function of a session and a transport.
