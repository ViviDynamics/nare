# Policy: allow or deny bash commands by pattern

Issue #230

## Scope

In: `Policy(bash_allow=..., bash_deny=...)` (library) and `--bash-allow PATTERN`
and `--bash-deny PATTERN` (command line, repeatable). A deny refuses a matching
bash call before any approval, so `--yes` does not override it. An allow runs a
matching call without asking. Every other call follows the existing approval
rules. Matches are recorded in the session, one entry per decided call, with
the effect and the pattern but not the command text. The lexer moves from
`nare.tui.approve` to `nare.shell_words` so the core can use it; the TUI imports
it from there unchanged.

Out: the TUI's per-program "always" grants and its destructive-git and
outside-root checks (`risks()`), which still apply to calls that ask. Patterns
do not understand a program's options: `git push *` does not match
`git -C dir push`. Sandboxing (the Undecided OS sandbox item).

## Pattern syntax

- `*` matches any run of characters, including none and spaces. `?` matches one
  character. Every other character matches itself, including `.`.
- A pattern is matched against a simple command's words as bash reads them after
  quote removal, joined by one space. `'git' diff` is `git diff`.

## Compound lines

The line is split at `&&`, `||`, `|`, `|&`, `;` and `&`, the lexer's separators.
A line is **readable** when the lexer reads it and it contains none of `(`, `)`,
`;;`, `$(`, backticks, process substitution, heredocs, newlines, an unterminated
quote, or a brace sequence such as `{1..3}`.

- **Allow** needs a readable line in which every simple command has only plain
  words (no `$`, glob, brace, tilde or other expansion), no redirection, and a
  whole-line match with an allow pattern. Otherwise the call is not allowed by
  rule and the approval rules apply, as before. A line like
  `git diff --stat && touch x` is allowed only if `touch x` also matches an allow
  pattern.
- **Deny** is checked on every *tail* of every simple command (each run of its
  words from any word, with the basename of the first word, and each with a
  trailing space). It ignores case. Under a deny every word must be plain (no
  `$`, glob, brace, tilde or `!`), the first word must hold no `=`, and a line
  that the lexer cannot read, a `.`/`source`, or a shell with no `-c` is
  refused. For a runner (shells, `eval`, `env`, `xargs`, `find`, `sudo`, `git`,
  and similar) each argument is also read again as a command line and checked by
  character run. Deny wins over allow.

Deny is a literal match on the spelled words, not a model of what a program
does. A pattern that must catch a program whatever its options needs a wildcard
that spans them (`git * push *`).

## Session record

The session gains `bash_rules`: one object per call decided by a rule, with
`tool_use_id`, `effect` (`allow` or `deny`) and `patterns`, the sorted list of
rules that decided the call (one for a deny, the rules covering every command for
an allow). A session that set
rules records them under the policy as `bash_allow` and `bash_deny` (sorted
lists), so a reader can see what was in force. A resumed session keeps its
recorded `bash_rules` list; it does not re-derive it.

## Contract version

Stays at 3. Two new command-line flags, a new session field with a default and
new keys under the recorded policy only add to the contract, as
"What changes the version" describes. No existing field, event, flag or exit
code changes meaning.

## Tasks

- [ ] 1. Tests first: allow and deny on plain commands; deny beats allow; `--yes`
  still denies; compound, substitution, backtick, subshell, wrapper and
  quote-splitting bypasses refused under deny and never allowed; redirect and
  expansion never allowed; unreadable line refused under deny; session record;
  policy validation.
- [ ] 2. Move the lexer to `nare.shell_words`; TUI tests still pass.
- [ ] 3. `Policy.bash_allow` / `bash_deny` with validation and `recorded()`.
- [ ] 4. Matching in `dispatch`: deny before approval, allow skips approval,
  record each decision through a callback, as `evidence` is recorded.
- [ ] 5. CLI flags, session field and resume default.
- [ ] 6. docs/contract.md (syntax, compound rule, session record) and README.
