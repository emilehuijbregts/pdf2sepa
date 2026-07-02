"""Assemble SettlementGroupOutput from per-invoice drafts and settlement groups."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from logic.credit_settlement import SettlementGroup, document_id
from logic.payment_decisions import (
    DECISION_EXCLUDED,
    DECISION_INCLUDED,
    DECISION_NEEDS_REVIEW,
    REASON_EXPORT_ALLOWED,
    REASON_INVALID_IBAN,
    REASON_MISSING_IBAN,
    PaymentDecision,
    build_decision,
)
from logic.settlement_payments import (
    SETTLEMENT_ZERO_AMOUNT,
    build_settlement_group_output,
    group_decision_from_invoice_decisions,
)

_MONEY_QUANT = Decimal("0.01")


class OwnershipIndex:
    """Build-time ownership guard for settlement graph nodes."""

    def __init__(self) -> None:
        self._owner_by_node: dict[str, tuple[str, str]] = {}
        self._sealed = False

    def _assert_open(self) -> None:
        if self._sealed:
            raise RuntimeError("ownership index is sealed")

    def _add(self, bucket: str, owner_id: str, node_id: str) -> None:
        self._assert_open()
        node = str(node_id or "").strip()
        if not node:
            return
        existing = self._owner_by_node.get(node)
        if existing is not None:
            raise ValueError(
                f"settlement node {node} already owned by {existing[0]}:{existing[1]}"
            )
        self._owner_by_node[node] = (bucket, owner_id)

    def add_group_member(self, group_id: str, document_id: str) -> None:
        self._add("group", group_id, document_id)

    def add_review_output(self, group_id: str, document_id: str) -> None:
        self._add("review", group_id, document_id)

    def validate_complete(self, expected_node_ids: set[str]) -> None:
        missing = sorted(n for n in expected_node_ids if n and n not in self._owner_by_node)
        if missing:
            raise ValueError(f"settlement ownership missing nodes: {', '.join(missing)}")

    def seal(self) -> None:
        self._sealed = True

    @property
    def sealed(self) -> bool:
        return self._sealed

    def summary(self) -> dict[str, Any]:
        return {
            "sealed": self._sealed,
            "nodes": {
                node: {"bucket": bucket, "owner_id": owner_id}
                for node, (bucket, owner_id) in sorted(self._owner_by_node.items())
            },
        }


@dataclass
class InvoiceDraft:
    doc_id: str
    inv_raw: dict
    te_betalen_dec: Decimal
    decision: PaymentDecision
    decision_trace: dict[str, Any]
    credit_notes_applied: list[str]
    warning: str | None
    iban: str
    iban_valid: bool
    trusted: bool
    raw_term: int
    effective_term: int
    inv_date_out: str | None
    invoice_date_source: str


def _member_documents_for_group(
    group: SettlementGroup,
    drafts: dict[str, InvoiceDraft],
    raw_by_id: dict[str, dict],
) -> list[dict[str, Any]]:
    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in group.invoices + group.credits:
        if line.document_id in seen:
            continue
        seen.add(line.document_id)
        raw = raw_by_id.get(line.document_id)
        if raw is not None:
            members.append({"raw": raw, "document_id": line.document_id})
        elif line.document_id in drafts:
            members.append({"raw": drafts[line.document_id].inv_raw, "document_id": line.document_id})
    return members


def _primary_draft(group: SettlementGroup, drafts: dict[str, InvoiceDraft]) -> InvoiceDraft | None:
    inv_drafts = [drafts[line.document_id] for line in group.invoices if line.document_id in drafts]
    if not inv_drafts:
        return None
    return max(inv_drafts, key=lambda d: d.te_betalen_dec)


def build_groups_from_drafts(
    settlement_groups: tuple[SettlementGroup, ...],
    drafts: dict[str, InvoiceDraft],
    raw_by_id: dict[str, dict],
    *,
    refund_doc_ids: set[str],
    execution_date: str,
    engine_version: str,
    override_history: list[dict[str, Any]] | None = None,
) -> list[dict]:
    """Build SettlementGroupOutput list from settlement topology + invoice drafts."""
    outputs: list[dict] = []
    ownership = OwnershipIndex()
    expected_nodes: set[str] = {
        line.document_id
        for group in settlement_groups
        for line in (*group.invoices, *group.credits)
    }
    for group in settlement_groups:
        primary = _primary_draft(group, drafts)
        inv_drafts = [drafts[line.document_id] for line in group.invoices if line.document_id in drafts]
        member_docs = _member_documents_for_group(group, drafts, raw_by_id)

        if not inv_drafts and group.credits:
            for line in group.credits:
                ownership.add_review_output(group.group_id, line.document_id)
            credit_notes = sorted({line.invoice_number for line in group.credits if line.invoice_number})
            group_decision = build_decision(
                status=DECISION_NEEDS_REVIEW,
                reason_code="credit_match_needs_review",
                reason_detail="unallocated_credit",
                editable=True,
                requires_rerun=True,
                causal_inputs=["credit_allocation"],
                input_fields={"row_id": group.group_id},
            )
            out = build_settlement_group_output(
                group,
                final_amount_due=Decimal("0.00"),
                iban="",
                decision=group_decision,
                decision_trace={"ownership_bucket": "review"},
                member_documents=member_docs,
                credit_notes_applied=credit_notes,
                primary_invoice_number="",
                primary_source_file=None,
                invoice_date=None,
                invoice_date_source="missing",
                execution_date=execution_date,
                supplier_term_trusted=False,
                raw_term=0,
                effective_term=0,
                warning="credit_match_needs_review",
                iban_mismatch=False,
                engine_version=engine_version,
            )
            out["exportable"] = False
            outputs.append(out)
            continue

        for line in (*group.invoices, *group.credits):
            ownership.add_group_member(group.group_id, line.document_id)

        if group.status == "refund_required":
            final_due = group.final_amount_due
            refund_detail = f"refund_amount={group.refund_amount}"
        else:
            final_due = sum((d.te_betalen_dec for d in inv_drafts), start=Decimal("0.00")).quantize(_MONEY_QUANT)
            refund_detail = None

        iban = primary.iban if primary else ""
        iban_valid = primary.iban_valid if primary else False
        decisions = [d.decision for d in inv_drafts] if inv_drafts else []
        if not decisions and primary is None:
            continue

        if not iban_valid and final_due > Decimal("0.00"):
            for d in inv_drafts:
                if not d.iban_valid:
                    decisions = [
                        build_decision(
                            status=DECISION_EXCLUDED,
                            reason_code=REASON_MISSING_IBAN if not d.iban else REASON_INVALID_IBAN,
                            reason_detail=None,
                            editable=True,
                            requires_rerun=True,
                            causal_inputs=["iban"],
                            input_fields={"row_id": d.doc_id},
                        )
                    ]
                    break

        settlement_status_override = None
        if any(document_id({"raw": d.inv_raw}) in refund_doc_ids for d in inv_drafts):
            settlement_status_override = "refund_required"

        credit_notes = sorted(
            {
                cn
                for d in inv_drafts
                for cn in d.credit_notes_applied
                if cn
            }
        )

        traces = [d.decision_trace for d in inv_drafts]
        merged_trace = traces[0] if traces else {}
        if len(traces) > 1:
            merged_trace = {**merged_trace, "merged_invoice_traces": traces[1:]}
        if override_history:
            group_credit_ids = {line.document_id for line in group.credits}
            relevant = [
                e
                for e in override_history
                if e.get("event") == "settlement_recomputed"
                or str(e.get("credit_document_id") or "") in group_credit_ids
            ]
            if relevant:
                merged_trace = {**merged_trace, "override_history": relevant}

        decision_inputs = decisions if decisions else [primary.decision] if primary else []
        if final_due > Decimal("0.00"):
            non_zero_amount_decisions = [
                d for d in decision_inputs if str(d.get("reason_code") or "") != "zero_amount"
            ]
            if non_zero_amount_decisions:
                decision_inputs = non_zero_amount_decisions

        group_decision = group_decision_from_invoice_decisions(
            decision_inputs,
            settlement_status=settlement_status_override or group.status,
            refund_detail=refund_detail,
        )
        if final_due <= Decimal("0.00") and group.status not in ("refund_required", "manual_review"):
            group_decision = group_decision_from_invoice_decisions(
                decisions,
                settlement_status=SETTLEMENT_ZERO_AMOUNT,
            )

        primary_inv_no = ""
        primary_source = None
        if primary:
            primary_inv_no = str(primary.inv_raw.get("invoice_number") or "")
            primary_source = str(primary.inv_raw.get("source_file") or "").strip() or None

        out = build_settlement_group_output(
            group,
            final_amount_due=final_due,
            iban=iban if iban_valid else (primary.iban if primary else ""),
            decision=group_decision,
            decision_trace=merged_trace,
            member_documents=member_docs,
            credit_notes_applied=credit_notes,
            primary_invoice_number=primary_inv_no,
            primary_source_file=primary_source,
            invoice_date=primary.inv_date_out if primary else None,
            invoice_date_source=primary.invoice_date_source if primary else "missing",
            execution_date=execution_date,
            supplier_term_trusted=primary.trusted if primary else False,
            raw_term=primary.raw_term if primary else 0,
            effective_term=primary.effective_term if primary else 0,
            warning=primary.warning if primary else None,
            iban_mismatch=bool(primary.inv_raw.get("iban_mismatch")) if primary else False,
            engine_version=engine_version,
        )
        if settlement_status_override == "refund_required":
            out["settlement_status"] = "refund_required"
            out["exportable"] = False
        outputs.append(out)
    ownership.validate_complete(expected_nodes)
    ownership.seal()
    ownership_summary = ownership.summary()
    for out in outputs:
        out["ownership"] = {
            **dict(out.get("ownership") or {}),
            "index": ownership_summary,
        }
        trace = dict(out.get("decision_trace") or {})
        trace["ownership_index_sealed"] = ownership.sealed
        out["decision_trace"] = trace
    return outputs


def build_singleton_group_from_draft(
    draft: InvoiceDraft,
    *,
    execution_date: str,
    engine_version: str,
    supplier_name: str,
) -> dict:
    """Singleton invoice without credit settlement group."""
    from logic.credit_settlement import SettlementGroup, SettlementLine, _group_id_from_doc_ids

    gid = _group_id_from_doc_ids([draft.doc_id])
    gross = draft.te_betalen_dec.copy_abs() if draft.te_betalen_dec > 0 else draft.inv_raw.get("amount")
    try:
        from logic.payment_amounts import amount_to_decimal

        gross_dec = amount_to_decimal(gross).copy_abs() if gross is not None else Decimal("0.00")
    except (ValueError, TypeError):
        gross_dec = Decimal("0.00")

    line = SettlementLine(
        document_id=draft.doc_id,
        invoice_number=str(draft.inv_raw.get("invoice_number") or ""),
        doc_type="invoice",
        gross_amount=gross_dec,
        amount_applied=Decimal("0.00"),
    )
    group = SettlementGroup(
        group_id=gid,
        supplier_name=supplier_name,
        invoices=(line,),
        credits=(),
        invoices_total=gross_dec,
        credits_total=Decimal("0.00"),
        final_amount_due=draft.te_betalen_dec,
        status="ok",
        refund_amount=None,
        match_methods=(),
        warnings=(),
    )
    return build_settlement_group_output(
        group,
        final_amount_due=draft.te_betalen_dec,
        iban=draft.iban,
        decision=draft.decision,
        decision_trace=draft.decision_trace,
        member_documents=[{"raw": draft.inv_raw, "document_id": draft.doc_id}],
        credit_notes_applied=[],
        primary_invoice_number=str(draft.inv_raw.get("invoice_number") or ""),
        primary_source_file=str(draft.inv_raw.get("source_file") or "").strip() or None,
        invoice_date=draft.inv_date_out,
        invoice_date_source=draft.invoice_date_source,
        execution_date=execution_date,
        supplier_term_trusted=draft.trusted,
        raw_term=draft.raw_term,
        effective_term=draft.effective_term,
        warning=draft.warning,
        iban_mismatch=bool(draft.inv_raw.get("iban_mismatch")),
        engine_version=engine_version,
    )
