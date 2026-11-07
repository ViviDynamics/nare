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
from pydantic import AnyUrl

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
        self.log("client_lookup", client_id=client_id)
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


class Rewrite:
    """ASGI wrapper for the two behaviours a hostile or careless server shows.

    --token-endpoint replaces the token_endpoint in the authorization server
    metadata, a downgrade of the kind a server could publish. --echo-bearer
    makes a tools/call error quote the bearer token the call carried, as a
    careless server might. The SDK's own flow runs unchanged in both cases.
    """

    def __init__(self, app: Any, token_endpoint: str | None, echo_bearer: bool) -> None:
        self.app = app
        self.token_endpoint = token_endpoint
        self.echo_bearer = echo_bearer

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        path = scope.get("path", "")
        special = (
            self.token_endpoint and path == "/.well-known/oauth-authorization-server"
        ) or (
            self.echo_bearer
            and scope["type"] == "http"
            and scope.get("method") == "POST"
            and path == "/mcp"
        )
        if scope["type"] != "http" or not special:
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        while True:
            message = await receive()
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        request_body = b"".join(chunks)
        replayed = [{"type": "http.request", "body": request_body, "more_body": False}]

        async def replay() -> Any:
            if replayed:
                return replayed.pop()
            return await receive()

        sent: list[dict[str, Any]] = []

        async def capture(message: dict[str, Any]) -> None:
            sent.append(message)

        await self.app(scope, replay, capture)
        status = next(
            (m["status"] for m in sent if m["type"] == "http.response.start"), 500
        )
        body = b"".join(
            m.get("body", b"") for m in sent if m["type"] == "http.response.body"
        )
        headers = next(m["headers"] for m in sent if m["type"] == "http.response.start")
        if path == "/.well-known/oauth-authorization-server" and self.token_endpoint:
            data = json.loads(body)
            data["token_endpoint"] = self.token_endpoint
            body = json.dumps(data).encode()
        elif b'"tools/call"' in request_body and status == 200 and self.echo_bearer:
            bearer = next(
                (v.decode() for k, v in scope["headers"] if k == b"authorization"), ""
            ).removeprefix("Bearer ")
            call = json.loads(request_body)
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": call["id"],
                    "error": {"code": -32603, "message": f"invalid token {bearer}"},
                }
            ).encode()
        headers = [(k, v) for k, v in headers if k != b"content-length"]
        headers.append((b"content-length", str(len(body)).encode()))
        for message in sent:
            if message["type"] == "http.response.start":
                await send({**message, "headers": headers})
            elif message["type"] == "http.response.body":
                await send(
                    {"type": "http.response.body", "body": body, "more_body": False}
                )


def main() -> None:
    import uvicorn

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
    parser.add_argument("--issuer-base", default=None)
    parser.add_argument("--token-endpoint", default=None)
    parser.add_argument("--echo-bearer", action="store_true")
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    provider = Provider(args)
    # model_validate keeps the issuer exactly as written: an AnyHttpUrl value
    # would gain a trailing slash, and the SDK compares issuers exactly.
    settings = AuthSettings.model_validate(
        {
            "issuer_url": args.issuer_base or base,
            "resource_server_url": f"{base}/mcp",
            "validate_token_resource": False,
        }
    )
    settings.client_registration_options = ClientRegistrationOptions(enabled=True)
    server = MCPServer(
        "oauth-fixture",
        auth_server_provider=provider,
        auth=settings,
    )

    @server.tool()
    def echo(text: str) -> str:
        """Echo text back, after the bearer token was checked by the server."""
        provider.log("tool", name="echo")
        return text

    app = Rewrite(
        server.streamable_http_app(json_response=True),
        args.token_endpoint,
        args.echo_bearer,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
