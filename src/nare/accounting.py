"""Prices above the transport; cumulative budgets count normalized usage."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, replace

from nare.session import Usage
from nare.transport import Reply


def finite_number(value: str | float, name: str, *, zero: bool = False) -> float:
    requirement = "non-negative" if zero else "positive"
    message = f"{name} must be a finite {requirement} number: {value!r}"
    try:
        parsed = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(message) from exc
    if not math.isfinite(parsed) or (parsed < 0 if zero else parsed <= 0):
        raise ValueError(message)
    return parsed


@dataclass(frozen=True)
class Prices:
    input: float
    output: float
    cache_read: float | None = None
    cache_write: float | None = None

    def __post_init__(self) -> None:
        for name in ("input", "output", "cache_read", "cache_write"):
            value = getattr(self, name)
            if value is not None:
                finite_number(value, f"price {name}", zero=True)


def prices_from_env() -> Prices | None:
    values: dict[str, float] = {}
    for name, field in [
        ("NARE_PRICE_IN", "input"),
        ("NARE_PRICE_OUT", "output"),
        ("NARE_PRICE_CACHE_READ", "cache_read"),
        ("NARE_PRICE_CACHE_WRITE", "cache_write"),
    ]:
        if name in os.environ:
            values[field] = finite_number(os.environ[name], name, zero=True)
    if "input" not in values or "output" not in values:
        return None
    return Prices(**values)


def turn_usage(reply: Reply, prices: Prices | None) -> Usage:
    cost = reply.cost
    if cost is None and prices is not None:
        u = reply.usage
        cost = (
            u.input * prices.input
            + u.output * prices.output
            + u.cache_read
            * (prices.cache_read if prices.cache_read is not None else prices.input)
            + u.cache_write
            * (prices.cache_write if prices.cache_write is not None else prices.input)
        ) / 1_000_000
    return replace(reply.usage, cost=cost)
