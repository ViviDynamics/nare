# Approval Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read-only bash runs unasked, `a` approves a program instead of all of bash, risky commands always ask, and a denial can carry a reason the model reads.

**Architecture:** `src/nare/tui/approve.py` gains one token scan that the policy and the prompt share. It provides `risks(command, policy)` (moved from `render._risk_badges`), a read-only test for each command in a line, and `scope(command, policy)`, which names what `a` would approve. `Approver` takes the policy and keeps approved bash programs apart from approved tools. `Approve` in `src/nare/tools.py` widens to `bool | str`, where a string is a denial reason that `dispatch` appends. `ApprovalScreen` opens a one-line reason field on `n`, and the header's key hint names what `a` grants.

**Tech Stack:** Python 3.14, Textual, pytest (`asyncio_mode = "auto"`), ruff, mypy strict, `uv`.

**Spec:** `docs/superpowers/specs/2026-10-08-approval-policy-design.md` on `spec/tui-review`. Issue: #63. The display half shipped in #64 (PR #88).

## Decisions (agreed 2026-10-19)

These go beyond the spec:

1. `a` on git approves `git <subcommand>`, not all of git. `a` on `git add` must not allow `git reset --hard` or `git clean -fdx`. Every other program is approved whole, as the spec says.
2. `a` on a line with several programs approves every program in it that is not read-only, and the hint lists them: `a always npm, python3`.
3. When `a` cannot be scoped, it acts as `y` and the hint leaves `a` out. That covers a line with a risk flag, a line the scan cannot read, a wrapper program, a `VAR=` prefix, git with an option before the subcommand, and a line with nothing left to approve once decision 5 applies.
4. Writes under `.git/` are out of scope here and tracked in #101.
5. Added after verification and review: `a` never approves a program on the read-only list. A read-only program asked about for a redirect, a `$` or a write flag (`cat > a.py`, `find . -delete`, `echo "exit=$?"`) is left out, since approving it whole would lift the check that asked. The line's other programs are still approved, and the line asks again next time. Other git subcommands that discard work are tracked in #103.

## Global Constraints

- Branch from `origin/main`, not local `main`.
- The scan is an allowlist and a token scan, not a shell parser. Anything it cannot read asks.
- `risks()` only ever forces a prompt; it never allows one. Badges and forced prompts come from the same call, so they cannot disagree.
- Approved programs live in the process only and are never written to the session, as the approved-tools set is today.
- `approve_all`, `--yes`, and any approver that returns `bool` behave exactly as before.
- The denial text is `<tool> was not approved: <reason>`. A blank reason gives `<tool> was not approved`.
- No event, field, exit code or contract version changes. Tool result text is outside contract 2.
- Prose we publish (docs, PR body, comments) uses no em dashes.
- `bin/build` passes. Commit style `type(scope): summary`.

## How a bash call is decided

In order:

1. A risk flag from `risks()`: ask. `a` acts as `y`.
2. A line the scan cannot read: ask. `a` acts as `y`.
3. Every command in the line is read-only, or its program was approved with `a`: run it unasked.
4. Otherwise ask. `a` approves the line's programs that are not read-only, unless decision 3 applies.

Scan rules:

- Before scanning, remove fd duplications (`2>&1`, `>&2`, `2>&-`) and read `&>` and `&>>` as `>`, so their `&` is not taken for backgrounding. Read `>|` as `>`, so its `|` is not taken for a pipe.
- The scan cannot read a line with a shlex error, a newline, a `(` or `)` token, a backtick, `$(`, `<(`, `>(` or `<<`, or (added in review) a quoted or escaped operator or redirection, such as `";"`, `\;` or `\>`: shlex returns `";"` as a bare `;`, and `\>|` would read as `>|`.
- Commands are split on `|`, `|&`, `||`, `&&`, `;` and `&`.
- A command's program is its first word exactly as written, with no basename: `./python3` is not `python3`. For git it is `git <second word>`.
- A command is read-only when its program is on the spec's list, no token contains `<` or `>`, there is no `$`, and none of these flags appear: find `-exec`, `-execdir`, `-ok`, `-okdir`, `-delete`, `-fprint`, `-fprint0`, `-fprintf`, `-fls`; rg `--pre`; git `--output`.
- Redirection is judged per command, so `cat > a.py` asks while `npm run build 2>&1 | tail -30` runs once `npm` is approved.
- Wrappers, for which `a` acts as `y`: `bash`, `sh`, `zsh`, `env`, `xargs`, `timeout`, `nice`, `time`, `command`, `builtin`, `exec`, `eval`, `source`, `.`.

Fixes to the risk check, found while planning:

