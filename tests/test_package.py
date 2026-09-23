from importlib.metadata import version

import nare


def test_version_is_exposed() -> None:
    # Not a literal: the version comes from the tag the release was cut at, so
    # asserting a hard-coded number here would pin the test to one release.
    assert nare.__version__ == version("nare")
    assert nare.__version__[0].isdigit()
