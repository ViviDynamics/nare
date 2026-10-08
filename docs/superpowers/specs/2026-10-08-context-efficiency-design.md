# nare - Context Efficiency: Prompt Caching and Dropping Dead Weight

**Date:** 2026-10-08
**Status:** Draft for review
**Size:** M
**Scope:** `messages_from` and request building in `transport/anthropic.py`,
cached-token reading in `transport/openai.py`, and `compact.py`.
**Waits on:** nothing

Long sessions re-send their whole history every turn at full price; caching and
dropping dead weight would cut the practice run's cost by about 80%. Covers E1
to E4.

## 1. The problem

The practice run had 0 cache reads and 0 cache writes over 50 turns, so about
1.82M re-sent tokens were billed at full price. Unsigned thinking was 38% of the
final context: all 44 thinking blocks had `signature: ''`, and one
23.9k-character planning block was re-sent 46 times. Write inputs were another
46%, and compaction only elides tool results (7.5%).

| ID | Severity | Issue |
| --- | --- | --- |
| E1 | High | No prompt caching: 0 cache reads over 50 turns. |
| E2 | Medium | Unsigned thinking is re-sent every turn (38% of the context). |
| E3 | Low | Empty text blocks are saved and re-sent. |
| E4 | Low | Compaction can't reach write inputs, which were 46% of the context. |

Letters are IDs from the [practice-run
evidence](../../evidence/2026-10-08-tui-practice-run.md), which has the line
references at 89b4aa2. V numbers are from the visual review of 177e8fe, whose
screenshots are in
[2026-10-08-tui-visual-run](../../evidence/2026-10-08-tui-visual-run/). Where
both reviews found the same problem, the row carries both IDs.

## 2. Changes

1. Prompt caching (E1): put `cache_control: {"type": "ephemeral"}` on the last
   block of the last message, which moves forward each turn, and one on the
   tools. Read `cached_tokens` on the OpenAI route. Compaction rewrites old
   messages and breaks the cache once per compaction. At a 10% cache-read price,
   the practice run would have cost about $0.06 instead of $0.30.
2. Drop unsigned thinking (E2): `messages_from` drops thinking blocks whose
   signature is empty or missing. The proxy makes these, they carry no server
   state, and the real API can't verify them. Signed blocks stay, and the OpenAI
   transport already drops thinking. Saves up to about 635k tokens (34%) on the
   practice run if the proxy forwards them to GLM; A/B it.
3. Drop empty text blocks (E3): the same filter drops
   `{"type":"text","text":""}`, and an assistant message left with nothing. The
   real API rejects them, which matters when a session resumes on another
   provider, and the TUI draws each one as a blank line.
4. Compact write inputs (E4): outside the protected tail, replace
   `write.content` and long `edit.old` / `edit.new` with
   `[elided by nare: wrote 4,521 chars to src/db/schema.ts at turn 9. Read the file if you need it.]`.
   `input` stays an object, and call/result pairs stay intact.

## 3. Acceptance

- [ ] Two calls with the same prefix to claude-haiku-4-5 through the proxy: the
      second shows `cache_read > 0`. For spark, check whether LiteLLM maps
      `cached_tokens`.
- [ ] `messages_from` drops `{"type":"thinking","signature":""}` and empty text
      blocks, and keeps signed thinking.
- [ ] A write-heavy fixture session drops below 60% of its window after
      compaction.
