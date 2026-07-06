"""In-memory document-type override session (invoice vs credit_note per document)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from logic.payment_decisions import now_utc_iso, stable_hash

DocumentType = Literal["invoice", "credit_note"]


@dataclass(frozen=True)
class DocumentTypeOverride:
    document_id: str
    document_type: DocumentType
    created_at: str
    reason: str


@dataclass(frozen=True)
class DocumentTypeOverrideSession:
    batch_key: str
    overrides: tuple[DocumentTypeOverride, ...]
    history: tuple[dict[str, Any], ...]


def document_type_override_session_fingerprint(session: DocumentTypeOverrideSession | None) -> str:
    if session is None or not session.overrides:
        return stable_hash({"document_type_overrides": []})
    payload = [
        {
            "document_id": o.document_id,
            "document_type": o.document_type,
        }
        for o in session.overrides
    ]
    return stable_hash({"document_type_overrides": payload})


def _override_to_dict(o: DocumentTypeOverride) -> dict[str, Any]:
    return {
        "document_id": o.document_id,
        "document_type": o.document_type,
        "created_at": o.created_at,
        "reason": o.reason,
    }


def _override_from_dict(raw: dict[str, Any]) -> DocumentTypeOverride | None:
    doc_type = str(raw.get("document_type") or "")
    if doc_type not in ("invoice", "credit_note"):
        return None
    doc_id = str(raw.get("document_id") or "").strip()
    if not doc_id:
        return None
    return DocumentTypeOverride(
        document_id=doc_id,
        document_type=doc_type,  # type: ignore[arg-type]
        created_at=str(raw.get("created_at") or ""),
        reason=str(raw.get("reason") or ""),
    )


def _session_from_storage(batch_key: str, raw: dict[str, Any]) -> DocumentTypeOverrideSession:
    overrides: list[DocumentTypeOverride] = []
    for item in raw.get("overrides") or []:
        if isinstance(item, dict):
            o = _override_from_dict(item)
            if o is not None:
                overrides.append(o)
    history: list[dict[str, Any]] = []
    for item in raw.get("history") or []:
        if isinstance(item, dict):
            history.append(dict(item))
    return DocumentTypeOverrideSession(
        batch_key=batch_key,
        overrides=tuple(overrides),
        history=tuple(history),
    )


@dataclass
class DocumentTypeOverrideStore:
    """Session-only overrides keyed by batch; not written to disk."""

    _sessions: dict[str, DocumentTypeOverrideSession] = field(default_factory=dict)

    def load_applicable_session(
        self,
        batch_key: str,
        document_ids: set[str],
    ) -> DocumentTypeOverrideSession | None:
        doc_ids = {str(d).strip() for d in document_ids if str(d).strip()}
        by_doc: dict[str, DocumentTypeOverride] = {}
        history: list[dict[str, Any]] = []
        for bk, session in self._sessions.items():
            for o in session.overrides:
                if o.document_id in doc_ids:
                    by_doc[o.document_id] = o
            history.extend(session.history)
        current = self.load_session(batch_key)
        if current:
            for o in current.overrides:
                by_doc[o.document_id] = o
            history = list(current.history) + [h for h in history if h not in current.history]
        if not by_doc:
            return current
        return DocumentTypeOverrideSession(
            batch_key=batch_key,
            overrides=tuple(by_doc.values()),
            history=tuple(history),
        )

    def load_session(self, batch_key: str) -> DocumentTypeOverrideSession | None:
        session = self._sessions.get(batch_key)
        if session is None or not session.overrides:
            return None
        return session

    def upsert_override(
        self,
        batch_key: str,
        override: DocumentTypeOverride,
        *,
        history_event: dict[str, Any] | None = None,
    ) -> DocumentTypeOverrideSession:
        session = self._sessions.get(batch_key)
        if session is None:
            session = DocumentTypeOverrideSession(batch_key=batch_key, overrides=(), history=())
        by_doc = {o.document_id: o for o in session.overrides}
        by_doc[override.document_id] = override
        history = list(session.history)
        if history_event:
            history.append(history_event)
        new_session = DocumentTypeOverrideSession(
            batch_key=batch_key,
            overrides=tuple(by_doc.values()),
            history=tuple(history),
        )
        self._sessions[batch_key] = new_session
        return new_session

    def remove_override(self, batch_key: str, document_id: str) -> None:
        session = self._sessions.get(batch_key)
        if session is None:
            return
        remaining = [o for o in session.overrides if o.document_id != document_id]
        history = list(session.history) + [{
            "event": "user_reset_document_type",
            "document_id": document_id,
            "at": now_utc_iso(),
        }]
        if remaining:
            self._sessions[batch_key] = DocumentTypeOverrideSession(
                batch_key=batch_key,
                overrides=tuple(remaining),
                history=tuple(history),
            )
        else:
            self._sessions.pop(batch_key, None)

    def clear_batch(self, batch_key: str) -> None:
        self._sessions.pop(batch_key, None)


def make_document_type_override(
    document_id: str,
    document_type: DocumentType,
    *,
    reason: str = "user_set_document_type",
) -> DocumentTypeOverride:
    return DocumentTypeOverride(
        document_id=document_id,
        document_type=document_type,
        created_at=now_utc_iso(),
        reason=reason,
    )
