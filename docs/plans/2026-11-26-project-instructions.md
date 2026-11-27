# Read AGENTS.md (CLAUDE.md fallback) in person-facing modes

Issue #330 (tracking: #331)

## Scope

In: `nare`, `nare "<task>"`, `nare -p`, `nare exec` load `$NARE_HOME/AGENTS.md`,
then one `AGENTS.md` per directory from the project root down to the working
directory (`CLAUDE.md` where a directory has none), after the system prompt and
before `--append-system`, capped at 32 KiB with a marker when cut;
`--no-project-instructions`; a `progress` event naming the files; `nare run
--project-instructions` to opt in; docs/contract.md's "What to put in
--append-system" scoped to callers.

Out: nothing from the issue.

## Assumptions

- The project root is the nearest directory above the working directory with a
  `.git`; with none, only the working directory is read, so an AGENTS.md
  somewhere above an unrelated folder is never picked up.
- The 32 KiB cap counts the files' bytes, not the headers nare adds.
- The event goes in the session's events, so `nare -p --json` and the line
  session show it; the TUI's sessions start inside the app, so it shows none.
- `nare run` without `--project-instructions` builds its system prompt exactly
  as before; `--project-instructions` is a new run flag, which keeps the
  contract.

## Tasks

- [x] 1. Root and subdirectory AGENTS.md load in order from the subdirectory: `tests/test_project_instructions.py`.
- [x] 2. A directory with only CLAUDE.md loads it; the home file first; append-system after.
- [x] 3. The cap, the event, `--no-project-instructions`.
- [x] 4. `nare run --yes --jsonl` loads neither; `--project-instructions` opts in.
