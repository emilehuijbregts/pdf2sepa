"""SEPA export input — SSOT only, type-guarded."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from logic.engine_result import EngineResult, SettlementGroupOutput
from logic.payment_decisions import stable_hash


class SettlementExportInput:
    """Marker type: alleen exportable SettlementGroupOutput mag SEPA in."""

    def __init__(self, groups: list[SettlementGroupOutput]) -> None:
        for g in groups:
            if not isinstance(g, dict) or "group_id" not in g:
                raise TypeError("SEPA input must be SettlementGroupOutput")
            if not g.get("exportable"):
                raise ValueError(f"group {g['group_id']} is not exportable")
            if not str(g.get("description") or "").strip():
                raise ValueError(f"group {g['group_id']} has no settlement description")
            if _has_unresolved_credit(g):
                raise ValueError(f"group {g['group_id']} has unresolved credit allocation")
        self.groups = groups


def exportable_groups(result: EngineResult) -> SettlementExportInput:
    if result.legacy_payments is not None:
        return SettlementExportInput([])
    return SettlementExportInput([g for g in result.settlement_groups if g.get("exportable")])


def exportable_legacy_payments(result: EngineResult) -> list[dict[str, Any]]:
    """Exportable legacy payment dicts (1:1 invoice rows). Empty when not on legacy path."""
    if result.legacy_payments is None:
        return []
    from output.sepa_xml import exportable_payments_from_decisions

    return exportable_payments_from_decisions(list(result.legacy_payments))


def settlement_groups_to_sepa_rows(groups: list[SettlementGroupOutput]) -> list[dict]:
    """Map SSOT groups to SEPA row dicts (export layer only)."""
    rows: list[dict] = []
    for g in groups:
        description = str(g.get("description") or "").strip()
        if not description:
            raise ValueError(f"group {g.get('group_id')} has no settlement description")
        rows.append(
            {
                "supplier_name": g.get("supplier_name"),
                "iban": g.get("iban"),
                "amount": g.get("final_amount_due"),
                "description": description,
                "invoice_number": g.get("invoice_number"),
                "execution_date": g.get("execution_date"),
                "decision": g.get("decision"),
                "decision_trace": g.get("decision_trace"),
                "settlement_group_id": g.get("group_id"),
                "_source_file": g.get("_source_file"),
            }
        )
    return rows


def _has_unresolved_credit(group: SettlementGroupOutput) -> bool:
    for allocation in group.get("credit_allocation") or []:
        if not isinstance(allocation, dict):
            continue
        if str(allocation.get("status") or "").startswith("unallocated_"):
            return True
        remaining = allocation.get("remaining_balance")
        if remaining is not None and Decimal(str(remaining)) > Decimal("0.00"):
            return True
    return False


def _end_to_end_id(group: SettlementGroupOutput) -> str:
    return stable_hash(
        {
            "iban": str(group.get("iban") or ""),
            "amount": str(group.get("final_amount_due") or ""),
            "description": str(group.get("description") or ""),
            "group_id": str(group.get("group_id") or ""),
        }
    )


def validate_engine_result_for_export(
    result: EngineResult,
    *,
    batch_credit_document_ids: set[str] | None = None,
    override_credit_document_ids: set[str] | None = None,
) -> list[str]:
    """Pre-export SSOT validation. Empty list means OK."""
    if result.legacy_payments is not None:
        return []
    errors: list[str] = []
    doc_to_groups: dict[str, list[str]] = {}
    exportable: list[SettlementGroupOutput] = []

    for group in result.settlement_groups:
        gid = str(group.get("group_id") or "")
        if group.get("exportable"):
            exportable.append(group)
            amount = group.get("final_amount_due")
            if amount is None or Decimal(str(amount)) <= Decimal("0"):
                errors.append(f"exportable group {gid} has amount <= 0")
            if not str(group.get("description") or "").strip():
                errors.append(f"exportable group {gid} has no settlement description")
            if _has_unresolved_credit(group):
                errors.append(f"exportable group {gid} has unresolved credit allocation")
        for doc in group.get("member_documents") or []:
            if not isinstance(doc, dict):
                continue
            doc_id = str(doc.get("document_id") or "").strip()
            if doc_id:
                doc_to_groups.setdefault(doc_id, []).append(gid)

    for doc_id, gids in doc_to_groups.items():
        if len(gids) > 1:
            errors.append(f"document {doc_id} appears in multiple settlement groups: {', '.join(gids)}")

    seen_e2e: dict[str, str] = {}
    for group in exportable:
        e2e = _end_to_end_id(group)
        gid = str(group.get("group_id") or "")
        if e2e in seen_e2e:
            errors.append(
                f"duplicate EndToEndId for groups {seen_e2e[e2e]} and {gid}"
            )
        else:
            seen_e2e[e2e] = gid

    seen_members: set[str] = set()
    for group in exportable:
        for doc in group.get("member_documents") or []:
            if not isinstance(doc, dict):
                continue
            doc_id = str(doc.get("document_id") or "").strip()
            if not doc_id:
                continue
            if doc_id in seen_members:
                errors.append(f"duplicate member_document {doc_id} in exportable groups")
            seen_members.add(doc_id)

    if batch_credit_document_ids:
        grouped_docs: set[str] = set()
        review_docs: set[str] = set()
        for group in result.settlement_groups:
            for doc in group.get("member_documents") or []:
                if isinstance(doc, dict):
                    raw = doc.get("raw") or {}
                    if str(raw.get("type") or "") == "credit_note":
                        grouped_docs.add(str(doc.get("document_id") or ""))
        for doc in result.review_documents:
            if str(doc.get("type") or "") == "credit_note":
                src = str(doc.get("source_file") or "").strip()
                if src:
                    review_docs.add(src)
                inv_no = str(doc.get("invoice_number") or "").strip()
                if inv_no:
                    review_docs.add(f"inv:{inv_no}")
        for cid in batch_credit_document_ids:
            if cid and cid not in grouped_docs and cid not in review_docs:
                errors.append(f"orphan credit document {cid}")

    if override_credit_document_ids and batch_credit_document_ids:
        for cid in override_credit_document_ids:
            if cid and cid not in batch_credit_document_ids:
                errors.append(f"orphan override for credit {cid}")

    return errors
