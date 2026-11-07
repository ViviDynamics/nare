"""Offline OAuth authorization server and MCP server, for subprocess tests.

It is the SDK's own server-side OAuth (protected resource metadata, the
authorization server metadata, dynamic registration, PKCE authorization code,
refresh) with an in-memory provider. Consent is automatic: `authorize` returns
the redirect at once, which stands in for a person at a browser. Every grant
and tool call is appended to --log as one JSON object, so tests can assert
what happened on the wire without parsing output.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import time
from typing import Any

from mcp.server import MCPServer
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl

REPLY_SENTINEL = "SENTINEL-REPLY-7731"


class Provider:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.access: dict[str, AccessToken] = {}
        self.refresh: dict[str, RefreshToken] = {}
        if args.preregistered:
            self.clients[args.preregistered] = OAuthClientInformationFull(
                client_id=args.preregistered,
                client_secret=os.environ.get("FAKE_SERVER_CLIENT_SECRET"),
                redirect_uris=[AnyUrl(args.redirect)],
                token_endpoint_auth_method="client_secret_post",
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
            )

    def log(self, event: str, **fields: Any) -> None:
        with open(self.args.log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": event, **fields}) + "\n")

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.clients[client_info.client_id] = client_info
        self.log(
            "register", redirect_uris=[str(u) for u in client_info.redirect_uris or []]
        )

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        code = secrets.token_urlsafe(16)
        self.codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + 60,
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        self.log("authorize", client_id=client.client_id)
        return construct_redirect_uri(
            str(params.redirect_uri), code=code, state=params.state
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        found = self.codes.get(authorization_code)
        return found if found and found.client_id == client.client_id else None

    def _issue(
        self, client_id: str, scopes: list[str], refresh: str | None
    ) -> OAuthToken:
        token = secrets.token_urlsafe(24)
        expires = int(time.time()) + self.args.access_ttl
        self.access[token] = AccessToken(
            token=token, client_id=client_id, scopes=scopes, expires_at=expires
        )
        refresh_token = refresh or secrets.token_urlsafe(24)
        self.refresh[refresh_token] = RefreshToken(
            token=refresh_token, client_id=client_id, scopes=scopes
        )
        return OAuthToken(
            access_token=token,
            expires_in=self.args.access_ttl,
            refresh_token=refresh_token,
            scope=" ".join(scopes) or None,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self.codes.pop(authorization_code.code, None)
        if self.args.code_mode == "reject":
            self.log("code_rejected")
            raise TokenError(
                "invalid_grant",
                f"{REPLY_SENTINEL} code {authorization_code.code} rejected",
            )
        self.log("grant", grant="authorization_code", client_id=client.client_id)
        return self._issue(client.client_id, authorization_code.scopes, None)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        found = self.refresh.get(refresh_token)
        return found if found and found.client_id == client.client_id else None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if self.args.refresh_mode == "reject":
            self.log("refresh_rejected")
            raise TokenError("invalid_grant", "refresh rejected by the fake server")
        if self.args.refresh_mode == "echo":
            # A reply that quotes the credential back, as a careless server might.
            self.log("refresh_rejected")
            raise TokenError(
                "invalid_grant",
                f"{REPLY_SENTINEL} refresh token {refresh_token.token} is expired",
            )
        self.log("grant", grant="refresh_token", client_id=client.client_id)
        return self._issue(client.client_id, refresh_token.scopes, refresh_token.token)

    async def load_access_token(self, token: str) -> AccessToken | None:
        found = self.access.get(token)
        if found is None or (
            found.expires_at is not None and found.expires_at < time.time()
        ):
            return None
        return found

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        return None

    async def exchange_identity_assertion(self, client: Any, params: Any) -> OAuthToken:
        raise TokenError("unsupported_grant_type", "identity assertion is not offered")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("port", type=int)
    parser.add_argument("--log", required=True)
    parser.add_argument("--access-ttl", type=int, default=3600)
    parser.add_argument(
        "--refresh-mode", choices=("ok", "reject", "echo"), default="ok"
    )
    parser.add_argument("--code-mode", choices=("ok", "reject"), default="ok")
    parser.add_argument("--preregistered", default=None)
    parser.add_argument("--redirect", default=None)
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    issuer = AnyHttpUrl(base)
    provider = Provider(args)
    server = MCPServer(
        "oauth-fixture",
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=issuer,
            resource_server_url=AnyHttpUrl(f"{base}/mcp"),
            client_registration_options=ClientRegistrationOptions(enabled=True),
            validate_token_resource=False,
        ),
    )

    @server.tool()
    def echo(text: str) -> str:
        """Echo text back, after the bearer token was checked by the server."""
        provider.log("tool", name="echo")
        return text

    server.run(transport="streamable-http", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
