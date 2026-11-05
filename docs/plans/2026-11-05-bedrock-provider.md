# Bedrock provider: call Claude on Amazon Bedrock with AWS credentials

Issue #74

## Scope
In: `--provider bedrock` as a subclass of the Anthropic transport, built on the SDK's Bedrock client. Region and credential resolution through the AWS chain, the Bedrock model-cap lookup, no context-window discovery, `--base-url` as the runtime endpoint, `--region`, the `bedrock` extra, the container image, the bash credential withholding, contract and README entries.
Out: a Bedrock price table (cost stays `None`), structured output on Bedrock (refused until a live check confirms the field), and the scrutare follow-up.

## Assumptions
- The branch is rebuilt on current `main` rather than rebased. The transport moved from `AsyncAnthropic` to `httpx2`, retries and the `Transient` classification, and effort and thinking handling, so the old commits would conflict in every transport file. The Bedrock change is re-applied on top of the new transport with three hooks on `AnthropicTransport`: `_check_credentials`, `_model_cap` and `_build_client`. Their default behavior is unchanged.
- The wire body is the Anthropic Messages body. The SDK moves it to `/model/{id}/invoke` and `/invoke-with-response-stream` and signs it with SigV4. Streaming uses the AWS event stream, which the SDK decodes.
- Features whose Bedrock acceptance cannot be checked offline are sent as the Anthropic rail sends them, and the live check confirms them: `output_config.effort`, `tool_choice: none`, `thinking` with a budget, and prompt-cache counters. Structured output is refused, since Bedrock's field for it is not verified here.
- `--base-url` is the runtime endpoint override, for a VPC interface endpoint. Discovery is skipped. `--region` on any other provider is refused.
- Credentials come from the botocore chain, or `AWS_BEARER_TOKEN_BEDROCK`. botocore is required on both paths, because the SDK decodes event streams with it.
- Bedrock's AWS credential variables join `CREDENTIAL_ENV`, so bash does not see them. The AWS profile and shared credential files are not environment variables and are not removed.
- A Bedrock model or inference-profile ID maps to its Anthropic name for the cap lookup. The SDK keys its table as `anthropic.<name>-v1:0`, so both forms are tried. An ARN keeps its last segment.

## Tasks
- [x] 1. Hooks on `AnthropicTransport` with no behavior change: the existing tests pass with their expectations updated only for the new provider name, the withheld credential list and the contract provider lists.
- [x] 2. Construction and refusals: region order, missing region or credentials naming both, missing extra, bearer token, endpoint override, temperature, API key, `proxy_num_retries`, structured output. Test: `tests/test_bedrock_transport.py`.
- [x] 3. Cap lookup for profile IDs: `bedrock_base_model` and `_cap_keys`.
- [x] 4. Wire, signed with SigV4 against fake keys: model path, body, effort, thinking budget, `tool_choice: none`, cache breakpoints, cut-offs, repeated keys in tool arguments, retry classification and timeouts.
- [x] 5. Streaming over a hand-framed AWS event stream: deltas, tool arguments, usage, repeated keys, a dropped stream and a streamed cut-off.
- [x] 6. CLI: exit 2 naming region and credentials, exit 2 naming the extra, `--region` refused elsewhere, the thinking budget accepted, structured output refused. Bash withholding.
- [x] 7. Packaging: the extra, the dev group, the image install. Contract entries for the provider lists and the withheld list.
- [ ] 8. A live Bedrock turn for each feature in the matrix, with credentials. Required before merge.
