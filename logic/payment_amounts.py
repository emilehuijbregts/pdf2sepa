"""Gedeelde Decimal-pijplijn voor betalingsbedragen (engine + SEPA XML)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

_QUANT = Decimal("0.01")


def amount_to_decimal(amount: object) -> Decimal:
    """Converteer bedrag naar Decimal met 2 decimalen (half-up).

    Ongeldige of lege input → ValueError (geen stille default naar 0.00).
    """
    if amount is None:
        raise ValueError("amount is None")
    if isinstance(amount, bool):
        raise ValueError("amount: bool is not valid money")
    if isinstance(amount, Decimal):
        return amount.quantize(_QUANT, rounding=ROUND_HALF_UP)
    if isinstance(amount, int):
        return Decimal(amount).quantize(_QUANT, rounding=ROUND_HALF_UP)
    if isinstance(amount, float):
        return Decimal(str(amount)).quantize(_QUANT, rounding=ROUND_HALF_UP)
    if isinstance(amount, str):
        s = amount.strip().replace(",", ".")
        if not s:
            raise ValueError("amount: empty string")
        try:
            return Decimal(s).quantize(_QUANT, rounding=ROUND_HALF_UP)
        except InvalidOperation as exc:
            raise ValueError("amount: invalid decimal string") from exc
    raise ValueError(f"amount: unsupported type {type(amount).__name__}")


def format_eur_xml(d: Decimal) -> str:
    """String voor pain.001 InstdAmt / CtrlSum (punt als decimaal, altijd 2 cijfers)."""
    q = d.quantize(_QUANT, rounding=ROUND_HALF_UP)
    return f"{q:.2f}"


def sum_decimals(values: list[Decimal]) -> Decimal:
    """Som met afronding op 2 decimalen na optellen."""
    s = sum(values, start=Decimal("0"))
    return s.quantize(_QUANT, rounding=ROUND_HALF_UP)


def resolved_payment_amount_for_export(
    *,
    amount_cell_text: str,
    amount_result: dict | None,
) -> Decimal:
    """Één bron voor export/engine-compat: UI ``amount_result`` wint vóór celtekst.

    - ``user_selected``: uitsluitend ``value`` / ``selected_amount`` (geen fallback naar
      legacy ``invoice.amount`` / cel bij inconsistente metadata).
    - ``confirmed`` / ``tentative``: idem zolang die velden parseerbaar zijn.
    - Anders: parse de zichtbare cel (nl. dubieuze rijen zonder bevestigde snapshot).
    """
    ar = amount_result if isinstance(amount_result, dict) else None
    # Defensive rule: if the visible amount cell contains a valid amount that differs
    # from a non-user-selected snapshot, prefer the cell. This prevents UI-vs-export
    # divergence when UI operations (e.g. discount) update the displayed amount but
    # not the stored snapshot.
    cell_dec: Decimal | None = None
    try:
        s0 = (amount_cell_text or "").strip()
        if s0:
            cell_dec = amount_to_decimal(s0)
    except ValueError:
        cell_dec = None
    if ar is not None:
        if ar.get("user_selected"):
            for key in ("value", "selected_amount"):
                raw = ar.get(key)
                if raw is not None and str(raw).strip():
                    return amount_to_decimal(str(raw))
            raise ValueError("user_selected amount_result mist geldige value/selected_amount")
        st = str(ar.get("status") or ar.get("amount_status") or "").strip().lower()
        if st in ("confirmed", "tentative"):
            for key in ("value", "selected_amount"):
                raw = ar.get(key)
                if raw is not None and str(raw).strip():
                    try:
                        snap_dec = amount_to_decimal(str(raw))
                        if cell_dec is not None and cell_dec != snap_dec:
                            return cell_dec
                        return snap_dec
                    except ValueError:
                        break
    s = (amount_cell_text or "").strip()
    if not s:
        raise ValueError("leeg bedrag")
    return amount_to_decimal(s)


def normalize_supplier_vat_rate_pct(raw: object) -> int:
    """Leveranciers-tarief als geheel percentage (0, 21, …); onbekend → 21 (veilige default)."""
    if isinstance(raw, bool):
        return 21
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return 21
    if v < 0 or v > 100:
        return 21
    return v


def incl_amount_to_excl_for_discount(incl: Decimal, vat_rate_pct: int) -> Decimal:
    """Exclusief bedrag voor kortingsberekening op basis van BTW-tarief leverancier."""
    rate = normalize_supplier_vat_rate_pct(vat_rate_pct)
    if rate == 21:
        excl = incl / Decimal("1.21")
    elif rate == 0:
        excl = incl
    else:
        factor = Decimal("1") + Decimal(rate) / Decimal("100")
        excl = incl / factor
    return excl.quantize(_QUANT, rounding=ROUND_HALF_UP)
