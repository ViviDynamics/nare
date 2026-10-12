"""Claude on Amazon Bedrock: the Anthropic transport with AWS SigV4 auth.

The wire format, streaming, tool use, thinking and usage are Anthropic's, so
everything but credentials, the endpoint and discovery is inherited.
"""

from __future__ import annotations

import importlib
import os
import re

import httpx2
from anthropic import AsyncAnthropic, AsyncAnthropicBedrock

from nare.transport.anthropic import (
    MODEL_NONSTREAMING_TOKENS,
    NONSTREAMING_MAX_TOKENS,
    AnthropicTransport,
    Effort,
)

REGION_SOURCES = ("NARE_AWS_REGION", "AWS_REGION", "AWS_DEFAULT_REGION")

_BEDROCK_ID = re.compile(r"^(?:[a-z]+\.)?anthropic\.(?P<base>.+?)(?:-v\d+(?::\d+)?)?$")


def bedrock_base_model(model: str) -> str:
    """us.anthropic.claude-x-v1:0 -> claude-x, for the SDK's per-model caps,
    which are keyed by Anthropic names. Anything else passes through."""
    match = _BEDROCK_ID.match(model)
    return match.group("base") if match else model


def _aws_credentials_resolve() -> bool:
    try:
        session = importlib.import_module("botocore.session")
    except ImportError as exc:
        raise ValueError(
            "--provider bedrock needs the bedrock extra, which brings boto3: "
            "pip install 'nare[bedrock]'"
        ) from exc
    return session.get_session().get_credentials() is not None


class BedrockTransport(AnthropicTransport):
    def __init__(
        self,
        *,
        model: str,
        region: str | None = None,
        base_url: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        effort: Effort | None = None,
        system: str | None = None,
        http_client: httpx2.AsyncClient | None = None,
        streaming: bool = False,
        image_input: bool = False,
    ) -> None:
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
            system=system,
            http_client=http_client,
            streaming=streaming,
            image_input=image_input,
        )

    def _check_credentials(self, api_key: str | None) -> None:
        bearer = bool(os.environ.get("AWS_BEARER_TOKEN_BEDROCK"))
        credentials = bearer or _aws_credentials_resolve()
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
        for key in (model, bedrock_base_model(model)):
            if key in MODEL_NONSTREAMING_TOKENS:
                return MODEL_NONSTREAMING_TOKENS[key]
        return NONSTREAMING_MAX_TOKENS

    def _build_client(
        self,
        api_key: str | None,
        base_url: str | None,
        http_client: httpx2.AsyncClient | None,
    ) -> AsyncAnthropic | AsyncAnthropicBedrock:
        return AsyncAnthropicBedrock(
            aws_region=self.region, base_url=base_url, http_client=http_client
        )

    async def context_window(self) -> int | None:
        return None
