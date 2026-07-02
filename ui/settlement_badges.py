"""Settlement status badge labels (render-only)."""

from __future__ import annotations

_BADGE_NL = {
    "ok": "OK",
    "zero_amount": "Volledig verrekend",
    "manual_review": "Controle credit",
    "refund_required": "Terugbetaling",
}


def settlement_badge_nl(status: str) -> str:
    return _BADGE_NL.get(str(status or "").strip(), str(status or ""))
