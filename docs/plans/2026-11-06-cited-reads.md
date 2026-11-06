# Evidence: hold a typed answer that cites files the run never read

Issue #217

## Scope

In: `nare run --require-cited-reads POINTER` (repeatable) and
`run(require_cited_reads=[...])`, valid only with `--schema`. A POINTER is a JSON
pointer into the typed answer in which `*` matches any array index or object key.
Each string found at a pointer is a path the answer cites. A final answer that
would otherwise be accepted is held while any cited path has not been read, with a
correction that names each unread path. The hold is a read correction, charged
against the shared correction budget, and an exhausted budget ends the run with
`reads_required`, as the other read holds do.

`check=` also receives the run's reads: an optional second parameter, given only
to a check that takes two positional parameters. A one-argument check is called
as before.

Out: deciding whether a cited path is correct, or whether a read supports the
claim the answer makes about it. nare checks that the file was read, not what the
answer says about it. Whole-file reads only (a stricter rule), search and list
results as evidence (they name files without reading them, and do not count).
Checking a pointer against the schema at startup, so a typo is refused rather than
holding nothing. Compaction code and the library default policy (#216).

## Assumptions

- A cited path is compared with evidence's root-relative paths the way
  `--require-read` compares its paths: a path under the root is made relative, a
  link inside the root counts as its target, and the comparison is the same helper
  `build()` uses, factored out so both share it.
- A cited path that resolves outside the root cannot be read, so it is never
  satisfied. The correction tells the model to remove it instead of asking for a
  read that cannot happen.
- A read counts as in the read requirements: a successful read inside the root,
  whole or with `offset` or `limit`. A cited file read only in part therefore
  passes. Making partial reads insufficient is a later flag, if the spike needs it.
- Only `read` evidence counts. A path that appeared in a `search_files` or
  `list_dir` result was never read, which is the failure this issue is about.
- A pointer that matches nothing holds nothing: the schema decides which fields
  exist, and an optional field may be absent. A matched value that is not a string
  is not a path and is skipped; the schema types it. The same path cited twice is
  one gap.
- Pointer syntax is RFC 6901 (`/` separators, `~0` and `~1` escapes) with `*` as
  a wildcard token. An empty pointer, a pointer without a leading `/`, an empty
  token, or an unknown `~` escape is refused at startup. A literal key named `*`
  cannot be addressed; `*` is always the wildcard.
- The check's second argument is the read record list (`path`, `sha256`, `size`,
  the same shape as the result's `reads`), empty when nothing was read. It is
  passed when the callable has two positional parameters (or one plus a
  defaulted one), so existing one-argument checks keep working. A callable with
  only `*args` keeps the one-argument call.
- Flags, stop reason, correction kind and the check's second argument are additive
  and keep contract version 3.
- The read requirement is taken from the answer that passed the schema and the
  check, so a schema or check error is corrected first, as the other holds are.
- `--require-cited-reads` is a read requirement: it needs the `read` tool, it
  turns on the `reads` result key, and its hold spends `--max-corrections`. It
  always needs `--schema`, so `--max-corrections` is valid with it only through
  `--schema` or another read requirement, as today.

## Tasks

- [x] 1. A pointer into a typed answer finds its string values, with `*` over
  arrays and objects; a missing field, a non-string value and a malformed pointer
  are handled as the assumptions state. Test: `tests/test_read_requirements.py`
  pointer cases.
- [x] 2. An answer citing an unread path is held once with a correction naming it;
  after the model reads it, the answer is accepted. A cited path that was read
  passes with no hold. Test: scripted run with `require_cited_reads`.
- [x] 3. A cited path outside the root is never satisfied: the correction says to
  remove it. Test: scripted run.
- [x] 4. The hold is charged to the shared budget and ends `reads_required` when
  spent. Test: budget of 1, second answer still citing an unread path.
- [x] 5. `check` with two positional parameters receives the read records; a
  one-argument check is called unchanged. Test: `tests/test_schema_corrections.py`
  or `tests/test_read_requirements.py`.
- [x] 6. Refused at startup without `--schema` (CLI and library), for a malformed
  pointer, and without the `read` tool. Without the option nothing changes.
- [x] 7. CLI: the result carries `reads`, the flag is in `nare contract` (flags
  and `cited_reads` block), and `docs/contract.md` documents the flag, the pointer
  syntax, the hold, the check argument and the library parameter. The Python
  section examples still run (`tests/test_python_api_docs.py`).
- [x] 8. Refactor: `build()` and the cited comparison share one path helper, and
  the existing read-requirement tests stay green.
