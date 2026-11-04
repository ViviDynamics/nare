# System prompt: a file route, and keep it in the session so a resume uses the same one

Issue #132

## Scope
In: `--system-file PATH` and `--append-system-file PATH`, read as UTF-8 exactly
like `--prompt-file`, each exclusive with its argv form (exit 2 naming both). The
effective system prompt is recorded in a new `system` session field, redacted on
save like the transcript. On `--resume`, a run given no system option sends the
recorded prompt; a run given one sends that, and when it differs from the record
emits one `progress` event saying so. Both `nare run` and `nare tui` take the
flags and honour the record. `nare contract` advertises the routes under
`system_input`. `docs/contract.md` documents the flags, the field and the event.
Out: templating or composing system prompts from several files (the issue's out of
scope). The prompt-cache breakpoint placement is unchanged.

## Assumptions
- `system` is `null` in a session saved before this change, `""` when the run
  sent no system prompt (`--system ''`, or `--tools none` with no system option),
  and the sent text otherwise. A blank part is never sent, so `""` cannot mean
  anything else. A `null` record keeps today's behaviour: the prompt comes from
  the flags (the default when none is given), and that prompt is recorded.
- Every one of `--system`, `--system-file`, `--append-system` and
  `--append-system-file` counts as "a system option". Given any of them, the
  prompt is built from the flags exactly as today, ignoring the record.
- The record is compared after redaction, because the file holds the redacted
  copy; the same `--system` resent on a resume is never reported as a change.
- A `system` that is neither a string nor null makes the session file malformed
  (exit 2): silently falling back to the default is the bug this issue removes.
- The new field has a default and the flags are new, so the contract version is
  unchanged (docs/contract.md, "What changes the version").

## Tasks
- [ ] 1. Flags: `--system-file` and `--append-system-file` read UTF-8 bytes verbatim; with their argv forms they exit 2 naming both; an unreadable file or invalid UTF-8 exits 2 naming the flag.
- [ ] 2. Session field: `system` defaults to null, round-trips, is redacted on save, refuses a non-string, and a pre-change file loads with null.
- [ ] 3. Resume: a session started with `--system-file` and resumed with no system option sends the same system bytes; a changed `--system` sends the new one and emits one progress event; an unchanged one emits none; a null record keeps today's default.
- [ ] 4. TUI: the flags and the record apply to `nare tui` too.
- [ ] 5. Discovery and docs: `nare contract` `system_input`; `docs/contract.md` and README.
