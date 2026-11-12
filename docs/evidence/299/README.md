# Issue 299: where a LiteLLM proxy reports the cost of a streamed turn

**Date:** 2026-11-12
**Endpoint:** a LiteLLM proxy (version 1.108.1), OpenAI-compatible `/v1/chat/completions`, with a budget-capped virtual key.
**Scope:** what the proxy offers at the end of a stream, to decide where nare reads a streamed turn's backend-reported cost.

The proxy host, the key and the proxy's model names are left out on purpose.

## Probe

A streamed request with `stream_options: {"include_usage": true}` and a small output cap, dumped raw with response headers (`curl -N --raw -i`, `TE: trailers`). Run twice, on two provider routes behind the proxy (an OpenAI model and an Anthropic model).

| Source | What arrived | Final? |
| --- | --- | --- |
| Response headers | `x-litellm-response-cost-original: 0.0` and the other `x-litellm-response-cost-*` headers all `0.0`; `x-litellm-response-cost` itself absent | No. They are sent before the first token, so they cannot know the charge |
| Final usage chunk (before `[DONE]`) | `usage.cost`: `7.6e-6` on the OpenAI route, `8.2e-6` on the Anthropic route, next to the token counts | Yes |
| HTTP trailers | none | n/a |

The OpenAI route's `7.6e-6` matches the token counts at that model's list prices (8 prompt and 18 completion tokens), so the field is dollars.

LiteLLM also keeps spend logs that can be looked up after the request, which would be one more request and may need a broader key. It is not needed: the in-stream source is final when the stream ends and costs no extra request.

## Decision

A streamed openai-rail turn reads `usage.cost` from the final usage chunk, reported as not estimated, the same as a non-streamed header cost. As with the headers, only a response carrying `x-litellm-*` headers is read, since another gateway's `usage.cost` may count in other units. With no such field, nothing changes: configured or discovered prices estimate the cost, and without prices it is unknown.
