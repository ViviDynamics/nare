"""What nare costs before it does anything: no import a run did not ask for.

The `mcp` package is most of `import nare.cli`'s time, and a run without
`--mcp-config` never uses it, so it is imported only when servers are
configured. Checked in a fresh interpreter, because this process has long
since imported everything.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

LOADED_MCP = (
    "import sys; "
    "print(sorted(m for m in sys.modules if m == 'mcp' or m.startswith('mcp.')))"
)


def fresh(code: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip().splitlines()[-1]


def test_importing_the_cli_does_not_import_mcp() -> None:
    assert fresh(f"import nare.cli; {LOADED_MCP}") == "[]"


def test_importing_the_library_does_not_import_mcp() -> None:
    assert fresh(f"import nare, nare.loop, nare.tools; {LOADED_MCP}") == "[]"


def test_a_run_without_mcp_config_never_imports_mcp(tmp_path: Path) -> None:
    script = tmp_path / "script.json"
    script.write_text(json.dumps({"replies": [{"text": "ok"}]}), encoding="utf-8")
    run = (
        "from nare.cli import main; "
        "code = main(['run', '--yes', '--jsonl', '--provider', 'fake', "
        f"'--replay', {str(script)!r}, 'go']); "
        "assert code == 0, code; "
    )
    assert fresh(run + LOADED_MCP, cwd=tmp_path) == "[]"


def test_contract_does_not_import_mcp() -> None:
    code = (
        "import contextlib, io; from nare.cli import main; "
        "out = io.StringIO()\n"
        "with contextlib.redirect_stdout(out): main(['contract'])\n" + LOADED_MCP
    )
    assert fresh(code) == "[]"
