"""Apply document-type resolution across a matched invoice batch."""

from __future__ import annotations

from typing import Any

from logic.credit_settlement import document_id
from logic.document_type_override_store import DocumentTypeOverrideSession
from logic.document_type_resolver import (
    DocumentType,
    apply_document_type_resolution,
    resolve_document_type,
)


def resolve_document_types(
    invoices: list[dict[str, Any]],
    override_session: DocumentTypeOverrideSession | None = None,
) -> list[dict[str, Any]]:
    """Resolve and attach document type metadata for each matched invoice."""
    overrides_by_id: dict[str, DocumentType] = {}
    if override_session is not None:
        for override in override_session.overrides:
            overrides_by_id[override.document_id] = override.document_type

    out: list[dict[str, Any]] = []
    for inv in invoices:
        doc_id = document_id({"raw": inv})
        user_override = overrides_by_id.get(doc_id)
        resolution = resolve_document_type(inv, user_override=user_override)
        out.append(apply_document_type_resolution(inv, resolution))
    return out
