"""Registry and label formatters for universal field candidate review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ui.i18n import tr, tr_or_code
from parser.field_model import FieldId

REVIEW_FIELD_IDS: tuple[FieldId, ...] = (
    "amount",
    "invoice_number",
    "customer_number",
    "iban",
    "vat_number",
    "kvk_number",
    "invoice_date",
    "email_domain",
)


@dataclass(frozen=True)
class FieldReviewSpec:
    field_id: FieldId
    result_snapshot_key: str
    legacy_value_key: str
    menu_empty_title_key: str
    menu_no_candidates_key: str
    pick_pending_reason: str


CUSTOMER_ABSENT_PICK_SOURCE = "USER_ABSENT_CUSTOMER"
CUSTOMER_ABSENT_STATE = "NOT_PRESENT_SUPPLIER_LEVEL"
CUSTOMER_ABSENT_MENU_LABEL_KEY = "field.customer.absent.label"


def make_customer_absent_pick_candidate() -> dict[str, Any]:
    """Kiesbewuste afwezigheid: geen klantnummer op de betalingsregel."""
    return {
        "value": "",
        "source": CUSTOMER_ABSENT_PICK_SOURCE,
        "confidence": 100,
        "context": "",
        "label": tr("field.customer.absent.short"),
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
        menu_empty_title_key="field.amount.menu_empty",
        menu_no_candidates_key="field.amount.menu_no_candidates",
        pick_pending_reason="amount_picked",
    ),
    "invoice_number": FieldReviewSpec(
        field_id="invoice_number",
        result_snapshot_key="invoice_number_result",
        legacy_value_key="invoice_number",
        menu_empty_title_key="field.invoice_number.menu_empty",
        menu_no_candidates_key="field.invoice_number.menu_no_candidates",
        pick_pending_reason="invoice_number_picked",
    ),
    "customer_number": FieldReviewSpec(
        field_id="customer_number",
        result_snapshot_key="customer_number_result",
        legacy_value_key="customer_number",
        menu_empty_title_key="field.customer_number.menu_empty",
        menu_no_candidates_key="field.customer_number.menu_no_candidates",
        pick_pending_reason="customer_number_picked",
    ),
    "iban": FieldReviewSpec(
        field_id="iban",
        result_snapshot_key="iban_result",
        legacy_value_key="iban",
        menu_empty_title_key="field.iban.menu_empty",
        menu_no_candidates_key="field.iban.menu_no_candidates",
        pick_pending_reason="iban_picked",
    ),
    "vat_number": FieldReviewSpec(
        field_id="vat_number",
        result_snapshot_key="vat_number_result",
        legacy_value_key="vat_number",
        menu_empty_title_key="field.vat_number.menu_empty",
        menu_no_candidates_key="field.vat_number.menu_no_candidates",
        pick_pending_reason="vat_number_picked",
    ),
    "kvk_number": FieldReviewSpec(
        field_id="kvk_number",
        result_snapshot_key="kvk_number_result",
        legacy_value_key="kvk_number",
        menu_empty_title_key="field.kvk_number.menu_empty",
        menu_no_candidates_key="field.kvk_number.menu_no_candidates",
        pick_pending_reason="kvk_number_picked",
    ),
    "invoice_date": FieldReviewSpec(
        field_id="invoice_date",
        result_snapshot_key="invoice_date_result",
        legacy_value_key="invoice_date",
        menu_empty_title_key="field.invoice_date.menu_empty",
        menu_no_candidates_key="field.invoice_date.menu_no_candidates",
        pick_pending_reason="invoice_date_picked",
    ),
    "email_domain": FieldReviewSpec(
        field_id="email_domain",
        result_snapshot_key="email_domain_result",
        legacy_value_key="email_domain",
        menu_empty_title_key="field.email_domain.menu_empty",
        menu_no_candidates_key="field.email_domain.menu_no_candidates",
        pick_pending_reason="email_domain_picked",
    ),
}


def nl_amount_candidate_source(source: str) -> str:
    s = str(source or "").strip()
    if not s:
        return tr("field.amount.source._default")
    return tr_or_code(f"field.amount.source.{s}", s.replace("_", " ").title() if s else tr("field.amount.source._default"))


def amount_candidate_type_hint_nl(cand: dict[str, Any]) -> str:
    """Korte tag zodat gemengde incl./excl.-kandidaten in het menu onderscheidbaar zijn."""
    t = str(cand.get("type") or "").strip().lower()
    if t == "incl":
        return ""
    if t == "excl":
        return tr("field.amount.type.excl")
    if t == "vat":
        return tr("field.amount.type.vat")
    if t == "unknown":
        return tr("field.amount.type.unknown")
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


def format_iban_candidate_menu_label(cand: dict[str, Any]) -> str:
    val = str(cand.get("value") or "").strip()
    src = str(cand.get("source") or cand.get("label") or "kandidaat").strip()
    src_nl = tr_or_code(
        f"field.iban.source.{src}",
        src.replace("_", " ").title() if src else "IBAN",
    )
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


def _display_diag_text(value: str | None, *, fallback_key: str = "") -> str:
    s = str(value or "").strip()
    if not s:
        return tr(fallback_key) if fallback_key else ""
    if s.count(".") >= 2:
        return tr_or_code(s, tr(fallback_key) if fallback_key else s)
    return s


def candidate_menu_tooltip(cand: dict[str, Any], *, max_len: int = 200) -> str:
    parts: list[str] = []
    ctx = str(cand.get("context") or "").strip()
    if ctx:
        parts.append(tr("field.tooltip.pdf_context", context=ctx))

    extraction_method = str(cand.get("extraction_method") or "").strip()
    extraction_method_nl = str(cand.get("extraction_method_nl") or "").strip()
    if extraction_method_nl:
        parts.append(tr("field.tooltip.method", method=_display_diag_text(extraction_method_nl)))
    elif extraction_method:
        parts.append(
            tr(
                "field.tooltip.method",
                method=tr_or_code(
                    f"field.extraction_method.{extraction_method}",
                    tr("field.extraction_method._default"),
                ),
            )
        )

    label_reason = str(cand.get("label_reason_nl") or cand.get("label_reason") or "").strip()
    if label_reason:
        parts.append(tr("field.tooltip.explanation", reason=label_reason))

    context_hint = str(cand.get("context_hint_nl") or cand.get("context_hint") or "").strip()
    if context_hint:
        if context_hint.count(".") >= 2:
            hint_text = _display_diag_text(context_hint, fallback_key="field.context_hint._default")
        else:
            hint_text = tr_or_code(
                f"field.context_hint.{context_hint}",
                tr("field.context_hint._default"),
            )
        parts.append(tr("field.tooltip.location", hint=hint_text))

    parse_path = str(cand.get("parse_path") or "").strip()
    if parse_path:
        parts.append(tr("field.tooltip.parse_path", path=parse_path))

    raw_detected = cand.get("raw_detected")
    if raw_detected is not None and str(raw_detected).strip():
        parts.append(tr("field.tooltip.raw_detected", value=str(raw_detected).strip()))

    normalized_iso = cand.get("normalized_iso")
    if normalized_iso is not None and str(normalized_iso).strip():
        parts.append(tr("field.tooltip.normalized", value=str(normalized_iso).strip()))

    score_breakdown = cand.get("score_breakdown")
    if isinstance(score_breakdown, dict) and score_breakdown:
        items = []
        for k, v in score_breakdown.items():
            ks = str(k).strip()
            if not ks:
                continue
            label = tr_or_code(f"field.score_label.{ks}", tr("field.score_label._default"))
            items.append(f"{label}={v}")
        if items:
            parts.append(tr("field.tooltip.score_breakdown", lines=", ".join(items[:6])))

    tip = "\n".join(parts).strip()
    if len(tip) > max_len:
        return tip[: max_len - 3] + "..."
    return tip
