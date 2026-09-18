# 2. Python 3.12+, uv, hatchling

Status: Accepted (2026-09-09)

## Context

nare ships alongside Conductor, which is Python on uv and hatchling. A second
language means a second toolchain, a second CI idiom, and a second skill set
for the same two people.

## Decision

Python 3.12+, uv for environments, hatchling for packaging.

## Consequences

One skill set across both repos, and `pip install nare` in the performer's
Dockerfile. The known cost is distribution: there is no true single binary.
Accepted — it is a packaging problem, not an architectural one, and it blocks
nothing today.
