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
folder. A refresh, and a logout, hold a lock beside that file so concurrent
runs take turns; `nare mcp logout` revokes the token where the server allows.
Nothing here goes into the session file or the event stream. Errors
that reach a caller say what kind of failure happened, never a reply from a
server, because the SDK's OAuth errors quote the authorization server.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
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
# How long a process waits for another nare process that holds a server's token
# lock (a refresh or a logout). A token exchange is bounded by its own timeout,
# so this only fails when something is badly wrong.
_LOCK_WAIT = 60.0
# A .mcp-*.tmp file older than this is a leftover from a crash, not a save in
# progress. A save takes milliseconds, so ten minutes is far past any live one.
_TMP_GRACE = 600.0
_REVOKE_TIMEOUT = 10.0
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


def _refresh_token_sent(request: httpx2.Request) -> str | None:
    """The refresh token a refresh-grant token request carries, else None."""
    if request.method != "POST":
        return None
    try:
        body = parse_qs(request.content.decode("utf-8"))
    except (UnicodeDecodeError, RuntimeError):
        return None
    if body.get("grant_type") != ["refresh_token"]:
        return None
    return body.get("refresh_token", [None])[0]


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


_ALIAS = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")


def _token_path(alias: str) -> Path:
    from nare.session import nare_home

    if not _ALIAS.fullmatch(alias) or "__" in alias:
        raise McpAuthError(f"MCP alias {alias!r} is not a valid server name")

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

    def reload(self) -> None:
        """Read the file again, as another process may have left it. A missing
        file means no stored authorization: nothing is kept from memory, so a
        token that another process removed is not used."""
        self.tokens = None
        self.expires_at = None
        self.metadata = None
        self._client = None
        self._load()

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
            self._save_shared(locked=False)

    def set_metadata(self, metadata: OAuthMetadata, *, locked: bool = False) -> None:
        """Keep the authorization server's endpoints, so a refresh in a later
        process goes where this one's did, not to the MCP server's origin.
        `locked` says the caller already holds the token lock."""
        if self.metadata is not None and self.metadata.model_dump(
            mode="json"
        ) == metadata.model_dump(mode="json"):
            return
        self.metadata = metadata
        self._save_shared(locked)

    def _save_shared(self, locked: bool) -> None:
        """Save metadata or a registration without writing back tokens this
        process holds in memory. Another process may have refreshed or logged
        out since they were read, so the file's tokens are kept. If the file is
        gone and this process held tokens, they were logged out elsewhere, and
        nothing is written. When the lock is free it is taken without waiting;
        when it is busy the save is skipped, and the next response saves it."""
        fd: int | None = None
        if not locked:
            fd = _try_lock(self.path.parent, self.path.stem)
            if fd is None:
                return
        try:
            if not self.path.exists():
                if self.tokens is not None:
                    return
            elif not self._adopt_disk_tokens():
                return
            self._save()
        finally:
            if fd is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _adopt_disk_tokens(self) -> bool:
        """Take the tokens and their expiry from the file. False when the file
        cannot be read as this server's record, so nothing is written over it."""
        from mcp.shared.auth import OAuthToken

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(data, dict) or data.get("server") != self.server_url:
            return False
        raw = data.get("tokens")
        try:
            self.tokens = OAuthToken.model_validate(raw) if raw is not None else None
        except ValueError:
            return False
        expires = data.get("expires_at")
        self.expires_at = (
            float(expires)
            if isinstance(expires, (int, float)) and not isinstance(expires, bool)
            else None
        )
        return True

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
        _sweep_leftovers(folder, keep=tmp)


