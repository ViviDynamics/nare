# nare - Redaction Removes Secrets, Not Code

**Date:** 2026-10-08
**Status:** Draft for review
**Size:** S
**Scope:** the credential rule in `events.py`, with fixtures from the practice
run. Bumps the contract to 3.
**Waits on:** a Conductor release that reads contract 3.

Redaction should remove secrets, not code. Covers D2. Changing what a redaction
rule matches bumps the contract to 3 (`docs/contract.md`, "What changes the
version"), so this needs a note for Conductor.

## 1. The problem

The practice run's `session.json` held 65 `[redacted]` markers. Whole lines of
the agent's own code were blanked: `authorId: number,` (the keyword `auth`
matches "author"), `token: text("token").primaryKey(),` and
`password: formData.get("password"),`. The saved `write` of `drizzle.config.ts`
is 349 characters, while the tool result says it wrote 355. `dumps()` redacts
every message on purpose, and the credential rule matches
`auth|token|secret|password|passwd|credential` followed by any word characters,
so a resumed or attached session shows the model damaged copies of its own
files.

| ID | Severity | Issue |
| --- | --- | --- |
| D2 | Medium | Redaction blanks code in the saved transcript (`authorId`, `password: formData.get(…)`), so a resumed session sees damaged files. |

Letters are IDs from the [practice-run
evidence](../../evidence/2026-10-08-tui-practice-run.md), which has the line
references at 89b4aa2. V numbers are from the visual review of 177e8fe, whose
screenshots are in
[2026-10-08-tui-visual-run](../../evidence/2026-10-08-tui-visual-run/). Where
both reviews found the same problem, the row carries both IDs.

## 2. Changes

1. Match the keyword only as a whole identifier segment, treating `_`, `-` and
   camelCase as boundaries, so `author` stops matching while `AUTH_TOKEN` still
   does.
2. Require the value to look like a secret: a quoted literal of 8 or more
   characters with mixed character classes; a bare value after `=` or `:` of 8
   or more characters with both letters and digits and no `(` after it; or a
   known prefix. A bare identifier, `number`, `{`, `formData.get(` or `text(`
   does not qualify. `ponytail:` a shape test, not entropy scoring; tighten it
   from the false positives the fixtures turn up.
3. Keep the prefix patterns (`sk-…`, `ghp_…`) everywhere.
4. Use this session's `write` inputs as negative fixtures in the tests.
5. Bump the contract to 3 and record the change in `docs/contract.md`.

## 3. Acceptance

- [ ] Redacting the practice run's `src/db/schema.ts` and `src/actions/auth.ts`
      changes nothing.
- [ ] `password = "hunter2hunter2"`, `AUTH_TOKEN=Xk92mQ7vLp` and `sk-ant-…` are
      still redacted.
