# Bouwt betaalopdrachten op basis van geparste factuurdata en leveranciersregels.
"""Verwerkt verrijkte factuurdicts naar betalingen en fouten.

Alleen ``match_status`` ``matched`` en ``new`` worden verder verwerkt. De parser/matcher
 gebruikt doorgaans ``no_hint`` i.p.v. ``new``; die status valt bij filter af.

Geen mutatie van invoerdicts.
"""

from __future__ import annotations

import re


def _clean_iban(iban: str | None) -> str:
    try:
        s = str(iban or "")
        s = re.sub(r"\s+", "", s)
        return s.upper().strip()
    except Exception:
        return ""


def _is_plausible_iban(iban: str) -> bool:
    """Syntactische IBAN-check: lengte ISO 15–34, 2-letter landcode, alleen A–Z en 0–9; geen mod-97."""
    if len(iban) < 15 or len(iban) > 34:
        return False
    return bool(re.fullmatch(r"[A-Z]{2}[0-9A-Z]{13,32}", iban))


def calculate_payments(invoices: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Returns:
        (payments, errors) waarbij ``payments`` succesvolle betaalregels zijn en
        ``errors`` documenten of groepen die niet verwerkt konden worden.
    """
    err = _ErrorBuckets()
    payments: list[dict] = []

    accepted: list[dict] = []
    for inv in invoices:
        ms = inv.get("match_status")
        if ms in ("matched", "new"):
            accepted.append(inv)
            continue
        if ms == "no_hint":
            reason = "no_supplier_hint"
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
        _process_supplier_group(group_invs, err, payments)

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

    for inv in sorted(
        valid_invoices,
        key=lambda x: (-x["amount"], str(x.get("invoice_number", ""))),
    ):
        creds = linked.get(id(inv), [])
        discount = float(inv.get("discount") or 0)
        warning: str | None = None

        if not creds:
            excl = inv.get("amount_excl_vat")
            if excl is not None:
                korting = excl * (discount / 100)
            else:
                korting = 0.0
                warning = "no_excl_vat_amount_discount_skipped"
            te_betalen = round((inv["amount"] - korting) + 1e-9, 2)
        else:
            saldo_incl = inv["amount"] - sum(c["amount"] for c in creds)
            if inv.get("amount_excl_vat") is not None and all(
                c.get("amount_excl_vat") is not None for c in creds
            ):
                saldo_excl = float(inv["amount_excl_vat"]) - sum(
                    float(c["amount_excl_vat"]) for c in creds
                )
                korting = saldo_excl * (discount / 100)
            else:
                korting = 0.0
                warning = "no_excl_vat_amount_discount_skipped"
            te_betalen = round((saldo_incl - korting) + 1e-9, 2)

        sup_out = inv.get("supplier_name")
        sup_for_err = sup_out if sup_out is not None else group_supplier

        if te_betalen == 0:
            err.add("zero_amount", sup_for_err, [inv])
            continue
        if te_betalen < 0:
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

        payments.append(
            {
                "supplier_name": str(sup_out) if sup_out is not None else display_name,
                "iban": iban,
                "amount": te_betalen,
                "description": inv.get("description") if inv.get("description") is not None else "",
                "invoice_number": str(inv["invoice_number"])
                if inv.get("invoice_number") is not None
                else "",
                "credit_notes_applied": credit_notes_applied,
                "warning": warning,
                "status": "ok",
            }
        )
