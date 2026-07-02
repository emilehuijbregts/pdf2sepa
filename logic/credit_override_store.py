"""Persisted credit-to-invoice override session (separate from settlement SSOT)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
from typing import Any, Literal

from logic.payment_decisions import now_utc_iso, stable_hash

OverrideAction = Literal["detach", "reassign", "allocation_adjust"]


@dataclass(frozen=True)
class CreditOverrideAllocation:
    invoice_document_id: str
    invoice_number: str
    amount_applied: Decimal


@dataclass(frozen=True)
class CreditOverride:
    credit_document_id: str
    action: OverrideAction
    target_invoice_ids: tuple[str, ...]
    allocations: tuple[CreditOverrideAllocation, ...]
    created_at: str
    reason: str


@dataclass(frozen=True)
class OverrideSession:
    batch_key: str
    overrides: tuple[CreditOverride, ...]
    history: tuple[dict[str, Any], ...]


def override_session_fingerprint(session: OverrideSession | None) -> str:
    if session is None or not session.overrides:
        return stable_hash({"overrides": []})
    payload = [
        {
            "credit_document_id": o.credit_document_id,
            "action": o.action,
            "target_invoice_ids": list(o.target_invoice_ids),
            "allocations": [
                {
                    "invoice_document_id": a.invoice_document_id,
                    "amount_applied": str(a.amount_applied),
                }
                for a in o.allocations
            ],
        }
        for o in session.overrides
    ]
    return stable_hash({"overrides": payload})


def _allocation_to_dict(a: CreditOverrideAllocation) -> dict[str, Any]:
    return {
        "invoice_document_id": a.invoice_document_id,
        "invoice_number": a.invoice_number,
        "amount_applied": str(a.amount_applied),
    }


def _allocation_from_dict(raw: dict[str, Any]) -> CreditOverrideAllocation | None:
    try:
        return CreditOverrideAllocation(
            invoice_document_id=str(raw.get("invoice_document_id") or ""),
            invoice_number=str(raw.get("invoice_number") or ""),
            amount_applied=Decimal(str(raw.get("amount_applied") or "0")),
        )
    except Exception:
        return None


def _override_to_dict(o: CreditOverride) -> dict[str, Any]:
    return {
        "credit_document_id": o.credit_document_id,
        "action": o.action,
        "target_invoice_ids": list(o.target_invoice_ids),
        "allocations": [_allocation_to_dict(a) for a in o.allocations],
        "created_at": o.created_at,
        "reason": o.reason,
    }


def _override_from_dict(raw: dict[str, Any]) -> CreditOverride | None:
    action = str(raw.get("action") or "")
    if action not in ("detach", "reassign", "allocation_adjust"):
        return None
    allocs: list[CreditOverrideAllocation] = []
    for item in raw.get("allocations") or []:
        if isinstance(item, dict):
            a = _allocation_from_dict(item)
            if a is not None:
                allocs.append(a)
    return CreditOverride(
        credit_document_id=str(raw.get("credit_document_id") or ""),
        action=action,  # type: ignore[arg-type]
        target_invoice_ids=tuple(str(x) for x in (raw.get("target_invoice_ids") or []) if str(x).strip()),
        allocations=tuple(allocs),
        created_at=str(raw.get("created_at") or ""),
        reason=str(raw.get("reason") or ""),
    )


def _session_from_storage(batch_key: str, raw: dict[str, Any]) -> OverrideSession:
    overrides: list[CreditOverride] = []
    for item in raw.get("overrides") or []:
        if isinstance(item, dict):
            o = _override_from_dict(item)
            if o is not None and o.credit_document_id:
                overrides.append(o)
    history: list[dict[str, Any]] = []
    for item in raw.get("history") or []:
        if isinstance(item, dict):
            history.append(dict(item))
    return OverrideSession(
        batch_key=batch_key,
        overrides=tuple(overrides),
        history=tuple(history),
    )


@dataclass
class CreditOverrideStore:
    """Persisted credit overrides per batch key."""

    path: Any
    version: int = 1

    def load_session(self, batch_key: str) -> OverrideSession | None:
        try:
            p = self.path
            text = p.read_text(encoding="utf-8")  # type: ignore[attr-defined]
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
        p = self.path
        try:
            text = p.read_text(encoding="utf-8")  # type: ignore[attr-defined]
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
        p = self.path
        payload = {"version": self.version, "batches": batches}
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        try:
            p.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[attr-defined]
        except Exception:
            pass
        p.write_text(text, encoding="utf-8")  # type: ignore[attr-defined]

    def upsert_override(
        self,
        batch_key: str,
        override: CreditOverride,
        *,
        history_event: dict[str, Any] | None = None,
    ) -> OverrideSession:
        data = self._read_batches()
        batches = data["batches"]
        raw = batches.get(batch_key)
        if not isinstance(raw, dict):
            raw = {"overrides": [], "history": []}
        session = _session_from_storage(batch_key, raw)
        by_credit = {o.credit_document_id: o for o in session.overrides}
        by_credit[override.credit_document_id] = override
        history = list(session.history)
        if history_event:
            history.append(history_event)
        new_session = OverrideSession(
            batch_key=batch_key,
            overrides=tuple(by_credit.values()),
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

    def remove_override(
        self,
        batch_key: str,
        credit_document_id: str,
        *,
        history_event: dict[str, Any] | None = None,
    ) -> OverrideSession | None:
        data = self._read_batches()
        batches = data["batches"]
        raw = batches.get(batch_key)
        if not isinstance(raw, dict):
            return None
        session = _session_from_storage(batch_key, raw)
        remaining = [o for o in session.overrides if o.credit_document_id != credit_document_id]
        history = list(session.history)
        if history_event:
            history.append(history_event)
        new_session = OverrideSession(
            batch_key=batch_key,
            overrides=tuple(remaining),
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

    def clear_credit(self, batch_key: str, credit_document_id: str) -> None:
        self.remove_override(
            batch_key,
            credit_document_id,
            history_event={
                "event": "user_reset",
                "credit_document_id": credit_document_id,
                "at": now_utc_iso(),
            },
        )

    def append_history(self, batch_key: str, event: dict[str, Any]) -> None:
        data = self._read_batches()
        batches = data["batches"]
        raw = batches.get(batch_key)
        if not isinstance(raw, dict):
            raw = {"overrides": [], "history": []}
        session = _session_from_storage(batch_key, raw)
        history = list(session.history) + [event]
        batches[batch_key] = {
            "overrides": [_override_to_dict(o) for o in session.overrides],
            "history": history,
        }
        try:
            self._write_batches(batches)
        except Exception:
            pass
