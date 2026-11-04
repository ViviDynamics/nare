# 8. nare names no caller, and defines its contracts on its own terms

Status: Accepted (2026-11-04)

## Context

nare was built for one caller first, and its early records show it: ADRs 0001,
0002, 0003, 0005 and 0006 explain their decisions by what that caller does,
the event documentation defined the stream as matching that caller's event
type, and source comments justified behaviour by how that caller used it. None
of that was a decision to serve one caller. It is how a generic tool stops
being generic: a caller's word in a docstring, then in a flag, then in the
shape of a feature, until the contract reads as an obligation to someone else
rather than a shape nare chose.

More than one product now drives nare, and each new request arrives in its
author's vocabulary.

## Decision

nare names no caller. No other product's name appears in its source, flags,
tool names, tests, benchmarks or living documentation, and nare's contracts
are defined on nare's own terms.

What belongs to nare:

- The contract: the event stream, the `result` line, the session file, exit
  codes, statuses, stop reasons and the redaction rule set, each defined by
  what it means in a run, and versioned by `docs/contract.md`.
- Flags, tools and fields named for what they do.
- Behaviour justified by what any caller can rely on ("a caller may resume
  the same session once per round"), not by what one caller does.

What belongs to a caller's adapter:

- Mapping nare's events, statuses and fields onto the caller's own types. If
  a caller's type happens to match nare's shape, that is the adapter's
  convenience, not a requirement on nare.
- The caller's vocabulary: cards, roles, review cycles, lifecycles, products.
- Policy across runs, such as budgets split between several runs.

A feature request is accepted in nare's vocabulary or rewritten into it before
it is designed. A request that only makes sense for one caller belongs in that
caller's adapter.

The earlier ADRs are records of their time and stay as written. Read in nare's
terms, ADR 0003's decision is that a caller integrates by spawning the CLI as a
subprocess, not by importing the library, and ADR 0005's is that resuming a
saved session is how a caller relays feedback.

## Consequences

`tests/test_caller_agnostic.py` fails when a caller's name appears in `src/`,
a flag, a tool name, `tests/`, `benchmarks/` or the documentation outside its
history (ADRs 0001 to 0007, plans, specs and evidence). Adding a product to
the organization means adding its name to that test. The pull request template
carries the review question, so bleed is noticed in review rather than in the
shape of the code later.

Renaming nothing on the wire is part of the decision: the event types and
fields keep their names, so no caller has to change. Only the reasoning
behind them is restated.

The cost is that a comment can no longer say "because the caller does X" with
a name; it has to say what any caller may do. That is the point.
