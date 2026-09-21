# Test commands

Each area's local command, mirroring `bin/build`, which CI runs. Run `bin/build` to do
all of them in the same order CI does.

| Area | Command |
| --- | --- |
| Formatting | `uv run ruff format --check src tests` |
| Lint | `uv run ruff check src tests` |
| Types | `uv run mypy src tests` |
| Tests | `uv run pytest -q` |
| One test | `uv run pytest -q tests/<file>.py::<test>` |
| Skills wiring | `.agents/skills/ci-safety/scripts/check-wiring` |