def _try_lock(folder: Path, stem: str) -> int | None:
    """The token lock, taken without waiting. None when another holder has it."""
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(folder / f"{stem}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def _sweep_leftovers(folder: Path, *, keep: str) -> None:
    """Remove .mcp-*.tmp files a crashed save left behind. A recent one may be
    another process's save in progress, so only old ones go."""
    cutoff = time.time() - _TMP_GRACE
    for leftover in folder.glob(".mcp-*.tmp"):
        if str(leftover) == keep:
            continue
        with contextlib.suppress(OSError):
            if leftover.stat().st_mtime < cutoff:
                leftover.unlink()


class _TokenLock:
    """An exclusive lock on one server's token file, held across a token
    exchange and across a logout. Two nare processes refreshing the same token
    would otherwise both spend the refresh token, and the server rotates it:
    the loser would store a token the server has already retired.

    The lock is an flock on a file beside the token file. It is taken without
    blocking the event loop, so the SDK's own in-process lock still works.
    """

    def __init__(self, folder: Path, alias: str) -> None:
        self.folder = folder
        self.alias = alias
        self._fd: int | None = None

    async def __aenter__(self) -> _TokenLock:
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.folder / f"{self.alias}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _LOCK_WAIT
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if loop.time() >= deadline:
                        raise McpAuthError(
                            f"MCP server {self.alias}: another nare process is "
                            "using its authorization; try again"
                        ) from None
                    await asyncio.sleep(0.05)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


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
            guard: _TokenLock | None = None
            try:
                outgoing = await flow.__anext__()
                while True:
                    if outgoing is not request:
                        _check_request(alias, outgoing, mcp_origin, as_origin)
                    spent = _refresh_token_sent(outgoing)
                    if spent is not None and guard is None:
                        # A refresh spends the refresh token, which the server
                        # may rotate. Take the lock, then read what another
                        # process stored while this one waited.
                        guard = _TokenLock(storage.path.parent, alias)
                        await guard.__aenter__()
                        storage.reload()
                        if storage.tokens is None:
                            raise McpAuthError(
                                f"MCP server {alias} needs authorization again; "
                                f"{login_hint(alias)}"
                            )
                        if storage.tokens.refresh_token != spent:
                            # Another process already refreshed. Use its tokens and
                            # do not spend the retired refresh token.
                            self.context.current_tokens = storage.tokens
                            self.context.token_expiry_time = (
                                storage.expires_at - _EXPIRY_SKEW
                                if storage.expires_at is not None
                                else None
                            )
                            await flow.aclose()
                            flow = super()._auth_flow(request)
                            outgoing = await flow.__anext__()
                            continue
                    response = yield outgoing
                    if self.context.oauth_metadata is not None:
                        storage.set_metadata(
                            self.context.oauth_metadata, locked=guard is not None
                        )
                    outgoing = await flow.asend(response)
                    if guard is not None and _refresh_token_sent(outgoing) is None:
                        # The exchange is over and its tokens are stored.
                        await guard.__aexit__()
                        guard = None
            except StopAsyncIteration:
                return
            finally:
                await flow.aclose()
                if guard is not None:
                    await guard.__aexit__()

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


# -- logout ------------------------------------------------------------------


async def logout(alias: str, server: Server | None = None) -> str:
    """Delete the stored tokens for ALIAS and return a fixed status sentence.

    With `server` (its entry in --mcp-config), the token is first revoked at
    the authorization server, when that server advertises a revocation
    endpoint. The endpoint must be https (or loopback) and must be on the
    authorization server's origin, the same rule as every other request in this
    module; it is never contacted otherwise. Without `server` nothing is
    revoked, and the sentence says so.

    The file is removed whether or not revocation worked. A failed revocation
    raises McpAuthError, because the token may still be valid at the server.
    The token lock is held throughout, so a refresh in another process cannot
    store a token after this logout.
    """
    path = _token_path(alias)
    async with _TokenLock(path.parent, alias):
        record = _read_record(path)
        if not path.exists():
            return f"no stored authorization for {alias}"
        outcome = "not revoked: no --mcp-config was given"
        failed = None
        if server is not None:
            outcome, failed = await _revoke(alias, server, record)
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        if failed is not None:
            raise McpAuthError(f"{failed}; the stored tokens were removed")
    return f"removed the stored tokens for {alias}; {outcome}"


def _read_record(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


async def _revoke(
    alias: str, server: Server, record: dict[str, Any] | None
) -> tuple[str, str | None]:
    """Revoke the stored tokens at the server. Returns (what happened, the
    failure in fixed text or None). Error text never includes a server reply."""
    import httpx2

    if server.auth is None or server.url is None or record is None:
        return "nothing to revoke", None
    if record.get("server") != server.url:
        # The file was written for another URL. Its tokens are not this server's.
        return "nothing to revoke", None
    tokens = record.get("tokens")
    metadata = record.get("metadata") or {}
    endpoint = (
        metadata.get("revocation_endpoint") if isinstance(metadata, dict) else None
    )
    if not isinstance(endpoint, str) or not endpoint:
        return (
            "the server advertises no revocation endpoint, so it was not revoked",
            None,
        )
    if not isinstance(tokens, dict):
        return "nothing to revoke", None
    # The refresh token first: the server may revoke the access token with it.
    # Both are sent, because a server need not cascade the revocation.
    held = [
        (tokens[key], hint)
        for key, hint in (
            ("refresh_token", "refresh_token"),
            ("access_token", "access_token"),
        )
        if isinstance(tokens.get(key), str) and tokens.get(key)
    ]
    if not held:
        return "nothing to revoke", None
    try:
        mcp_origin = _origin(server.url)
        pinned = server.auth.authorization_server
        as_origin = _origin(pinned) if pinned is not None else mcp_origin
    except ValueError:
        return "", f"MCP server {alias}: its address is not valid, so nothing was sent"

    client_id = server.auth.client_id
    if client_id is None and isinstance(record.get("client"), dict):
        client_id = record["client"].get("client_id")
    # The SDK's revocation model requires the key, so a public client sends it
    # empty. The value is never logged; it is only the secret when one is set.
    secret = ""
    if server.auth.client_secret_env is not None:
        secret = os.environ.get(server.auth.client_secret_env) or ""
        if not secret:
            return "", (
                f"MCP server {alias}: environment variable "
                f"{server.auth.client_secret_env} is not set, so it was not revoked"
            )

    async with httpx2.AsyncClient(
        follow_redirects=False, trust_env=False, timeout=_REVOKE_TIMEOUT
    ) as client:
        for token, hint in held:
            data: dict[str, str] = {
                "token": token,
                "token_type_hint": hint,
                "client_secret": secret,
            }
            if client_id:
                data["client_id"] = client_id
            try:
                request = httpx2.Request("POST", endpoint, data=data)
                _check_request(alias, request, mcp_origin, as_origin)
            except McpAuthError as exc:
                return "", f"{exc}; the revocation was not sent"
            except (ValueError, httpx2.InvalidURL):
                return "", (
                    f"MCP server {alias}: the revocation endpoint is not a valid "
                    "URL; the revocation was not sent"
                )
            try:
                reply = await client.send(request)
            except httpx2.TimeoutException:
                return "", f"MCP server {alias}: revocation timed out"
            except (httpx2.HTTPError, OSError):
                return "", f"MCP server {alias}: revocation could not reach the server"
            if reply.status_code != 200:
                return "", (
                    f"MCP server {alias}: the server refused the revocation "
                    f"(HTTP {reply.status_code})"
                )
    return "revoked at the server", None
