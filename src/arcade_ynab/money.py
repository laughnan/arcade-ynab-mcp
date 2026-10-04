"""Conversion between YNAB milliunits and currency units.

YNAB stores amounts as integers in milliunits (1000 = 1.00 in the plan's currency).
Tools accept and return currency units so models never see milliunits.
"""

from decimal import ROUND_HALF_EVEN, Decimal


def to_milliunits(amount: float | int | str) -> int:
    """Convert a currency amount (e.g. -42.5) to milliunits (-42500)."""
    value = Decimal(str(amount)) * 1000
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def from_milliunits(milliunits: int | None) -> float | None:
    """Convert milliunits (e.g. -42500) to a currency amount (-42.5)."""
    if milliunits is None:
        return None
    return float(Decimal(milliunits) / 1000)
