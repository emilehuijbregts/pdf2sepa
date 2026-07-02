"""Expandable settlement row roles and breakdown child rows."""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from ui.settlement_badges import settlement_badge_nl
from ui.settlement_view import SettlementGroupVM, settlement_group_vm_from_engine

_QT_USER_ROLE = 256
_ROW_SETTLEMENT_ROW_KIND_ROLE = _QT_USER_ROLE + 30
_ROW_SETTLEMENT_DOC_ID_ROLE = _QT_USER_ROLE + 31
_ROW_SETTLEMENT_GROUP_ID_ROLE = _QT_USER_ROLE + 20


class SettlementRowKind(IntEnum):
    GROUP_HEADER = 1
    INVOICE_CHILD = 2
    CREDIT_CHILD = 3
    GROUP_FOOTER = 4
    ALLOCATION_CHILD = 5


def settlement_row_kind(item) -> SettlementRowKind | None:
    if item is None:
        return None
    raw = item.data(_ROW_SETTLEMENT_ROW_KIND_ROLE)
    try:
        return SettlementRowKind(int(raw))
    except (TypeError, ValueError):
        return None


def is_settlement_child_row(row: int, table) -> bool:
    it = table.item(row, 0)
    kind = settlement_row_kind(it)
    return kind in (
        SettlementRowKind.INVOICE_CHILD,
        SettlementRowKind.CREDIT_CHILD,
        SettlementRowKind.ALLOCATION_CHILD,
        SettlementRowKind.GROUP_FOOTER,
    )


def expand_indicator(expanded: bool) -> str:
    return "▼" if expanded else "▶"


def breakdown_child_rows(vm: SettlementGroupVM, *, expanded: bool) -> list[dict[str, Any]]:
    """Child row specs for a settlement group (render-only from VM)."""
    if not expanded:
        return []
    rows: list[dict[str, Any]] = []
    rows.append({"kind": SettlementRowKind.INVOICE_CHILD, "label": "Facturen", "amount": "", "meta": {}})
    for inv in vm.invoices:
        rows.append(
            {
                "kind": SettlementRowKind.INVOICE_CHILD,
                "label": inv.invoice_number,
                "amount": inv.gross_amount,
                "meta": {"doc_type": "invoice", "invoice_number": inv.invoice_number},
            }
        )
    rows.append({"kind": SettlementRowKind.CREDIT_CHILD, "label": "Credits", "amount": "", "meta": {}})
    for cr in vm.credits:
        amount = cr.amount_applied or cr.gross_amount
        if amount and not str(amount).startswith("-"):
            amount = f"-{amount}"
        suffix = ""
        if cr.remaining_balance and cr.remaining_balance not in ("0", "0.00", "0,00"):
            suffix = f" (remaining: {cr.remaining_balance})"
        rows.append(
            {
                "kind": SettlementRowKind.CREDIT_CHILD,
                "label": f"{cr.invoice_number}{suffix}",
                "amount": amount,
                "meta": {
                    "doc_type": "credit_note",
                    "invoice_number": cr.invoice_number,
                    "remaining_balance": cr.remaining_balance,
                },
            }
        )
    if vm.allocations:
        rows.append({"kind": SettlementRowKind.ALLOCATION_CHILD, "label": "Toewijzing", "amount": "", "meta": {}})
        for allocation in vm.allocations:
            status = allocation.status
            if status == "matched":
                target = allocation.invoice_number or "?"
                amount = allocation.amount_applied
            else:
                target = "UNMATCHED"
                amount = allocation.remaining_balance or allocation.amount_applied
            rows.append(
                {
                    "kind": SettlementRowKind.ALLOCATION_CHILD,
                    "label": f"{allocation.credit_number} -> {target}",
                    "amount": amount,
                    "meta": {
                        "doc_type": "allocation",
                        "status": status,
                        "credit_number": allocation.credit_number,
                        "invoice_number": allocation.invoice_number,
                    },
                }
            )
    rows.append(
        {
            "kind": SettlementRowKind.GROUP_FOOTER,
            "label": "Te betalen",
            "amount": vm.final_amount_due,
            "meta": {},
        }
    )
    return rows


def apply_child_row_items(table, row: int, spec: dict[str, Any], settlement_col: int, group_id: str) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QBrush
    from PySide6.QtWidgets import QTableWidgetItem

    kind = spec["kind"]
    label = str(spec.get("label") or "")
    amount = str(spec.get("amount") or "")
    meta = spec.get("meta") or {}
    read_only = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    prefix = "  "
    if kind == SettlementRowKind.GROUP_FOOTER:
        prefix = "— "
    elif kind in (
        SettlementRowKind.INVOICE_CHILD,
        SettlementRowKind.CREDIT_CHILD,
        SettlementRowKind.ALLOCATION_CHILD,
    ) and label in ("Facturen", "Credits", "Toewijzing"):
        prefix = ""

    sup_item = QTableWidgetItem(f"{prefix}{label}")
    sup_item.setFlags(read_only)
    sup_item.setData(_ROW_SETTLEMENT_ROW_KIND_ROLE, int(kind))
    if meta.get("doc_type") == "credit_note":
        doc_id = meta.get("document_id") or meta.get("invoice_number")
        if doc_id:
            sup_item.setData(_ROW_SETTLEMENT_DOC_ID_ROLE, str(doc_id))
    if kind == SettlementRowKind.CREDIT_CHILD:
        sup_item.setForeground(QBrush(QColor("#b54708")))
    elif kind == SettlementRowKind.ALLOCATION_CHILD:
        if str(meta.get("status") or "").startswith("unallocated_"):
            sup_item.setForeground(QBrush(QColor("#b54708")))
        else:
            sup_item.setForeground(QBrush(QColor("#667085")))
    table.setItem(row, 0, sup_item)

    amt_item = QTableWidgetItem(amount)
    amt_item.setFlags(read_only)
    amt_item.setData(_ROW_SETTLEMENT_ROW_KIND_ROLE, int(kind))
    if kind == SettlementRowKind.CREDIT_CHILD:
        amt_item.setForeground(QBrush(QColor("#b54708")))
    elif kind == SettlementRowKind.ALLOCATION_CHILD:
        if str(meta.get("status") or "").startswith("unallocated_"):
            amt_item.setForeground(QBrush(QColor("#b54708")))
        else:
            amt_item.setForeground(QBrush(QColor("#667085")))
    table.setItem(row, 2, amt_item)

    sett_item = QTableWidgetItem("")
    sett_item.setFlags(read_only)
    sett_item.setData(_ROW_SETTLEMENT_ROW_KIND_ROLE, int(kind))
    sett_item.setData(_ROW_SETTLEMENT_GROUP_ID_ROLE, group_id)
    table.setItem(row, settlement_col, sett_item)


def header_supplier_label(vm: SettlementGroupVM, expanded: bool) -> str:
    return f"{expand_indicator(expanded)} {vm.supplier_name}"


def vm_from_group(group: dict[str, Any]) -> SettlementGroupVM:
    return settlement_group_vm_from_engine(group)


def badge_for_group(group: dict[str, Any]) -> str:
    return settlement_badge_nl(str(group.get("settlement_status") or ""))


def mark_group_header_row(table, row: int, group_id: str) -> None:
    sup = table.item(row, 0)
    if sup is not None:
        sup.setData(_ROW_SETTLEMENT_ROW_KIND_ROLE, int(SettlementRowKind.GROUP_HEADER))
