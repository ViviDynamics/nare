# 1. Build an own harness, not a wrapper

Status: Accepted (2026-09-09)

## Context

Conductor drives eight external CLIs through adapters totalling ~5,300 lines.
They are large because they reverse-engineer structure out of tools that were
never built to emit it: blocked states parsed from prose, events reconstructed
from stdout line shapes, stop reasons inferred and often wrong.

## Decision

nare implements the loop, the tools, and the provider calls itself. It is not a
wrapper around other harnesses and not a meta-harness.

## Consequences

A harness that emits blocked + questions, typed events, honest stop_reason, and
resumable sessions natively turns an 800-line adapter into roughly 80. The cost
is that nare owns provider compatibility forever, which the transport layer
(ADR 0007 territory, see docs/superpowers/specs) is designed to absorb.
