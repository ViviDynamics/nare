from decimal import Decimal

from billing.invoice import total


def test_single_line_with_discount():
    assert total([("10.00", 1, "15%")]) == Decimal("8.50")


def test_total_rounds_once_at_the_end():
    # Three lines of 0.025 after discount: the exact 0.075 rounds to 0.08 once.
    # Rounding each line first would give 0.09.
    assert total([("0.05", 1, "50%")] * 3) == Decimal("0.08")


def test_tax_applies_to_the_discounted_subtotal():
    assert total([("100.00", 2, "10%")], tax="8.25%") == Decimal("194.85")
