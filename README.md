# nare

An internal agent harness for optimizing Coordinare across the environments it targets.

## Install

Every release publishes a wheel and a container image. nare is not on PyPI, so
`pip install nare` does not work and this file will not pretend it does.

Pin a version. `latest` is fine for a look around and wrong for anything that
records what produced a result:

    # wheel, from the release (any version from the Releases page that has
    # artifacts attached: publishing starts with the first release after #10)
    V=2026.9.6
    uv tool install "https://github.com/ViviDynamics/nare/releases/download/$V/nare-$V-py3-none-any.whl"

    # container, same argv as the CLI
    docker run --rm -e ANTHROPIC_API_KEY -v "$PWD:/work" \
      ghcr.io/vividynamics/nare:$V run --yes --jsonl "..."

    # from source, at a tag
    pip install "git+https://github.com/ViviDynamics/nare@$V"

`nare --version` prints the installed release, the `result` line carries it as
`nare`, and so does the session file. A caller records the version that
produced a result rather than whatever main was that day.

Set `ANTHROPIC_API_KEY` in the environment. There is no `--api-key` flag:
argv is world-readable through `ps` and `/proc`.

## Use

    nare run --yes --jsonl "add a docstring to foo() in bar.py"

Stdout is typed JSONL, terminated by one `result` line carrying status,
questions, usage, and stop_reason. Stderr is logging. `--session PATH` writes
the session, and `--resume PATH` continues it.

`--yes` approves every tool call, including `bash`. nare runs
model-generated shell commands with your privileges and no sandbox of its
own, so treat it like piping a script you have not read: run it in a
container, a VM, or a throwaway checkout.

Two flags narrow a run. `--tools read,bash` allows only the tools you name,
and `--tools none` allows no tool at all, which is what a caller wants when
it needs one answer and no side effects. `--root DIR` confines `read`,
`write` and `edit` to a directory, refusing any path that leaves it,
including through a symlink. Both are recorded in the session, so a run's
permissions can be read afterwards rather than inferred.

`--root` gives `bash` that directory to start in. It is not a jail: a shell
can still walk upward, so real confinement stays the sandbox's job.

`--schema FILE` makes the answer data rather than prose. The final answer must
satisfy the JSON Schema in that file; a violation is reported back to the model
once, and a second violation ends the run with `stop_reason=schema_violation`
and no partial object. A validating answer is parsed onto the session, carried
in the `output` event's detail, and included in the final `result` line.

nare validates a subset of JSON Schema: `type`, `properties`, `required`,
`items`, `enum`, and `additionalProperties`. A schema using anything else is
refused at startup rather than validated in part, because an answer that was
only partly checked is worse than one that was not checked at all.

`nare contract` prints the machine contract this build speaks: the version,
the exit codes, the statuses and the event types. A caller pins what it
understands with `nare run --contract N`, and nare refuses to start when the
numbers differ rather than emitting a stream the caller would mis-read.

See [docs/contract.md](docs/contract.md) for what a caller may rely on and what
changes the version, [docs/architecture.md](docs/architecture.md) for the
shape, and [docs/adr/](docs/adr/) for the decisions behind it.

## Working on nare

`bin/build` runs everything CI runs: formatting, lint, strict types, tests.

Agent workflow skills for this repo live in [`.agents/skills/`](.agents/skills/)
(`.claude/skills` and `.opencode/skill` point at the same copies): `conventions`,
`ci-safety`, `watch-ci`, and `merge-pr`. They were adapted from the org's internal
skill set at tag `2026.09.8` and are maintained here, tuned to nare. To use them in a
fresh clone:

    cp repo.env.example repo.env
    .agents/skills/ci-safety/scripts/check-wiring

The scripts need `gh`, `jq`, and `git`.

## Licensing

nare is source-available under the
[Elastic License 2.0](LICENSE). You may run, modify, and self-host it,
including commercially. You may not offer it to third parties as a hosted or
managed service.

## Contributing

Issues yes, pull requests no. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
policy and the reasoning behind it, and [SECURITY.md](SECURITY.md) for how to
report a vulnerability.
