# 4. One approval seam, one implementation, explicit --yes

Status: Accepted (2026-09-09)

## Context

The TUI in slice 3 needs per-tool permission prompts. Slice 1 runs headless and
needs none. Building a rule engine now would be speculation; building nothing
would mean retrofitting the dispatcher later.

## Decision

One dispatcher and one `approve(tool, args) -> bool` callable. Slice 1 ships
`approve_all`, and `nare run` refuses to start unattended without `--yes`.

## Consequences

The seam slice 3 needs exists from the start at a cost of one parameter. No
rule engine, no policy config, no allowlist file. `--yes` is what keeps a
single permissive implementation honest rather than lax.
