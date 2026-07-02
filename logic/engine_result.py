"""Engine output contract: settlement_groups SSOT."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, TypedDict

from logic.payment_decisions import PaymentDecision


class SettlementGroupOutput(TypedDict, total=False):
    """SSOT settlement group — enige bron voor bedragen, exportability, decisions."""

    group_id: str
    supplier_name: str
    iban: str
    customer_number: str | None
    description: str
    final_amount_due: Decimal
    exportable: bool
    settlement_status: str
    decision: PaymentDecision
    breakdown: dict[str, Any]
    member_documents: list[dict[str, Any]]
    member_documents_structured: dict[str, list[dict[str, Any]]]
    credit_allocation: list[dict[str, Any]]
    ownership: dict[str, Any]
    decision_trace: dict[str, Any]
    amount_display: str
    invoice_number: str
    _source_file: str | None
    invoice_date: str | None
    invoice_date_source: str
    execution_date: str
    date_mode: str
    supplier_term_trusted: bool
    supplier_payment_term_days_raw: int
    supplier_payment_term_days_effective: int
    credit_notes_applied: list[str]
    warning: str | None
    iban_mismatch: bool
    engine_version: str


@dataclass(frozen=True)
class EngineResult:
    settlement_groups: list[SettlementGroupOutput]
    review_documents: list[dict[str, Any]]
    legacy_payments: list[dict[str, Any]] | None = None
    pipeline: str = "settlement"

    @property
    def uses_settlement(self) -> bool:
        return self.legacy_payments is None
