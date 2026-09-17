# 6. No graph framework in the core

Status: Accepted (2026-09-09)

## Context

Conductor runs on LangGraph and should: it orchestrates a nine-role lifecycle
with real branching. nare's loop is a while loop over one model call and a list
of tool calls.

## Decision

No LangGraph, and no graph framework, in nare's public surface.

## Consequences

The runtime dependency set stays at one package, which is the whole claim
behind nare being nearly impossible to conflict with when vendored into another
project's container. If nare ever needs branching orchestration, that belongs
above it, in the consumer that already has a graph.
