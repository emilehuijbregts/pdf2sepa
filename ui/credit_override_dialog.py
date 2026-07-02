"""Dialog to manually reassign credit to invoices."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from logic.credit_override_store import CreditOverrideAllocation
from logic.credit_settlement import document_id


class CreditOverrideDialog(QDialog):
    """Select target invoice(s) and allocation for a credit note."""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        credit: dict[str, Any],
        available_invoices: list[dict[str, Any]],
        title: str = "Credit koppelen",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._credit = credit
        self._invoices = available_invoices
        self._alloc_inputs: dict[str, QLineEdit] = {}
        self._result_allocations: tuple[CreditOverrideAllocation, ...] = ()

        credit_no = str(credit.get("invoice_number") or "")
        credit_amt = credit.get("amount")
        header = QLabel(f"Credit: {credit_no} — € {credit_amt}")

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        for inv in available_invoices:
            inv_no = str(inv.get("invoice_number") or "")
            src = str(inv.get("source_file") or "")
            amt = inv.get("amount")
            label = f"{inv_no}  ({src})  € {amt}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, inv)
            self._list.addItem(item)

        self._alloc_container = QWidget()
        self._alloc_layout = QFormLayout(self._alloc_container)

        self._list.currentItemChanged.connect(self._on_invoice_selected)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(header)
        layout.addWidget(QLabel("Beschikbare facturen"))
        layout.addWidget(self._list)
        layout.addWidget(QLabel("Nieuwe allocation"))
        layout.addWidget(self._alloc_container)
        layout.addWidget(buttons)

        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _on_invoice_selected(self) -> None:
        while self._alloc_layout.count():
            child = self._alloc_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._alloc_inputs.clear()
        item = self._list.currentItem()
        if not item:
            return
        inv = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(inv, dict):
            return
        doc_id = document_id({"raw": inv})
        inv_no = str(inv.get("invoice_number") or "")
        credit_amt = self._credit.get("amount")
        default = str(credit_amt) if credit_amt is not None else ""
        edit = QLineEdit(default)
        self._alloc_inputs[doc_id] = edit
        self._alloc_layout.addRow(f"{inv_no}:", edit)

    def _on_accept(self) -> None:
        item = self._list.currentItem()
        if not item:
            QMessageBox.warning(self, "Credit koppelen", "Selecteer een factuur.")
            return
        inv = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(inv, dict):
            return
        doc_id = document_id({"raw": inv})
        inv_no = str(inv.get("invoice_number") or "")
        edit = self._alloc_inputs.get(doc_id)
        if edit is None:
            QMessageBox.warning(self, "Credit koppelen", "Allocation ontbreekt.")
            return
        try:
            amount = Decimal(str(edit.text().replace(",", ".").strip()))
        except (InvalidOperation, ValueError):
            QMessageBox.warning(self, "Credit koppelen", "Ongeldig bedrag.")
            return
        if amount <= Decimal("0"):
            QMessageBox.warning(self, "Credit koppelen", "Bedrag moet groter dan 0 zijn.")
            return
        self._result_allocations = (
            CreditOverrideAllocation(
                invoice_document_id=doc_id,
                invoice_number=inv_no,
                amount_applied=amount.quantize(Decimal("0.01")),
            ),
        )
        self.accept()

    def allocations(self) -> tuple[CreditOverrideAllocation, ...]:
        return self._result_allocations

    @property
    def credit_document_id(self) -> str:
        return document_id({"raw": self._credit})
