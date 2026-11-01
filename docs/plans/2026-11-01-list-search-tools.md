# Read-only navigation: list and search tools confined to the root

Issue #124

## Scope

In:

- A `list` tool: the entries of one directory, sorted by name, each with its
  kind (dir, file, symlink, other), the size of a file, and a binary marker for
  a file whose first 8 KiB hold a NUL byte. Paginated by an opaque cursor and
  bounded per page by an entry limit and by `MAX_TOOL_OUTPUT`.
- A `search` tool: the files under a directory (or one file) whose path
  matches an optional glob, and, with a `pattern`, the lines of those files
  that match a Python regular expression. Each match is `path:line: text`,
  the text cut to a fixed width. Paginated and bounded the same way.
- Both resolve their `path` through `Policy.resolve`, like `read`, and run in
  a worker thread with no subprocess.
- Neither follows a symlink during traversal: `list` reports a symlink as a
  symlink, `search` skips it. `search` skips `.git/` directories and binary
  files.
- Both join `TOOLS` and `TOOL_SCHEMAS`, so `--tools`, its help, `Policy` and
  the default "all tools" pick them up. The TUI never asks about them, like
  `read`. `nare contract` advertises the built-in tool names and the
  navigation limits (additive; the contract version stays 2).
- Docs: README, docs/contract.md, docs/architecture.md.

Out:

- Per-read evidence records (#125). The result is built as a list of small
  records rendered at the end, so #125 can add fields without reshaping it.
- Ignore files beyond the fixed `.git/` skip, and fuzzy or semantic search
  (out of scope in the issue).
- A change to the default system prompt (see Assumptions).

## Assumptions

- Paths in results are relative to `--root` when there is one (which is what
  `read` accepts), else relative to the working directory when under it, else
  absolute.
- Line numbers start at 1, as in `rg`; the description says `read`'s offset is
  the line number minus one.
- A cursor encodes the call it belongs to (tool, directory, glob, pattern) and
  the position after the last result: the last name for `list`, the last path
  (as name parts, which is the order of the sorted walk) and line for `search`.
  A cursor from another call is refused by name. Resuming by position, not by
  index, keeps pages consistent when the tree changes between calls.
- A page stops at its entry limit or when the next line plus the cursor footer
  would pass `MAX_TOOL_OUTPUT`, whichever is first, so a page is never cut by
  `_truncate` and never loses its cursor.
- `limit` outside its range is clamped (list: 1 to 1000, default 200; search:
  1 to 1000, default 100) rather than refused.
- The default system prompt stays as it is. The model learns about `list` and
  `search` from their descriptions, which it is shown only when they are
  allowed. Steering a default run (which allows `bash` too) away from `rg` is
  a behaviour change that ADR 0007 says is shown with the benchmarks first.
- "Binary files are skipped by `search`" applies to line search. A search
  without a pattern opens no file, so it stays a cheap walk and can find a
  PNG for `read --image-input`; `list` marks binary files.
- An empty file is never binary, so listing one costs no open.
- Regular expressions run in Python's `re`, which cannot be interrupted; a
  catastrophic pattern is marked as a known ceiling in the source.

## Tasks

- [x] 1. Register `list` and `search`: test that `TOOLS` and `TOOL_SCHEMAS`
  hold seven tools and the new schemas name their arguments.
- [x] 2. `list`: tests for sorted entries with kinds, sizes and the binary
  marker, a symlink listed and not followed, pagination with a cursor, a
  foreign or broken cursor refused, and a page bounded by the output cap.
- [x] 3. `search`: tests for glob only, glob plus pattern, binary and `.git/`
  skipped, symlinks (to a directory or a file, inside or outside) never
  traversed, matched lines cut to the fixed width, pagination, and identical
  results for repeated calls.
- [x] 4. Dispatch and policy: tests that a path outside the root is refused,
  that the default path is the root, that results are relative to the root,
  and that the tools run only when the allowlist names them.
- [x] 5. Acceptance: a CLI run with `--tools read,list,search` finds and reads
  a file it was not told about; a tree of 100,000 files pages through with no
  result over the cap.
- [x] 6. TUI: `list` and `search` never prompt; the call line names what was
  searched.
- [x] 7. Contract and docs: `nare contract` names the tools and the navigation
  limits, and docs/contract.md, README and docs/architecture.md describe them.
