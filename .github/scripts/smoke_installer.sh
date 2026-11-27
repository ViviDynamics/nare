#!/usr/bin/env bash
# Installs a rendered release from DIST into a clean HOME, then again, then
# without the terminal UI, and checks each time that the installed nare
# reports VERSION and that its TUI imports, or with --no-tui that textual is
# not there at all.
#
#   .github/scripts/smoke_installer.sh DIST VERSION [--without-uv]
#
# DIST holds what the release attaches: the wheel and install.sh. The first
# run is the documented `curl ... | sh`; the second runs the file again, which
# is what a reader does to repair or refresh an install.
#
# --without-uv runs the installer with a PATH that has no uv on it, so it has
# to install uv itself with Astral's real installer (this needs the network),
# and checks that a repeat run uses that uv rather than installing another.
# In a container with no uv at all, such as a clean debian:stable-slim with
# only curl added, it is the whole first-run experience.
set -euo pipefail

usage='usage: smoke_installer.sh DIST VERSION [--without-uv]'
dist=$(cd "${1:?$usage}" && pwd)
version=${2:?$usage}
without_uv=0
case "${3:-}" in
    '') ;;
    --without-uv) without_uv=1 ;;
    *) echo "$usage" >&2; exit 2 ;;
esac

HOME=$(mktemp -d)
export HOME
# A clean install: nothing from the runner's uv setup carries over, not its
# tool directories, its cache, its pinned Python or an active environment.
unset UV_TOOL_DIR UV_TOOL_BIN_DIR UV_CACHE_DIR UV_PYTHON UV_PYTHON_INSTALL_DIR \
    VIRTUAL_ENV XDG_DATA_HOME XDG_BIN_HOME XDG_CACHE_HOME XDG_CONFIG_HOME \
    NARE_INSTALL_NO_TUI
export NARE_RELEASE_URL="file://$dist"

if [ "$without_uv" = 1 ]; then
    # The system's own tools and nothing a runner or a developer added, so
    # the uv that setup-uv or Homebrew installed is out of reach.
    PATH=/usr/bin:/bin:/usr/sbin:/sbin
    export PATH
    if found=$(command -v uv); then
        echo "not a clean install: uv is still on PATH at $found" >&2
        exit 1
    fi
    uv=$HOME/.local/bin/uv
else
    uv=uv
fi

check() {
    local bin_dir tool_dir printed
    bin_dir=$("$uv" tool dir --bin)
    tool_dir=$("$uv" tool dir)
    printed=$("$bin_dir/nare" --version)
    if [ "$printed" != "$version" ]; then
        echo "installed nare reports $printed, release is $version" >&2
        exit 1
    fi
    if [ "${1:-}" = headless ]; then
        if "$tool_dir/nare/bin/python" -c 'import textual' 2>/dev/null; then
            echo "--no-tui installed textual" >&2
            exit 1
        fi
        echo "ok: nare $printed without the TUI, in $bin_dir"
        return
    fi
    "$tool_dir/nare/bin/python" -c 'import textual, nare.tui.app'
    "$bin_dir/nare" tui --help >/dev/null
    echo "ok: nare $printed with the TUI, in $bin_dir"
}

# Runs the installer, shows its output, and keeps it for the checks below.
run_installer() {
    local out=$HOME/install.log
    "$@" 2>&1 | tee "$out"
}

if [ "$without_uv" = 0 ] && [ -e "$(uv tool dir)/nare" ]; then
    echo "not a clean install: nare is already in $(uv tool dir)" >&2
    exit 1
fi

echo "== clean install, piped from curl"
run_installer sh -c "curl -fsSL 'file://$dist/install.sh' | sh"
if [ "$without_uv" = 1 ]; then
    grep -q 'uv is not installed' "$HOME/install.log" \
        || { echo "the installer did not say it was installing uv" >&2; exit 1; }
    [ -x "$uv" ] || { echo "the installer did not put uv at $uv" >&2; exit 1; }
    "$uv" --version
    # The exact line it printed puts nare on PATH in this shell.
    line=$(sed -n 's/^  \(export PATH=.*\)$/\1/p' "$HOME/install.log")
    [ -n "$line" ] || { echo "the installer printed no export PATH line" >&2; exit 1; }
    (eval "$line" && nare --version)
fi
check

echo "== repeat install"
run_installer sh "$dist/install.sh"
if grep -q 'uv is not installed' "$HOME/install.log"; then
    echo "the repeat run installed uv again" >&2
    exit 1
fi
check

# uv reads the installed wheel again on upgrade, so the installer must have
# kept it somewhere that outlives the run.
echo "== uv tool upgrade"
"$uv" tool upgrade nare
check

echo "== headless, piped from curl with --no-tui"
run_installer sh -c "curl -fsSL 'file://$dist/install.sh' | sh -s -- --no-tui"
check headless
