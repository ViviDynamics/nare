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

## Note (2026-10-07)

Slice 3 uses the seam as reserved. `Approve` also accepts an async callable,
`(tool, args) -> bool | Awaitable[bool]`, and `dispatch()` awaits the result
when it is awaitable: a person answering a prompt is an await, not a blocking
call. `approve_all` and every synchronous approver are unchanged.

## Note (2026-10-19)

A denial can carry a reason. `Approve` returns `bool | str`, or an awaitable of
one, and a str means denied: `dispatch()` sends the model
`<tool> was not approved: <reason>`, or the plain denial when the reason is
blank. A reason is truthy, so the str is tested for before anything runs.
`approve_all`, `--yes`, and every approver that returns `bool` are unchanged.
