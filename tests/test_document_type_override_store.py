"""Tests for in-memory document type override store."""

from __future__ import annotations

from logic.document_type_override_store import (
    DocumentTypeOverrideStore,
    document_type_override_session_fingerprint,
    make_document_type_override,
)


def test_document_type_override_store_session_memory() -> None:
    store = DocumentTypeOverrideStore()
    override = make_document_type_override("doc-1", "invoice")
    store.upsert_override("batch-a", override)
    session = store.load_session("batch-a")
    assert session is not None
    assert len(session.overrides) == 1
    assert session.overrides[0].document_type == "invoice"

    store2 = DocumentTypeOverrideStore()
    assert store2.load_session("batch-a") is None


def test_document_type_override_fingerprint_changes() -> None:
    empty = document_type_override_session_fingerprint(None)
    session = DocumentTypeOverrideStore().upsert_override(
        "batch-a",
        make_document_type_override("doc-1", "credit_note"),
    )
    nonempty = document_type_override_session_fingerprint(session)
    assert empty != nonempty
