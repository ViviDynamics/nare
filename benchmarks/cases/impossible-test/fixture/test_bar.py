from bar import normalize


def test_normalize_strips_and_uppercases():
    assert normalize("  Hi  ") == "HI"
