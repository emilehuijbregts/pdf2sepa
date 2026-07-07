"""Tests for SettlementEngineCache fingerprinting."""

from __future__ import annotations

from logic.document_type_override_store import document_type_override_session_fingerprint
from logic.engine_cache import SettlementEngineCache
from logic.engine_result import EngineResult


def _empty_engine_result() -> EngineResult:
    return EngineResult(settlement_groups=[], review_documents=[], legacy_payments=[])


def test_engine_cache_accepts_document_type_override_fingerprint() -> None:
    cache = SettlementEngineCache()
    invoices = [{"source_file": "a.pdf", "invoice_number": "1", "amount": "10"}]
    doc_fp = document_type_override_session_fingerprint(None)

    first = cache.get_or_compute(
        invoices,
        None,
        _empty_engine_result,
        document_type_override_fingerprint=doc_fp,
    )
    second = cache.get_or_compute(
        invoices,
        None,
        lambda: (_ for _ in ()).throw(AssertionError("should use cache")),
        document_type_override_fingerprint=doc_fp,
    )
    assert first is second
