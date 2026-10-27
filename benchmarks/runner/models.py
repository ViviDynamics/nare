"""What the endpoint serves, read live.

Aliases on a proxy change without notice, and a list in a document goes
stale. `bin/bench models` prints this before a person or an agent picks a
model, and `run --matrix` checks its models against it before spending
anything.

httpx2, never urllib: Cloudflare in front of the proxy refuses urllib's
default User-Agent (error 1010). It reads the `model_info` keys ModelMetadata
reads, and nothing from `litellm_params`, which names upstream endpoints.
ModelMetadata is not reused: a transport builds it with that transport's
client and auth header, and it keeps only its own model's entry.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2

from benchmarks.runner.report import load_baseline

TIMEOUT = 10.0


class ListingError(Exception):
    """The endpoint did not say what it serves. Never carries the endpoint."""


@dataclass(frozen=True)
class ServedModel:
    name: str
    window: int | None = None
    # Dollars per million tokens.
    input_price: float | None = None
    output_price: float | None = None


@dataclass(frozen=True)
class Listing:
    models: tuple[ServedModel, ...]
    # False when only /v1/models answered: names, with no window or price.
    detailed: bool

    @property
    def names(self) -> set[str]:
        return {m.name for m in self.models}


def proxy_root(base_url: str) -> str:
    """As ModelMetadata.load does: the listing hangs off the root, not /v1."""
    return base_url.rstrip("/").removesuffix("/v1")


def _window(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _per_million(value: Any) -> float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value < 0
    ):
        return None
    return value * 1_000_000


def _get(client: httpx2.Client, root: str, path: str, api_key: str) -> Any:
    try:
        response = client.get(
            f"{root}{path}", headers={"authorization": f"Bearer {api_key}"}
        )
    except httpx2.HTTPError as exc:
        # The type only: an exception's text can carry the URL.
        raise ListingError(f"{path}: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise ListingError(f"{path}: HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise ListingError(f"{path}: the body is not JSON") from exc


def _entries(payload: Any, path: str) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ListingError(f"{path}: no data list")
    return [entry for entry in data if isinstance(entry, dict)]


def _from_model_info(payload: Any) -> tuple[ServedModel, ...]:
    served: dict[str, ServedModel] = {}
    for entry in _entries(payload, "/v1/model/info"):
        name = entry.get("model_name")
        if not isinstance(name, str) or name in served:
            # A proxy lists one entry per deployment; the first one speaks.
            continue
        info = entry.get("model_info")
        info = info if isinstance(info, dict) else {}
        served[name] = ServedModel(
            name,
            _window(info.get("max_input_tokens")),
            _per_million(info.get("input_cost_per_token")),
            _per_million(info.get("output_cost_per_token")),
        )
    if not served:
        raise ListingError("/v1/model/info: no models")
    return tuple(served[name] for name in sorted(served))


def _from_models(payload: Any) -> tuple[ServedModel, ...]:
    names = {
        entry["id"]
        for entry in _entries(payload, "/v1/models")
        if isinstance(entry.get("id"), str)
    }
    return tuple(ServedModel(name) for name in sorted(names))


def list_models(
    base_url: str, api_key: str, *, client: httpx2.Client | None = None
) -> Listing:
    """LiteLLM's /v1/model/info, with each model's window and prices, or
    /v1/models, with names only, when that does not answer.

    Raises ListingError when neither answers. The key goes as a Bearer token.
    """
    root = proxy_root(base_url)
    http = client or httpx2.Client(timeout=TIMEOUT)
    try:
        try:
            info = _get(http, root, "/v1/model/info", api_key)
            return Listing(_from_model_info(info), detailed=True)
        except ListingError:
            pass
        return Listing(_from_models(_get(http, root, "/v1/models", api_key)), False)
    finally:
        if client is None:
            http.close()


def baseline_tiers(directory: Path) -> dict[str, list[str]]:
    """Each model that has a baseline, and the tiers it has one for.

    Matched on the baseline's [meta] model, since file names are slugs.
    """
    found: dict[str, list[str]] = {}
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.toml")):
        tier = path.stem.rsplit(".", 1)[-1]
        found.setdefault(load_baseline(path).meta.model, []).append(tier)
    return {model: sorted(tiers) for model, tiers in found.items()}


def _price(value: float | None) -> str:
    return "-" if value is None else f"{value:g}"


def render_models(listing: Listing, tiers: Mapping[str, Sequence[str]]) -> str:
    rows = [("model", "window", "$/M in", "$/M out", "baselines")]
    for m in listing.models:
        rows.append(
            (
                m.name,
                "-" if m.window is None else f"{m.window:,}",
                _price(m.input_price),
                _price(m.output_price),
                ", ".join(tiers.get(m.name, ())) or "-",
            )
        )
    width = max(len(row[0]) for row in rows)
    lines = [
        f"{name:<{width}}  {window:>9}  {cost_in:>6}  {cost_out:>7}  {held}"
        for name, window, cost_in, cost_out, held in rows
    ]
    if not listing.detailed:
        lines.append(
            "names only: /v1/model/info did not answer, so windows and prices "
            "are unknown"
        )
    for name in sorted(set(tiers) - listing.names):
        lines.append(
            f"not served, though it has a baseline: {name} ({', '.join(tiers[name])})"
        )
    return "\n".join(lines)
