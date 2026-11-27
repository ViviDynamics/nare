# Entry points like Claude Code and Codex: nare, nare "<task>", nare -p, nare exec

Issue #324 (tracking: #331)

## Scope

In:
- `nare` with no arguments opens the terminal UI when the `tui` extra is
  installed and the terminal is interactive; otherwise it prints a short
  getting-started and exits 0.
- `nare "<task>"` opens the terminal UI with that first prompt. Without the
  `tui` extra it runs a plain line-mode session at the terminal: progress on
  stderr, the answer on stdout, a prompt for follow-ups, and an approval
  question before each tool call that changes something (the TUI approver's
  policy, `nare.tui.approve.Approver`).
- `nare -p "<task>"` / `--print` and `nare exec "<task>"`: one-shot, root is the
  working directory, the answer on stdout and readable progress on stderr.
  Piped stdin is extra context; `-` reads the whole prompt from stdin.
  `--output-format text|json|stream-json`, and `--json` for `stream-json`.
  With no permission flag a tool call that needs approval is denied and
  reported, and the run goes on; `--yes`, `--dangerously-skip-permissions` and
  `--yolo` approve everything.
- `nare run` without `--yes` asks at the terminal when stdin is a terminal,
  and refuses exactly as today when it is not.
- `nare --help` leads with the person-facing forms and lists `contract`,
  `redact` and `mcp` under a heading for callers.
- A first word that names a subcommand keeps meaning the subcommand;
  `nare -- run` sends "run" as a task.

Out:
- Flag aliases beyond the three permission spellings the issue names (#326).
- A default session store, `-c`, `-r` (#327). `nare -p` saves a session only
  with `--session`, as `nare run` does; `nare "<task>"` through the TUI saves
  as `nare tui` does today.
- `nare auth login` (#328): the getting-started names the key variables only
  until that ships.
- MCP config files (#329), AGENTS.md (#330).

## Assumptions

- `stream-json` (and `--json`, `--jsonl`) prints exactly what
  `nare run --jsonl` prints: the same events and the same result line, from
  the same code. `json` prints the result line alone. Neither changes the
  contract: they are new spellings of an existing stream, and `nare run`'s
  flags, events, result and exit codes are untouched.
- `nare -p` and `nare exec` take every `nare run` flag, so a script can move
  between them without losing one.
- Piped stdin is read only when stdin is a pipe or a regular file, and is
  appended to the prompt in a `<stdin>` block. A terminal or `/dev/null` is not
  read.
- "An interactive terminal" for `nare run` is `sys.stdin.isatty()`. A caller
  that spawns nare never gives it a terminal, so its runs refuse
  without `--yes` exactly as before.
- `nare "<task>"` with no terminal exits 2 and names `nare -p`, since there is
  nobody to approve a change.
- Person-facing modes default `--root` to the working directory, as Claude
  Code and Codex confine work to it.

## Tasks

- [x] 1. Routing: `nare`, `nare "<task>"`, `nare -p`, `nare exec`, `nare -- run`
  and subcommands go to the right place: routing tests in `tests/test_entry_points.py`.
- [x] 2. `nare -p` / `nare exec` output formats, stdin context and the deny
  approver: tests running the fake provider through `main()`.
- [x] 3. `nare run` asks at a terminal and refuses without one: a test with
  a fake terminal and a subprocess test with a pipe on stdin.
- [x] 4. Line-mode session and getting-started without the tui extra: tests
  with `find_spec` stubbed and a scripted terminal.
- [x] 5. `nare --help` grouping, README first screen and docs: help test.
