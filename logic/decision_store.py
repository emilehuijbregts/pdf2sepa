"""Append-only in-memory decision reconciliation store."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Literal

from logic.payment_decisions import now_utc_iso, stable_hash

RunState = Literal["running", "committed", "failed"]


@dataclass(frozen=True)
class DecisionRunRecord:
    run_id: str
    parent_run_id: str | None
    timestamp: str
    input_snapshot_hash: str
    decision_map: dict[str, dict[str, Any]]
    run_global_hash: str
    engine_run_state: RunState
    xml_output_hash: str | None = None


class DecisionStore:
    """Runtime reconciliation layer.

    The store is append-only for records. The only mutable state is:
    - latest_committed_run_id pointer
    - pending run scratchpad during running state
    """

    def __init__(self) -> None:
        self._runs: list[DecisionRunRecord] = []
        self.latest_committed_run_id: str | None = None

    def all_runs(self) -> list[DecisionRunRecord]:
        return list(self._runs)

    def get_run(self, run_id: str) -> DecisionRunRecord | None:
        for run in self._runs:
            if run.run_id == run_id:
                return run
        return None

    def begin_run(
        self,
        *,
        run_id: str,
        input_snapshot_hash: str,
        decision_map: dict[str, dict[str, Any]],
    ) -> DecisionRunRecord:
        parent = self.latest_committed_run_id
        rec = DecisionRunRecord(
            run_id=run_id,
            parent_run_id=parent,
            timestamp=now_utc_iso(),
            input_snapshot_hash=input_snapshot_hash,
            decision_map=decision_map,
            run_global_hash="",
            engine_run_state="running",
            xml_output_hash=None,
        )
        self._runs.append(rec)
        return rec

    def commit_run(self, run_id: str, *, xml_output_hash: str | None = None) -> DecisionRunRecord:
        rec = self.get_run(run_id)
        if rec is None:
            raise ValueError(f"unknown run_id: {run_id}")
        payload = {
            "run_id": rec.run_id,
            "parent_run_id": rec.parent_run_id,
            "input_snapshot_hash": rec.input_snapshot_hash,
            "decision_map": rec.decision_map,
            "xml_output_hash": xml_output_hash or "",
        }
        committed = DecisionRunRecord(
            run_id=rec.run_id,
            parent_run_id=rec.parent_run_id,
            timestamp=rec.timestamp,
            input_snapshot_hash=rec.input_snapshot_hash,
            decision_map=rec.decision_map,
            run_global_hash=stable_hash(payload),
            engine_run_state="committed",
            xml_output_hash=xml_output_hash,
        )
        self._replace(run_id, committed)
        self.latest_committed_run_id = run_id
        return committed

    def fail_run(self, run_id: str) -> DecisionRunRecord:
        rec = self.get_run(run_id)
        if rec is None:
            raise ValueError(f"unknown run_id: {run_id}")
        failed = DecisionRunRecord(
            run_id=rec.run_id,
            parent_run_id=rec.parent_run_id,
            timestamp=rec.timestamp,
            input_snapshot_hash=rec.input_snapshot_hash,
            decision_map=rec.decision_map,
            run_global_hash=rec.run_global_hash,
            engine_run_state="failed",
            xml_output_hash=rec.xml_output_hash,
        )
        self._replace(run_id, failed)
        return failed

    def committed_decision_map(self, run_id: str | None = None) -> dict[str, dict[str, Any]]:
        rid = run_id or self.latest_committed_run_id
        if not rid:
            return {}
        rec = self.get_run(rid)
        if rec is None or rec.engine_run_state != "committed":
            return {}
        return rec.decision_map

    def _replace(self, run_id: str, new_record: DecisionRunRecord) -> None:
        for idx, existing in enumerate(self._runs):
            if existing.run_id == run_id:
                self._runs[idx] = new_record
                return
        raise ValueError(f"unknown run_id: {run_id}")


@dataclass
class UserApprovalStore:
    """Persisted user approvals per batch key.

    Stored under the configured user data directory (same root as settings/suppliers).
    This is intentionally simple JSON so it can be inspected/edited if needed.
    """

    path: Any  # Path-like; keep loose to avoid importing pathlib here.
    version: int = 1

    def load_batch(self, batch_key: str) -> dict[str, dict[str, Any]]:
        try:
            p = self.path
            text = p.read_text(encoding="utf-8")  # type: ignore[attr-defined]
            data = json.loads(text or "{}")
            if not isinstance(data, dict):
                return {}
            if int(data.get("version") or 0) != self.version:
                return {}
            batches = data.get("batches")
            if not isinstance(batches, dict):
                return {}
            raw = batches.get(batch_key)
            if not isinstance(raw, dict):
                return {}
            out: dict[str, dict[str, Any]] = {}
            for rid, dec in raw.items():
                if isinstance(rid, str) and isinstance(dec, dict):
                    out[rid] = dec
            return out
        except FileNotFoundError:
            return {}
        except Exception:
            return {}

    def clear_batch(self, batch_key: str) -> None:
        """Remove all approvals for a batch key (best-effort)."""
        p = self.path
        try:
            try:
                existing_txt = p.read_text(encoding="utf-8")  # type: ignore[attr-defined]
                data = json.loads(existing_txt or "{}")
            except FileNotFoundError:
                return
            if not isinstance(data, dict):
                return
            if int(data.get("version") or 0) != self.version:
                return
            batches = data.get("batches")
            if not isinstance(batches, dict):
                return
            if batch_key not in batches:
                return
            batches.pop(batch_key, None)
            payload = {"version": self.version, "batches": batches}
            text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            try:
                p.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[attr-defined]
            except Exception:
                pass
            p.write_text(text, encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            return

    def remove_from_batch(self, batch_key: str, row_ids: set[str]) -> None:
        """Verwijder goedkeuringen voor gegeven row_ids uit een batch."""
        if not row_ids:
            return
        p = self.path
        try:
            try:
                existing_txt = p.read_text(encoding="utf-8")  # type: ignore[attr-defined]
                data = json.loads(existing_txt or "{}")
            except FileNotFoundError:
                return
            if not isinstance(data, dict):
                return
            batches = data.get("batches")
            if not isinstance(batches, dict):
                return
            merged = batches.get(batch_key)
            if not isinstance(merged, dict):
                return
            for rid in row_ids:
                merged.pop(rid, None)
            batches[batch_key] = merged
            payload = {"version": self.version, "batches": batches}
            text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            try:
                p.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[attr-defined]
            except Exception:
                pass
            p.write_text(text, encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            return

    def upsert_batch(self, batch_key: str, approvals: dict[str, dict[str, Any]]) -> None:
        p = self.path
        try:
            try:
                existing_txt = p.read_text(encoding="utf-8")  # type: ignore[attr-defined]
                data = json.loads(existing_txt or "{}")
            except FileNotFoundError:
                data = {}
            if not isinstance(data, dict):
                data = {}
            batches = data.get("batches")
            if not isinstance(batches, dict):
                batches = {}
            merged = dict(batches.get(batch_key) or {}) if isinstance(batches.get(batch_key), dict) else {}
            for rid, dec in approvals.items():
                if isinstance(rid, str) and isinstance(dec, dict):
                    merged[rid] = dec
            batches[batch_key] = merged
            payload = {"version": self.version, "batches": batches}
            text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            # Ensure parent exists if path is a pathlib.Path.
            try:
                p.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[attr-defined]
            except Exception:
                pass
            p.write_text(text, encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            # Best-effort persistence: approval should still work in-session.
            return
