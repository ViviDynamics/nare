"""Invoice totals."""

from decimal import ROUND_HALF_UP, Decimal

from billing.pricing import CENT, line_amount, parse_rate


def total(lines, tax="0%"):
    """The invoice total in dollars.

    Each line is (unit_price, quantity, discount). The line amounts are exact,
    summed, then taxed, and the total is rounded half-up to the cent once, at
    the end. Rounding each line first would lose cents on a long invoice.
    """
    subtotal = sum(
        (line_amount(price, qty, discount) for price, qty, discount in lines),
        Decimal(0),
    )
    return (subtotal * (1 + parse_rate(tax))).quantize(CENT, rounding=ROUND_HALF_UP)
