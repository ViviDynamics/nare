"""Line prices and discounts. Amounts are Decimal dollars, never floats."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def parse_rate(text):
    """'15%' is 0.15; an empty string or '0%' is zero."""
    text = (text or "").strip()
    if not text:
        return Decimal(0)
    if not text.endswith("%"):
        raise ValueError(f"a rate needs a percent sign: {text!r}")
    return Decimal(text[:-1]) / 100


def line_amount(unit_price, quantity, discount="0%"):
    """The line's amount after its discount, in dollars, rounded to the cent."""
    gross = Decimal(unit_price) * quantity
    return (gross * (1 - parse_rate(discount))).quantize(CENT, rounding=ROUND_HALF_UP)
