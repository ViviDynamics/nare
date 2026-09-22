# benchmarks

Measures whether a change to nare made the harness better: whether real tasks
get done, at what quality, for how many tokens.

This is development tooling. It is not installed with nare - the wheel
contains only `src/nare` - so running it means working from a checkout.

## Running it

Docker is required; every repetition runs in a throwaway container.

    export ANTHROPIC_API_KEY=...        # or your proxy's key
    export NARE_MODEL=claude-haiku
    export NARE_BASE_URL=https://...    # only if you use a proxy

    bin/bench verify                    # free: are the cases well-formed?
    bin/bench run --tier smoke          # measure
    bin/bench bless --tier smoke        # promote the numbers to a baseline

`run` compares against `baselines/<model>.<tier>.toml` and exits 1 on a
confirmed regression. A case whose pass rate drops is re-run at seven
repetitions before it is called one, because sampling cannot be pinned and
three repetitions are not conclusive on their own.

## Writing a case

A case is a directory: `case.toml` plus a `fixture/` tree that becomes the
agent's working directory.

**Never name a model, a provider, or a base URL in a case.** Those come from
the environment, which is what lets the same case run against a proxy alias
and a first-party model unchanged. `case.py` rejects them.

Checks are `bash` (must exit 0) or `result` (asserts on nare's result line).
The optional `[judge]` block lists binary, objectively checkable claims about
the diff; `min_met` turns it into a gate. A judge can only ever fail a
repetition that passed its checks - it can never rescue one that failed.

Run `bin/bench verify` on any case you add. It proves the checks fail on the
pristine fixture, which is the difference between a case that measures
something and a case that is green no matter what the agent does.

## Containment

The container bounds the filesystem and the process tree. It does not bound
the network - the agent's endpoint has to be reachable, so `--network none` is
not available. Treat a benchmark run like any other unattended execution of
model-generated shell commands.
