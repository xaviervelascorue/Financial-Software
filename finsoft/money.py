"""Utilidades de importes: se guardan en céntimos enteros y se operan como Decimal."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def D(x) -> Decimal:
    if isinstance(x, Decimal):
        return x
    if x is None or x == "":
        return Decimal("0")
    return Decimal(str(x))


def r2(x) -> Decimal:
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def to_cents(x) -> int:
    return int((r2(x) * 100).to_integral_value(rounding=ROUND_HALF_UP))


def from_cents(c) -> Decimal:
    return (Decimal(int(c or 0)) / 100).quantize(CENT)


def fmt(x) -> str:
    """Formato español: 1.234,56"""
    s = f"{r2(x):,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")
