# Test commands

Run by `preflight` before every push. A row runs only when the branch's diff touches
its paths; rows run top to bottom, cheapest first, and stop at the first failure.
`{files}` is the changed files the row matched. These mirror `bin/build`, which CI
runs; `bin/build` does all of them in CI's order.

| Area | Paths | Command |
| --- | --- | --- |
| Formatting, changed files | *.py | uv run ruff format --check {files} |
| Lint, changed files | *.py | uv run ruff check {files} |
| Whole-tree lint and format | pyproject.toml, uv.lock | uv run ruff format --check src tests benchmarks && uv run ruff check src tests benchmarks |
| Types | src/*.py, tests/*.py, benchmarks/*.py, pyproject.toml | uv run mypy src tests benchmarks |
| Changed tests | tests/*.py | uv run pytest -q {files} |
| Benchmark tests | benchmarks/* | uv run pytest -q benchmarks/tests |
| Tests | src/*, tests/*, pyproject.toml, uv.lock | uv run pytest -q |
| Skills wiring | .agents/*, repo.env.example | .agents/skills/ci-safety/scripts/check-wiring |

To run one test: `uv run pytest -q tests/<file>.py::<test>`.
