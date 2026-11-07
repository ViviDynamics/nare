# 10. A bounded sub-run tool is not built

Status: Accepted (2026-11-07)

## Context

A run is one session with one message list. Its budgets (tokens, USD, tool
calls, turns, cut-off replies) are checked against that session, and the
loop checks the token and USD ceilings before each model request
(`loop.py`, `check_budget`). Context grows with every tool result. Elision
(`compact.py`) already shortens old bash and read output, and an overflow
capture (docs/evidence/41/overflow) completes eight large tool outputs on a
32000-token window. Context compaction (#42 to #44) is the in-flight work on
that growth.

The feature-gap decision (docs/plans/2026-11-05-feature-gap-decision.md)
cut in-loop subagents (C1) and left a bounded sub-run open as U2. Its revisit
condition for C1 is: "a measured task needs an isolated context inside one
run". Issue #241 proposes a `subrun` tool that starts a fresh context for a
sub-task, draws its budget from the parent, and returns a summary.

Two rules bear on the choice:

- ADR 0008 names "policy across runs, such as budgets split between several
  runs" as the caller's adapter's, not nare's.
- ADR 0009 lets a caller register an in-process tool whose `call` may run
  another `nare.run`. A caller can therefore compose a sub-run today.

## Options

A. Build `subrun` in nare as #241 proposes: the loop creates a child session,
   narrows the policy, charges the child to the parent's budget, forwards the
   child's events into the parent stream, and refuses recursion.

B. Keep composition in the caller (the status quo): a caller registers a
   `nare.Tool` that runs a child `nare.run`, and does its own budget and event
   bookkeeping.

C. Build option A later, after context compaction is measured, if the
   measurement shows a need compaction does not meet.

## What option A would have to decide

The issue lists the cases a sub-run must cover. Each one is a decision nare
would own, so each is stated here for the revisit.

- **Tools and policy.** The child's tool allowlist is a subset of the
  parent's, and so are `bash_allow` and `bash_deny` (deny is the union). A
  child's root must lie inside the parent's root. Bash credentials and MCP
  servers are inherited only when the parent has them. The child can narrow a
  policy and can never widen it.

- **Approval.** Every child write, edit and bash call goes through the
  parent's approval seam (ADR 0004). A child never gets an approver of its
  own, and `--yes` covers a child call only as it covers the parent's.

- **Budget.** The child's ceilings are slices of the parent's remaining
  budget, and its usage is charged to the parent when it returns. The parent
  checks its ceiling only between turns. A child runs its whole loop inside
  one parent tool call, so the child must enforce its own ceilings or the
  parent overshoots by the child's full run. A USD slice cannot be enforced
  when cost is unknown, which is already the rule for a run (`check_budget`).

- **Depth.** The child's tool list omits `subrun`, so the default is depth
  one by construction. A depth field would still be recorded.

- **Events and transcript.** Child events would be tagged with the parent's
  `tool_use_id`, as an additive field under docs/contract.md. A caller that
  ignores unknown fields keeps working, so the contract version would not
  change. The child's transcript would be saved with the parent's session
  file and redacted by the same rules.

- **Failure.** A child that stops with any status other than `done` returns
  an error result to the parent, as a caller tool's failure does (ADR 0009,
  rule 4). It never raises through the loop.

- **Resume.** The child runs synchronously inside a tool call. If the process
  dies mid-child, the parent's unanswered call is handled as any unanswered
  call is (`session.unanswered`). The child is lost and re-runs. Nothing is
  resumed partway.

- **Compaction.** The child has its own message list, so compaction (#42 to
  #44) acts on the child's transcript only. The parent receives the child's
  summary text as one tool result, which compaction treats as it treats any
  other result. A sub-run and compaction both keep the parent's context
  small, so a benchmark for #43 must measure them apart. This ADR changes
  nothing in `compact.py`, and option A would not change it either.

## Evidence

- The benchmark comparison in docs/evidence/114 reports 100 of 100 nare
  results as `end_turn`, done. None stopped on context size.
- The Claude Code arm of that comparison had two `max_turns` errors. Both are
  turn-limit stops, not context stops. That arm ran with subagents disallowed,
  so the comparison measured no sub-run.
- The overflow capture (docs/evidence/41/overflow) shows that elision handles
  the large-output case the window guard was built for.

No measured task in the repository needs an isolated context within one run.
The revisit condition is not met.

## Decision

nare does not build a sub-run tool. Composition stays with the caller, through
ADR 0009's in-process tools. Three reasons, in order of weight:

1. **The condition is not met.** U2 and C1 both name a measured need. The
   benchmarks show none. The one context stress capture completes on elision,
   though it used a fake model and no live run.

2. **The budget rule belongs to the caller.** The issue's first acceptance
   criterion, budget charged to the parent, is a budget split between two
   runs. ADR 0008 assigns that to the adapter. Building it into nare would
   move a caller's policy into the harness, and the child's ceiling would have
   to be enforced inside a tool call where the parent cannot stop it.

3. **It would confound the context work.** Compaction (#42 to #44) is the
   measured route to context growth. A second mechanism shipped before #43's
   benchmark would make that benchmark's result unattributable.

The parts a caller cannot do alone are event forwarding into the parent's
stream and charging a child's usage to the parent's ceiling. Both are
accounting, which 0008 leaves to the caller.

## Revisit conditions

Reopen this decision only when one of these holds, and then write a new ADR
that answers each point under "What option A would have to decide":

- A benchmark task, run with compaction enabled, fails on a context-size stop
  that a fresh context would have avoided, and the failure is shown in the
  evidence.
- A measured comparison shows that a fresh context improves pass rate per
  dollar over compaction on the same tasks.

## Consequences

No code, tests, contract or documentation change. The contract stays at
version 3. The U2 entry in the feature-gap decision remains Jason's to close;
this ADR records the recommendation and the reasons for it. Issue #241 closes
with this decision.
