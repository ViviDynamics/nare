# Bedrock provider: call Claude on Amazon Bedrock with AWS credentials

Issue #74

## Scope
In: `--provider bedrock` on `AsyncAnthropicBedrock`, region resolution, AWS credential check, base-model cap lookup, no discovery, `--base-url` as the runtime endpoint, the `bedrock` extra in the package and the image, README.
Out: a Bedrock price table (cost stays `None`; `--budget-usd` already prices from `NARE_PRICE_*`), a context-window table (the SDK has none), the scrutare follow-up.

## Assumptions
- Open question 1: a `BedrockTransport` subclass in `transport/bedrock.py`. `AnthropicTransport` gains three hooks (`_check_credentials`, `_model_cap`, `_build_client`) and keeps its behavior.
- Open question 2: `--base-url` passes through as the runtime endpoint override, for VPC interface endpoints. Discovery is still skipped.
- Open question 3: `Reply.cost` stays `None`, as on any rail without a LiteLLM cost header.
- Region order: `--region`, `NARE_AWS_REGION`, `AWS_REGION`, `AWS_DEFAULT_REGION`. `--region` on another provider is refused rather than ignored, and `NARE_AWS_REGION` is read only by bedrock, so a global env var cannot break other rails.
- Credentials are resolved through botocore at construction, so a missing chain exits 2 before a session starts. `AWS_BEARER_TOKEN_BEDROCK` counts, since the SDK uses it when set.
- A Bedrock ID maps to its base model for the cap lookup by dropping the geo prefix, `anthropic.` and the `-vN:M` suffix; an ARN passes through to the global ceiling.

## Tasks
- [x] 1. Construction tests (client type, region order, refusals naming region and credentials, missing extra, endpoint override, temperature): `BedrockTransport` and hooks.
- [x] 2. Cap lookup tests for profile IDs: `bedrock_base_model` and `_model_cap`.
- [x] 3. Wire tests over a mock transport: SigV4-signed invoke with thinking and normalized usage; streaming decoded from a hand-framed AWS event stream; no discovery request.
- [x] 4. CLI tests: exit 2 naming both, exit 2 naming the extra, `--region` refused elsewhere, tui shares the flag.
- [x] 5. Packaging tests: the extra exists and the Dockerfile installs it. Verified in clean venvs that the base wheel refuses with exit 2 and the extra brings boto3.
- [x] 6. README Providers entry.
- [ ] 7. A live Bedrock turn: no AWS credentials on the build machine.
