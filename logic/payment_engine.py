# Bouwt betaalopdrachten op basis van geparste factuurdata en leveranciersregels.
"""Verwerkt verrijkte factuurdicts naar betalingen en fouten.

Statuses ``matched``, ``new``, ``confirmed``, ``reviewed`` worden verder verwerkt.
``load_failed`` (met ``load_error`` in het factuurdict) wordt als PDF-fout gerapporteerd.

Geen mutatie van invoerdicts.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from logic.payment_amounts import amount_to_decimal
from logic.validation import clean_iban, is_plausible_iban

_clean_iban = clean_iban
_is_plausible_iban = is_plausible_iban

def calculate_payments(
    invoices: list[dict],
    *,
    session_date: date | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Returns:
        (payments, errors) waarbij ``payments`` succesvolle betaalregels zijn en
        ``errors`` documenten of groepen die niet verwerkt konden worden.

    Args:
        session_date: Kalenderdatum voor ``execution_date`` bij modus direct;
            default ``date.today()`` indien None.
    """
    err = _ErrorBuckets()
    payments: list[dict] = []
    sess = session_date if session_date is not None else date.today()

    _ACCEPTED_STATUSES = {"matched", "new", "confirmed", "reviewed"}

    accepted: list[dict] = []
    for inv in invoices:
        ms = inv.get("match_status")
        if ms == "load_failed":
            code = str(inv.get("load_error") or "read_failed")
            reason = "pdf_no_text" if code == "no_text" else "pdf_read_failed"
            err.add(reason, inv.get("supplier_name"), [inv])
            continue
        if ms in _ACCEPTED_STATUSES:
            accepted.append(inv)
            continue
        if ms == "no_hint":
            reason = "no_supplier_hint"
        elif ms == "needs_review":
            reason = "needs_review"
        else:
            reason = "unmatched_supplier"
        err.add(reason, inv.get("supplier_name"), [inv])

    groups: dict[str, list[dict]] = {}
    for inv in accepted:
        sn = inv.get("supplier_name")
        if sn is None or (isinstance(sn, str) and not str(sn).strip()):
            err.add("missing_supplier_name", None, [inv])
            continue
        gkey = str(sn).strip().lower()
        groups.setdefault(gkey, []).append(inv)

    for _gkey, group_invs in sorted(groups.items(), key=lambda x: x[0]):
        _process_supplier_group(group_invs, err, payments, sess)

    pay_sorted = sorted(
        payments,
        key=lambda p: (p.get("supplier_name") or "", p.get("invoice_number") or ""),
    )
    return pay_sorted, err.to_list()

