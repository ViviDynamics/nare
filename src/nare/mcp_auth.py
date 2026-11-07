"""OAuth for remote MCP servers, headless.

A remote server that needs OAuth is authorized once, by an explicit
`nare mcp login ALIAS`, run by a person on a machine with a browser (or with
the loopback port forwarded to one). The authorization code flow with PKCE,
the discovery of the authorization server, dynamic client registration and
token refresh all come from the MCP SDK's client provider. `nare run` never
opens a browser: a server with no stored authorization, or whose refresh
fails, stops the run before any model call and names the login command.

Where credentials may go is decided here, not by the server. Every request
the SDK's authorization flow sends passes `_check_request`: it must be https
(or loopback), a POST (which carries the client secret, the code and the
tokens) must go to the authorization server's origin, and a GET may go to the
MCP server's origin or that authorization server's. The authorization server
is the MCP server's own origin, unless the configuration pins another one with
`authorization_server`. Nothing else is trusted to name a destination.

Tokens, the discovered authorization server metadata and any dynamic
registration live in $NARE_HOME/mcp-auth/ALIAS.json, written 0600 in a 0700
folder. Nothing here goes into the session file or the event stream. Errors
that reach a caller say what kind of failure happened, never a reply from a
server, because the SDK's OAuth errors quote the authorization server.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import secrets
import stat
import tempfile
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from nare.events import redact

if TYPE_CHECKING:
    import httpx2
    from mcp.shared.auth import (
        AuthorizationCodeResult,
        OAuthClientInformationFull,
        OAuthMetadata,
        OAuthToken,
    )

    from nare.mcp import Server

DEFAULT_LOGIN_TIMEOUT = 300.0
# A stored token is treated as expired this long before its lifetime ends, so a
# headless run refreshes it rather than meeting the server's 401 at the boundary,
# where the SDK would ask for a browser.
_EXPIRY_SKEW = 30.0
_PLACEHOLDER_REDIRECT = "http://127.0.0.1/callback"
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_KEYS = frozenset(
    {"client_id", "client_secret_env", "scope", "callback_port", "authorization_server"}
)
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_LOGGERS = (
    "mcp.client.auth.oauth2",
    "mcp.client.auth.utils",
    "mcp.client.streamable_http",
    "mcp.client.session",
    "mcp.shared._httpx_utils",
)


class McpAuthError(Exception):
    """An authorization failure. Its text is fixed, so it can be printed,
    recorded in a session and written to a log as it is."""


def login_hint(alias: str) -> str:
    return f"run `nare mcp login {alias}` with the same --mcp-config"


@dataclass(frozen=True)
class OAuthSettings:
    """The `auth` object of a remote server. Absent: no OAuth, as before.

    `client_id` names a pre-registered client, and `client_secret_env` names
    the environment variable that holds its secret, so a secret never sits in
    the configuration file or on a command line. Without `client_id` the
    client is registered dynamically at login, when the server supports it.
    `authorization_server` pins the authorization server when it is not on the
    MCP server's origin; without it, the server's own origin is used.
    """

    client_id: str | None = None
    client_secret_env: str | None = None
    scope: str | None = None
    callback_port: int | None = None
    authorization_server: str | None = None

    def __post_init__(self) -> None:
        if self.client_id is not None and (
            not isinstance(self.client_id, str) or not self.client_id
        ):
            raise ValueError("MCP auth client_id must be a nonempty string")
        if self.client_secret_env is not None:
            if self.client_id is None:
                raise ValueError("MCP auth client_secret_env needs a client_id")
            if not isinstance(self.client_secret_env, str) or not _ENV_NAME.fullmatch(
                self.client_secret_env
            ):
                raise ValueError("MCP auth client_secret_env must name a variable")
        if self.scope is not None and not isinstance(self.scope, str):
            raise ValueError("MCP auth scope must be a string")
        port = self.callback_port
        if port is not None and (
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 1 <= port <= 65535
        ):
            raise ValueError("MCP auth callback_port must be 1-65535")
        auth_server = self.authorization_server
        if auth_server is not None:
            parts = urlsplit(auth_server) if isinstance(auth_server, str) else None
            if parts is None or not parts.hostname or parts.username or parts.password:
                raise ValueError(
                    "MCP auth authorization_server must be a URL without credentials"
                )
            if not secure_url(auth_server):
                raise ValueError(
                    "MCP auth authorization_server needs an https URL "
                    "(or a loopback host)"
                )


def parse_oauth(value: Any) -> OAuthSettings:
    if not isinstance(value, dict) or set(value) - _KEYS:
        raise ValueError(
            "MCP auth must be an object with client_id, client_secret_env, "
            "scope, callback_port or authorization_server"
        )
    return OAuthSettings(**value)


def secure_url(url: str) -> bool:
    """Credentials travel only over https, or to a loopback host for local
    testing. A plain-http URL elsewhere would carry the client secret and the
    tokens in the clear."""
    parts = urlsplit(url)
    return parts.scheme == "https" or (
        parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS
    )


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    port = parts.port or {"https": 443, "http": 80}.get(parts.scheme)
    return (parts.scheme, (parts.hostname or "").lower(), port)


def _check_request(
    alias: str,
    request: httpx2.Request,
    mcp_origin: tuple[str, str, int | None],
    as_origin: tuple[str, str, int | None],
) -> None:
    """The one rule for where authorization traffic may go (see the module
    docstring). It runs on every request the SDK's flow sends, so it covers
    discovery, registration, the token exchange and refresh alike."""
    url = str(request.url)
    if not secure_url(url):
        raise McpAuthError(
            f"MCP server {alias}: authorization requests must use https "
            "(or a loopback host)"
        )
    target = _origin(url)
    if request.method == "POST":
        allowed = target == as_origin
    else:
        allowed = target in (mcp_origin, as_origin)
    if not allowed:
        raise McpAuthError(
            f"MCP server {alias}: authorization sent a request outside its "
            "authorization "
            "server; set authorization_server in its auth to allow it"
        )


def failure(alias: str, exc: BaseException) -> BaseException:
    """What a caller sees of an exception from an OAuth server.

    The SDK raises its errors through anyio task groups, so the groups are
    looked through first. An OAuth error quotes the authorization server's
    reply, and any other error may quote the MCP server's text, so neither
    text is passed on: the caller gets the kind of failure. The returned
    exception has no chained context. Callers raise it after their except
    block, so Python sets no context on it.
    """
    from mcp.client.auth.exceptions import (
        OAuthFlowError,
        OAuthRegistrationError,
        OAuthTokenError,
    )

    leaves = _leaves(exc)
    for leaf in leaves:
        if isinstance(leaf, McpAuthError):
            return McpAuthError(str(leaf))
    for leaf in leaves:
        if isinstance(leaf, (OAuthFlowError, OAuthRegistrationError, OAuthTokenError)):
            return McpAuthError(
                f"MCP server {alias}: authorization failed; {login_hint(alias)}"
            )
    first = leaves[0]
    if isinstance(first, TimeoutError):
        return McpAuthError(f"MCP server {alias}: timed out")
    return McpAuthError(f"MCP server {alias}: {type(first).__name__}")


def _leaves(exc: BaseException) -> list[BaseException]:
    """The exceptions inside a task group's ExceptionGroup, however nested."""
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for sub in exc.exceptions for leaf in _leaves(sub)]
    return [exc]


