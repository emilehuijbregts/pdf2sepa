"""Settlement group view model — 1:1 presentation mapping from SSOT."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from logic.engine_result import SettlementGroupOutput


@dataclass(frozen=True)
class SettlementLineVM:
    doc_type: str
    invoice_number: str
    gross_amount: str
    amount_applied: str
    remaining_balance: str


@dataclass(frozen=True)
class SettlementAllocationVM:
    credit_number: str
    invoice_number: str
    amount_applied: str
    remaining_balance: str
    status: str


@dataclass(frozen=True)
class SettlementGroupVM:
    group_id: str
    supplier_name: str
    customer_number: str
    description: str
    final_amount_due: str
    settlement_status: str
    exportable: bool
    invoices: tuple[SettlementLineVM, ...]
    credits: tuple[SettlementLineVM, ...]
    allocations: tuple[SettlementAllocationVM, ...]
    invoices_total: str
    credits_total: str


def _line_vm(line: dict[str, Any]) -> SettlementLineVM:
    return SettlementLineVM(
        doc_type=str(line.get("doc_type") or "invoice"),
        invoice_number=str(line.get("invoice_number") or ""),
        gross_amount=str(line.get("gross_amount") or ""),
        amount_applied=str(line.get("amount_applied") or ""),
        remaining_balance=str(line.get("remaining_balance") or ""),
    )


def _allocation_vm(allocation: dict[str, Any]) -> SettlementAllocationVM:
    return SettlementAllocationVM(
        credit_number=str(allocation.get("credit_number") or ""),
        invoice_number=str(allocation.get("invoice_number") or ""),
        amount_applied=str(allocation.get("amount_applied") or ""),
        remaining_balance=str(allocation.get("remaining_balance") or ""),
        status=str(allocation.get("status") or ""),
    )


def settlement_group_vm_from_engine(group: SettlementGroupOutput) -> SettlementGroupVM:
    breakdown = group.get("breakdown") or {}
    linked = breakdown.get("linked_groups") or (breakdown.get("breakdown") or {}).get("linked_groups") or []
    allocation_rows = group.get("credit_allocation") or breakdown.get("allocations") or (breakdown.get("breakdown") or {}).get("allocations") or []
    inv_lines: list[SettlementLineVM] = []
    cred_lines: list[SettlementLineVM] = []
    if linked:
        block = linked[0] if isinstance(linked, list) else {}
        inv_lines = [_line_vm(x) for x in (block.get("invoices") or [])]
        cred_lines = [_line_vm(x) for x in (block.get("credits") or [])]
    return SettlementGroupVM(
        group_id=str(group.get("group_id") or ""),
        supplier_name=str(group.get("supplier_name") or ""),
        customer_number=str(group.get("customer_number") or ""),
        description=str(group.get("description") or ""),
        final_amount_due=str(group.get("final_amount_due") or ""),
        settlement_status=str(group.get("settlement_status") or ""),
        exportable=bool(group.get("exportable")),
        invoices=tuple(inv_lines),
        credits=tuple(cred_lines),
        allocations=tuple(_allocation_vm(x) for x in allocation_rows if isinstance(x, dict)),
        invoices_total=str((breakdown.get("breakdown") or {}).get("invoices_total") or breakdown.get("invoices_total") or ""),
        credits_total=str((breakdown.get("breakdown") or {}).get("credits_total") or breakdown.get("credits_total") or ""),
    )


def build_settlement_group_vms(groups: list[SettlementGroupOutput]) -> list[SettlementGroupVM]:
    return [settlement_group_vm_from_engine(g) for g in groups]
