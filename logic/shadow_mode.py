"""Shadow-mode validation: dual-pipeline comparison without affecting production output."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from logic.batch_trace import assert_legacy_output_isolation, log_shadow_result
from logic.credit_settlement import document_id
from logic.engine_result import EngineResult
from logic.payment_decisions import stable_hash
from logic.payment_engine import batch_requires_settlement, calculate_payments
from logic.settlement_call_guard import allocation_edges_from_result
from ui.settlement_table import payment_stub_from_group

_ACCEPTED_STATUSES = frozenset({"matched", "new", "confirmed", "reviewed"})

_COMPARE_FIELDS = ("amount", "iban", "supplier_name")


@dataclass(frozen=True)
class ShadowReport:
    batch_id: str
    batch_type: Literal["no-credit", "credit"]
    production_pipeline: str
    legacy_rows: int
    settlement_rows: int
    review_docs: int
    pipeline_match: bool
    diffs: tuple[str, ...] = ()
    status: Literal["PASS", "FAIL"] = "PASS"
    extra: dict[str, str] = field(default_factory=dict)


def batch_id_from_invoices(invoices: list[dict[str, Any]]) -> str:
    parts = [
        stable_hash(
            {
                "source_file": str(inv.get("source_file") or ""),
                "invoice_number": str(inv.get("invoice_number") or ""),
                "type": str(inv.get("type") or "invoice"),
            }
        )
        for inv in sorted(
            invoices,
            key=lambda x: (str(x.get("source_file") or ""), str(x.get("invoice_number") or "")),
        )
    ]
    return stable_hash({"docs": parts})[:16]


def _doc_key(doc: dict[str, Any]) -> str:
    src = str(doc.get("source_file") or doc.get("_source_file") or "").strip()
    if src:
        return src
    inv_no = str(doc.get("invoice_number") or "").strip()
    if inv_no:
        return f"inv:{inv_no}"
    return stable_hash({"anon": id(doc)})[:12]


def _decision_status(doc: dict[str, Any]) -> str:
    decision = doc.get("decision")
    if isinstance(decision, dict):
        return str(decision.get("status") or "")
    return ""


def _amount_value(doc: dict[str, Any]) -> Decimal | None:
    amount = doc.get("amount")
    if amount is None:
        return None
    try:
        return Decimal(str(amount))
    except Exception:
        return None


def _accepted_invoices(invoices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [inv for inv in invoices if inv.get("match_status") in _ACCEPTED_STATUSES]


def _settlement_fingerprint(result: EngineResult) -> tuple[Any, ...]:
    groups: list[tuple[Any, ...]] = []
    for group in sorted(result.settlement_groups, key=lambda g: str(g.get("group_id") or "")):
        alloc_statuses = tuple(
            sorted(str(a.get("status") or "") for a in (group.get("credit_allocation") or []))
        )
        groups.append(
            (
                group.get("group_id"),
                str(group.get("final_amount_due")),
                bool(group.get("exportable")),
                alloc_statuses,
            )
        )
    return (len(result.settlement_groups), tuple(groups))


def _member_document_ids(result: EngineResult) -> set[str]:
    ids: set[str] = set()
    for group in result.settlement_groups:
        for doc in group.get("member_documents") or []:
            if isinstance(doc, dict):
                doc_id = str(doc.get("document_id") or "").strip()
                if doc_id:
                    ids.add(doc_id)
    return ids


def _review_document_ids(review_documents: list[dict[str, Any]]) -> set[str]:
    return {document_id({"raw": doc}) for doc in review_documents}


def _compare_no_credit_parity(
    production: EngineResult,
    shadow: EngineResult,
) -> tuple[bool, tuple[str, ...]]:
    diffs: list[str] = []

    if production.pipeline != "legacy":
        diffs.append("production_not_legacy")
    if production.legacy_payments is None:
        diffs.append("missing_legacy_payments")

    assert_legacy_output_isolation(production)

    legacy_rows = len(production.legacy_payments or [])
    settlement_rows = len(shadow.settlement_groups)
    if legacy_rows != settlement_rows:
        diffs.append("grouping_mismatch")

    if len(production.review_documents) != len(shadow.review_documents):
        diffs.append("review_leakage")

    edges = allocation_edges_from_result(shadow.settlement_groups)
    if edges > 0:
        diffs.append("allocation_graph_exists")

    legacy_by_key = {_doc_key(p): p for p in (production.legacy_payments or [])}
    shadow_by_key = {
        _doc_key(payment_stub_from_group(g)): payment_stub_from_group(g)
        for g in shadow.settlement_groups
    }

    for key in sorted(set(legacy_by_key) | set(shadow_by_key)):
        legacy_doc = legacy_by_key.get(key)
        shadow_doc = shadow_by_key.get(key)
        if legacy_doc is None:
            diffs.append(f"row_extra_in_shadow: {key}")
            continue
        if shadow_doc is None:
            diffs.append(f"row_missing: {key}")
            continue
        for field_name in _COMPARE_FIELDS:
            legacy_val = legacy_doc.get(field_name)
            shadow_val = shadow_doc.get(field_name)
            if field_name == "amount":
                if _amount_value(legacy_doc) != _amount_value(shadow_doc):
                    diffs.append(f"amount_mismatch: {key}")
            elif str(legacy_val or "") != str(shadow_val or ""):
                diffs.append(f"{field_name}_mismatch: {key}")
        if _decision_status(legacy_doc) != _decision_status(shadow_doc):
            diffs.append(f"decision_mismatch: {key}")

    return (not diffs, tuple(diffs))


def _check_credit_determinism(
    invoices: list[dict[str, Any]],
    *,
    override_session=None,
    session_date=None,
) -> tuple[bool, tuple[str, ...], int]:
    runs = [
        calculate_payments(
            copy.deepcopy(invoices),
            override_session=override_session,
            session_date=session_date,
        )
        for _ in range(3)
    ]
    fingerprints = [_settlement_fingerprint(r) for r in runs]
    diffs: list[str] = []
    if fingerprints[0] != fingerprints[1] or fingerprints[1] != fingerprints[2]:
        diffs.append("determinism_mismatch")
    return (not diffs, tuple(diffs), len(runs[0].settlement_groups))


def _check_credit_coverage(
    invoices: list[dict[str, Any]],
    result: EngineResult,
) -> tuple[bool, tuple[str, ...]]:
    diffs: list[str] = []
    accepted = _accepted_invoices(invoices)
    in_groups = _member_document_ids(result)
    in_review = _review_document_ids(result.review_documents)

    seen_in_groups: set[str] = set()
    for group in result.settlement_groups:
        for doc in group.get("member_documents") or []:
            if not isinstance(doc, dict):
                continue
            doc_id = str(doc.get("document_id") or "").strip()
            if not doc_id:
                continue
            if doc_id in seen_in_groups:
                diffs.append(f"duplicate_document: {doc_id}")
            seen_in_groups.add(doc_id)

    for inv in accepted:
        doc_id = document_id({"raw": inv})
        if doc_id not in in_groups and doc_id not in in_review:
            diffs.append(f"coverage_missing: {doc_id}")

    return (not diffs, tuple(diffs))


def run_shadow_validation(
    invoices: list[dict[str, Any]],
    production: EngineResult,
    *,
    batch_id: str | None = None,
    override_session=None,
    session_date=None,
    log: bool = True,
) -> ShadowReport:
    """Run shadow validation; production output is never mutated."""
    bid = batch_id or batch_id_from_invoices(invoices)
    is_credit = batch_requires_settlement(invoices)

    if not is_credit:
        shadow = calculate_payments(
            copy.deepcopy(invoices),
            override_session=override_session,
            session_date=session_date,
            force_pipeline="settlement",
        )
        pipeline_match, diffs = _compare_no_credit_parity(production, shadow)
        report = ShadowReport(
            batch_id=bid,
            batch_type="no-credit",
            production_pipeline=production.pipeline,
            legacy_rows=len(production.legacy_payments or []),
            settlement_rows=len(shadow.settlement_groups),
            review_docs=len(production.review_documents),
            pipeline_match=pipeline_match,
            diffs=diffs,
            status="PASS" if pipeline_match else "FAIL",
        )
    else:
        det_ok, det_diffs, settlement_rows = _check_credit_determinism(
            invoices,
            override_session=override_session,
            session_date=session_date,
        )
        cov_ok, cov_diffs = _check_credit_coverage(invoices, production)
        all_diffs = det_diffs + cov_diffs
        report = ShadowReport(
            batch_id=bid,
            batch_type="credit",
            production_pipeline=production.pipeline,
            legacy_rows=0,
            settlement_rows=settlement_rows,
            review_docs=len(production.review_documents),
            pipeline_match=det_ok and cov_ok,
            diffs=all_diffs,
            status="PASS" if (det_ok and cov_ok) else "FAIL",
            extra={
                "determinism": "PASS" if det_ok else "FAIL",
                "coverage": "PASS" if cov_ok else "FAIL",
            },
        )

    if log:
        log_shadow_result(report)
    return report


def shadow_mode_enabled() -> bool:
    import os

    return os.environ.get("PDF2SEPA_SHADOW_MODE", "").strip() in ("1", "true", "yes")