# -- token storage -----------------------------------------------------------


def _token_path(alias: str) -> Path:
    from nare.session import nare_home

    try:
        home = nare_home()
    except ValueError:
        raise McpAuthError(
            "NARE_HOME must be an absolute path for MCP tokens"
        ) from None
    if home is None:
        raise McpAuthError("no home directory for MCP tokens: set NARE_HOME")
    return home / "mcp-auth" / f"{alias}.json"


class TokenFile:
    """The SDK's TokenStorage, kept in one 0600 file per server alias.

    The file is bound to the server URL it was written for. A file for another
    URL is ignored and then replaced, so credentials never travel to a server
    they were not issued by. A fresh login does not read the file at all, so a
    file left loose by an earlier run does not block the login that repairs it.
    """

    def __init__(
        self,
        path: Path,
        server_url: str,
        settings: OAuthSettings,
        secret: str | None,
        redirect_uri: str,
        *,
        fresh: bool = False,
    ) -> None:
        self.path = path
        self.server_url = server_url
        self.settings = settings
        self.secret = secret
        self.redirect_uri = redirect_uri
        self.tokens: OAuthToken | None = None
        self.expires_at: float | None = None
        self.metadata: OAuthMetadata | None = None
        self._client: OAuthClientInformationFull | None = None
        if not fresh:
            self._load()

    def _load(self) -> None:
        from mcp.shared.auth import (
            OAuthClientInformationFull,
            OAuthMetadata,
            OAuthToken,
        )

        try:
            mode = os.stat(self.path).st_mode
        except FileNotFoundError:
            return
        if stat.S_IMODE(mode) & 0o077:
            raise McpAuthError(
                f"MCP token file {self.path.name} is readable by other users; "
                "run nare mcp login again to replace it"
            )
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("server") != self.server_url:
                return
            if data.get("tokens") is not None:
                self.tokens = OAuthToken.model_validate(data["tokens"])
            if data.get("metadata") is not None:
                self.metadata = OAuthMetadata.model_validate(data["metadata"])
            if data.get("client") is not None and self.settings.client_id is None:
                self._client = OAuthClientInformationFull.model_validate(data["client"])
            expires = data.get("expires_at")
            if isinstance(expires, (int, float)) and not isinstance(expires, bool):
                self.expires_at = float(expires)
        except (OSError, ValueError):
            raise McpAuthError(
                f"MCP token file {self.path.name} is unreadable; "
                "run nare mcp login again"
            ) from None

    def reset(self) -> None:
        """Forget the tokens, the discovered metadata and any dynamic
        registration: a fresh login registers again, because the registered
        redirect URI names a port that the new login may not use."""
        self.tokens = None
        self.expires_at = None
        self.metadata = None
        self._client = None
        self._save()

    def _configured(self) -> OAuthClientInformationFull | None:
        from mcp.shared.auth import OAuthClientInformationFull

        if self.settings.client_id is None:
            return None
        return OAuthClientInformationFull(
            client_id=self.settings.client_id,
            client_secret=self.secret,
            redirect_uris=[_redirect(self.redirect_uri)],
            token_endpoint_auth_method="client_secret_post" if self.secret else "none",
            grant_types=["authorization_code", "refresh_token"],
        )

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens
        self.expires_at = (
            time.time() + tokens.expires_in if tokens.expires_in is not None else None
        )
        self._save()

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self._configured() or self._client

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        if self.settings.client_id is None:
            self._client = client_info
            self._save()

    def set_metadata(self, metadata: OAuthMetadata) -> None:
        """Keep the authorization server's endpoints, so a refresh in a later
        process goes where this one's did, not to the MCP server's origin."""
        if self.metadata is not None and self.metadata.model_dump(
            mode="json"
        ) == metadata.model_dump(mode="json"):
            return
        self.metadata = metadata
        self._save()

    def _save(self) -> None:
        payload = {
            "version": 1,
            "server": self.server_url,
            "tokens": (
                self.tokens.model_dump(mode="json", exclude_none=True)
                if self.tokens is not None
                else None
            ),
            "expires_at": self.expires_at,
            "metadata": (
                self.metadata.model_dump(mode="json", exclude_none=True)
                if self.metadata is not None
                else None
            ),
            "client": (
                self._client.model_dump(mode="json", exclude_none=True)
                if self._client is not None
                else None
            ),
        }
        folder = self.path.parent
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(folder, 0o700)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".mcp-", suffix=".tmp")
        try:
            os.chmod(tmp, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise


def _redirect(uri: str) -> Any:
    from pydantic import AnyUrl

    return AnyUrl(uri)


# -- the flow ----------------------------------------------------------------


@dataclass(frozen=True)
class Authorizer:
    """How a login reaches the person: `present` shows the authorization URL,
    `receive` waits for the redirect. Only `nare mcp login` supplies one; a
    run without it never reaches a browser."""

    redirect_uri: str
    present: Callable[[str], Awaitable[None]]
    receive: Callable[[], Awaitable[AuthorizationCodeResult]]


def _install_log_scrub() -> None:
    for name in _LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, _Scrub) for f in logger.filters):
            logger.addFilter(_Scrub())


