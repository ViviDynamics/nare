from bar import parse


def test_parse_handles_spaces():
    assert parse("1, 2, 3") == [1, 2, 3]


def test_parse_handles_an_empty_string():
    assert parse("") == []
