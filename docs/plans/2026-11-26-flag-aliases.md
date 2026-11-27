# Accept Claude Code's and Codex's flag spellings as aliases

Issue #326 (tracking: #331)

## Scope

In: every alias in the issue's table, on `nare run` (and so on `nare -p` and
`nare exec`, which take its flags), and the shared ones on `nare tui` and
`nare "<task>"`; `flag_aliases` in `nare contract`; the flag table; `nare run
--help` printing each alias beside its canonical flag; Codex's `--json` as an
explicit alias of `--jsonl`.

Out: `--permission-mode` and `--yolo` in the terminal UI, which always asks;
nothing in the issue asks for it.

## Assumptions

- Each alias is its own parser option. `nare contract` keys `flags` by an
  option's longest name, so an alias added to an existing option would rename
  that option's key, which bumps the contract. As options of their own they are
  additive and listed, and no existing key moves.
- camelCase (`--allowedTools`) and short (`-m`, `-a`, `-s`) spellings share an
  option with a long kebab name; the flag table test reads `--[a-z0-9-]+` names,
  so they are named in the table's text and in `flag_aliases`.
- "Given" means spelled on the command line. An environment default is not a
  conflict, so `NARE_BUDGET_USD` with `--max-budget-usd` takes the flag.
- `--model provider/model` splits only when the prefix is a nare provider and
  neither `--provider`, `NARE_PROVIDER` nor `--base-url` is set. Any invocation
  that names a provider or an endpoint keeps its model name whole, so a proxy's
  `vendor/model` names work as before.
- An alias matches only spelled in full, so no argparse prefix that worked before
  (`--json` for `--jsonl`, `--y` for `--yes`, `--add` for `--add-root`) changes.
- Only the `--yes` spellings (and `bypassPermissions`, `-a never`) stand in for
  `--yes`; `nare run` without a terminal refuses every other mode exactly as it
  refuses a run without `--yes`.

## Tasks

- [x] 1. Aliases resolve to their canonical flags: table-driven test in `tests/test_flag_aliases.py`.
- [x] 2. Conflicts exit 2 naming both; equal values and repeatable flags combine.
- [x] 3. Help shows aliases on the canonical line; `flags` and `flag_aliases` advertise them; the flag table lists them.
- [x] 4. Old prefixes keep their meaning.
