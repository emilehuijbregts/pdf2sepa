"""Dialog to manually reassign credit to one or more invoices."""

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
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from logic.credit_override_store import CreditOverrideAllocation
from logic.credit_settlement import document_id

_MONEY_TOL = Decimal("0.01")


def _parse_money(text: str) -> Decimal:
    return Decimal(str(text or "").replace(",", ".").strip())


def _credit_amount_dec(credit: dict[str, Any]) -> Decimal:
    raw = credit.get("amount_dec")
    if isinstance(raw, Decimal):
        return raw.copy_abs().quantize(_MONEY_TOL)
    try:
        return _parse_money(str(credit.get("amount") or "0")).copy_abs().quantize(_MONEY_TOL)
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def _invoice_amount_dec(inv: dict[str, Any]) -> Decimal:
    raw = inv.get("amount_dec")
    if isinstance(raw, Decimal):
        return raw.copy_abs().quantize(_MONEY_TOL)
    try:
        return _parse_money(str(inv.get("amount") or "0")).copy_abs().quantize(_MONEY_TOL)
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def build_allocations_from_inputs(
    credit: dict[str, Any],
    invoices: list[dict[str, Any]],
    alloc_inputs: dict[str, str],
) -> tuple[CreditOverrideAllocation, ...] | None:
    """Parse per-invoice allocation fields; return None when invalid."""
    credit_amt = _credit_amount_dec(credit)
    invoice_capacity = sum(_invoice_amount_dec(inv) for inv in invoices).quantize(_MONEY_TOL)
    if credit_amt > invoice_capacity + _MONEY_TOL:
        return None
    allocations: list[CreditOverrideAllocation] = []
    total = Decimal("0.00")
    for inv in invoices:
        doc_id = document_id({"raw": inv})
        raw_text = alloc_inputs.get(doc_id, "")
        if not str(raw_text or "").strip():
            continue
        try:
            amount = _parse_money(str(raw_text)).quantize(_MONEY_TOL)
        except (InvalidOperation, ValueError):
            return None
        if amount <= _MONEY_TOL:
            continue
        inv_no = str(inv.get("invoice_number") or "")
        allocations.append(
            CreditOverrideAllocation(
                invoice_document_id=doc_id,
                invoice_number=inv_no,
                amount_applied=amount,
            )
        )
        total += amount
    if not allocations:
        return None
    if total > credit_amt + _MONEY_TOL:
        return None
    if total + _MONEY_TOL < credit_amt:
        return None
    return tuple(allocations)


def validate_credit_reassign(
    credit: dict[str, Any],
    invoices: list[dict[str, Any]],
    alloc_inputs: dict[str, str],
) -> tuple[tuple[CreditOverrideAllocation, ...] | None, str | None]:
    """Validate reassign inputs; return (allocations, error_code)."""
    credit_amt = _credit_amount_dec(credit)
    invoice_capacity = sum(_invoice_amount_dec(inv) for inv in invoices).quantize(_MONEY_TOL)
    if credit_amt > invoice_capacity + _MONEY_TOL:
        return None, "insufficient_invoices"
    total = Decimal("0.00")
    any_input = False
    for inv in invoices:
        doc_id = document_id({"raw": inv})
        raw_text = alloc_inputs.get(doc_id, "")
        if not str(raw_text or "").strip():
            continue
        any_input = True
        try:
            amount = _parse_money(str(raw_text)).quantize(_MONEY_TOL)
        except (InvalidOperation, ValueError):
            return None, "invalid_amount"
        if amount > _MONEY_TOL:
            total += amount
    if not any_input:
        return None, "empty"
    if total > credit_amt + _MONEY_TOL:
        return None, "exceeds_credit"
    if total + _MONEY_TOL < credit_amt:
        return None, "partial_allocation"
    allocations = build_allocations_from_inputs(credit, invoices, alloc_inputs)
    if allocations is None:
        return None, "invalid_amount"
    return allocations, None


