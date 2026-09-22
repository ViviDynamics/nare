# 7. Benchmarks are unshipped dev tooling, isolated by container

Status: Accepted (2026-09-21)

## Context

Every test in `tests/` runs against `FakeProvider`, so it measures whether the
harness honours its contract given a scripted model. Nothing measures whether
a real task gets done, or what it costs. The levers nare can actually improve
— tool descriptions, the system prompt, turn accounting, output truncation —
are invisible to a suite whose model replies are frozen.

Measuring them requires running a real model against a real task, which means
running model-generated shell commands unattended, in a loop, many times per
change. The README already states that nare has no sandbox of its own and that
containment is the caller's job.

## Decision

The benchmark lives in a top-level `benchmarks/` directory, outside
`src/nare/`, and is never installed. Each repetition runs in a throwaway
container with the case fixture as its working directory.

## Consequences

The shipped package keeps its current surface and its single dependency no
matter how large the benchmark grows; `packages = ["src/nare"]` already
enforces this, so no packaging change is required. Docker orchestration and a
judge client never reach a user of `nare run`, and no one files a bug about
`nare bench` on a machine we cannot see.

Docker becomes a prerequisite for benchmarking, though not for using or
developing nare — `bin/build` is unaffected. Containment covers the filesystem
and the process tree, not the network, because the container must reach the
model API.

The cost is that someone who installs nare cannot benchmark it without cloning
the repo. If the suite ever grades harnesses other than nare, that is the
trigger to promote it to its own package under a workspace; until then a
workspace is overhead for a need that has not arrived.
