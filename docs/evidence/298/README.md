# Issue 298: the anthropic rail's constrained correction turn after tool use

**Date:** 2026-11-12
**nare:** `2026.11.103.dev1+gd8fb7c2d1` (main at d8fb7c2)
**Endpoint:** the Anthropic Messages API (`/v1/messages`) through a LiteLLM proxy, with a budget-capped virtual key. The proxy host and the key are left out on purpose.
**Models:** Claude Haiku 5.5 (`claude-haiku-5-5`) and Claude Sonnet 5.5 (`claude-sonnet-5-5`).
**Scope:** whether a live endpoint accepts the schema correction turn that `--structured-output json_schema` sends after a tool-using run, when the transcript holds `tool_use` and `tool_result` blocks and the request defines no tools.

## Result

Accepted on both models. No fallback is needed.

## The shape, without nare

A request whose transcript holds a `tool_use` block and its `tool_result`, sent with no `tools` field and again with `"tools": []`:

| Model | No `tools` field | `"tools": []` |
| --- | --- | --- |
| Claude Haiku 5.5 | HTTP 200 | HTTP 200 |
| Claude Sonnet 5.5 | HTTP 200 | HTTP 200 |

Each reply answered from the tool result (`{"name": "ada"}`) when given room; at a 64-token cap two replies ended `max_tokens` inside their thinking, which is the cap, not a refusal.

## The turn, through nare

A first attempt asked the model to give a schema-invalid answer on purpose. Both models answered correctly instead, so no correction turn was sent, which proves nothing. The run below forces the correction with a caller check (`--check-command`, #296) that refuses the first answer once and passes after that. A check error is charged as a schema correction, so the next turn is the constrained one.

    nare run --yes --jsonl --provider anthropic --base-url PROXY --model MODEL \
      --tools read --root ROOT --schema person.json --structured-output json_schema \
      --check-command '["python3", "/abs/refuse_once.py", "/abs/marker"]' \
      --max-corrections 2 --max-turns 6 --session session.json \
      "Read NOTES.txt, then answer with the name of the person on call."

`person.json` requires `{"name": string}` and nothing else. `ROOT/NOTES.txt` says the person on call is ada. The check script lives outside the root.

For both models the session's messages were:

| # | Role | Blocks |
| --- | --- | --- |
| 0 | user | task, schema statement |
| 1 | assistant | `tool_use` (`read`, `NOTES.txt`) |
| 2 | user | `tool_result`, reminder |
| 3 | assistant | the first answer, `{"name": "ada"}` (Haiku also sent a `thinking` block) |
| 4 | user | the correction from the check |
| 5 | assistant | the constrained answer, `{"name": "ada"}` |

The events for turn 5:

    progress  schema correction 1 (charged): name: refused once on purpose to exercise the correction turn
    progress  schema-constrained answer turn (json_schema); tools withheld
              {"structured_output": {"mode": "json_schema", "tools_withheld": true}}

| Model | `status` | `stop_reason` | `output` | Turns | Cost (reported, not estimated) |
| --- | --- | --- | --- | --- | --- |
| Claude Haiku 5.5 | `done` | `end_turn` | `{"name": "ada"}` | 3 | $0.000203 |
| Claude Sonnet 5.5 | `done` | `end_turn` | `{"name": "ada"}` | 3 | $0.001008 |

So the request for turn 5 carried messages 0 to 4, with the `tool_use` and `tool_result` blocks, the `json_schema` output format and an empty tool list, and the API answered it.

## Limits

- The requests went through a LiteLLM proxy, not straight to the Anthropic API. The proxy forwards the Messages format to Anthropic; a refusal of this shape would have come back as an upstream 400, and none did. A direct run with an Anthropic key would remove the proxy from the evidence.
- The bedrock rail sends the same body through the SDK's Bedrock client. It was not checked here: the self-hosted Bedrock-compatible endpoint is not Anthropic's model, so it could not show whether Anthropic refuses the shape, and AWS Bedrock needs credentials not yet available. That check belongs with the other AWS checks in #301.
