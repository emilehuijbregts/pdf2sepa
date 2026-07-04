"""Persisted amount override session — same architectural pattern as CreditOverrideStore.

Each AmountOverride records the user's intent to change the gross amount of a specific
document during the current app session.  The UI keeps overrides in memory only; they are
cleared when PDFs are re-read for a batch.  This module remains for tests and future use.

IMPORTANT: applying these overrides does NOT mutate _matched_invoices.  Instead,
amount_override_apply.apply_amount_overrides() returns a patched *copy* that is fed
to the engine.  The original _matched_invoices list is never touched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from logic.payment_decisions import now_utc_iso, stable_hash


@dataclass(frozen=True)
class AmountOverride:
    document_id: str
    old_amount: Decimal
    new_amount: Decimal
    reason: str
    created_at: str


@dataclass(frozen=True)
class AmountOverrideSession:
    batch_key: str
    overrides: tuple[AmountOverride, ...]
    history: tuple[dict[str, Any], ...]


def amount_override_session_fingerprint(session: AmountOverrideSession | None) -> str:
    if session is None or not session.overrides:
        return stable_hash({"amount_overrides": []})
    payload = [
        {
            "document_id": o.document_id,
            "new_amount": str(o.new_amount),
        }
        for o in session.overrides
    ]
    return stable_hash({"amount_overrides": payload})


def _override_to_dict(o: AmountOverride) -> dict[str, Any]:
    return {
        "document_id": o.document_id,
        "old_amount": str(o.old_amount),
        "new_amount": str(o.new_amount),
        "reason": o.reason,
        "created_at": o.created_at,
    }


def _override_from_dict(raw: dict[str, Any]) -> AmountOverride | None:
    try:
        return AmountOverride(
            document_id=str(raw.get("document_id") or ""),
            old_amount=Decimal(str(raw.get("old_amount") or "0")),
            new_amount=Decimal(str(raw.get("new_amount") or "0")),
            reason=str(raw.get("reason") or ""),
            created_at=str(raw.get("created_at") or ""),
        )
    except Exception:
        return None


def _session_from_storage(batch_key: str, raw: dict[str, Any]) -> AmountOverrideSession:
    overrides: list[AmountOverride] = []
    for item in raw.get("overrides") or []:
        if isinstance(item, dict):
            o = _override_from_dict(item)
            if o is not None and o.document_id:
                overrides.append(o)
    history: list[dict[str, Any]] = []
    for item in raw.get("history") or []:
        if isinstance(item, dict):
            history.append(dict(item))
    return AmountOverrideSession(
        batch_key=batch_key,
        overrides=tuple(overrides),
        history=tuple(history),
    )


@dataclass
class AmountOverrideStore:
    """Persisted amount overrides per batch key."""

    path: Any
    version: int = 1

    def load_applicable_session(
        self,
        batch_key: str,
        document_ids: set[str],
    ) -> AmountOverrideSession | None:
        """Load amount overrides for batch_key plus overrides for docs in document_ids."""
        doc_ids = {str(d).strip() for d in document_ids if str(d).strip()}
        if not doc_ids:
            return self.load_session(batch_key)
        by_doc: dict[str, AmountOverride] = {}
        history: list[dict[str, Any]] = []
        data = self._read_batches()
        batches = data.get("batches") or {}
        if not isinstance(batches, dict):
            batches = {}
        for bk, raw in batches.items():
            if not isinstance(raw, dict):
                continue
            session = _session_from_storage(str(bk), raw)
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
        return AmountOverrideSession(
            batch_key=batch_key,
            overrides=tuple(by_doc.values()),
            history=tuple(history),
        )

    def load_session(self, batch_key: str) -> AmountOverrideSession | None:
        try:
            text = self.path.read_text(encoding="utf-8")
            data = json.loads(text or "{}")
            if not isinstance(data, dict):
                return None
            if int(data.get("version") or 0) != self.version:
                return None
            batches = data.get("batches")
            if not isinstance(batches, dict):
                return None
            raw = batches.get(batch_key)
            if not isinstance(raw, dict):
                return None
            return _session_from_storage(batch_key, raw)
        except FileNotFoundError:
            return None
        except Exception:
            return None

    def _read_batches(self) -> dict[str, Any]:
        try:
            text = self.path.read_text(encoding="utf-8")
            data = json.loads(text or "{}")
        except FileNotFoundError:
            return {"version": self.version, "batches": {}}
        if not isinstance(data, dict):
            return {"version": self.version, "batches": {}}
        batches = data.get("batches")
        if not isinstance(batches, dict):
            batches = {}
        return {"version": self.version, "batches": batches}

    def _write_batches(self, batches: dict[str, Any]) -> None:
        payload = {"version": self.version, "batches": batches}
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self.path.write_text(text, encoding="utf-8")

    def upsert_override(
        self,
        batch_key: str,
        override: AmountOverride,
        *,
        history_event: dict[str, Any] | None = None,
    ) -> AmountOverrideSession:
        data = self._read_batches()
        batches = data["batches"]
        raw = batches.get(batch_key)
        if not isinstance(raw, dict):
            raw = {"overrides": [], "history": []}
        session = _session_from_storage(batch_key, raw)
        by_doc = {o.document_id: o for o in session.overrides}
        by_doc[override.document_id] = override
        history = list(session.history)
        if history_event:
            history.append(history_event)
        new_session = AmountOverrideSession(
            batch_key=batch_key,
            overrides=tuple(by_doc.values()),
            history=tuple(history),
        )
        batches[batch_key] = {
            "overrides": [_override_to_dict(o) for o in new_session.overrides],
            "history": list(new_session.history),
        }
        try:
            self._write_batches(batches)
        except Exception:
            pass
        return new_session

    def remove_override(self, batch_key: str, document_id: str) -> None:
        data = self._read_batches()
        batches = data["batches"]
        raw = batches.get(batch_key)
        if not isinstance(raw, dict):
            return
        session = _session_from_storage(batch_key, raw)
        remaining = [o for o in session.overrides if o.document_id != document_id]
        history = list(session.history) + [{
            "event": "user_reset_amount",
            "document_id": document_id,
            "at": now_utc_iso(),
        }]
        batches[batch_key] = {
            "overrides": [_override_to_dict(o) for o in remaining],
            "history": history,
        }
        try:
            self._write_batches(batches)
        except Exception:
            pass
