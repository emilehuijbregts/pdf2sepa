"""Beheer van de leveranciersdatabase (PySide6)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
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

from logic.payment_amounts import normalize_supplier_vat_rate_pct
from parser.supplier_db import SupplierDB


def _discount_edit_text(discount_val: object) -> str:
    try:
        return str(float(discount_val))
    except (TypeError, ValueError):
        return "0"


def _list_widget_with_buttons(
    *,
    title: str,
    add_label: str,
    on_add,
) -> tuple[QVBoxLayout, QListWidget]:
    outer = QVBoxLayout()
    outer.addWidget(QLabel(title))
    row = QHBoxLayout()
    lw = QListWidget()
    row.addWidget(lw, stretch=1)
    btns = QVBoxLayout()
    btn_add = QPushButton(add_label)
    btn_add.clicked.connect(on_add)
    btns.addWidget(btn_add)
    btns.addStretch(1)
    row.addLayout(btns)
    outer.addLayout(row)
    return outer, lw


class SuppliersDialog(QDialog):
    """Toon, voeg toe, bewerk en verwijder leveranciers in ``data/suppliers.json``."""

    def __init__(self, db_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db = SupplierDB(path=db_path)
        self.setWindowTitle("Mijn leveranciers")
        self.setMinimumSize(880, 680)
        self._editing_original_name: str | None = None
        self._suppress_list_signal = False

        root = QHBoxLayout(self)

        left = QVBoxLayout()
        left.addWidget(QLabel("Leveranciers"))
        self._list = QListWidget()
        self._list.currentTextChanged.connect(self._on_list_changed)
        left.addWidget(self._list, stretch=1)
        left_btns = QHBoxLayout()
        self._btn_new = QPushButton("Nieuw")
        self._btn_new.clicked.connect(self._on_new)
        left_btns.addWidget(self._btn_new)
        self._btn_delete_supplier = QPushButton("Verwijder leverancier")
        self._btn_delete_supplier.clicked.connect(self._on_delete)
        left_btns.addWidget(self._btn_delete_supplier)
        left_btns.addStretch(1)
        left.addLayout(left_btns)
        root.addLayout(left, stretch=1)

        right = QVBoxLayout()
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Officiële naam")
        self._name_edit.setMinimumWidth(280)
        form.addRow(QLabel("Naam:"), self._name_edit)

        self._iban_edit = QLineEdit()
        self._iban_edit.setPlaceholderText("NL…")
        self._iban_edit.setMinimumWidth(360)
        form.addRow(QLabel("IBAN:"), self._iban_edit)

        self._discount_edit = QLineEdit()
        self._discount_edit.setPlaceholderText("0 of 2,5")
        form.addRow(QLabel("Korting %:"), self._discount_edit)

        self._term_edit = QLineEdit()
        self._term_edit.setPlaceholderText("0, 7, 14, 30 …")
        form.addRow(QLabel("Betaaltermijn (dagen):"), self._term_edit)

        self._vat_rate_combo = QComboBox()
        self._vat_rate_combo.addItem("21% (standaard)", 21)
        self._vat_rate_combo.addItem("0%", 0)
        form.addRow(QLabel("BTW-tarief (korting):"), self._vat_rate_combo)
        right.addLayout(form)

        alias_lo, self._alias_list = _list_widget_with_buttons(
            title="Aliassen",
            add_label="Alias toevoegen",
            on_add=self._on_alias_add,
        )
        right.addLayout(alias_lo)

        cc_lo, self._customer_code_list = _list_widget_with_buttons(
            title="Klantcodes / lidnummers",
            add_label="Code toevoegen",
            on_add=self._on_customer_code_add,
        )
        right.addLayout(cc_lo)

        vat_lo, self._vat_list = _list_widget_with_buttons(
            title="BTW-nummers (voor automatische match)",
            add_label="BTW toevoegen",
            on_add=self._on_vat_add,
        )
        right.addLayout(vat_lo)

        kvk_lo, self._kvk_list = _list_widget_with_buttons(
            title="KvK-nummers (voor automatische match)",
            add_label="KvK toevoegen",
            on_add=self._on_kvk_add,
        )
        right.addLayout(kvk_lo)

        dom_lo, self._domain_list = _list_widget_with_buttons(
            title="E-maildomeinen (voor automatische match)",
            add_label="Domein toevoegen",
            on_add=self._on_domain_add,
        )
        right.addLayout(dom_lo)

        btn_row = QHBoxLayout()
        self._btn_save = QPushButton("Opslaan")
        self._btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self._btn_save)
        self._btn_remove_list_item = QPushButton("Verwijder")
        self._btn_remove_list_item.setToolTip(
            "Verwijdert het geselecteerde item uit de lijst waarin je een rij hebt geselecteerd."
        )
        self._btn_remove_list_item.clicked.connect(self._on_remove_selected_from_lists)
        btn_row.addWidget(self._btn_remove_list_item)
        btn_row.addStretch(1)
        right.addLayout(btn_row)

        bbox = QDialogButtonBox()
        bbox.addButton("Sluiten", QDialogButtonBox.ButtonRole.AcceptRole)
        bbox.accepted.connect(self.accept)
        right.addWidget(bbox)
        root.addLayout(right, stretch=3)

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

    def _parse_term_days(self) -> int:
        s = (self._term_edit.text() or "").strip().replace(",", ".")
        if not s:
            return 0
        try:
            v = int(float(s))
            return max(0, v)
        except ValueError:
            raise ValueError("term")

    def _clear_form(self) -> None:
        self._name_edit.clear()
        self._iban_edit.clear()
        self._discount_edit.clear()
        self._term_edit.clear()
        self._alias_list.clear()
        self._customer_code_list.clear()
        self._vat_list.clear()
        self._kvk_list.clear()
        self._domain_list.clear()
        self._vat_rate_combo.setCurrentIndex(0)

    def _fill_str_list_widget(self, lw: QListWidget, items: object) -> None:
        if not isinstance(items, list):
            items = []
        seen: set[str] = set()
        for x in items:
            t = str(x or "").strip()
            if t and t not in seen:
                seen.add(t)
                lw.addItem(t)

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
            try:
                self._term_edit.setText(str(int(s.get("default_payment_term_days") or 0)))
            except (TypeError, ValueError):
                self._term_edit.setText("0")
            self._fill_str_list_widget(self._alias_list, s.get("aliases"))
            self._fill_str_list_widget(self._customer_code_list, s.get("customer_codes"))
            self._fill_str_list_widget(self._vat_list, s.get("vat_numbers"))
            self._fill_str_list_widget(self._kvk_list, s.get("kvk_numbers"))
            self._fill_str_list_widget(self._domain_list, s.get("email_domains"))
            vr = normalize_supplier_vat_rate_pct(s.get("vat_rate", 21))
            idx = self._vat_rate_combo.findData(vr)
            self._vat_rate_combo.setCurrentIndex(idx if idx >= 0 else 0)
            break

    def _on_new(self) -> None:
        self._suppress_list_signal = True
        self._list.clearSelection()
        self._suppress_list_signal = False
        self._editing_original_name = None
        self._clear_form()
        self._name_edit.setReadOnly(False)

    def _current_list_strings(self, lw: QListWidget) -> list[str]:
        out: list[str] = []
        for i in range(lw.count()):
            t = lw.item(i).text().strip()
            if t:
                out.append(t)
        return out

    def _current_aliases_list(self) -> list[str]:
        return self._current_list_strings(self._alias_list)

    def _on_alias_add(self) -> None:
        text, ok = QInputDialog.getText(self, "Alias", "Nieuwe alias:")
        if ok and text.strip():
            self._alias_list.addItem(text.strip())

    def _current_customer_codes_list(self) -> list[str]:
        return self._current_list_strings(self._customer_code_list)

    def _on_customer_code_add(self) -> None:
        text, ok = QInputDialog.getText(self, "Klantcode", "Nieuwe klantcode of lidnummer:")
        if ok and text.strip():
            self._customer_code_list.addItem(text.strip())

    def _on_vat_add(self) -> None:
        text, ok = QInputDialog.getText(self, "BTW-nummer", "NL123456789B01")
        if ok and text.strip():
            self._vat_list.addItem(text.strip().upper())

    def _on_kvk_add(self) -> None:
        text, ok = QInputDialog.getText(self, "KvK-nummer", "7 of 8 cijfers")
        if ok and text.strip():
            self._kvk_list.addItem(text.strip())

    def _on_domain_add(self) -> None:
        text, ok = QInputDialog.getText(self, "E-maildomein", "bijv. leverancier.nl")
        if ok and text.strip():
            d = text.strip().lower().lstrip("@")
            self._domain_list.addItem(d)

    def _on_remove_selected_from_lists(self) -> None:
        for lw in (
            self._alias_list,
            self._customer_code_list,
            self._vat_list,
            self._kvk_list,
            self._domain_list,
        ):
            row = lw.currentRow()
            if row >= 0:
                lw.takeItem(row)
                return
        QMessageBox.information(
            self,
            "Verwijderen uit lijst",
            "Selecteer eerst een regel in een lijst (Aliassen, Klantcodes, BTW, KvK of E-maildomeinen).\n\n"
            "Wil je de hele leverancier verwijderen, gebruik dan ‘Verwijder leverancier’ links.",
        )

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
        try:
            term_days = self._parse_term_days()
        except ValueError:
            QMessageBox.warning(self, "Leveranciers", "Ongeldige betaaltermijn (geheel aantal dagen).")
            return

        aliases = self._current_aliases_list()
        customer_codes = self._current_customer_codes_list()
        vat_numbers = self._current_list_strings(self._vat_list)
        kvk_numbers = self._current_list_strings(self._kvk_list)
        email_domains = self._current_list_strings(self._domain_list)

        vat_rate_raw = self._vat_rate_combo.currentData()
        vat_rate = normalize_supplier_vat_rate_pct(
            vat_rate_raw if vat_rate_raw is not None else 21
        )

        if self._editing_original_name is None:
            before = len(self._db.get_all())
            self._db.add_supplier(
                name,
                iban,
                discount,
                aliases=aliases,
                customer_codes=customer_codes,
                default_payment_term_days=term_days,
                vat_numbers=vat_numbers,
                kvk_numbers=kvk_numbers,
                email_domains=email_domains,
                vat_rate=vat_rate,
            )
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
            default_payment_term_days=term_days,
            vat_rate=vat_rate,
            aliases=aliases,
            overwrite_aliases=True,
            customer_codes=customer_codes,
            overwrite_customer_codes=True,
            vat_numbers=vat_numbers,
            overwrite_vat_numbers=True,
            kvk_numbers=kvk_numbers,
            overwrite_kvk_numbers=True,
            email_domains=email_domains,
            overwrite_email_domains=True,
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
