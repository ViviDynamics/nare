# Fork a saved session

Issue #232

## Scope

In: `nare run --resume PATH --fork --session NEW` reads the saved session at PATH,
writes a new session file at NEW, and leaves PATH byte-identical. The new file
records the session and turn it was forked from. The library gets the equivalent,
`nare.fork(session, source=...)`. `docs/contract.md` documents the flag, the fork
record and the library call.

Out: TUI support (`nare tui --fork` is refused by argparse, exit 2). Compaction
code is not touched. No change to the contract version.

## Assumptions

- The fork is a resume into a new file. Redaction, the recorded system prompt, the
  changed-prefix rules (issue #100: signed thinking dropped when the tool set or
  the system prompt changes) and budgets all apply exactly as on resume, because
  the code path is the same after the copy.
- `--session` names the new file. Conservative: a fork with no `--session` exits 2
  rather than inventing a path the caller would have to discover.
- Refused: `--session` equal to the resumed path (that is a resume), and a
  `--session` path that already exists (a fork never overwrites a file).
- `--fork` without `--resume` exits 2.
- The fork gets a new session `id`. Its `process_groups` are cleared: a bash
  process left running by the original belongs to the original run, and a fork
  must not offer to kill it. `evidence` and `usage` carry over, as a resume does.
- Contract version stays 3. A new flag, a new session field with a default, and a
  new `describe()` key are additive (docs/contract.md, "What changes the version").
- Fork record on the session file, `forked_from`, an object or null:
  `{"session_id": <original id>, "turn": <original turns at fork time>, "path": <absolute path of the original>}`.
  It is redacted on save like the system prompt.
- A `progress` event `{"fork": {...}}` says the fork happened, before the first turn.

## Tasks

- [x] 1. Session model: `forked_from` field, loads type check, dumps redacts it.
  Test: `tests/test_session_fork.py::test_forked_from_round_trips_and_defaults_to_none`.
- [x] 2. Library `fork(session, source=None)`: deep copy, new id, record, cleared
  process groups, original untouched. Exported from `nare`.
  Test: `test_library_fork_copies_and_leaves_the_original_untouched`.
- [x] 3. CLI `--fork`: validation (requires `--resume` and `--session`, refuses a
  resume path and an existing file), fork before reopen in `_load_or_new`,
  `--session` defaults to the new path and never the resumed one.
  Tests: `test_fork_writes_a_new_file_and_leaves_the_original_byte_identical`,
  `test_fork_refusals_exit_two_with_no_stdout`, `test_the_fork_record_names_the_session_and_turn_it_came_from`.
- [x] 4. Redaction and changed-prefix on fork: secrets read `[redacted]` in the new
  file only; a changed tool set drops signed thinking in the new file and the
  original keeps its thinking.
  Tests: `test_fork_redacts_like_resume_and_the_original_keeps_its_secret`, `test_fork_drops_signed_thinking_on_changed_tools_and_keeps_it_in_the_original`.
- [x] 5. `describe()` advertises the fork; `docs/contract.md` documents the flag row,
  the fork section and the library call.
  Tests: `test_contract_describes_the_fork_and_keeps_the_version`, existing
  `test_every_run_flag_is_documented_in_the_contract`.
