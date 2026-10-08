# nare - A Default System Prompt

**Date:** 2026-10-08
**Status:** Draft for review
**Size:** S
**Scope:** S of text plus one benchmark case, in `cli.py` and the tool
descriptions in `tools.py`.
**Waits on:** the Bash tool spec, for the bash description.

nare sends no system prompt (`--system` has no default, and the Anthropic
transport then omits `system`), so every behaviour in B2 to B5 and V1 is the
model's default. Covers B1 to B6, and the cause of V1.

## 1. The problem

The practice run installed Postgres from guessed URLs (10 turns, 462k tokens),
overclaimed in its summary, didn't mention what it left running, and committed
under an invented git identity. The visual run wrote README, SUMMARY and
OVERVIEW files nobody asked for (175k tokens).

| ID | Severity | Issue |
| --- | --- | --- |
| B1 | High | nare sends no system prompt, so nothing checks the model's defaults. |
| B2 | High | Unrequested install from guessed URLs, including an x86 binary run on aarch64. |
| B3 | High | The final summary overclaims: "verified end-to-end" after GET requests only, "threaded comments" with no parent column. |
| B4 | High | Side effects not disclosed (a running server, 270 MB in /tmp, a trust-auth superuser), and next steps contradict the environment. |
| B5 | Medium | Invented git identity and an unrequested commit. |
| B6 | Medium | Tool descriptions invite rewrite-then-stale-edit and one call per turn; `ask` doesn't state a default. |

V1, unrequested wrap-up, is tracked in the Steering spec; rule 7 below removes
most of its cause.

Letters are IDs from the [practice-run
evidence](../../evidence/2026-10-08-tui-practice-run.md), which has the line
references at 89b4aa2. V numbers are from the visual review of 177e8fe, whose
screenshots are in
[2026-10-08-tui-visual-run](../../evidence/2026-10-08-tui-visual-run/). Where
both reviews found the same problem, the row carries both IDs.

## 2. Changes

1. A short default prompt that `--system` replaces or extends (B1). Draft rules:
    1. Stay in scope. Offer extra setup, such as installing services or
       downloading tools, in your final message instead of doing it. (B2)
    2. Do not install system software, download and run executables, or start
       long-lived services unless asked. (B2)
    3. Claim only what tool output showed, and say what you did not test. (B3)
    4. Stop what you start. List anything left running or changed outside the
       project root, with how to undo it. (B4)
    5. Do not commit or push unless asked, and never set a git identity. (B5)
    6. Tool calls in one turn run in order, so batch an edit with the command
       that checks it. (B6)
    7. When the task is done, stop: no summary files, banners or extra
       documentation unless asked. (V1)
2. Tool descriptions (B6). `write`: "prefer `edit` for files that already
   exist." `edit`: "re-read a file before editing it if you rewrote it." `bash`:
   the shell semantics from the Bash tool spec, and "kill by `$!`". `ask`:
   "state the default you will use if unanswered"; the current description
   already asks the model to name the readings.

## 3. Acceptance

- [ ] A new benchmark case, "bootstrap X in an environment without its
      database", ends with a build and instructions, starts no daemon, makes no
      commit, and its final message lists what was not tested.
- [ ] The visual run's expense-tracker prompt ends within 8 turns, with no
      README, SUMMARY or OVERVIEW file.
- [ ] A CLI test pins whether `--system` replaces or extends the default.

## 4. Open questions

Does `--system` replace the default or append to it? Appending keeps the safety
rules for callers that only add a persona.

## 5. Related specs

The Bash tool spec defines the shell semantics the bash description states. The
Steering spec is the person's lever for whatever this prompt misses. The risk
list in the Approval policy spec is the backstop for rules 2 and 5.