class _Scrub(logging.Filter):
    """The SDK logs failures with their exception, and an exception can quote a
    reply. Keep the message, redacted, and drop the traceback text."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def build_provider(
    alias: str,
    url: str,
    settings: OAuthSettings,
    authorizer: Authorizer | None,
) -> Any:
    """The httpx auth for one remote server. Without an authorizer the flow is
    not interactive: stored tokens may be refreshed, and anything else fails."""
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    _install_log_scrub()
    secret = None
    if settings.client_secret_env is not None:
        secret = os.environ.get(settings.client_secret_env)
        if not secret:
            raise McpAuthError(
                f"MCP server {alias}: environment variable "
                f"{settings.client_secret_env} is not set"
            )
    redirect = authorizer.redirect_uri if authorizer else _PLACEHOLDER_REDIRECT
    storage = TokenFile(
        _token_path(alias),
        url,
        settings,
        secret,
        redirect,
        fresh=authorizer is not None,
    )
    present_handler: Callable[[str], Awaitable[None]]
    receive_handler: Callable[[], Awaitable[AuthorizationCodeResult]]
    if authorizer is None:
        if storage.tokens is None:
            raise McpAuthError(
                f"MCP server {alias} needs authorization; {login_hint(alias)}"
            )

        async def present(_: str) -> None:
            raise McpAuthError(
                f"MCP server {alias} needs authorization again; {login_hint(alias)}"
            )

        async def receive() -> AuthorizationCodeResult:
            raise McpAuthError(
                f"MCP server {alias} needs authorization again; {login_hint(alias)}"
            )

        present_handler, receive_handler = present, receive
    else:
        storage.reset()
        present_handler, receive_handler = authorizer.present, authorizer.receive

    metadata = OAuthClientMetadata(
        redirect_uris=[_redirect(redirect)],
        token_endpoint_auth_method="client_secret_post" if secret else "none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=settings.scope,
        client_name="nare",
    )
    mcp_origin = _origin(url)
    pinned = settings.authorization_server
    as_origin = _origin(pinned) if pinned is not None else mcp_origin

    class _Provider(OAuthClientProvider):
        def _select_authorization_server(self, advertised: list[str]) -> str:
            if pinned is not None:
                return pinned
            chosen = super()._select_authorization_server(advertised)
            if _origin(chosen) != mcp_origin:
                raise McpAuthError(
                    f"MCP server {alias} names an authorization server on another "
                    "origin; "
                    "set authorization_server in its auth to allow it"
                )
            return chosen

        async def _auth_flow(
            self, request: httpx2.Request
        ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
            # Every request the SDK's flow makes passes through here before it
            # is sent. The original request is the MCP call itself, which the
            # transport already addresses to the server.
            flow = super()._auth_flow(request)
            try:
                outgoing = await flow.__anext__()
                while True:
                    if outgoing is not request:
                        _check_request(alias, outgoing, mcp_origin, as_origin)
                    response = yield outgoing
                    if self.context.oauth_metadata is not None:
                        storage.set_metadata(self.context.oauth_metadata)
                    outgoing = await flow.asend(response)
            except StopAsyncIteration:
                return
            finally:
                await flow.aclose()

        async def _initialize(self) -> None:
            await super()._initialize()
            # The SDK keeps no token expiry or endpoint metadata across a
            # restart. Without the expiry a stored token reads as valid and the
            # refresh is never tried before the server's 401 starts a full
            # authorization; without the metadata the refresh goes to the
            # MCP server's origin.
            if self.context.current_tokens is not None and storage.expires_at:
                self.context.token_expiry_time = storage.expires_at - _EXPIRY_SKEW
            if storage.metadata is not None and self.context.oauth_metadata is None:
                self.context.oauth_metadata = storage.metadata

    return _Provider(
        server_url=url,
        client_metadata=metadata,
        storage=storage,
        redirect_handler=present_handler,
        callback_handler=receive_handler,
    )


class _Loopback:
    """A one-shot HTTP listener on 127.0.0.1 for the authorization redirect."""

    def __init__(self, port: int | None, timeout: float) -> None:
        self._port = port or 0
        self._timeout = timeout
        self._server: asyncio.AbstractServer | None = None
        self._future: asyncio.Future[AuthorizationCodeResult] | None = None
        # The state of the authorization this listener is waiting for. It is
        # set when the authorization URL is presented; until then, and for
        # any other state, a delivery is refused and the wait goes on.
        self._state: str | None = None
        self.port = 0

    def expect(self, state: str | None) -> None:
        self._state = state

    async def __aenter__(self) -> _Loopback:
        self._future = asyncio.get_running_loop().create_future()
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", self._port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}/callback"

    async def receive(self) -> AuthorizationCodeResult:
        assert self._future is not None
        return await asyncio.wait_for(self._future, self._timeout)

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        status, body = "400 Bad Request", "nare: bad request"
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            method, target, _ = (
                head.split(b"\r\n", 1)[0].decode("latin-1").split(" ", 2)
            )
            parts = urlsplit(target)
            if method != "GET" or parts.path != "/callback":
                status, body = "404 Not Found", "nare: not found"
            else:
                status, body = self._deliver(parse_qs(parts.query))
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            ValueError,
            TimeoutError,
            OSError,
        ):
            pass
        finally:
            data = body.encode()
            with contextlib.suppress(OSError):
                writer.write(
                    f"HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\n"
                    f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode()
                    + data
                )
                await writer.drain()
            writer.close()

    def _deliver(self, query: dict[str, list[str]]) -> tuple[str, str]:
        from mcp.shared.auth import AuthorizationCodeResult

        assert self._future is not None
        if self._future.done():
            return "409 Conflict", "nare: this authorization already arrived"
        state = query.get("state", [""])[0]
        if self._state is None or not secrets.compare_digest(state, self._state):
            # Not this login's redirect (a stray or forged request): refuse it,
            # and keep waiting for the real one.
            return "400 Bad Request", "nare: this is not the expected authorization"
        if "error" in query:
            self._future.set_exception(
                McpAuthError("the authorization server refused access")
            )
            return "200 OK", "nare: access was refused. You can close this window."
        code = query.get("code", [""])[0]
        if not code:
            return "400 Bad Request", "nare: no authorization code"
        self._future.set_result(
            AuthorizationCodeResult(
                code=code,
                state=query.get("state", [None])[0],
                iss=query.get("iss", [None])[0],
            )
        )
        return "200 OK", "nare has the authorization. You can close this window."


async def login(
    alias: str,
    server: Server,
    *,
    timeout: float = DEFAULT_LOGIN_TIMEOUT,
    present: Callable[[str], Awaitable[None]],
) -> list[str]:
    """Run the interactive flow once, store the tokens, and return the tool
    names the server offers. `present` gets the authorization URL to show."""
    from nare.mcp import connect

    if server.auth is None or server.url is None:
        raise McpAuthError(f"MCP server {alias} does not use OAuth")
    if not (0 < timeout < math.inf):
        raise McpAuthError("MCP login timeout must be a positive finite number")
    async with _Loopback(server.auth.callback_port, timeout) as loopback:

        async def present_expecting(url: str) -> None:
            state = parse_qs(urlsplit(url).query).get("state", [None])[0]
            loopback.expect(state)
            await present(url)

        authorizer = Authorizer(
            redirect_uri=loopback.redirect_uri,
            present=present_expecting,
            receive=loopback.receive,
        )
        async with connect(
            {alias: server}, startup_timeout=timeout, authorizer=authorizer
        ) as tools:
            return sorted(tools)
