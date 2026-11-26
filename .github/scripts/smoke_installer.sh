#!/usr/bin/env bash
# Installs a rendered release from DIST into a clean HOME, then again, and
# checks each time that the installed nare reports VERSION and that its TUI
# imports.
#
#   .github/scripts/smoke_installer.sh DIST VERSION
#
# DIST holds what the release attaches: the wheel and install.sh. The first
# run is the documented `curl ... | sh`; the second runs the file again, which
# is what a reader does to repair or refresh an install.
set -euo pipefail

dist=$(cd "${1:?usage: smoke_installer.sh DIST VERSION}" && pwd)
version=${2:?usage: smoke_installer.sh DIST VERSION}

HOME=$(mktemp -d)
export HOME
# A clean install: nothing from the runner's uv setup carries over, not its
# tool directories, its cache, its pinned Python or an active environment.
unset UV_TOOL_DIR UV_TOOL_BIN_DIR UV_CACHE_DIR UV_PYTHON UV_PYTHON_INSTALL_DIR \
    VIRTUAL_ENV XDG_DATA_HOME XDG_BIN_HOME XDG_CACHE_HOME XDG_CONFIG_HOME
export NARE_RELEASE_URL="file://$dist"

check() {
    local bin_dir tool_dir printed
    bin_dir=$(uv tool dir --bin)
    tool_dir=$(uv tool dir)
    printed=$("$bin_dir/nare" --version)
    if [ "$printed" != "$version" ]; then
        echo "installed nare reports $printed, release is $version" >&2
        exit 1
    fi
    "$tool_dir/nare/bin/python" -c 'import textual, nare.tui.app'
    "$bin_dir/nare" tui --help >/dev/null
    echo "ok: nare $printed with the TUI, in $bin_dir"
}

if [ -e "$(uv tool dir)/nare" ]; then
    echo "not a clean install: nare is already in $(uv tool dir)" >&2
    exit 1
fi

echo "== clean install, piped from curl"
curl -fsSL "file://$dist/install.sh" | sh
check

echo "== repeat install"
sh "$dist/install.sh"
check

# uv reads the installed wheel again on upgrade, so the installer must have
# kept it somewhere that outlives the run.
echo "== uv tool upgrade"
uv tool upgrade nare
check
