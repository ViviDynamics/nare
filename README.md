# nare

An internal agent harness for optimizing Coordinare across the environments it targets.

## Install

    uv tool install nare        # or: pip install nare

Set `ANTHROPIC_API_KEY` in the environment. There is no `--api-key` flag —
argv is world-readable through `ps` and `/proc`.

## Use

    nare run --yes --jsonl "add a docstring to foo() in bar.py"

Stdout is typed JSONL, terminated by one `result` line carrying status,
questions, usage, and stop_reason. Stderr is logging. `--session PATH` writes
the session, and `--resume PATH` continues it.

`--yes` approves every tool call, including `bash`. nare runs
model-generated shell commands with your privileges and no sandbox of its
own, so treat it like piping a script you have not read: run it in a
container, a VM, or a throwaway checkout. Containment is the caller's job —
slice 1 ships the approval seam, not a policy engine.

See [docs/architecture.md](docs/architecture.md) for the shape, and
[docs/adr/](docs/adr/) for the decisions behind it.

## Licensing

nare is source-available under the
[Elastic License 2.0](LICENSE). You may run, modify, and self-host it,
including commercially. You may not offer it to third parties as a hosted or
managed service.

## Contributing

Issues yes, pull requests no. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
policy and the reasoning behind it, and [SECURITY.md](SECURITY.md) for how to
report a vulnerability.
