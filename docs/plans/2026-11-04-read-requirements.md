# Evidence: require reads before a final answer, and report what was read

Issue #186

## Scope

In: `nare run --require-reads N` and `--require-read PATH` (repeatable), with
`run(require_reads=N, require_read=[...])`. A final answer that would be
accepted is held back until the run has read at least N distinct files, and
each listed PATH. The hold is a correction, charged against the correction
budget from #182. The result lists each distinct file read, with its digest,
when a requirement is set.

Out: the caller's citation check. nare supplies the count and the list; the
caller decides what its answers cite. Tool-call health benchmarking (#159).

## Assumptions

- A read counts when it succeeded and stayed inside the root: those are the
  only reads that leave an evidence record (`session.evidence`, `tool` is
  `read`). A failed read or one refused as outside the root leaves none, so it
  cannot count.
- Reads count over the session's life, as its evidence does. A resumed run
  keeps the files it already read.
- The gate applies only to a final answer, a turn with no tool calls. It runs
  after the schema check, so an answer that fails the schema gets the schema
  correction first, as today.
- A read correction is never free: its gap is not a count of rule failures that
  can be compared with an earlier one. It shares the budget with schema
  corrections (`schema_corrections` counts every correction charged), and its
  correction entries carry `kind: "reads"` (schema corrections carry
  `kind: "schema"`).
- A read correction does not set `schema_retried`, so the next turn still offers
  tools even under `--structured-output`. Setting it would withhold the tools
  the model needs to read what it was asked to read.
- When the budget is spent the run ends `error` with a new stop reason,
  `reads_required`, which names what was missing.
- `require_read` paths are compared with the root-relative paths evidence
  records. An absolute path under the root is made relative. A path outside the
  root can never be read, so it is refused at startup along with a requirement
  that names no `read` tool. Invalid values (non-positive count, empty path)
  never start.
- The result's `reads` key (a list of `{path, sha256, size}`, one per distinct
  path, the latest digest) is present only when a requirement is set. Without
  the option the result is unchanged, so default output keeps its keys.
- Flags, stop reason, result keys and correction `kind` are additive and keep
  contract version 2.
- A read with `offset` or `limit` counts as a read of its file.
- A read hold clears `schema_retried`, so the turn after it offers tools even
  when an earlier schema correction constrained the turn before.
- `--max-corrections` is accepted with a read requirement and no `--schema`,
  since the budget is the same.

## Tasks

- [x] 1. An answer after one read with `require_reads=2` is corrected; after a
  second distinct read the answer is accepted and the run is `done`.
- [x] 2. A failed read and a read outside the root do not count; the answer is
  corrected until two distinct files have actually been read.
- [x] 3. `require_read=["b.txt"]`: an answer after reading only `a.txt` is
  corrected naming `b.txt`; after reading `b.txt` it is accepted.
- [x] 4. A read correction is charged against the #182 budget, never free, and
  does not set `schema_retried` (tools stay offered under a constrained answer).
- [x] 5. Budget spent: the run ends `error` with `stop_reason` `reads_required`.
- [x] 6. Through the CLI, the result carries `reads` with the path and sha256 of
  each distinct file read, and the `budget` reports the correction count.
- [x] 7. Refused at startup: a non-positive count, an empty path, and a
  requirement with no `read` tool. Without the options nothing changes.
- [x] 8. Contract: the two flags, the `reads_required` stop reason, the `reads`
  key and the correction `kind` documented.