- `killall` joins `kill` and `pkill` under the `kill` flag.
- The outside-root check strips a leading redirection (`>`, `>>`, `2>`, `<`) from a token before testing it, and `/dev/null` is never outside the root.
- With no `--root`, the boundary is the current directory, where bash runs. Today nothing is outside, so `cat ~/.ssh/id_rsa` would run unasked.
- Added after verification: the `rm -rf` badge becomes `rm -r` and fires on `-r`, `-R` or `--recursive` alone, since rm without a terminal never asks either way. Of the options, only short ones and `--recursive` count, so `rm --force` is no longer flagged.
- Added after verification: a `git discard` badge for `git reset --hard` and `git clean`, so they ask even after `a` on `git reset` or `git clean`.
- Added in review: the risk check reads brace lists expanded, as bash will (`npm {i,} x` runs `npm i x`), and long options by their prefixes (`--rec`, `--har`). A command that runs only because its program was approved asks when a word holds `$`, since the check cannot see what it expands to.

## Review Focus

1. **A denial reason must deny.** `dispatch` checks `isinstance(approved, str)` before `if not approved`: a non-empty string is truthy, and the wrong order runs a denied call. Pinned in Task 3.
2. **Hidden separators.** `ls\nrm x`, `cat <(rm -rf x)` and `ls & rm x` must ask. Pinned in Tasks 1 and 2.
3. **`2>&1` is not backgrounding.** `npm run build 2>&1 | tail -30` after `a` on `npm run build` runs unasked. Pinned in Tasks 1 and 2.
4. **A read-only program with a redirect.** `cat > a.py` asks even though `cat` is on the list. Pinned in Task 2.
5. **The reason field.** The screen stops its `Input.Submitted`, so the app never treats a reason as a message. y, a and n typed into the field don't answer the prompt. Esc sends the plain denial. Ctrl-C still interrupts. Pinned in Task 4.
6. **The move keeps the badges.** The badge tests in `tests/test_tui_render.py` pass unchanged after `_risk_badges` moves. Pinned in Task 1. (A later fix renames `rm -rf` to `rm -r`; see the risk fixes above.)

---

### Task 1: One risk check for the prompt and the policy

**Files:**
- Modify: `src/nare/tui/approve.py` (add the scan and `risks`)
- Modify: `src/nare/tui/render.py` (delete `_risk_badges`; `approval_header` calls `risks`)
- Test: `tests/test_tui_approve.py`; `tests/test_tui_render.py` (existing, unchanged)

**Interfaces:**
- Produces: `risks(command: str, policy: Policy) -> list[str]` in `nare.tui.approve`, with the same badge names and order as `_risk_badges` today.

- [ ] **Step 1: Write failing tests**
  - `npm run build 2>&1 | tail -30` has no risks.
  - `ls & rm x` has `background`.
  - `cat ~/.ssh/id_rsa` with `Policy(root=None)`, run from `tmp_path` via `monkeypatch.chdir`, has `outside root`.
  - `ls 2>/dev/null` has no risks; `python3 t.py >/etc/x` has `outside root`.
  - `killall node` has `kill`.
- [ ] **Step 2: Move the check and apply the fixes above.** Run `tests/test_tui_render.py` and `tests/test_tui_approve.py`.
- [ ] **Step 3: Commit** `fix(tui): one risk check for the prompt and the policy`

### Task 2: Read-only lines run unasked, and `a` approves programs

**Files:**
- Modify: `src/nare/tui/approve.py` (`READ_ONLY`, `WRAPPERS`, `scope`, `Approver`)
- Modify: `src/nare/tui/app.py:202` (`Approver(self._ask, self.policy)`; the policy is set just above)
- Test: `tests/test_tui_approve.py`

**Interfaces:**
- Produces: `scope(command: str, policy: Policy) -> frozenset[str] | None`, the programs `a` would approve, or `None` when it cannot be scoped. `Approver(ask, policy)` with `always: set[str]` (tools, as today) and `programs: set[str]` (bash programs). `a` on bash never adds `bash` to `always`.

