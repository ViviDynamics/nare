# nare architecture

Written after the skeleton ran, describing what is there.

## The shape

The core is a library. Every surface is a thin adapter over it.

    cli.py            argparse, JSONL out, session file in and out
      |
      v
    loop.py           step() and run() — turn counting, status, events
      |          \
      |           v
      |         tools.py     five tools, their schemas, dispatch, approval,
      |                      and the policy that narrows both
      v
    transport/        everything vendor-shaped, turn() plus context_window()
                      anthropic.py and openai.py; which rail is configuration

`events.py` depends on nothing at all. `session.py` depends only on
`events.py`, because a session carries the events its steps produced. `session.py` also imports
the contract identity. Events drain to callers rather than persisting in sessions.
Everything else sits on those two.

## The layer boundary

The transport owns client construction, base_url and auth, the request and
response wire format, tool schema translation, sampling parameters, retry
policy, and `stop_reason` and `usage` normalization.

Everything else stays above it: the loop, turn counting, status, tool
execution, approval, events and redaction, and cost in dollars.

Tool schema translation is the easy one to misplace. `TOOL_SCHEMAS` are
Anthropic-shaped; a future OpenAI transport converts them at its own edge. If
that conversion lands anywhere but the transport, the layer leaks on the day
the second transport arrives.

## What nare knows nothing about

Git, GitHub, pull requests, branches, cards, roles, review cycles, lifecycle.
Conductor derives all of that from four statuses plus work it does itself. A
change that teaches nare any of those words is crossing the boundary.

## Determinism

`step()` is a function of a session and a transport. `FakeProvider` replays a
scripted list of replies, inherits nothing, and imports no vendor code, so
every path is an ordinary unit test with no mocking framework and no network.
The one live test is deselected by default.

## Known ceilings

Extension points are marked in the source with `ponytail:` comments.

- Tool calls dispatch sequentially in `loop.py`. `asyncio.gather` is the
  upgrade once a run is measurably slow because of it.
- `make_transport()` has one `match` arm. That arm is the extension point; a
  registry is slice 4.

## Measuring it

`tests/` proves the harness honours its contract given a scripted model.
`benchmarks/` asks the other question - whether real tasks get done, at what
quality, for how many tokens - by running the real thing in a container. It is
development tooling, excluded from the wheel, and it is where a change to a
tool description or the turn budget is shown to have helped. See
[benchmarks/README.md](../benchmarks/README.md) and ADR 0007.

## Context and accounting

`compact.py` elides only eligible old tool-result bodies, before each call. The
80%/60% thresholds use measured last input plus appended serialized characters
at four characters per token. Persisted message-count and character boundaries
allow the estimate to account for merged feedback and prefix elision across
resumes. A fresh/legacy session estimates the whole transcript. The estimate is
a heuristic; protected context can still cause a `context` stop, and provider
rejection still maps to `max_tokens`. Model-written summaries remain a future
extension after elision.

Transports report normalized token categories and raw reported cost. Backend
window discovery stays below the transport boundary. `accounting.py` prices
normalized usage above that boundary; unknown dollars propagate through totals.
`loop.run()` owns cumulative budget enforcement, including the exhausted resume
guard and done/blocked precedence. The CLI validates settings before vendor
construction, records effective limits and emits machine-readable usage.

The CLI subprocess tests replace only transport construction. Parsing, tools,
step/run, persistence, JSONL rendering and exit codes all execute normally. The
[issue 41 evidence](evidence/41/README.md) includes an offline 32000-window
benchmark probe. No claim about live model quality follows from scripted replies.