class _ErrorBuckets:
    """Fouten gegroepeerd op (reason, supplier_name)."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str | None], list[dict]] = {}

    def add(self, reason: str, supplier_name: str | None, invoice_dicts: list[dict]) -> None:
        key = (reason, supplier_name)
        self._data.setdefault(key, []).extend(invoice_dicts)

    def to_list(self) -> list[dict]:
        return [
            {"supplier_name": sup, "reason": reason, "invoices": invs}
            for (reason, sup), invs in sorted(
                self._data.items(),
                key=lambda item: (item[0][0], item[0][1] or ""),
            )
        ]

def _doc_type(d: dict) -> str:
    t = d.get("type")
    if t == "credit_note":
        return "credit_note"
    return "invoice"

def _process_supplier_group(
    group_invs: list[dict],
    err: _ErrorBuckets,
    payments: list[dict],
    session: date,
) -> None:
    group_supplier = group_invs[0].get("supplier_name")
    display_name = str(group_supplier) if group_supplier is not None else ""

    credits = [x for x in group_invs if _doc_type(x) == "credit_note"]
    all_invoices_raw = [x for x in group_invs if _doc_type(x) != "credit_note"]

    for c in credits:
        if c.get("amount") is None:
            rest = [x for x in group_invs if x is not c]
            err.add("missing_amount", group_supplier, [c] + rest)
            return

    valid_invoices: list[dict] = []
    for inv in all_invoices_raw:
        if inv.get("amount") is None:
            err.add(
                "missing_amount",
                inv.get("supplier_name"),
                [inv],
            )
        else:
            valid_invoices.append(inv)

    if not valid_invoices and credits:
        err.add("credit_note_only", group_supplier, list(credits))
        return

    if not valid_invoices and not credits:
        return

    linked: dict[int, list[dict]] = {}
    for credit in credits:
        kandidaten = [inv for inv in valid_invoices if inv["amount"] >= credit["amount"]]
        if not kandidaten:
            err.add(
                "credit_exceeds_available_invoices",
                group_supplier,
                [credit] + valid_invoices,
            )
            return
        best = min(
            kandidaten,
            key=lambda inv: (inv["amount"], str(inv.get("invoice_number", ""))),
        )
        linked.setdefault(id(best), []).append(credit)

    for inv in valid_invoices:
        creds = linked.get(id(inv), [])
        if not creds:
            continue
        total_c = sum(c["amount"] for c in creds)
        if total_c > inv["amount"]:
            err.add(
                "credit_exceeds_invoice_total",
                group_supplier,
                list(group_invs),
            )
            return

    pct_100 = amount_to_decimal("100")

    for inv in sorted(
        valid_invoices,
        key=lambda x: (-x["amount"], str(x.get("invoice_number", ""))),
    ):
        creds = linked.get(id(inv), [])
        discount = float(inv.get("discount") or 0)
        warn_parts: list[str] = []
        if inv.get("iban_mismatch"):
            warn_parts.append("iban_mismatch_supplier")
        if inv.get("supplier_term_trusted") is False:
            warn_parts.append("supplier_term_not_applied")
        inv_date = inv.get("invoice_date")
        src = str(inv.get("invoice_date_source") or "missing")
        if not inv_date:
            warn_parts.append("missing_invoice_date")

        if not creds:
            excl = inv.get("amount_excl_vat")
            amt_dec = amount_to_decimal(inv["amount"])
            if excl is not None:
                korting = amount_to_decimal(excl) * amount_to_decimal(discount) / pct_100
            else:
                korting = amount_to_decimal(0)
                if discount > 0:
                    warn_parts.append("no_excl_vat_amount_discount_skipped")
            te_betalen_dec = (amt_dec - korting).quantize(Decimal("0.01"))
        else:
            saldo_incl = amount_to_decimal(inv["amount"]) - sum(
                (amount_to_decimal(c["amount"]) for c in creds), start=Decimal("0")
            )
            saldo_incl = saldo_incl.quantize(Decimal("0.01"))
            if inv.get("amount_excl_vat") is not None and all(
                c.get("amount_excl_vat") is not None for c in creds
            ):
                saldo_excl = amount_to_decimal(inv["amount_excl_vat"]) - sum(
                    (amount_to_decimal(c["amount_excl_vat"]) for c in creds), start=Decimal("0")
                )
                korting = (saldo_excl * amount_to_decimal(discount) / pct_100).quantize(
                    Decimal("0.01")
                )
            else:
                korting = amount_to_decimal(0)
                if discount > 0:
                    warn_parts.append("no_excl_vat_amount_discount_skipped")
            te_betalen_dec = (saldo_incl - korting).quantize(Decimal("0.01"))

        warning: str | None = "|".join(warn_parts) if warn_parts else None

        sup_out = inv.get("supplier_name")
        sup_for_err = sup_out if sup_out is not None else group_supplier

        if te_betalen_dec <= Decimal("0"):
            if te_betalen_dec == Decimal("0"):
                err.add("zero_amount", sup_for_err, [inv])
            else:
                err.add("negative_amount", sup_for_err, [inv])
            continue

        iban_raw = (inv.get("iban") or "").strip()
        if not iban_raw:
            err.add("missing_iban", sup_for_err, [inv])
            continue
        iban = _clean_iban(iban_raw)
        if not iban or not _is_plausible_iban(iban):
            err.add("invalid_iban", sup_for_err, [inv])
            continue

        credit_notes_applied = [
            str(c["invoice_number"])
            for c in creds
            if c.get("invoice_number") is not None
        ]

        trusted = bool(inv.get("supplier_term_trusted"))
        try:
            raw_term = int(inv.get("supplier_payment_term_days_raw") or 0)
        except (TypeError, ValueError):
            raw_term = 0
        effective_term = raw_term if trusted else 0
        inv_date_out = inv.get("invoice_date")
        if inv_date_out is not None:
            inv_date_out = str(inv_date_out).strip() or None

        payments.append(
            {
                "supplier_name": str(sup_out) if sup_out is not None else display_name,
                "iban": iban,
                "amount": float(te_betalen_dec),
                "description": inv.get("description") if inv.get("description") is not None else "",
                "invoice_number": str(inv["invoice_number"])
                if inv.get("invoice_number") is not None
                else "",
                "credit_notes_applied": credit_notes_applied,
                "warning": warning,
                "iban_mismatch": bool(inv.get("iban_mismatch")),
                "status": "ok",
                "invoice_date": inv_date_out,
                "invoice_date_source": src
                if src in ("parsed", "manual", "missing")
                else "missing",
                "supplier_term_trusted": trusted,
                "supplier_payment_term_days_raw": raw_term,
                "supplier_payment_term_days_effective": effective_term,
                "date_mode": "direct",
                "execution_date": session.isoformat(),
            }
        )
