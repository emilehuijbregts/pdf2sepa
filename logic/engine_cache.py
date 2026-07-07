"""Cache for settlement engine results keyed by invoice + override fingerprints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from logic.credit_override_store import OverrideSession, override_session_fingerprint
from logic.engine_result import EngineResult
from logic.payment_decisions import stable_hash

def invoice_batch_fingerprint(invoices: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for inv in sorted(invoices, key=lambda x: str(x.get("source_file") or x.get("invoice_number") or "")):
        parts.append(
            stable_hash(
                {
                    "source_file": str(inv.get("source_file") or ""),
                    "invoice_number": str(inv.get("invoice_number") or ""),
                    "amount": str(inv.get("amount") or ""),
                    "type": str(inv.get("type") or ""),
                    "match_status": str(inv.get("match_status") or ""),
                }
            )
        )
    return stable_hash(parts)


@dataclass
class EngineCacheEntry:
    invoice_fingerprint: str
    override_fingerprint: str
    amount_override_fingerprint: str
    document_type_override_fingerprint: str
    result: EngineResult


class SettlementEngineCache:
    def __init__(self) -> None:
        self._entry: EngineCacheEntry | None = None

    def invalidate(self, reason: str = "") -> None:
        self._entry = None

    def get_or_compute(
        self,
        invoices: list[dict[str, Any]],
        override_session: OverrideSession | None,
        compute_fn: Callable[[], EngineResult],
        *,
        amount_override_fingerprint: str | None = None,
        document_type_override_fingerprint: str | None = None,
    ) -> EngineResult:
        inv_fp = invoice_batch_fingerprint(invoices)
        ov_fp = override_session_fingerprint(override_session)
        amt_fp = amount_override_fingerprint or stable_hash({"amount_overrides": []})
        doc_fp = document_type_override_fingerprint or stable_hash({"document_type_overrides": []})
        if (
            self._entry is not None
            and self._entry.invoice_fingerprint == inv_fp
            and self._entry.override_fingerprint == ov_fp
            and self._entry.amount_override_fingerprint == amt_fp
            and self._entry.document_type_override_fingerprint == doc_fp
        ):
            return self._entry.result
        result = compute_fn()
        self._entry = EngineCacheEntry(
            invoice_fingerprint=inv_fp,
            override_fingerprint=ov_fp,
            amount_override_fingerprint=amt_fp,
            document_type_override_fingerprint=doc_fp,
            result=result,
        )
        return result
