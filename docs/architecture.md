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
    transport/        everything vendor-shaped, behind one method
                      anthropic.py and openai.py; which rail is configuration

`events.py` depends on nothing at all. `session.py` depends only on
`events.py`, because a session carries the events its steps produced.
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

Both are marked in the source with `ponytail:` comments.

- Tool calls dispatch sequentially in `loop.py`. `asyncio.gather` is the
  upgrade once a run is measurably slow because of it.
- `make_transport()` has one `match` arm. That arm is the extension point; a
  registry is slice 4.
