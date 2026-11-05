"""Claude on Amazon Bedrock: the Anthropic transport, signed with AWS credentials.

The request body, streaming, tool use, thinking, usage, stop reasons, retries
and the duplicate-key check are the Anthropic transport's, unchanged. What
differs is the client (the SDK's Bedrock client, which signs with SigV4 or a
bearer token), the region, the per-model output caps keyed by Bedrock model
IDs, and context-window discovery, which Bedrock does not offer.

Structured output is refused here: the schema field Bedrock accepts has not
been checked against a live endpoint, so it is not claimed.
"""

from __future__ import annotations

import importlib
import os
import re
from typing import Any

import httpx2
from anthropic import DEFAULT_TIMEOUT, AsyncAnthropic, AsyncAnthropicBedrock

from nare.accounting import Prices
from nare.transport import Effort
from nare.transport.anthropic import (
    MODEL_NONSTREAMING_TOKENS,
    NONSTREAMING_MAX_TOKENS,
    AnthropicTransport,
)

# The order the SDK and the AWS CLI read a region in, after --region.
REGION_SOURCES = ("NARE_AWS_REGION", "AWS_REGION", "AWS_DEFAULT_REGION")

# us.anthropic.claude-sonnet-4-5-20250929-v1:0 -> claude-sonnet-4-5-20250929.
# The optional prefix is a cross-region inference profile (us., eu., global.).
_BEDROCK_ID = re.compile(r"^(?:[a-z]+\.)?anthropic\.(?P<base>.+?)(?:-v\d+(?::\d+)?)?$")


def bedrock_base_model(model: str) -> str:
    """The Anthropic name for a Bedrock model or inference-profile ID, for the
    SDK's per-model output caps, which are keyed by Anthropic names. A full
    ARN keeps its last segment. Anything unrecognized passes through."""
    tail = model.rsplit("/", 1)[-1]
    match = _BEDROCK_ID.match(tail)
    return match.group("base") if match else model


def _cap_keys(model: str) -> tuple[str, ...]:
    """The names the SDK's cap table may hold this model under. It keys Bedrock
    IDs as anthropic.<name>-v1:0, with no region prefix, and the Anthropic
    names as they are, so a profile ID is tried in each of those forms."""
    tail = model.rsplit("/", 1)[-1]
    bare = re.sub(r"^[a-z]+\.(?=anthropic\.)", "", tail)
    return (model, tail, bare, bedrock_base_model(model))


def _botocore_session() -> Any:
    """botocore is required even with a bearer token: the SDK decodes Bedrock's
    event-stream responses with it, and signs with it when there is no token."""
    try:
        return importlib.import_module("botocore.session")
    except ImportError as exc:
        raise ValueError(
            "--provider bedrock needs the bedrock extra, which brings boto3: "
            "pip install 'nare[bedrock]'"
        ) from exc


def _credentials_resolve() -> bool:
    session = _botocore_session()
    if os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        return True
    return session.get_session().get_credentials() is not None


class BedrockTransport(AnthropicTransport):
    # A constrained answer turn is refused rather than sent unverified; see the
    # module docstring. The loop reads this flag and refuses before any request.
    supports_structured_output = False

    def __init__(
        self,
        *,
        model: str,
        region: str | None = None,
        base_url: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        effort: Effort | None = None,
        thinking_budget: int | None = None,
        system: str | None = None,
        http_client: httpx2.AsyncClient | None = None,
        streaming: bool = False,
        image_input: bool = False,
        request_timeout: float | None = None,
    ) -> None:
        # Set before the base class runs: its credential check, output cap and
        # client construction all read the region.
        self.region = region or next(
            (os.environ[name] for name in REGION_SOURCES if os.environ.get(name)),
            None,
        )
        super().__init__(
            model=model,
            base_url=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
            effort=effort,
            thinking_budget=thinking_budget,
            system=system,
            http_client=http_client,
            streaming=streaming,
            image_input=image_input,
            request_timeout=request_timeout,
        )

    def _check_credentials(self, api_key: str | None) -> None:
        if api_key is not None:
            raise ValueError(
                "bedrock takes AWS credentials, not an API key. For a bearer token "
                "set AWS_BEARER_TOKEN_BEDROCK in the environment."
            )
        credentials = _credentials_resolve()
        if self.region and credentials:
            return
        raise ValueError(
            "bedrock needs an AWS region and credentials. "
            f"Region: {self.region or 'not set'} (--region, "
            + ", ".join(REGION_SOURCES)
            + "). Credentials: "
            + ("found" if credentials else "not found")
            + " (the AWS chain: AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, "
            "AWS_PROFILE, IRSA web identity, or an instance role)"
        )

    def _model_cap(self, model: str) -> int:
        for key in _cap_keys(model):
            if key in MODEL_NONSTREAMING_TOKENS:
                return MODEL_NONSTREAMING_TOKENS[key]
        return NONSTREAMING_MAX_TOKENS

    def _build_client(
        self,
        *,
        api_key: str | None,
        base_url: str | None,
        http_client: httpx2.AsyncClient | None,
        timeout: float | None,
    ) -> AsyncAnthropic | AsyncAnthropicBedrock:
        # The SDK reads AWS_BEARER_TOKEN_BEDROCK itself when no key is passed,
        # and otherwise signs with the botocore chain. max_retries=0 as on the
        # Anthropic rail: nare's own retry counts every resend.
        return AsyncAnthropicBedrock(
            aws_region=self.region,
            base_url=base_url,
            http_client=http_client,
            max_retries=0,
            timeout=timeout if timeout is not None else DEFAULT_TIMEOUT,
        )

    async def context_window(self) -> int | None:
        # Bedrock has no model-info endpoint. --context-window is the way to say it.
        return None

    async def prices(self) -> Prices | None:
        return None
