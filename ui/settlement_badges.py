"""Settlement status badge labels (render-only)."""

from __future__ import annotations

from ui.i18n import tr

_BADGE_KEYS = {
    "ok": "settlement.badge.ok",
    "zero_amount": "settlement.badge.zero_amount",
    "manual_review": "settlement.badge.manual_review",
    "refund_required": "settlement.badge.refund_required",
    "detached": "settlement.badge.detached",
}


def settlement_badge_label(status: str) -> str:
    key = _BADGE_KEYS.get(str(status or "").strip())
    if key:
        return tr(key)
    return str(status or "")


def settlement_badge_nl(status: str) -> str:
    """Backward-compatible alias for settlement_badge_label."""
    return settlement_badge_label(status)


def settlement_badge_for_group(group: dict) -> str:
    """Render-only badge; detached = credit-only manual_review group."""
    status = str(group.get("settlement_status") or "").strip()
    if status == "manual_review" and _is_credit_only_group(group):
        return settlement_badge_label("detached")
    return settlement_badge_label(status)


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
