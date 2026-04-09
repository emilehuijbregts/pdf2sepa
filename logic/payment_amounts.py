"""Gedeelde Decimal-pijplijn voor betalingsbedragen (engine + SEPA XML)."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

_QUANT = Decimal("0.01")


def amount_to_decimal(amount: object) -> Decimal:
    """Converteer bedrag (float, int, str, Decimal) naar Decimal met 2 decimalen (half-up)."""
    if amount is None:
        return Decimal("0.00")
    if isinstance(amount, Decimal):
        return amount.quantize(_QUANT, rounding=ROUND_HALF_UP)
    try:
        s = str(amount).strip().replace(",", ".")
        if not s:
            return Decimal("0.00")
        return Decimal(s).quantize(_QUANT, rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def format_eur_xml(d: Decimal) -> str:
    """String voor pain.001 InstdAmt / CtrlSum (punt als decimaal, altijd 2 cijfers)."""
    q = d.quantize(_QUANT, rounding=ROUND_HALF_UP)
    return f"{q:.2f}"


def sum_decimals(values: list[Decimal]) -> Decimal:
    """Som met afronding op 2 decimalen na optellen."""
    s = sum(values, start=Decimal("0"))
    return s.quantize(_QUANT, rounding=ROUND_HALF_UP)
