"""Settlement status badge labels (render-only)."""

from __future__ import annotations

_BADGE_NL = {
    "ok": "OK",
    "zero_amount": "Volledig verrekend",
    "manual_review": "Controle credit",
    "refund_required": "Terugbetaling",
    "detached": "Losgekoppeld",
}


def settlement_badge_nl(status: str) -> str:
    return _BADGE_NL.get(str(status or "").strip(), str(status or ""))


def settlement_badge_for_group(group: dict) -> str:
    """Render-only badge; detached = credit-only manual_review group."""
    status = str(group.get("settlement_status") or "").strip()
    if status == "manual_review" and _is_credit_only_group(group):
        return settlement_badge_nl("detached")
    return settlement_badge_nl(status)


def _is_credit_only_group(group: dict) -> bool:
    members = group.get("member_documents") or []
    has_invoice = False
    has_credit = False
    for doc in members:
        if not isinstance(doc, dict):
            continue
        raw = doc.get("raw") if isinstance(doc.get("raw"), dict) else {}
        if str(raw.get("type") or "") == "credit_note":
            has_credit = True
        else:
            has_invoice = True
    return has_credit and not has_invoice
