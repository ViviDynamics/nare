# Resume: state a changed --schema, and document correction by resume

Issue #148

## Scope

In:

- A new session field, `schema_sha256`: the SHA-256 of the schema last stated
  to the model, over its canonical JSON (sorted keys, no whitespace), so key
  order and formatting are not a change.
- On a run with a schema: a session that never stated one states it, as
  today, and records the digest. A session whose recorded digest differs from
  the schema passed now gets one user message saying the schema changed,
  followed by the new schema, and one `progress` event with `detail`
  `{"schema": {"changed": true}}`; the new digest is recorded. The same schema
  adds nothing.
- On a change, a retained `output` that does not satisfy the new schema is
  dropped, so `result.output` stays a document that passes the schema the run
  was given.
- docs/contract.md: the field, the event, and a "Correction by resume"
  section: `--resume PATH --tools none --max-turns 2 --schema NEW` with the
  correction as the prompt, and how budgets, turns and the correction round
  carry over.

Out:

- A `--validate CMD` hook (out of scope in the issue).

## Assumptions

- A file saved before this change has no digest. Resuming it states nothing
  new, exactly as before, and records the digest of the schema it is given
  from then on, as `system` does for a file without a recorded prompt (#132).
- The change message reuses `schema_instruction`, prefixed with one sentence,
  so the stated schema reads the same as a first statement.
- Additive: a new session field with a default, a new `progress` detail key.
  The contract stays at version 2.

## Tasks

- [ ] 1. First statement records the digest: a run with a schema saves
  `schema_sha256` of its canonical JSON.
- [ ] 2. A resume with a different schema puts it in the transcript once,
  with the change event; a second resume with the same schema adds nothing.
- [ ] 3. A resume with the same schema, keys reordered, adds nothing.
- [ ] 4. A retained output failing the new schema is dropped; one passing it
  is kept.
- [ ] 5. A session without a digest loads, states nothing new on resume, and
  records the digest.
- [ ] 6. CLI: `--resume --schema NEW` puts the new schema in the saved
  transcript once. Docs.
