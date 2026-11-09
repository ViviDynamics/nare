from importlib.metadata import version

import nare


def test_version_is_exposed() -> None:
    # Not a literal: the version comes from the tag the release was cut at, so
    # asserting a hard-coded number here would pin the test to one release.
    assert nare.__version__ == version("nare")
    assert nare.__version__[0].isdigit()


def test_the_bedrock_extra_brings_the_aws_signer() -> None:
    from importlib.metadata import requires

    assert any(
        "anthropic[bedrock]" in r and "extra == 'bedrock'" in r
        for r in requires("nare") or []
    )


def test_the_container_image_installs_the_bedrock_extra() -> None:
    from pathlib import Path

    dockerfile = Path(__file__).parents[1] / "Dockerfile"
    assert "[bedrock]" in dockerfile.read_text()
