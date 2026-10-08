# nare - Turn Limit and Cost: Warn Before the Cap, Real or Unknown Cost

**Date:** 2026-10-08
**Status:** Draft for review
**Size:** M
**Scope:** `loop.py`, `reported_cost` in `transport/__init__.py`, `turn_usage`
in `accounting.py`, and the `/v1/model/info` call that `discover_context_window`
already makes.
**Waits on:** nothing

A run should never hit the turn cap without warning or a summary, and cost
should be either real or marked unknown. Covers C1 and C2 · V19.

## 1. The problem

The practice run used 48 of its 50 turns: one more failed download would have
ended in `error: stopped after 50 turns`, with no summary and processes still
running. Both runs reported $0.00, against 1.87M and 207k tokens. Streamed
replies carry no `x-litellm-response-cost`, only
`x-litellm-response-cost-original: 0.0` (headers go out before usage is known),
and `reported_cost` falls back to it. `NARE_PRICE_*` is then ignored, because
prices apply only when cost is `None`.

| ID | Severity | Issue |
| --- | --- | --- |
| C1 | Medium | 48 of 50 turns used with no warning to the model or the person; hitting the cap ends the run without a summary. |
| C2 · V19 | Medium | Cost shows $0.00 on every streamed turn, so `--budget-usd` can never trip. |

Letters are IDs from the [practice-run
evidence](../../evidence/2026-10-08-tui-practice-run.md), which has the line
references at 89b4aa2. V numbers are from the visual review of 177e8fe, whose
screenshots are in
[2026-10-08-tui-visual-run](../../evidence/2026-10-08-tui-visual-run/). Where
both reviews found the same problem, the row carries both IDs.

## 2. Changes

1. Warn the model (C1): when 5 or fewer turns remain, append "N turns left: wrap
   up and summarise." to the next user message.
2. A final summary turn (C1): at the cap, run one more turn with tools disabled
   so the run ends with a summary. Status stays `error` with `stop_reason`
   `max_turns`, so the contract doesn't change.
3. Say how to continue (C1): the outcome reads "Turn limit reached (50 per run).
   Type `continue` for up to 50 more." Today any text silently reopens the
   session with a fresh 50.
4. Real cost (C2): ignore header cost on streamed replies. Read the four
   `*_cost_per_token` fields (×1e6) from `/v1/model/info` and use them as
   `Prices` when `NARE_PRICE_*` is unset. With neither, cost is `None`, and
   computed costs are labelled as estimates.

## 3. Acceptance

- [ ] A scripted run that hits the cap still ends with an assistant summary, and
      its outcome includes the `continue` hint.
- [ ] With 5 turns left, the next request carries the wrap-up note.
- [ ] A streamed reply with only `-original: 0.0`, plus model/info prices, gives
      a nonzero cost; with no prices, cost is `None` and the TUI shows "cost
      unknown".

## 4. Related specs

The Status line spec displays the cost figure this spec computes, and colours
the turn counter as the cap nears (C1).
