# Bash sandbox: optional OS confinement

Issue #240. Decision U1 in `docs/plans/2026-11-05-feature-gap-decision.md`,
accepted by Jason on 2026-11-07.

## Scope

In: `--bash-sandbox` and `Policy(bash_sandbox=True)`, which run each `bash`
command under an OS confinement profile. macOS uses Seatbelt (`sandbox-exec`).
Linux uses bubblewrap (`bwrap`). Writes are allowed only inside `--root` and a
private temporary directory made per call. Reads are allowed except for
credential locations. The network is off unless `--bash-network` (or
`Policy(bash_network=True)`) allows it. The profile in force is recorded in the
session's policy, and `nare contract` reports whether confinement is available
on this host. When confinement was requested and cannot start, the call is
refused: nothing runs unconfined.

Out: Landlock. It is the proposal's fallback where bubblewrap is missing. This
change refuses clearly there instead, so the fallback is a follow-up. Confinement
of caller tools (`--command-tools`) and MCP stdio servers, which are not `bash`.
Limits that remain are listed in docs/contract.md.

## Assumptions

- The flag is off by default. Without it nothing changes: no new session key,
  no new probe, the same spawn.
- Refusal is at two points: `Policy` construction (startup, exit 2 from the
  CLI) and dispatch (a per-call check, so a changed host cannot run a call
  unconfined). Neither falls back to running the command.
- `--bash-sandbox` needs `--root`. Without a root there is no writable
  directory to name, and the default would be "write anywhere".
- `--bash-network` needs `--bash-sandbox`. A network switch alone would be a
  silent no-op.
- The contract version stays at 3. The flags, the `bash_sandbox` session
  record and the `nare contract` key are all additions with defaults, which
  `docs/contract.md` says keep the number.
- Credential locations are a fixed list (`~/.ssh`, `~/.aws`, `~/.gnupg`,
  `~/.kube`, `~/.docker`, `~/.azure`, `~/.config/gh`, `~/.config/gcloud`,
  `~/.netrc`, `~/.npmrc`, `~/.pypirc`, `~/.git-credentials`, the git credentials
  file, and on macOS `~/Library/Keychains`), plus all of `NARE_HOME`. NARE_HOME
  holds MCP tokens and saved sessions, so the whole folder is denied, which is
  a superset of the token files.
- macOS also denies the Security framework's mach services. Without that a
  `security find-generic-password` call inside Seatbelt returns a keychain
  secret, which a path rule cannot stop.
- A credential path is masked when it exists at spawn. A file created inside the
  root during one call is not masked on Linux (bubblewrap cannot create a mount
  point in a writable tree without touching the host). Seatbelt denies by path
  and covers it.
- On Linux the sandbox runs in its own PID namespace, so `/proc/<pid>` names
  nothing outside it, and `/proc/self/root` leads to the sandbox root.
- `--new-session` is not passed to bubblewrap: the spawn already starts a new
  session, and a second `setsid` would move the shell out of the process group
  that a timeout kills.

## Tasks

- [x] 1. Escape tests first, live where the platform supports it: write
  outside the root, read a denied credential, symlink out of the root, hard link
  from a credential, network connect, `python -c` write, subshell and `env`
  write, `/proc/self/root` read (Linux), keychain read (macOS). They fail
  before the change.
- [x] 2. Profile builders with unit tests: Seatbelt text and bubblewrap argv,
  with escaping, credential list, and nested-path folding.
- [x] 3. Availability probe and the refusal paths: unsupported platform, missing
  `bwrap`, a failing probe, a sandbox with no root, a network switch without a
  sandbox, a root of `/`.
- [x] 4. Wiring: `Policy` fields and `recorded()`, `dispatch` refusal before
  approval, `bash_outcome` spawns the wrapped argv with a per-call TMPDIR, and
  the scratch directory is removed after the call.
- [x] 5. CLI flags, `nare contract` `bash_sandbox` key, `docs/contract.md` rows
  and section, README note.
- [x] 6. CI: install bubblewrap on the Linux job and require the live tests
  there (`NARE_REQUIRE_SANDBOX=1`).
- [x] 7. Adversarial review round with a fresh agent (diffs only), fixes with
  tests that fail without them, and mutation checks on each protection.
  Review findings fixed: a root holding a credential (rename escape); host
  secrets in the environment; a home-less fallback; host sockets in /tmp and
  /run on Linux; the credential list and the System keychain.
