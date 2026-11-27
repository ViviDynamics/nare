# One-command install: install.sh brings uv when it is missing

Issue #323

## Scope
In: `install.sh` installs uv with Astral's official installer when no uv is
found, and says so; a `--no-tui` flag and a `NARE_INSTALL_NO_TUI=1` environment
variable that install nare without the `tui` extra; the exact `export PATH=...`
line when uv's tool bin dir is not on `PATH`; the README's Install section led
by the single command, with every other route under "Other ways to install";
offline tests for the missing-uv path with a stub uv installer; a smoke run
with no uv on `PATH` on Linux and macOS, and one in a clean
`debian:stable-slim` container with only curl added.
Out: PyPI and Homebrew; Windows; a prompt before installing uv (the issue asks
for none under `curl | sh`, and `sh install.sh` behaves the same, so the
command does one thing wherever it runs).

## Assumptions
- uv is installed only after the wheel's SHA-256 has matched, so a checksum
  mismatch still installs nothing, uv included.
- uv goes to `~/.local/bin` (`UV_INSTALL_DIR`), the path the installer already
  looks in, so a repeat run finds it even when that directory is not on `PATH`
  and never installs a second uv. An existing uv, on `PATH` or in the places
  uv's own installer uses, is always used as is.
- Astral's installer is fetched from `https://astral.sh/uv/install.sh`, run
  with stdin from `/dev/null`, and keeps its own default of adding its
  directory to the shell profile. `NARE_UV_INSTALLER_URL` (https or file URLs
  only) replaces that URL for the offline tests, as `NARE_RELEASE_URL` does for
  the wheel.
- The headless option has two spellings so a caller can use whichever its
  shell makes easy: `sh -s -- --no-tui` after the pipe, or
  `NARE_INSTALL_NO_TUI=1 sh`. Any other value of the variable than empty, `0`
  or `1` is refused. Headless skips the TUI import check.
- The PATH line is printed for POSIX shells: `export PATH="<bin dir>:$PATH"`,
  with `$PATH` literal, plus `uv tool update-shell` for new terminals.
- The pinned form stays `releases/download/<version>/install.sh`; nothing in
  the URL shape changes.

## Tasks
- [ ] 1. Missing uv is installed after the digest check: tests with a stub uv
  installer (installed into `~/.local/bin`, says so, no prompt, a repeat run
  does not install it again, a mismatch never runs it, a failed installer
  stops with the docs link, an existing uv is used).
- [ ] 2. Headless option: tests that `--no-tui` and `NARE_INSTALL_NO_TUI=1`
  install `nare @ ...` without the extra and skip the TUI check, and that a bad
  value is refused.
- [ ] 3. PATH line: test that the exact `export PATH=` line is printed when the
  bin dir is off `PATH`, and not when it is on it.
- [ ] 4. README: tests that the Install section leads with the single command
  and holds the pinned and headless forms and one "Other ways to install"
  heading.
- [ ] 5. Smoke: `smoke_installer.sh --without-uv` (real Astral installer, uv
  stripped from `PATH`, then repeat, upgrade and headless) in CI on Linux and
  macOS, and in a `debian:stable-slim` container on Linux.
