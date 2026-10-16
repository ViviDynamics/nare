# Turn limit and cost: warn before the cap, and report real or unknown cost

Issue #65

## Scope
In: pre-cap warnings, one tools-disabled summary turn, continuation hint, reliable streamed cost and discovered prices, estimate labels.
Out: status-line redesign (#66), automatic process termination (existing lifecycle controls retain ownership).

## Assumptions
- The summary is one extra accounted turn; status remains error/max_turns.
- Explicit prices override discovered prices. Missing usage or prices means unknown cost.
- Metadata discovery is shared by context and pricing, including with an explicit context window.

## Tasks
- [x] 1. Turn limits: scripted requests prove warnings, a final summary, no tool execution, and the continuation hint.
- [x] 2. Cost: streamed-header regressions and metadata tests prove discovered pricing and unknown fallback.
- [ ] 3. Verify build, self-review full diff, CI and merge.
