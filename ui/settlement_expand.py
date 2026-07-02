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
# Extended metadata roles — populated for every child row in breakdown rendering.
# These are cheap to store now and make every future contextmenu action trivial.
_ROW_SETTLEMENT_DOC_TYPE_ROLE = _QT_USER_ROLE + 32
_ROW_SETTLEMENT_SUPPLIER_ROLE = _QT_USER_ROLE + 33
_ROW_SETTLEMENT_SOURCE_PDF_ROLE = _QT_USER_ROLE + 34


class SettlementRowKind(IntEnum):
    GROUP_HEADER = 1
    INVOICE_CHILD = 2
    CREDIT_CHILD = 3
    GROUP_FOOTER = 4
    ALLOCATION_CHILD = 5
    WARNING_CHILD = 6


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
        SettlementRowKind.WARNING_CHILD,
    )


def expand_indicator(expanded: bool) -> str:
    return "▼" if expanded else "▶"


def _settlement_warning_message(vm: SettlementGroupVM) -> str | None:
    unallocated = next(
        (a for a in vm.allocations if str(a.status or "").startswith("unallocated_")),
        None,
    )
    if unallocated is not None:
        credit_no = unallocated.credit_number
        balance = unallocated.remaining_balance or unallocated.amount_applied
        if credit_no and balance:
            return f"Credit {credit_no} niet toegewezen ({balance})"
        return "Credit niet toegewezen"
    unresolved = next(
        (
            c
            for c in vm.credits
            if c.remaining_balance and c.remaining_balance not in ("0", "0.00", "0,00", "")
        ),
        None,
    )
    if unresolved is not None:
        credit_no = unresolved.invoice_number
        balance = unresolved.remaining_balance
        if credit_no and balance:
            return f"Credit {credit_no} niet volledig verrekend ({balance})"
        return "Credit niet volledig verrekend"
    return None


def _build_doc_map(group: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map invoice_number → {document_id, raw, doc_type} from member_documents."""
    doc_map: dict[str, dict[str, Any]] = {}
    for doc in group.get("member_documents") or []:
        raw = doc.get("raw") if isinstance(doc.get("raw"), dict) else doc
        if not isinstance(raw, dict):
            continue
        inv_no = str(raw.get("invoice_number") or "").strip()
        doc_id = str(doc.get("document_id") or "").strip()
        doc_type = str(raw.get("type") or "invoice")
        if inv_no:
            doc_map[inv_no] = {"document_id": doc_id, "raw": raw, "doc_type": doc_type}
        # Also index by doc_id for fallback lookups
        if doc_id and doc_id not in doc_map:
            doc_map[doc_id] = {"document_id": doc_id, "raw": raw, "doc_type": doc_type}
    return doc_map


def breakdown_child_rows(
    vm: SettlementGroupVM,
    *,
    expanded: bool,
    group: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Child row specs for a settlement group (render-only from VM).

    When *group* (the raw SettlementGroupOutput dict) is supplied, each spec is
    enriched with ``document_id`` and ``raw_invoice`` so that the full-row renderer
    can populate all payment columns and store rich UserRole metadata.
    """
    if not expanded:
        return []
    doc_map = _build_doc_map(group) if group is not None else {}
    supplier_name = str((group or {}).get("supplier_name") or vm.supplier_name)
    group_id = str((group or {}).get("group_id") or vm.group_id)
    rows: list[dict[str, Any]] = []
    for inv in vm.invoices:
        doc_info = doc_map.get(inv.invoice_number) or {}
        rows.append(
            {
                "kind": SettlementRowKind.INVOICE_CHILD,
                "label": inv.invoice_number,
                "amount": inv.gross_amount,
                "document_id": doc_info.get("document_id", ""),
                "supplier_name": supplier_name,
                "group_id": group_id,
                "raw_invoice": doc_info.get("raw") or {},
                "meta": {"doc_type": "invoice", "invoice_number": inv.invoice_number},
            }
        )
    for cr in vm.credits:
        applied = str(cr.amount_applied or "").strip()
        if applied in ("", "0", "0.00", "0,00"):
            amount = cr.gross_amount
        else:
            amount = applied
        if amount and not str(amount).startswith("-"):
            amount = f"-{amount}"
        doc_info = doc_map.get(cr.invoice_number) or {}
        rows.append(
            {
                "kind": SettlementRowKind.CREDIT_CHILD,
                "label": cr.invoice_number,
                "amount": amount,
                "document_id": doc_info.get("document_id", ""),
                "supplier_name": supplier_name,
                "group_id": group_id,
                "raw_invoice": doc_info.get("raw") or {},
                "meta": {
                    "doc_type": "credit_note",
                    "invoice_number": cr.invoice_number,
                    "remaining_balance": cr.remaining_balance,
                },
            }
        )
    warning = _settlement_warning_message(vm)
    if warning:
        rows.append(
            {
                "kind": SettlementRowKind.WARNING_CHILD,
                "label": warning,
                "amount": "",
                "document_id": "",
                "supplier_name": supplier_name,
                "group_id": group_id,
                "raw_invoice": {},
                "meta": {"doc_type": "warning"},
            }
        )
    return rows


def apply_child_row_items(table, row: int, spec: dict[str, Any], settlement_col: int, group_id: str) -> None:
    """Minimal 3-column fallback renderer.  Kept for backward compatibility;
    the full-row renderer (_apply_settlement_child_row_full on MainWindow) is
    preferred when called from _append_settlement_breakdown_rows.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QBrush
    from PySide6.QtWidgets import QTableWidgetItem

    kind = spec["kind"]
    label = str(spec.get("label") or "")
    amount = str(spec.get("amount") or "")
    meta = spec.get("meta") or {}
    read_only = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    prefix = "  "
    if kind == SettlementRowKind.WARNING_CHILD:
        prefix = "  ⚠ "

    sup_item = QTableWidgetItem(f"{prefix}{label}")
    sup_item.setFlags(read_only)
    sup_item.setData(_ROW_SETTLEMENT_ROW_KIND_ROLE, int(kind))
    # Prefer explicit document_id from enriched spec; fall back to meta fields.
    doc_id = str(spec.get("document_id") or meta.get("document_id") or meta.get("invoice_number") or "")
    if doc_id and kind == SettlementRowKind.CREDIT_CHILD:
        sup_item.setData(_ROW_SETTLEMENT_DOC_ID_ROLE, doc_id)
    # Store extended metadata roles so contextmenus work without extra lookups.
    doc_type = "credit_note" if kind == SettlementRowKind.CREDIT_CHILD else "invoice"
    sup_item.setData(_ROW_SETTLEMENT_DOC_TYPE_ROLE, doc_type)
    sup_item.setData(_ROW_SETTLEMENT_SUPPLIER_ROLE, str(spec.get("supplier_name") or ""))
    raw = spec.get("raw_invoice") or {}
    if raw.get("source_file"):
        sup_item.setData(_ROW_SETTLEMENT_SOURCE_PDF_ROLE, str(raw["source_file"]))
    if kind in (SettlementRowKind.CREDIT_CHILD, SettlementRowKind.WARNING_CHILD):
        sup_item.setForeground(QBrush(QColor("#b54708")))
    table.setItem(row, 0, sup_item)

    amt_item = QTableWidgetItem(amount)
    amt_item.setFlags(read_only)
    amt_item.setData(_ROW_SETTLEMENT_ROW_KIND_ROLE, int(kind))
    if kind == SettlementRowKind.CREDIT_CHILD:
        amt_item.setForeground(QBrush(QColor("#b54708")))
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
