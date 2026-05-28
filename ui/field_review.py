"""Registry and label formatters for universal field candidate review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from parser.field_model import FieldId

REVIEW_FIELD_IDS: tuple[FieldId, ...] = (
    "amount",
    "invoice_number",
    "customer_number",
    "iban",
)

_AMOUNT_SOURCE_NL: dict[str, str] = {
    "total_label_payable": "Totaal te betalen",
    "total_label_invoice": "Factuurbedrag",
    "total_label_generic": "Totaal",
    "total_label_excl": "Totaal excl. BTW",
    "total_line_hint": "Totaalregel (fallback)",
    "fallback_last_token": "Laatste bedrag in PDF",
    "INCL_CONFLICT": "Meerdere incl.-bedragen",
    "CONFLICTING_HIGH_CONFIDENCE": "Conflicterende totalen",
}


@dataclass(frozen=True)
class FieldReviewSpec:
    field_id: FieldId
    result_snapshot_key: str
    legacy_value_key: str
    menu_empty_title_nl: str
    menu_no_candidates_nl: str
    pick_pending_reason: str


CUSTOMER_ABSENT_PICK_SOURCE = "USER_ABSENT_CUSTOMER"
CUSTOMER_ABSENT_STATE = "NOT_PRESENT_SUPPLIER_LEVEL"
CUSTOMER_ABSENT_MENU_LABEL_NL = "— Geen klantnummer (leverancier heeft geen klantcode)"


def make_customer_absent_pick_candidate() -> dict[str, Any]:
    """Kiesbewuste afwezigheid: geen klantnummer op de betalingsregel."""
    return {
        "value": "",
        "source": CUSTOMER_ABSENT_PICK_SOURCE,
        "confidence": 100,
        "context": "",
        "label": "Geen klantnummer",
        "absent": True,
    }


def is_customer_absent_pick(cand: dict[str, Any] | None) -> bool:
    if not isinstance(cand, dict):
        return False
    if cand.get("absent") is True:
        return True
    src = str(cand.get("source") or "").strip()
    return src in (CUSTOMER_ABSENT_PICK_SOURCE, CUSTOMER_ABSENT_STATE)


FIELD_REVIEW_SPECS: dict[FieldId, FieldReviewSpec] = {
    "amount": FieldReviewSpec(
        field_id="amount",
        result_snapshot_key="amount_result",
        legacy_value_key="amount",
        menu_empty_title_nl="Bedrag kiezen",
        menu_no_candidates_nl="Er zijn geen parser-kandidaten om uit te kiezen.",
        pick_pending_reason="amount_picked",
    ),
    "invoice_number": FieldReviewSpec(
        field_id="invoice_number",
        result_snapshot_key="invoice_number_result",
        legacy_value_key="invoice_number",
        menu_empty_title_nl="Factuur-/polisnummer",
        menu_no_candidates_nl="Geen meerdere parser-kandidaten om uit te kiezen.",
        pick_pending_reason="invoice_number_picked",
    ),
    "customer_number": FieldReviewSpec(
        field_id="customer_number",
        result_snapshot_key="customer_number_result",
        legacy_value_key="customer_number",
        menu_empty_title_nl="Klantnummer",
        menu_no_candidates_nl="Geen meerdere parser-kandidaten om uit te kiezen.",
        pick_pending_reason="customer_number_picked",
    ),
    "iban": FieldReviewSpec(
        field_id="iban",
        result_snapshot_key="iban_result",
        legacy_value_key="iban",
        menu_empty_title_nl="IBAN kiezen",
        menu_no_candidates_nl="Geen meerdere IBAN-kandidaten om uit te kiezen.",
        pick_pending_reason="iban_picked",
    ),
}


def nl_amount_candidate_source(source: str) -> str:
    s = str(source or "").strip()
    return _AMOUNT_SOURCE_NL.get(s, s.replace("_", " ").title() if s else "Bedrag")


def amount_candidate_type_hint_nl(cand: dict[str, Any]) -> str:
    """Korte tag zodat gemengde incl./excl.-kandidaten in het menu onderscheidbaar zijn."""
    t = str(cand.get("type") or "").strip().lower()
    if t == "incl":
        return ""
    if t == "excl":
        return " [excl. BTW]"
    if t == "vat":
        return " [BTW]"
    if t == "unknown":
        return " [type onbekend]"
    return f" [{t}]" if t else ""


def format_amount_candidate_menu_label(
    cand: dict[str, Any],
    *,
    format_amount_nl: Any,
) -> str:
    raw_v = cand.get("value")
    try:
        disp = format_amount_nl(raw_v) if raw_v is not None else "?"
    except Exception:
        disp = str(raw_v or "?")
    label = (
        f"{disp} — {nl_amount_candidate_source(str(cand.get('source') or ''))}"
        f"{amount_candidate_type_hint_nl(cand)}"
    )
    conf = cand.get("confidence")
    if conf is not None:
        label += f" ({int(conf)}%)"
    return label


_IBAN_SOURCE_NL: dict[str, str] = {
    "pdf_text": "PDF-tekst",
    "ocr": "OCR",
    "USER_PICKED": "Handmatige keuze",
}


def format_iban_candidate_menu_label(cand: dict[str, Any]) -> str:
    val = str(cand.get("value") or "").strip()
    src = str(cand.get("source") or cand.get("label") or "kandidaat").strip()
    src_nl = _IBAN_SOURCE_NL.get(src, src.replace("_", " ").title() if src else "IBAN")
    conf = cand.get("confidence")
    text = f"{val} — {src_nl}"
    if conf is not None:
        text += f" ({int(conf)}%)"
    return text


def format_ident_candidate_menu_label(cand: dict[str, Any]) -> str:
    val = str(cand.get("value") or "").strip()
    lbl = str(cand.get("label") or cand.get("source") or "kandidaat").strip()
    conf = cand.get("confidence")
    text = f"{val} — {lbl}"
    if conf is not None:
        text += f" ({int(conf)}%)"
    return text


def candidate_menu_tooltip(cand: dict[str, Any], *, max_len: int = 200) -> str:
    ctx = str(cand.get("context") or "")
    if len(ctx) > max_len:
        return ctx[: max_len - 3] + "..."
    return ctx
