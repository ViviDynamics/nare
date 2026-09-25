from report import summary
from shape import calc_area


def test_area():
    assert calc_area(2, 3) == 6


def test_summary():
    assert summary(2, 3) == "area is 6"
