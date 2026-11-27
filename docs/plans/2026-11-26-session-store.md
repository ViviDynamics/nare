# Sessions a person can continue: -c/--continue, -r/--resume <id>, a default store

Issue #327 (tracking: #331)

## Scope

In: the store `$NARE_HOME/sessions/<project>/` for `nare`, `nare "<task>"`,
`nare -p` and `nare exec` given no `--session`; `-c/--continue`, `-r <id|path>`
and `--fork-session` on those forms; Codex's `nare resume`, `nare fork` and
`nare exec resume`; `nare sessions` (and `--json`); the resume picker listing
the store.

Out: `nare run` keeps today's behaviour: without `--session` it writes
nothing, and its `--resume` (now also `-r`) takes a path. `nare tui` keeps its
default `$NARE_HOME/sessions/<id>.json`, which its tests and docs pin.

## Assumptions

- `<project>` is the working directory's real path with every character
  outside `[A-Za-z0-9._-]` made `-`, as Claude Code names its project folders.
- A store file is named for its session id, so `-r <id>` matches the file's
  name; it also matches the session's own id and a unique prefix of either.
- "started" needs a record: a new session field `started_at`, set by the
  person-facing forms and written only when set, so a `nare run` session file
  is byte for byte what it was. A field with a default keeps the contract.
- `-c` and `--fork-session` are not `nare run` flags, so neither the flag
  table nor `nare contract` changes for them; `-r` joins `--resume`, whose key
  stays `--resume`.

## Tasks

- [x] 1. The store: `nare -p` saves there 0600 in a 0700 folder, redacted: `tests/test_session_store.py`.
- [x] 2. `-c`, `-r <id>`, `-r <path>`, an unknown id exiting 2 naming the store, `--fork-session`.
- [x] 3. `nare resume`, `nare fork`, `nare exec resume`, `nare sessions`.
- [x] 4. `nare run` without `--session` writes nothing to the store; its session file has no `started_at`.
