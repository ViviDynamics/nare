"""In-run retries of transient provider failures (issue 134)."""

from __future__ import annotations

import httpx2
import pytest

from nare.transport import (
    ProviderStatusError,
    StreamDropped,
    Transient,
    transient,
)

REQUEST = httpx2.Request("POST", "http://proxy.invalid/v1/chat/completions")


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (httpx2.ReadTimeout("read timed out", request=REQUEST), "timeout"),
        (httpx2.ConnectTimeout("connect timed out", request=REQUEST), "timeout"),
        (httpx2.WriteTimeout("write timed out", request=REQUEST), "timeout"),
        (httpx2.ConnectError("connection refused", request=REQUEST), "connection"),
        (httpx2.ReadError("connection reset", request=REQUEST), "connection"),
        (
            httpx2.RemoteProtocolError("peer closed connection", request=REQUEST),
            "connection",
        ),
        (StreamDropped("incomplete model stream: final marker missing"), "stream"),
    ],
)
def test_transport_failures_are_transient(exc: Exception, reason: str) -> None:
    assert transient(exc) == Transient(reason)


@pytest.mark.parametrize("status", [429, 502, 503, 504, 529])
def test_retryable_statuses_are_transient(status: int) -> None:
    exc = ProviderStatusError(f"HTTP {status}", status_code=status)
    assert transient(exc) == Transient("status", status=status)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422, 500, 524])
def test_other_statuses_are_permanent(status: int) -> None:
    assert transient(ProviderStatusError("no", status_code=status)) is None


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("connection reset"),
        ValueError("schema refused"),
        RuntimeError("incomplete model stream: invalid final usage"),
        httpx2.ProxyError("proxy refused", request=REQUEST),
        httpx2.UnsupportedProtocol("no such scheme", request=REQUEST),
    ],
)
def test_everything_else_is_permanent(exc: Exception) -> None:
    assert transient(exc) is None


def test_a_wrapped_cause_is_classified() -> None:
    # The anthropic SDK raises its own error from httpx's.
    try:
        try:
            raise httpx2.ReadTimeout("read timed out", request=REQUEST)
        except httpx2.ReadTimeout as inner:
            raise RuntimeError("Request timed out.") from inner
    except RuntimeError as outer:
        assert transient(outer) == Transient("timeout")


@pytest.mark.parametrize(
    ("header", "expected"), [("7", 7.0), ("0.5", 0.5), ("soon", None), ("-1", None)]
)
def test_retry_after_seconds_are_read(header: str, expected: float | None) -> None:
    exc = ProviderStatusError("slow down", status_code=429, retry_after=header)
    assert transient(exc) == Transient("status", status=429, retry_after=expected)
