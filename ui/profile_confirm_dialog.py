"""Dialog: bevestig factuurvelden en optioneel leer extractieprofiel."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from logic.payment_amounts import amount_to_decimal


class ProfileConfirmDialog(QDialog):
    """Bevestig bedrag / factuurnr / klantnr; geen DB- of PDF-logica."""

    def __init__(
        self,
        *,
        supplier_name: str,
        amount_placeholder: str = "",
        invoice_placeholder: str = "",
        customer_placeholder: str = "",
        amount_initial: str = "",
        invoice_initial: str = "",
        customer_initial: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Factuurgegevens bevestigen")
        self.setMinimumWidth(420)
        self._save_profile = False

        root = QVBoxLayout(self)
        root.addWidget(
            QLabel(
                "Controleer de velden. Met «Bevestig en leer profiel» wordt een "
                "extractieprofiel voor deze leverancier opgeslagen."
            )
        )

        form = QFormLayout()
        self._supplier_edit = QLineEdit(supplier_name.strip())
        self._supplier_edit.setReadOnly(True)
        form.addRow("Leverancier", self._supplier_edit)

        self._amount_edit = QLineEdit(amount_initial.strip())
        if amount_placeholder.strip():
            self._amount_edit.setPlaceholderText(amount_placeholder.strip())
        form.addRow("Bedrag", self._amount_edit)

        self._invoice_edit = QLineEdit(invoice_initial.strip())
        if invoice_placeholder.strip():
            self._invoice_edit.setPlaceholderText(invoice_placeholder.strip())
        form.addRow("Factuur-/polisnummer", self._invoice_edit)

        self._customer_edit = QLineEdit(customer_initial.strip())
        if customer_placeholder.strip():
            self._customer_edit.setPlaceholderText(customer_placeholder.strip())
        form.addRow("Klantnummer", self._customer_edit)

        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)

        learn_btn = QPushButton("Bevestig en leer profiel")
        learn_btn.clicked.connect(self._on_learn)
        buttons.addButton(learn_btn, QDialogButtonBox.ButtonRole.AcceptRole)

        confirm_btn = QPushButton("Alleen bevestigen")
        confirm_btn.clicked.connect(self._on_confirm_only)
        buttons.addButton(confirm_btn, QDialogButtonBox.ButtonRole.ActionRole)

        root.addWidget(buttons)

    def _on_learn(self) -> None:
        if not self._validate_amount():
            return
        self._save_profile = True
        self.accept()

    def _on_confirm_only(self) -> None:
        if not self._validate_amount():
            return
        self._save_profile = False
        self.accept()

    def _validate_amount(self) -> bool:
        t = self._amount_edit.text().strip()
        ph = self._amount_edit.placeholderText().strip()
        if not t and not ph:
            return True
        raw = t or ph
        try:
            dec = amount_to_decimal(raw.replace(",", "."))
            if dec <= Decimal("0.00"):
                raise ValueError("non-positive")
        except (TypeError, ValueError, InvalidOperation):
            self._amount_edit.setFocus()
            return False
        return True

    @property
    def save_profile(self) -> bool:
        return self._save_profile

    def get_confirmed(self) -> dict[str, Any]:
        """Ruwe invoer; normalisatie in logic.profile_learning."""
        out: dict[str, Any] = {}
        amt_t = self._amount_edit.text().strip()
        amt_ph = self._amount_edit.placeholderText().strip()
        if amt_t or amt_ph:
            out["amount"] = amt_t or amt_ph
        inv_t = self._invoice_edit.text().strip()
        inv_ph = self._invoice_edit.placeholderText().strip()
        if inv_t or inv_ph:
            out["invoice_number"] = inv_t or inv_ph
        cust_t = self._customer_edit.text().strip()
        cust_ph = self._customer_edit.placeholderText().strip()
        if cust_t or cust_ph:
            out["customer_number"] = cust_t or cust_ph
        return out
