from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest

from benchmarks.runner.models import (
    Listing,
    ListingError,
    ServedModel,
    baseline_tiers,
    list_models,
    render_models,
)
from benchmarks.runner.report import BaselineMeta, dumps_baseline

INFO = {
    "data": [
        {
            "model_name": "claude-haiku-5-5",
            "litellm_params": {"api_base": "https://upstream.example/secret"},
            "model_info": {
                "max_input_tokens": 1_000_000,
                "input_cost_per_token": 1e-7,
                "output_cost_per_token": 5e-7,
            },
        },
        {"model_name": "gpt-5-nano", "model_info": {"max_input_tokens": 400_000}},
        # A second deployment of the same alias.
        {"model_name": "gpt-5-nano", "model_info": {"max_input_tokens": 1}},
    ]
}
NAMES = {"data": [{"id": "gpt-5-nano"}, {"id": "claude-haiku-5-5"}]}


def client(
    answer: Callable[[httpx2.Request], httpx2.Response],
    seen: list[httpx2.Request] | None = None,
) -> httpx2.Client:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(request)
        return answer(request)

    return httpx2.Client(transport=httpx2.MockTransport(handler))


def by_path(**bodies: Any) -> Callable[[httpx2.Request], httpx2.Response]:
    """Answer each path with its body, or with the status it maps to."""

    def answer(request: httpx2.Request) -> httpx2.Response:
        body = bodies.get(request.url.path.strip("/").replace("/", "_"))
        if isinstance(body, int):
            return httpx2.Response(body, text="no")
        if body is None:
            return httpx2.Response(404, text="no")
        return httpx2.Response(200, json=body)

    return answer


def test_model_info_gives_window_and_price_per_million() -> None:
    seen: list[httpx2.Request] = []
    listing = list_models(
        "https://proxy.example/v1/",
        "sk-proxy",
        client=client(by_path(v1_model_info=INFO), seen),
    )
    assert listing.detailed
    haiku, nano = listing.models
    assert (haiku.name, haiku.window) == ("claude-haiku-5-5", 1_000_000)
    assert (haiku.input_price, haiku.output_price) == pytest.approx((0.1, 0.5))
    # The first deployment speaks for the alias, and missing prices are None.
    assert nano == ServedModel("gpt-5-nano", 400_000, None, None)
    # The listing hangs off the root, with the run's key as a Bearer token.
    assert [str(r.url) for r in seen] == ["https://proxy.example/v1/model/info"]
    assert seen[0].headers["authorization"] == "Bearer sk-proxy"


def test_without_model_info_the_names_come_from_v1_models() -> None:
    listing = list_models(
        "https://proxy.example",
        "k",
        client=client(by_path(v1_model_info=404, v1_models=NAMES)),
    )
    assert not listing.detailed
    assert listing.names == {"claude-haiku-5-5", "gpt-5-nano"}
    assert all(m.window is None for m in listing.models)


@pytest.mark.parametrize(
    "fail",
    [
        lambda request: httpx2.Response(401, text="no"),
        lambda request: httpx2.Response(200, text="not json"),
        lambda request: httpx2.Response(200, json={"error": "x"}),
    ],
)
def test_a_listing_neither_endpoint_answers_raises(
    fail: Callable[[httpx2.Request], httpx2.Response],
) -> None:
    with pytest.raises(ListingError):
        list_models("https://proxy.example", "k", client=client(fail))


def test_a_listing_error_never_names_the_endpoint() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("failed: https://secret.example", request=request)

    with pytest.raises(ListingError) as caught:
        list_models("https://secret.example", "k", client=client(refuse))
    assert "secret.example" not in str(caught.value)
    assert "ConnectError" in str(caught.value)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://[secret-proxy.example]",
        "https://secret-proxy\xa0.example",
        "https://secret-proxy.example:8o8o",
    ],
)
def test_a_malformed_base_url_is_a_listing_error_naming_no_host(
    base_url: str,
) -> None:
    # httpx2 raises InvalidURL, which is not an HTTPError, and its text can
    # carry the host.
    with pytest.raises(ListingError) as caught:
        list_models(base_url, "k", client=client(by_path(v1_models=NAMES)))
    for part in ("secret", "proxy", "example"):
        assert part not in str(caught.value)
    assert "InvalidURL" in str(caught.value)


def test_render_shows_window_price_and_baselines_but_no_upstream() -> None:
    listing = list_models(
        "https://proxy.example", "k", client=client(by_path(v1_model_info=INFO))
    )
    text = render_models(listing, {"gpt-5-nano": ["full", "smoke"]})
    haiku = next(line for line in text.splitlines() if "haiku" in line)
    nano = next(line for line in text.splitlines() if "nano" in line)
    assert haiku.split() == ["claude-haiku-5-5", "1,000,000", "0.1", "0.5", "-"]
    assert nano.split() == ["gpt-5-nano", "400,000", "-", "-", "full,", "smoke"]
    assert "upstream" not in text


def test_render_says_when_only_names_came_back() -> None:
    text = render_models(Listing((ServedModel("m"),), detailed=False), {})
    assert "names only" in text


def test_render_names_a_baseline_the_endpoint_does_not_serve() -> None:
    listing = Listing((ServedModel("gpt-5-nano"),), detailed=True)
    text = render_models(listing, {"claude-haiku-4-5": ["smoke"]})
    assert "not served, though it has a baseline: claude-haiku-4-5 (smoke)" in text


def test_baselines_are_matched_on_their_meta_model(tmp_path: Path) -> None:
    for name, model in (
        ("onprem-glm-5-3-flash.smoke.toml", "onprem/glm-5.3-flash"),
        ("onprem-glm-5-3-flash.full.toml", "onprem/glm-5.3-flash"),
        ("gpt-5-nano.smoke.toml", "gpt-5-nano"),
    ):
        meta = BaselineMeta("t", "abc1234", "openai", model, "j")
        (tmp_path / name).write_text(dumps_baseline(meta, []))
    assert baseline_tiers(tmp_path) == {
        "gpt-5-nano": ["smoke"],
        "onprem/glm-5.3-flash": ["full", "smoke"],
    }


def test_no_baselines_directory_means_no_baselines(tmp_path: Path) -> None:
    assert baseline_tiers(tmp_path / "missing") == {}
