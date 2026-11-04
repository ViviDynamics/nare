# nare contract: list every run flag

Issue #149

## Scope

In:

- A `flags` key in `nare contract`: an object keyed by every `nare run`
  option's name (`--stream`, `--budget-tokens`, ...), each holding
  `takes_value` and `choices` (a list, or `null` when the option has none).
  Generated from the argument parser at call time, so it cannot drift.
- A `## Run flags` table in docs/contract.md listing every flag, and a test
  that fails when the parser and the table disagree. A new flag shows up in
  `flags` with no edit, and the test makes its author document it.

Out:

- Defaults. Several read the environment (`NARE_BASE_URL`, `NARE_MODEL`), so
  a default in the contract would print the caller's environment and differ
  between machines. The contract is what this build accepts, not how it is
  configured.
- The positional prompt and `--help`: the prompt routes are advertised under
  `prompt_input`, and `--help` is argparse's, not nare's.
- `nare tui` flags. The issue is about `nare run`, the subprocess surface.

## Assumptions

- Keyed by name, not a list, because the caller's question is "does this
  flag exist": `"--stream" in contract["flags"]`.
- Additive: a new discovery key keeps the contract version.
- `describe()` imports the parser from `nare.cli` inside the function, the
  same pattern it uses for `nare.session` and `nare.tools`.

## Tasks

- [x] 1. `flags` lists every run option with `takes_value` and `choices`:
  a test walks the parser's actions and compares.
- [x] 2. A new parser option appears without an edit: a test adds one to a
  parser and reads it back.
- [x] 3. docs/contract.md `## Run flags` table matches `flags` exactly: a
  test parses the table.
