"""Beheer van de leveranciersdatabase (PySide6)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from parser.supplier_db import SupplierDB


def _discount_edit_text(discount_val: object) -> str:
    try:
        return str(float(discount_val))
    except (TypeError, ValueError):
        return "0"


class SuppliersDialog(QDialog):
    """Toon, voeg toe, bewerk en verwijder leveranciers in ``data/suppliers.json``."""

    def __init__(self, db_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db = SupplierDB(path=db_path)
        self.setWindowTitle("Mijn leveranciers")
        self.setMinimumSize(700, 500)
        self._editing_original_name: str | None = None
        self._suppress_list_signal = False

        root = QHBoxLayout(self)

        left = QVBoxLayout()
        left.addWidget(QLabel("Leveranciers"))
        self._list = QListWidget()
        self._list.currentTextChanged.connect(self._on_list_changed)
        left.addWidget(self._list, stretch=1)
        self._btn_new = QPushButton("Nieuw")
        self._btn_new.clicked.connect(self._on_new)
        left.addWidget(self._btn_new)
        root.addLayout(left, stretch=1)

        right = QVBoxLayout()
        form = QFormLayout()
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Officiële naam")
        form.addRow(QLabel("Naam:"), self._name_edit)

        self._iban_edit = QLineEdit()
        self._iban_edit.setPlaceholderText("NL…")
        form.addRow(QLabel("IBAN:"), self._iban_edit)

        self._discount_edit = QLineEdit()
        self._discount_edit.setPlaceholderText("0 of 2,5")
        form.addRow(QLabel("Korting %:"), self._discount_edit)
        right.addLayout(form)

        right.addWidget(QLabel("Aliassen"))
        alias_row = QHBoxLayout()
        self._alias_list = QListWidget()
        alias_row.addWidget(self._alias_list, stretch=1)
        av = QVBoxLayout()
        self._btn_alias_add = QPushButton("Alias toevoegen")
        self._btn_alias_add.clicked.connect(self._on_alias_add)
        av.addWidget(self._btn_alias_add)
        self._btn_alias_del = QPushButton("Alias verwijderen")
        self._btn_alias_del.clicked.connect(self._on_alias_remove)
        av.addStretch(1)
        alias_row.addLayout(av)
        right.addLayout(alias_row)

        right.addWidget(QLabel("Klantcodes / lidnummers"))
        cc_row = QHBoxLayout()
        self._customer_code_list = QListWidget()
        cc_row.addWidget(self._customer_code_list, stretch=1)
        cc_btns = QVBoxLayout()
        self._btn_cc_add = QPushButton("Code toevoegen")
        self._btn_cc_add.clicked.connect(self._on_customer_code_add)
        cc_btns.addWidget(self._btn_cc_add)
        self._btn_cc_del = QPushButton("Code verwijderen")
        self._btn_cc_del.clicked.connect(self._on_customer_code_remove)
        cc_btns.addStretch(1)
        cc_row.addLayout(cc_btns)
        right.addLayout(cc_row)

        btn_row = QHBoxLayout()
        self._btn_save = QPushButton("Opslaan")
        self._btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self._btn_save)
        self._btn_delete = QPushButton("Verwijderen")
        self._btn_delete.clicked.connect(self._on_delete)
        btn_row.addWidget(self._btn_delete)
        btn_row.addStretch(1)
        right.addLayout(btn_row)

        bbox = QDialogButtonBox()
        bbox.addButton("Sluiten", QDialogButtonBox.ButtonRole.AcceptRole)
        bbox.accepted.connect(self.accept)
        right.addWidget(bbox)
        root.addLayout(right, stretch=2)

        if not self._supplier_names_sorted():
            self._on_new()
        else:
            self._reload_list(select_name=None)

    def _supplier_names_sorted(self) -> list[str]:
        return sorted(
            (str(s.get("name") or "") for s in self._db.get_all() if str(s.get("name") or "").strip()),
            key=str.casefold,
        )

    def _reload_list(self, select_name: str | None, *, select_first_if_none: bool = True) -> None:
        self._suppress_list_signal = True
        self._list.clear()
        for n in self._supplier_names_sorted():
            self._list.addItem(n)
        self._suppress_list_signal = False
        if select_name:
            items = self._list.findItems(select_name, Qt.MatchFlag.MatchExactly)
            if items:
                self._list.setCurrentItem(items[0])
                return
        if select_first_if_none and self._list.count():
            self._list.setCurrentRow(0)

    def _on_list_changed(self, text: str) -> None:
        if self._suppress_list_signal:
            return
        name = (text or "").strip()
        if not name:
            self._on_new()
            return
        self._load_supplier_into_form(name)

    def _parse_discount(self) -> float:
        s = (self._discount_edit.text() or "").strip().replace(",", ".")
        if not s:
            return 0.0
        return float(s)

    def _clear_form(self) -> None:
        self._name_edit.clear()
        self._iban_edit.clear()
        self._discount_edit.clear()
        self._alias_list.clear()
        self._customer_code_list.clear()

    def _load_supplier_into_form(self, name_key: str) -> None:
        self._clear_form()
        self._editing_original_name = name_key
        self._name_edit.setText(name_key)
        self._name_edit.setReadOnly(True)
        for s in self._db.get_all():
            if str(s.get("name") or "").strip() != name_key.strip():
                continue
            self._iban_edit.setText(str(s.get("iban") or ""))
            self._discount_edit.setText(_discount_edit_text(s.get("discount")))
            aliases = s.get("aliases") or []
            if isinstance(aliases, list):
                seen: set[str] = set()
                for a in aliases:
                    t = str(a or "").strip()
                    if t and t not in seen:
                        seen.add(t)
                        self._alias_list.addItem(t)
            codes = s.get("customer_codes") or []
            if isinstance(codes, list):
                seen_c: set[str] = set()
                for c in codes:
                    t = str(c or "").strip()
                    if t and t not in seen_c:
                        seen_c.add(t)
                        self._customer_code_list.addItem(t)
            break

    def _on_new(self) -> None:
        self._suppress_list_signal = True
        self._list.clearSelection()
        self._suppress_list_signal = False
        self._editing_original_name = None
        self._clear_form()
        self._name_edit.setReadOnly(False)

    def _current_aliases_list(self) -> list[str]:
        out: list[str] = []
        for i in range(self._alias_list.count()):
            t = self._alias_list.item(i).text().strip()
            if t:
                out.append(t)
        return out

    def _on_alias_add(self) -> None:
        text, ok = QInputDialog.getText(self, "Alias", "Nieuwe alias:")
        if ok and text.strip():
            self._alias_list.addItem(text.strip())

    def _on_alias_remove(self) -> None:
        row = self._alias_list.currentRow()
        if row >= 0:
            self._alias_list.takeItem(row)

    def _current_customer_codes_list(self) -> list[str]:
        out: list[str] = []
        for i in range(self._customer_code_list.count()):
            t = self._customer_code_list.item(i).text().strip()
            if t:
                out.append(t)
        return out

    def _on_customer_code_add(self) -> None:
        text, ok = QInputDialog.getText(self, "Klantcode", "Nieuwe klantcode of lidnummer:")
        if ok and text.strip():
            self._customer_code_list.addItem(text.strip())

    def _on_customer_code_remove(self) -> None:
        row = self._customer_code_list.currentRow()
        if row >= 0:
            self._customer_code_list.takeItem(row)

    def _on_save(self) -> None:
        name = (self._name_edit.text() or "").strip()
        if not name:
            QMessageBox.warning(self, "Leveranciers", "Vul een naam in.")
            return
        iban = (self._iban_edit.text() or "").strip()
        try:
            discount = self._parse_discount()
        except ValueError:
            QMessageBox.warning(self, "Leveranciers", "Ongeldige korting.")
            return

        aliases = self._current_aliases_list()
        customer_codes = self._current_customer_codes_list()

        if self._editing_original_name is None:
            before = len(self._db.get_all())
            self._db.add_supplier(name, iban, discount, aliases=aliases, customer_codes=customer_codes)
            after = len(self._db.get_all())
            if after == before:
                QMessageBox.information(
                    self,
                    "Leveranciers",
                    "Kon niet toevoegen (bestaat mogelijk al).",
                )
                return
            self._reload_list(select_name=name)
            self._load_supplier_into_form(name)
            QMessageBox.information(self, "Leveranciers", f"Leverancier '{name}' toegevoegd.")
            return

        self._db.update_supplier(
            self._editing_original_name,
            iban=iban,
            discount=discount,
            aliases=aliases,
            overwrite_aliases=True,
            customer_codes=customer_codes,
            overwrite_customer_codes=True,
        )
        QMessageBox.information(self, "Leveranciers", "Opgeslagen.")
        self._reload_list(select_name=self._editing_original_name)

    def _on_delete(self) -> None:
        key = self._editing_original_name
        if not key:
            QMessageBox.information(self, "Leveranciers", "Selecteer eerst een leverancier.")
            return
        if (
            QMessageBox.question(
                self,
                "Leveranciers",
                f"Leverancier '{key}' verwijderen?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        if self._db.delete_supplier(key):
            QMessageBox.information(self, "Leveranciers", "Verwijderd.")
            self._reload_list(select_name=None, select_first_if_none=False)
            self._on_new()
        else:
            QMessageBox.warning(self, "Leveranciers", "Verwijderen mislukt.")