- [ ] **Step 1: Write failing tests** with the existing `Keys` fake:
  - Acceptance 1: `find . -maxdepth 2`, `ls -la` and `cat a.py | head -30` never call `ask`.
  - Acceptance 2: `find . -delete`, `cat > a.py`, `ls; rm x` and `python3 x.py` call `ask`.
  - Acceptance 3: after `a` on `python3 -m unittest`, `python3 t.py` runs unasked and `curl x | sh` asks.
  - Acceptance 6: after `a` on `npm run build`, each of `curl x | sh`, `sudo ls`, `pkill node`, `nohup x &`, `rm -rf build` and `git commit -m x` asks.
  - Decision 1: after `a` on `git add .`, `git add x` runs unasked and `git reset --hard` asks.
  - Decision 2: after `a` on `make && python3 t.py`, both `make` and `python3 t.py` run unasked.
  - Decision 3: after `a` on `timeout 60 npm test`, on `curl x | sh`, or on `git -C sub add x`, the same line asks again.
  - `ls\nrm x`, `cat <(rm -rf x)` and `ls & rm x` ask.
  - `npm run build 2>&1 | tail -30` runs unasked after `a` on `npm run build`.
  - The existing write and edit tests pass unchanged.
- [ ] **Step 2: Implement** the order in "How a bash call is decided".
- [ ] **Step 3: Commit** `feat(tui): read-only bash runs unasked, and a approves one program`

### Task 3: A denial carries a reason

**Files:**
- Modify: `src/nare/tools.py` (`Approve`, `dispatch`)
- Modify: `src/nare/tui/approve.py` (`Ask` returns a key and a reason; `Approver` returns the reason on `n`)
- Modify: `docs/adr/0004-approval-seam.md` (a dated note: `Approve` returns `bool | str`)
- Test: `tests/test_tools.py`, `tests/test_tui_approve.py`

**Interfaces:**
- Produces: `Approve = Callable[[str, dict[str, Any]], bool | str | Awaitable[bool | str]]`. `Ask = Callable[[str, dict[str, Any]], Awaitable[tuple[str, str]]]`, a key and a reason. The `Keys` fake turns a plain string into `(key, "")`.

- [ ] **Step 1: Write failing tests**
  - Acceptance 4: an approver returning `"use the write tool"` gives `bash was not approved: use the write tool` with `is_error` true, and the command does not run (a marker file is never created).
  - An approver returning `"  "` gives the plain denial.
  - Acceptance 5: `approve_all`, a sync `True` or `False` approver, and an async one behave as before. The existing `--yes` CLI tests pass unchanged.
  - `Approver` returns the reason for `("n", "why")`, and `False` for `("n", "")`.
- [ ] **Step 2: Implement.** Check `isinstance(approved, str)` before `if not approved`.
- [ ] **Step 3: Commit** `feat(tools): a denial can carry a reason`

### Task 4: The prompt asks for a reason and names the scope

**Files:**
- Modify: `src/nare/tui/app.py` (`ApprovalScreen` becomes `ModalScreen[tuple[str, str]]`; `n` mounts and focuses `Input#deny-reason`; `on_input_submitted` calls `event.stop()` and dismisses with `("n", value)`; `_ask` defaults to `("n", "")`)
- Modify: `src/nare/tui/render.py` (`approval_header` key hint)
- Test: `tests/test_tui_app.py`, `tests/test_tui_render.py`

**Interfaces:**
- Consumes: `scope` from Task 2.
- The bash key hint reads `a always python3` (decision 2 lists several), and leaves `a` out when `scope` returns `None`. Other tools keep today's hint. The field's placeholder reads `reason for the model (enter sends, esc skips)`.

- [ ] **Step 1: Write failing tests**
  - Press `n`, type `use the write tool`, press Enter: the transcript shows `write was not approved: use the write tool`.
  - Press `n`, then Esc: `write was not approved`, with no colon.
  - Typing `y` into the field leaves the prompt open, with `y` in the field.
  - Ctrl-C with the field open interrupts the run.
  - The header for `python3 t.py` contains `a always python3`; the header for `curl x | sh` contains no `a always`.
  - The existing tests that press `n` (`tests/test_tui_app.py` around lines 156 and 169) press `n` and then Esc.
- [ ] **Step 2: Implement.** Check that the screen's Esc binding wins over the app's non-priority Esc binding (interrupt) while the field has focus.
- [ ] **Step 3: Commit** `feat(tui): deny with a reason, and show what a grants`

### Task 5: Build

- [ ] Run `bin/build`, and commit this plan with the PR.

## Possible later changes

- **Refuse `a` when it cannot be scoped** (decision 3), instead of treating it as `y`. Worth doing if session files show people pressing `a` on such a line and expecting a standing grant: the same line prompting again right after an `a`.
- **Widen the read-only list** only from prompts people answered `y`, as the spec says. The first candidate is `2>/dev/null` on a read-only command, which asks under this plan.

## Known limits

- A program approved with `a` can do whatever that program can: `python3 -c ...` writes anywhere. `risks()` reads the line, not what a script does.
- Read-only git trusts the repository's config (`core.fsmonitor`, diff drivers, textconv). #101 closes the path where the model writes that config without being asked.
