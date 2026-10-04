# Compaction, Cost and Budget Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Deliver #41 and unblock Scrutare with cumulative session budgets and durable partial results.

**Architecture:** Normalize provider token categories in transports. The loop prices replies, compacts context before calls, and enforces budgets after complete steps and before resumed calls. The CLI validates settings before constructing transports and emits additive contract fields.

**Tech Stack:** Python, dataclasses, argparse, httpx2 MockTransport, pytest, uv.

**Spec:** ../specs/2026-10-01-nare-compaction-and-budget-design.md (approved).

## Global Constraints

- Contract and session version stay 1; additions have defaults.
- Budgets cover input + output + cache_read + cache_write across the session.
- Check after the complete step, including tools. Done and blocked win.
- No live model calls or credential changes. No Scrutare allocation/orchestration.
- Possible crossing-turn overshoot must be explicit; no strict spend ceiling.
- The approved live benchmark is replaced by deterministic CLI overflow evidence under the user's no-live constraint.

## Review Focus

- Exhausted resume must make zero model calls, preserve usage and available findings, and report the effective budget.
- OpenAI cached input must not be counted twice; Anthropic cache writes remain separate.
- Legacy usage lacks cost history: treat nonempty old usage as unknown dollars, not zero.
- Compaction estimates must survive resumes and account for rewritten prefixes without repeated elision.
- Budget errors must be distinguished from provider failure, schema failure and done on a crossing turn.

### Task 1: Cumulative token budget and CLI evidence

**Files:** src/nare/{session,loop,cli}.py; tests/test_budget_cli.py; tests/budget_probe.py.
**Interfaces:** Usage.total_tokens; run(..., budget_tokens: int | None); Session.budget dict; result.budget and error detail.budget.
- [x] Write actual CLI tests for the three-turn max-tokens probe, exhaustion after two tool turns, cache accounting, invalid values, env precedence, exhausted resume and larger-budget continuation, done/blocked precedence, valid structured text on tool turns.
- [x] Run uv run pytest -q tests/test_budget_cli.py; observe missing flag failures.
- [x] Implement validation, persisted effective limits, pre-call guard, post-step check and schema-valid partial output retention.
- [x] Run the new tests and existing CLI/loop/schema tests; commit.

### Task 2: Context compaction

**Files:** src/nare/compact.py; src/nare/{session,loop,cli}.py; tests/test_compact.py; tests/fake_provider.py.
**Interfaces:** compact(Session, int) -> CompactionReport | None; estimate(Session) -> float; Session.last_input_tokens and last_input_messages.
- [x] Write tests for 80/60 thresholds, oldest-first elision, protected two turns/task/assistant/error/ask, pairs, fresh and measured estimates, exhausted context without a call and resume compaction.
- [x] Run tests and observe missing module/flag failures.
- [x] Implement compaction and explicit/default window handling, recording measured input prefix boundaries.
- [x] Run relevant suites; commit.

### Task 3: Cost, USD budgets and backend discovery

**Files:** src/nare/accounting.py; src/nare/transport/{__init__,openai,anthropic}.py; src/nare/{session,loop,cli}.py; tests/test_accounting.py; tests/test_budget_cli.py; tests/test_transport_metadata.py.
**Interfaces:** Reply.cost; Usage.cost; Prices; transport.context_window(); reported_cost(headers); discover_context_window(client, base, model, headers).
- [x] Write deterministic tests for reported/priced/unknown precedence, unknown totals, USD stop, finite validation, legacy sessions, both rails' raw headers and model-info success/failure/timeout/no-direct-request.
- [x] Run tests and observe failures.
- [x] Implement cost helpers, validated env prices/USD budget, raw Anthropic response and backend window discovery.
- [x] Run suites; commit.

### Task 4: Documentation, review and shipping

**Files:** README.md; docs/{contract,architecture}.md; docs/evidence/41/; benchmark overflow fixture/test.
- [x] Capture actual CLI JSONL/session evidence with deterministic fake transports, including max-tokens baseline and cumulative budget stop, resume, done crossing, cache and context overflow.
- [x] Document flags, token normalization, persisted usage, limit selection on resume, partial findings recovery, stop reasons and Scrutare strict-budget mismatch.
- [ ] Run bin/build; review complete diff against approved scope and user requirements; fix findings with regression tests.
- [ ] Preflight and quality guard; open PR, link thread, CI, review gate, verified squash merge, automatic release read-back.
