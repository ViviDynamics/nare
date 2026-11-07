import pytest
from inventory.report import render_report

ITEMS = [
    {
        "sku": "b-2",
        "name": "Bolt",
        "warehouse": "north",
        "qty": 10,
        "unit_cost": "0.25",
    },
    {
        "sku": "a-1",
        "name": "Anchor",
        "warehouse": "south",
        "qty": 0,
        "unit_cost": "4.00",
    },
    {"sku": "c-3", "name": "Chain", "warehouse": "", "qty": 3, "unit_cost": "2.50"},
]


def test_zero_stock_is_dropped_by_default():
    assert "Anchor" not in render_report(ITEMS)


def test_each_warehouse_gets_a_section():
    text = render_report(ITEMS)
    assert "== north ==" in text
    assert "== unassigned ==" in text


def test_show_empty_keeps_zero_stock():
    assert "A-1" in render_report(ITEMS, show_empty=True)


def test_an_unknown_sort_is_an_error():
    with pytest.raises(ValueError):
        render_report(ITEMS, sort_by="colour")