def suggest_allocations_across_invoices(
    credit: dict[str, Any],
    invoices: list[dict[str, Any]],
) -> dict[str, Decimal]:
    """Greedy split: allocate up to each invoice gross, largest invoices first."""
    remaining = _credit_amount_dec(credit)
    suggested: dict[str, Decimal] = {}
    for inv in sorted(invoices, key=_invoice_amount_dec, reverse=True):
        if remaining <= _MONEY_TOL:
            break
        doc_id = document_id({"raw": inv})
        cap = _invoice_amount_dec(inv)
        applied = min(cap, remaining).quantize(_MONEY_TOL)
        if applied > _MONEY_TOL:
            suggested[doc_id] = applied
            remaining = (remaining - applied).quantize(_MONEY_TOL)
    return suggested


_REASSIGN_ERRORS_NL = {
    "insufficient_invoices": (
        "Credit kan niet worden verrekend: het creditbedrag is hoger dan het totaal "
        "van de beschikbare facturen in deze batch."
    ),
    "exceeds_credit": "De toegewezen bedragen overschrijden het creditbedrag.",
    "partial_allocation": "Het volledige creditbedrag moet worden toegewezen aan facturen.",
    "invalid_amount": "Ongeldig bedrag ingevuld.",
    "empty": "Wijs het creditbedrag toe aan minstens één factuur.",
}


class CreditOverrideDialog(QDialog):
    """Allocate a credit note across one or more target invoices."""

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
        self.setMinimumWidth(520)
        self._credit = credit
        self._invoices = list(available_invoices)
        self._alloc_inputs: dict[str, QLineEdit] = {}
        self._result_allocations: tuple[CreditOverrideAllocation, ...] = ()

        credit_no = str(credit.get("invoice_number") or "")
        credit_amt = _credit_amount_dec(credit)
        header = QLabel(f"Credit: {credit_no} — totaal € {credit_amt}")

        hint = QLabel(
            "Vul per factuur het toe te passen creditbedrag in. "
            "Het volledige creditbedrag moet worden verdeeld; de som van de facturen "
            "moet minstens het creditbedrag zijn."
        )
        hint.setWordWrap(True)

        self._alloc_container = QWidget()
        self._alloc_layout = QFormLayout(self._alloc_container)

        suggested = suggest_allocations_across_invoices(credit, self._invoices)
        for inv in self._invoices:
            inv_no = str(inv.get("invoice_number") or "")
            src = str(inv.get("source_file") or "")
            pdf = src.rsplit("/", 1)[-1] if src else ""
            inv_amt = _invoice_amount_dec(inv)
            doc_id = document_id({"raw": inv})
            default = suggested.get(doc_id)
            edit = QLineEdit(str(default) if default is not None else "")
            edit.setPlaceholderText("0,00")
            self._alloc_inputs[doc_id] = edit
            label = f"{inv_no}  ({pdf})  max € {inv_amt}"
            self._alloc_layout.addRow(label, edit)

        auto_btn = QPushButton("Verdeel automatisch over facturen")
        auto_btn.clicked.connect(self._apply_auto_split)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(header)
        layout.addWidget(hint)
        layout.addWidget(self._alloc_container)
        auto_row = QHBoxLayout()
        auto_row.addWidget(auto_btn)
        auto_row.addStretch(1)
        layout.addLayout(auto_row)
        layout.addWidget(buttons)

    def _apply_auto_split(self) -> None:
        suggested = suggest_allocations_across_invoices(self._credit, self._invoices)
        for doc_id, edit in self._alloc_inputs.items():
            val = suggested.get(doc_id)
            edit.setText(str(val) if val is not None else "")

    def _on_accept(self) -> None:
        raw_inputs = {doc_id: edit.text() for doc_id, edit in self._alloc_inputs.items()}
        allocations, err = validate_credit_reassign(self._credit, self._invoices, raw_inputs)
        if err is not None:
            QMessageBox.warning(
                self,
                "Credit koppelen",
                _REASSIGN_ERRORS_NL.get(err, "Ongeldige verdeling."),
            )
            return
        self._result_allocations = allocations or ()
        self.accept()

    def allocations(self) -> tuple[CreditOverrideAllocation, ...]:
        return self._result_allocations

    @property
    def credit_document_id(self) -> str:
        return document_id({"raw": self._credit})
