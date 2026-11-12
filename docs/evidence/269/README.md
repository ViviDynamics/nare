# Issue 269: the bedrock rail against a Bedrock-compatible endpoint

**Date:** 2026-11-12
**Endpoint:** a self-hosted Bedrock-compatible endpoint backed by an 8B open-weights model. It serves `InvokeModel` and `InvokeModelWithResponseStream` with SigV4, and `list-foundation-models`. Its one Claude model ID is not an Anthropic model, and it has no Haiku model.
**nare:** 2026.11.98, `--provider bedrock --region us-east-1 --base-url <endpoint>`, `env -u ANTHROPIC_API_KEY`.
**Scope:** the wire path, not AWS. No turn has been run against AWS Bedrock itself. The AWS checks are in [#301](https://github.com/ViviDynamics/nare/issues/301).

Model IDs, the endpoint host and credentials are left out on purpose. Each command below uses `<MODEL>` for the endpoint's Claude model ID.

## Classification

- **(a) confirmed:** nare's wire path works. The request was signed, sent in the right shape, and the endpoint's response was handled correctly.
- **(b) shim limit:** the result reflects the endpoint or its model, not nare or AWS.
- **(c) nare bug:** none found in this run.

## Matrix

| # | Item | Command (abbreviated) | Result | Class |
| --- | --- | --- | --- | --- |
| 0 | SigV4, negative control | valid run, then a deliberately wrong secret | the wrong secret gets `403 signature does not match`, so the signed successful runs were checked | (a) |
| 1 | Endpoint lists the model | `aws bedrock list-foundation-models` | one Claude model and one embedding model | (a) |
| 2 | Minimal call, default tools | `nare run --yes --jsonl ... "Reply with the word ok."` | signed request accepted, 1487 in / 93 out. The model answered through the `ask` tool, so `status: blocked` | (a) wire; the `ask` choice is the model |
| 3 | Non-streaming turn | `--effort low --tools none --max-tokens 400` | `status: done`, text `ok`, 19 in / 105 out | (a) |
| 4 | Streaming | `--stream --tools none` | `StreamDropped: message_stop missing` after 3 retries | (b) |
| 5 | Effort | `--effort low` | accepted, `status: done` | (a) |
| 6 | Thinking budget | `--thinking-budget 1024 --max-tokens 2048` | accepted, a `thinking` event arrived, `status: done` | (a) |
| 7 | Tool round trip | `--tools read --root <dir>` | `tool_use read`, the tool result returned, and the final answer contains the file's code word. The surrounding text is garbled | (a) |
| 8 | Prompt caching, two turns | two runs over one large `--system-file` (about 11.9k input tokens) | both `cache_read: 0`. nare sent the `cache_control` marker on the newest block (captured). A direct request with the same marker also returned zero counters | (b) |
| 9 | Turn-limit summary | `--max-turns 1` on a prompt that wants a tool | the summary request carried `tool_choice: {"type": "none"}` with the tool set kept (captured). The endpoint accepted it, with no 400. Outcome `error`/`max_turns` with the `continue` hint, as the contract says. The summary text was empty | (a) |
| 10 | Cut-off | `--max-tokens 32` | the endpoint returned `end_turn` with 32 output tokens. A direct request agreed | (b) |
| 11 | Structured output, first run | `--structured-output json_schema --tools none --max-tokens 300` | `schema_violation`: no JSON in the answer. The 300-token cap ran out during reasoning | (b) |
| 11 | Structured output, rerun | the same at `--max-tokens 2048` | `status: done`; the answer validated against the schema | (a) |
| 12 | Bash, credential withholding | `--tools bash --root <dir>`, with or without `--thinking-budget` | no bash call was made in any of five runs. A raw replay of nare's exact request (captured) also made none. The check did not run | (b) |

## Notes

- **Default toolset.** With all tools on, the endpoint's model answers through `ask`, so a plain prompt ends `blocked`. Checks that are not about tools use `--tools none`.
- **Reasoning on this endpoint.** The model reasons before it answers. Without `--thinking-budget`, the endpoint drops that reasoning, so an answer can come back empty when the output cap is small. The thinking variant shows the reasoning as `thinking` events. The bash case could not be run because the model's reasoning ended in no tool call.
- **Streaming.** The endpoint's stream is valid AWS event-stream framing, and its inner Anthropic events are complete, ending in `message_stop`. The defect is the payload envelope. AWS puts `{"bytes": <base64>}` in each event payload, and the endpoint wraps it as `{"chunk": {"bytes": ...}}`. The Anthropic SDK reads `chunk.bytes` after botocore parses the event, so it received no events. nare's streaming path was not reached. The fix belongs in the endpoint.
- **Fixed thinking budget.** `docs/contract.md` says current Claude models refuse a fixed budget with a 400. This endpoint accepted it. That is the endpoint's behavior, so the refusal is a question for AWS (#301).
- **Timeouts.** No call took longer than about 56 seconds, under botocore's 60-second default. nare's client timeout (600 seconds by default) was not changed.
- **nare bugs.** None found. Every failure above traces to the endpoint or its model, so no nare change was needed.

## Reproduce

    env -u ANTHROPIC_API_KEY nare run --yes --jsonl --provider bedrock --region us-east-1 \
      --base-url <ENDPOINT> --model <MODEL> --tools none --max-tokens 400 "Reply with the word ok."
