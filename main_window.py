"""PDF2SEPA desktop client entry: opens the PySide6 main window."""

from __future__ import annotations

import logging
import sys
from copy import deepcopy
from datetime import date
from enum import IntEnum
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple, Optional

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from logic.invoice_folder_loader import load_invoices_from_folder
from logic.payment_engine import calculate_payments, clean_iban, is_plausible_iban
from logic.settings import (
    DEFAULT_SETTINGS,
    load_settings,
    merge_debtor_with_defaults,
    resolve_settings_path,
    save_settings,
    validate_debtor_for_export,
)
from output.sepa_xml import generate_xml
from parser.pdf_parser import format_remittance_text
from parser.supplier_db import SupplierDB
from parser.supplier_matcher import match_suppliers
from ui.suppliers_dialog import SuppliersDialog

logger = logging.getLogger(__name__)

APP_BASE = Path(__file__).resolve().parent

_ERROR_REASON_NL: dict[str, str] = {
    "no_supplier_hint": "Geen leveranciersnaam herkend in PDF; voeg een alias toe of vul handmatig in.",
    "unmatched_supplier": "Leverancier niet gevonden in database; controleer IBAN of aliassen.",
    "needs_review": "Slechts 1 kenmerk gevonden; bevestig de leverancier handmatig.",
    "missing_supplier_name": "Interne fout: leveranciersnaam ontbreekt.",
    "missing_amount": "Bedrag ontbreekt of niet leesbaar in PDF.",
    "credit_note_only": "Alleen creditnota’s zonder bijbehorende factuur.",
    "credit_exceeds_available_invoices": "Creditnota past niet bij beschikbare factuurbedragen.",
    "credit_exceeds_invoice_total": "Creditnota’s overschrijden het factuurbedrag.",
    "zero_amount": "Te betalen bedrag is nul na korting/credit.",
    "negative_amount": "Te betalen bedrag is negatief.",
    "missing_iban": "IBAN ontbreekt in PDF of niet ingevuld.",
    "invalid_iban": "IBAN is ongeldig.",
}

_WARNING_NL: dict[str, str] = {
    "no_excl_vat_amount_discount_skipped": "Geen bedrag excl. BTW; korting niet toegepast.",
    "iban_mismatch_supplier": "IBAN op factuur wijkt af van ‘mijn leveranciers’; controleer of je de leverancier wilt bijwerken.",
}


def _nl_error_reason(reason: str) -> str:
    return _ERROR_REASON_NL.get(reason, reason)


def _nl_payment_warning(warn: object | None) -> str:
    if not warn:
        return ""
    s = str(warn).strip()
    parts = [p.strip() for p in s.split("|") if p.strip()]
    if not parts:
        return ""
    out: list[str] = []
    for key in parts:
        out.append(_WARNING_NL.get(key, key))
    return " · ".join(out)


def _pdf_basename_from_dict(d: dict[str, Any]) -> str:
    sf = d.get("_source_file") or d.get("source_file")
    return Path(str(sf)).name if sf else ""


def _error_row_supplier(inv: dict[str, Any]) -> str:
    sn = inv.get("supplier_name")
    if sn and str(sn).strip():
        return str(sn).strip()
    hint = inv.get("supplier_hint")
    if hint and str(hint).strip():
        return str(hint).strip()
    return ""


def _discount_str_from_inv(inv: dict[str, Any]) -> str:
    d = inv.get("discount")
    if d is None:
        return "0"
    try:
        if isinstance(d, float):
            return str(d).rstrip("0").rstrip(".")
        return str(d)
    except Exception:
        return "0"


class PaymentColumn(IntEnum):
    """Kolomindices voor de betalingstabel."""

    SUPPLIER = 0
    IBAN = 1
    AMOUNT = 2
    CUSTOMER_CODE = 3
    DESCRIPTION = 4
    PDF = 5
    DISCOUNT = 6
    STATUS = 7
    ERROR = 8


# Factuurnummer voor SEPA EndToEndId; opgeslagen op leveranciercel (UserRole).
_ROW_INVOICE_META_ROLE = Qt.ItemDataRole.UserRole
# Ruwe warning-code(s) pipe-gescheiden; voor IBAN-bijwerken en tonen na bewerken.
_ROW_WARNING_RAW_ROLE = Qt.ItemDataRole.UserRole + 1


_READ_ONLY_FLAGS = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

# Zichtbare uitleg voor Instellingen / SEPA (zelfde toon voor toekomstige velden).
_UW_GEGEVENS_XML_HINT = (
    "Deze gegevens (naam, IBAN en BIC) worden gebruikt voor het genereren van de SEPA XML. "
    "Vul ze in via Instellingen."
)

# (key, label, placeholder, inputMask of None). Alleen deze lijst uitbreiden voor nieuwe debtor-velden.
DEBTOR_FORM_FIELDS: tuple[tuple[str, str, str, str | None], ...] = (
    ("name", "Uw naam / bedrijfsnaam:", "Uw naam of bedrijfsnaam", None),
    ("iban", "Uw IBAN:", "NL91 ABNA 0417 1643 00", None),
    ("bic", "Uw BIC:", "ABNANL2A", ">XXXXXXXXxxx;_"),
)


def _normalize_debtor_field(key: str, value: str) -> str:
    if key == "name":
        return str(value or "").strip()
    if key == "iban":
        return clean_iban(value)
    if key == "bic":
        return "".join(c for c in str(value or "") if c.isalnum()).upper()
    return str(value or "").strip()


class PaymentSource(NamedTuple):
    """Gelabelde factuurbron: naam voor status/UI en loader zonder argumenten."""

    name: str
    load: Callable[[], list[dict]]


def _format_amount_nl(amount: float) -> str:
    return f"{amount:.2f}".replace(".", ",")


def _parse_amount_str(raw: str) -> float:
    s = (raw or "").strip().replace(",", ".")
    if not s:
        raise ValueError("leeg bedrag")
    return float(s)


class _AmountTableItem(QTableWidgetItem):
    """Tabelcel Bedrag met numerieke sorteer-sleutel in UserRole."""

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, QTableWidgetItem):
            return NotImplemented
        a = self.data(Qt.ItemDataRole.UserRole)
        b = other.data(Qt.ItemDataRole.UserRole)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return float(a) < float(b)
        return super().__lt__(other)


class SettingsDialog(QDialog):
    """Dialoog voor SEPA debtor-gegevens; formuliervelden komen uit ``DEBTOR_FORM_FIELDS``."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Instellingen")
        self.setMinimumWidth(560)
        self.resize(560, 340)
        root = QVBoxLayout(self)
        info = QLabel("Deze gegevens zijn nodig voor correcte SEPA-export")
        info.setWordWrap(True)
        root.addWidget(info)
        form = QFormLayout()
        root.addLayout(form)
        self._field_edits: dict[str, QLineEdit] = {}
        self._selected_export_dir: Optional[Path] = None
        self._build_form(form)
        self._build_export_dir_row(form)

        bbox = QDialogButtonBox()
        bbox.addButton("Opslaan", QDialogButtonBox.ButtonRole.AcceptRole)
        bbox.addButton("Annuleer", QDialogButtonBox.ButtonRole.RejectRole)
        bbox.accepted.connect(self._on_save)
        bbox.rejected.connect(self.reject)
        root.addWidget(bbox)

    def _build_form(self, form: QFormLayout) -> None:
        mw = self.parent()
        for key, label_text, placeholder, mask in DEBTOR_FORM_FIELDS:
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.setMinimumWidth(300)
            if mask:
                edit.setInputMask(mask)
            elif key == "iban":
                edit.setMaxLength(42)
            elif key == "bic":
                edit.setMaxLength(11)
            if isinstance(mw, MainWindow):
                if key == "name":
                    edit.setText(mw.get_debtor_name())
                elif key == "iban":
                    edit.setText(mw.get_debtor_iban())
                elif key == "bic":
                    edit.setText(mw.get_debtor_bic())
            edit.setToolTip(_UW_GEGEVENS_XML_HINT)
            self._field_edits[key] = edit
            form.addRow(QLabel(label_text), edit)

    def _build_export_dir_row(self, form: QFormLayout) -> None:
        mw = self.parent()
        export_path = None
        if isinstance(mw, MainWindow):
            export_path = mw._resolve_export_dir()

        container = QVBoxLayout()
        container.setSpacing(4)

        self._export_dir_edit = QLineEdit()
        self._export_dir_edit.setReadOnly(True)
        self._export_dir_edit.setMinimumWidth(300)
        self._export_dir_edit.setText(str(export_path) if export_path else "")
        self._export_dir_edit.setToolTip("Hier worden gegenereerde XML bestanden opgeslagen.")
        self._export_dir_edit.setStyleSheet("background-color: palette(window);")
        container.addWidget(self._export_dir_edit)

        btn = QPushButton("Kies map…")
        btn.setFixedWidth(120)
        btn.clicked.connect(self._on_choose_export_dir)
        container.addWidget(btn)

        wrapper = QWidget()
        wrapper.setLayout(container)
        form.addRow(QLabel("Exportmap:"), wrapper)

    def _on_choose_export_dir(self) -> None:
        mw = self.parent()
        if not isinstance(mw, MainWindow):
            return
        start = str(self._selected_export_dir) if self._selected_export_dir else str(mw._resolve_export_dir())
        path: Optional[str] = QFileDialog.getExistingDirectory(self, "Selecteer exportmap", start)
        if not path:
            return
        selected = Path(path).resolve()
        self._selected_export_dir = selected
        self._export_dir_edit.setText(str(selected))

    def _on_save(self) -> None:
        mw = self.parent()
        if not isinstance(mw, MainWindow):
            self.reject()
            return
        updates = {key: self._field_edits[key].text() for key in self._field_edits}
        if not mw._apply_debtor_and_save(updates):
            QMessageBox.warning(
                self,
                "Instellingen",
                "Uw gegevens konden niet worden opgeslagen. Controleer schrijfrechten op "
                "data/settings.json.",
            )
            return
        if self._selected_export_dir is not None:
            if not mw._persist_export_dir(self._selected_export_dir):
                QMessageBox.warning(
                    self,
                    "Instellingen",
                    "De exportmap kon niet worden opgeslagen. Controleer schrijfrechten op "
                    "data/settings.json.",
                )
                return
        self.accept()


class MainWindow(QMainWindow):
    """
    Hoofdvenster voor de PDF2SEPA desktop client.

    Biedt mapselectie voor facturen, een bewerkbaar overzicht van betalingen
    en een actie om SEPA XML te genereren.
    """

    APP_VERSION = "1.0.0"

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PDF2SEPA Desktop Client")
        self._settings: dict[str, Any] = load_settings(str(APP_BASE / "data" / "settings.json"))
        self._ensure_debtor_dict()
        self._selected_folder: Optional[Path] = None
        self._payment_sources: list[PaymentSource] = []
        self._status_label = QLabel("")
        self._table: QTableWidget
        self._filter_edit: QLineEdit
        self._persist_sort_column: Optional[int] = None
        self._persist_sort_order: Qt.SortOrder = Qt.SortOrder.AscendingOrder
        self._sort_persist_connected: bool = False
        self._deleted_rows_undo: list[list[tuple[int, str]]] = []
        self._restore_selected_folder_from_settings()
        self._setup_ui()
        self._setup_shortcuts()
        self._restore_window_geometry()

    def _supplier_db_path(self) -> str:
        return str(APP_BASE / "data" / "suppliers.json")

    def _settings_path(self) -> Path:
        return APP_BASE / "data" / "settings.json"

    def _restore_selected_folder_from_settings(self) -> None:
        raw = str(self._settings.get("last_invoice_dir") or "").strip()
        if not raw:
            self._selected_folder = None
            return
        p = resolve_settings_path(raw, base_dir=APP_BASE)
        self._selected_folder = p if p.is_dir() else None

    def _persist_invoice_folder(self, folder: Path) -> None:
        folder = folder.resolve()
        try:
            rel = folder.relative_to(APP_BASE)
            self._settings["last_invoice_dir"] = str(rel)
        except ValueError:
            self._settings["last_invoice_dir"] = str(folder)
        if not save_settings(self._settings, str(self._settings_path())):
            logger.warning("Kon last_invoice_dir niet opslaan")

    def _persist_export_dir(self, folder: Path) -> bool:
        folder = folder.resolve()
        try:
            rel = folder.relative_to(APP_BASE)
            self._settings["export_dir"] = str(rel)
        except ValueError:
            self._settings["export_dir"] = str(folder)
        if not save_settings(self._settings, str(self._settings_path())):
            logger.warning("Kon export_dir niet opslaan")
            return False
        return True

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(10)

        def _font_primary_button(btn: QPushButton) -> None:
            f = btn.font()
            f.setWeight(QFont.Weight.DemiBold)
            btn.setFont(f)

        toolbar = QFrame()
        toolbar.setFrameShape(QFrame.Shape.StyledPanel)
        tb_outer = QVBoxLayout(toolbar)
        tb_outer.setContentsMargins(8, 8, 8, 8)
        tb_outer.setSpacing(8)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter op leverancier, omschrijving, PDF of klantcode…")
        self._filter_edit.setMinimumWidth(220)
        self._filter_edit.textChanged.connect(self._on_filter_text_changed)

        row_main = QHBoxLayout()
        row_main.setSpacing(8)

        btn_folder = QPushButton("Map selecteren")
        btn_folder.clicked.connect(self._on_select_folder)
        _font_primary_button(btn_folder)
        row_main.addWidget(btn_folder, alignment=Qt.AlignmentFlag.AlignLeft)
        btn_reread = QPushButton("PDF’s uitlezen")
        btn_reread.clicked.connect(self._on_reread_pdfs)
        _font_primary_button(btn_reread)
        row_main.addWidget(btn_reread, alignment=Qt.AlignmentFlag.AlignLeft)
        btn_xml = QPushButton("Maak XML bestand")
        btn_xml.clicked.connect(self._on_make_xml)
        btn_xml.setDefault(True)
        _font_primary_button(btn_xml)
        row_main.addWidget(btn_xml, alignment=Qt.AlignmentFlag.AlignLeft)

        row_main.addSpacing(12)

        btn_add_row = QToolButton()
        btn_add_row.setText("+")
        btn_add_row.setToolTip("Voeg rij toe")
        btn_add_row.clicked.connect(self._on_add_row)
        btn_add_row.setFixedWidth(34)
        row_main.addWidget(btn_add_row, alignment=Qt.AlignmentFlag.AlignLeft)
        btn_del_sel = QToolButton()
        btn_del_sel.setText("\u2212")
        btn_del_sel.setToolTip("Verwijder geselecteerde rijen")
        btn_del_sel.clicked.connect(self._on_delete_selected_rows)
        btn_del_sel.setFixedWidth(34)
        row_main.addWidget(btn_del_sel, alignment=Qt.AlignmentFlag.AlignLeft)

        row_main.addStretch(1)

        btn_suppliers = QPushButton("Mijn leveranciers")
        btn_suppliers.clicked.connect(self._on_open_suppliers)
        row_main.addWidget(btn_suppliers, alignment=Qt.AlignmentFlag.AlignRight)
        btn_sync_suppliers = QPushButton("Voeg toe / update")
        btn_sync_suppliers.setToolTip(
            "Schrijft de geselecteerde rijen naar de leveranciersdatabase "
            "(naam, IBAN, klantcode, korting)."
        )
        btn_sync_suppliers.clicked.connect(self._on_sync_selected_to_suppliers)
        row_main.addWidget(btn_sync_suppliers, alignment=Qt.AlignmentFlag.AlignRight)
        btn_settings = QPushButton()
        btn_settings.setToolTip(_UW_GEGEVENS_XML_HINT)
        btn_settings.setAccessibleName("Instellingen")
        gear = QIcon.fromTheme("preferences-system")
        if not gear.isNull():
            btn_settings.setIcon(gear)
            btn_settings.setIconSize(QSize(22, 22))
        else:
            btn_settings.setText("\u2699")
        btn_settings.clicked.connect(self._on_open_settings)
        btn_settings.setFixedSize(34, 34)
        row_main.addWidget(btn_settings, alignment=Qt.AlignmentFlag.AlignRight)

        row_filter = QHBoxLayout()
        row_filter.setSpacing(8)
        row_filter.addWidget(self._filter_edit, stretch=1)

        tb_outer.addLayout(row_main)
        tb_outer.addLayout(row_filter)

        layout.addWidget(toolbar)

        headers = [
            "Leverancier",
            "IBAN",
            "Bedrag",
            "Klantcode",
            "Omschrijving",
            "PDF",
            "Korting",
            "Status",
            "Foutmelding",
        ]
        self._table = QTableWidget(0, len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        hdr = self._table.horizontalHeader()
        self._DEFAULT_COL_WIDTHS = {
            PaymentColumn.SUPPLIER: 160,
            PaymentColumn.IBAN: 180,
            PaymentColumn.AMOUNT: 90,
            PaymentColumn.CUSTOMER_CODE: 100,
            PaymentColumn.DESCRIPTION: 250,
            PaymentColumn.PDF: 140,
            PaymentColumn.DISCOUNT: 65,
            PaymentColumn.STATUS: 80,
            PaymentColumn.ERROR: 180,
        }
        for col in range(len(headers)):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        self._restore_column_widths()
        hdr.sectionHandleDoubleClicked.connect(
            lambda idx: self._table.resizeColumnToContents(idx)
        )
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)
        layout.addWidget(self._table, stretch=1)

        self._payment_sources = []
        self._refresh_initial_table_and_status()

        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

    def get_debtor_name(self) -> str:
        self._ensure_debtor_dict()
        return str(self._settings["debtor"].get("name") or "").strip()

    def get_debtor_iban(self) -> str:
        self._ensure_debtor_dict()
        return clean_iban(str(self._settings["debtor"].get("iban") or ""))

    def get_debtor_bic(self) -> str:
        self._ensure_debtor_dict()
        return str(self._settings["debtor"].get("bic") or "").strip().upper()

    def get_debtor_dict_for_xml(self) -> dict[str, Any]:
        self._ensure_debtor_dict()
        return {
            "name": self.get_debtor_name(),
            "iban": self.get_debtor_iban(),
            "bic": self.get_debtor_bic(),
        }

    def _ensure_debtor_dict(self) -> None:
        self._settings["debtor"] = merge_debtor_with_defaults(self._settings.get("debtor"))

    def _apply_debtor_and_save(self, updates: dict[str, str]) -> bool:
        self._ensure_debtor_dict()
        prev: dict[str, str] = deepcopy(self._settings["debtor"])
        template = DEFAULT_SETTINGS["debtor"]
        try:
            for key, raw in updates.items():
                if key not in template:
                    continue
                self._settings["debtor"][key] = _normalize_debtor_field(key, raw)
        except Exception:
            self._settings["debtor"] = prev
            logger.exception("Instellingen debtor-normalisatie mislukt")
            return False
        if not save_settings(self._settings, str(self._settings_path())):
            self._settings["debtor"] = prev
            logger.error("Instellingen opslaan mislukt (save_settings heeft False geretourneerd)")
            return False
        return True

    def _on_open_settings(self) -> None:
        SettingsDialog(self).exec()

    def _resolve_export_dir(self) -> Path:
        raw: str = str(self._settings.get("export_dir") or "exports")
        return resolve_settings_path(raw, base_dir=APP_BASE)

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)

    @staticmethod
    def _item_editable(text: str) -> QTableWidgetItem:
        return QTableWidgetItem(text)

    @staticmethod
    def _item_readonly(text: str) -> QTableWidgetItem:
        it = QTableWidgetItem(text)
        it.setFlags(_READ_ONLY_FLAGS)
        return it

    @staticmethod
    def _item_amount(amount_display: str) -> QTableWidgetItem:
        it = _AmountTableItem()
        it.setText(amount_display)
        try:
            key: float = _parse_amount_str(amount_display)
        except ValueError:
            key = float("-inf")
        it.setData(Qt.ItemDataRole.UserRole, key)
        return it

    def _on_sort_indicator_changed(self, logical_index: int, order: Qt.SortOrder) -> None:
        if logical_index < 0:
            self._persist_sort_column = None
            return
        self._persist_sort_column = logical_index
        self._persist_sort_order = order

    def _on_filter_text_changed(self, text: str) -> None:
        self._apply_filter_to_table(text)

    def _row_matches_filter(self, row: int, needle: str) -> bool:
        if not needle:
            return True
        supplier = self._cell_text(row, PaymentColumn.SUPPLIER).casefold()
        description = self._cell_text(row, PaymentColumn.DESCRIPTION).casefold()
        pdf = self._cell_text(row, PaymentColumn.PDF).casefold()
        cust = self._cell_text(row, PaymentColumn.CUSTOMER_CODE).casefold()
        return (
            needle in supplier
            or needle in description
            or needle in pdf
            or needle in cust
        )

    def _apply_filter_to_table(self, filter_text: str) -> None:
        needle = filter_text.strip().casefold()
        for r in range(self._table.rowCount()):
            self._table.setRowHidden(r, bool(needle) and not self._row_matches_filter(r, needle))

    _COLOR_CONFIRMED = QColor(220, 245, 220)
    _COLOR_NEEDS_REVIEW = QColor(255, 248, 200)
    _COLOR_ERROR = QColor(255, 220, 220)
    _TEXT_COLOR_ON_TINT = QColor(0, 0, 0)

    def _apply_row_colors(self) -> None:
        for r in range(self._table.rowCount()):
            status = self._cell_text(r, PaymentColumn.STATUS).lower()
            if status == "fout":
                color = self._COLOR_ERROR
            elif status in ("needs_review", "needs review"):
                color = self._COLOR_NEEDS_REVIEW
            elif status in ("ok", "confirmed", "reviewed", "handmatig"):
                color = self._COLOR_CONFIRMED
            else:
                continue
            for c in range(self._table.columnCount()):
                it = self._table.item(r, c)
                if it:
                    it.setBackground(color)
                    it.setForeground(self._TEXT_COLOR_ON_TINT)

    def _restore_column_widths(self) -> None:
        saved = self._settings.get("column_widths")
        for col, default_w in self._DEFAULT_COL_WIDTHS.items():
            w = default_w
            if isinstance(saved, dict) and str(int(col)) in saved:
                sv = saved[str(int(col))]
                if isinstance(sv, int) and sv > 20:
                    w = sv
            self._table.setColumnWidth(col, w)

    def _save_column_widths(self) -> None:
        widths: dict[str, int] = {}
        for col in range(self._table.columnCount()):
            widths[str(col)] = self._table.columnWidth(col)
        self._settings["column_widths"] = widths

    def _auto_resize_columns_to_content(self) -> None:
        for col in range(self._table.columnCount()):
            self._table.resizeColumnToContents(col)

    def _refresh_initial_table_and_status(self) -> None:
        self._table.setRowCount(0)
        folder_txt = str(self._selected_folder) if self._selected_folder else "—"
        export_path = self._resolve_export_dir()
        self._set_status(
            f"Geen facturen geladen. Laatste map: {folder_txt}. "
            f"Kies een map of klik ‘PDF’s uitlezen’. Exportmap: {export_path}"
        )

    def _on_open_suppliers(self) -> None:
        SuppliersDialog(self._supplier_db_path(), self).exec()

    def _flatten_unique_error_invoices(self, errors: list[dict]) -> list[tuple[dict, str]]:
        seen: set[int] = set()
        out: list[tuple[dict, str]] = []
        for bucket in errors:
            reason = str(bucket.get("reason") or "")
            invs = bucket.get("invoices")
            if not isinstance(invs, list):
                continue
            for inv in invs:
                if not isinstance(inv, dict):
                    continue
                iid = id(inv)
                if iid in seen:
                    continue
                seen.add(iid)
                out.append((inv, reason))
        return out

    def _enrich_payments_with_source_files(
        self, payments: list[dict], invoices: list[dict]
    ) -> None:
        for p in payments:
            sup = str(p.get("supplier_name") or "")
            inv_no = str(p.get("invoice_number") or "")
            for inv in invoices:
                if str(inv.get("supplier_name") or "") != sup:
                    continue
                if inv_no != str(inv.get("invoice_number") or ""):
                    continue
                sf = inv.get("source_file")
                if sf:
                    p["_source_file"] = sf
                break

    def _make_map_folder_source(self, folder: Path) -> PaymentSource:
        selected = folder.resolve()
        debtor_iban = self.get_debtor_iban() or None

        def load() -> list[dict]:
            return load_invoices_from_folder(selected, debtor_iban=debtor_iban)

        return PaymentSource(name=f"Map: {selected.name}", load=load)

    def _deduplicate_invoices(self, invoices: list[dict]) -> tuple[list[dict], int]:
        """Remove duplicate invoices based on filename or invoice_number+supplier_hint."""
        seen_files: set[str] = set()
        seen_keys: set[str] = set()
        unique: list[dict] = []
        skipped = 0

        for inv in invoices:
            sf = str(inv.get("source_file") or "").strip()
            if sf:
                basename = Path(sf).name
                if basename in seen_files:
                    skipped += 1
                    continue
                seen_files.add(basename)

            inv_no = str(inv.get("invoice_number") or "").strip()
            hint = str(inv.get("supplier_hint") or "").strip().lower()
            if inv_no and hint:
                key = f"{hint}|{inv_no}"
                if key in seen_keys:
                    skipped += 1
                    continue
                seen_keys.add(key)

            unique.append(inv)

        return unique, skipped

    def _load_payments_from_sources(self) -> None:
        all_raw: list[dict] = []
        per_source_counts: list[tuple[str, int]] = []
        for src in self._payment_sources:
            invs = src.load()
            per_source_counts.append((src.name, len(invs)))
            all_raw.extend(invs)

        progress = QProgressDialog("PDF's verwerken…", None, 0, 0, self)
        progress.setWindowTitle("Laden")
        progress.setMinimumDuration(300)
        progress.setValue(0)
        QApplication.processEvents()

        all_raw, n_dupes = self._deduplicate_invoices(all_raw)
        if n_dupes:
            logger.info("Duplicaten overgeslagen: %d", n_dupes)

        db = SupplierDB(path=self._supplier_db_path())
        matched = match_suppliers(all_raw, db)
        payments, errors = calculate_payments(matched)
        self._enrich_payments_with_source_files(payments, matched)
        n_err_rows = self._populate_table_from_load(payments, errors, matched)
        progress.close()

        n_pdf = len(all_raw)
        n_pay = len(payments)
        for name, count in per_source_counts:
            logger.info("bron %r: %d pdf-facturen", name, count)
        logger.info("betalingsregels: %d, foutregels: %d", n_pay, n_err_rows)
        self._update_load_status_after_load(
            n_pdf=n_pdf, n_payments=n_pay, n_error_rows=n_err_rows
        )

    def _update_load_status_after_load(
        self,
        *,
        n_pdf: int,
        n_payments: int,
        n_error_rows: int,
    ) -> None:
        folder_txt = str(self._selected_folder) if self._selected_folder else "—"
        export_path = self._resolve_export_dir()
        self._set_status(
            f"PDF’s: {n_pdf}, betalingsregels: {n_payments}, foutregels: {n_error_rows}. "
            f"Map: {folder_txt}. Exportmap: {export_path}"
        )

    def _discount_for_payment(self, invoices: list[dict], payment: dict) -> str:
        """Zoek kortingpercentage uit brondict op leverancier/factuurnummer."""
        sup = str(payment.get("supplier_name") or "")
        inv_no = str(payment.get("invoice_number") or "")
        for inv in invoices:
            if str(inv.get("supplier_name") or "") != sup:
                continue
            if inv_no and str(inv.get("invoice_number") or "") != inv_no:
                continue
            d = inv.get("discount")
            if d is None:
                return "0"
            return str(d).rstrip("0").rstrip(".") if isinstance(d, float) else str(d)
        d = payment.get("discount")
        if d is not None:
            return str(d)
        return "0"

    def _invoice_fields_for_payment(self, invoices: list[dict], payment: dict) -> tuple[str, str]:
        """`(customer_number, invoice_number)` uit de brondict voor deze betalingsregel."""
        sup = str(payment.get("supplier_name") or "")
        inv_no_pay = str(payment.get("invoice_number") or "")
        for inv in invoices:
            if str(inv.get("supplier_name") or "") != sup:
                continue
            if inv_no_pay and str(inv.get("invoice_number") or "") != inv_no_pay:
                continue
            cn = inv.get("customer_number")
            cns = str(cn).strip() if cn is not None else ""
            ins = (
                str(inv.get("invoice_number") or "").strip()
                if inv.get("invoice_number") is not None
                else inv_no_pay
            )
            return cns, ins
        return "", inv_no_pay

    def _get_row_invoice_number(self, row: int) -> str:
        it = self._table.item(row, PaymentColumn.SUPPLIER)
        if not it:
            return ""
        v = it.data(_ROW_INVOICE_META_ROLE)
        return str(v).strip() if v is not None else ""

    def _append_table_row(
        self,
        supplier: str,
        iban: str,
        amount_display: str,
        customer_code: str,
        description: str,
        pdf_name: str,
        discount: str,
        status: str,
        error_msg: str,
        *,
        invoice_number_meta: str = "",
        warning_raw: str | None = None,
    ) -> None:
        r = self._table.rowCount()
        self._table.insertRow(r)
        sup_item = self._item_editable(supplier)
        if invoice_number_meta:
            sup_item.setData(_ROW_INVOICE_META_ROLE, invoice_number_meta)
        self._table.setItem(r, PaymentColumn.SUPPLIER, sup_item)
        self._table.setItem(r, PaymentColumn.IBAN, self._item_editable(iban))
        self._table.setItem(r, PaymentColumn.AMOUNT, self._item_amount(amount_display))
        self._table.setItem(r, PaymentColumn.CUSTOMER_CODE, self._item_editable(customer_code))
        self._table.setItem(r, PaymentColumn.DESCRIPTION, self._item_editable(description))
        pdf_disp = pdf_name if pdf_name.strip() else "—"
        self._table.setItem(r, PaymentColumn.PDF, self._item_readonly(pdf_disp))
        self._table.setItem(r, PaymentColumn.DISCOUNT, self._item_editable(discount))
        self._table.setItem(r, PaymentColumn.STATUS, self._item_readonly(status))
        err_item = self._item_readonly(error_msg)
        if warning_raw:
            err_item.setData(_ROW_WARNING_RAW_ROLE, warning_raw)
        self._table.setItem(r, PaymentColumn.ERROR, err_item)

    def _populate_table_from_load(
        self,
        payments: list[dict],
        errors: list[dict],
        invoices: list[dict],
    ) -> int:
        hdr = self._table.horizontalHeader()
        hdr.blockSignals(True)
        error_row_count = 0
        try:
            self._table.setSortingEnabled(False)
            self._table.setRowCount(0)
            for p in payments:
                amt = p.get("amount")
                amount_str = _format_amount_nl(float(amt)) if amt is not None else ""
                err_cell = _nl_payment_warning(p.get("warning"))
                disc = self._discount_for_payment(invoices, p)
                cust, inv_meta = self._invoice_fields_for_payment(invoices, p)
                desc = format_remittance_text(
                    cust if cust else None,
                    inv_meta if inv_meta else None,
                    p.get("description"),
                )
                pdf = _pdf_basename_from_dict(p)
                wr = p.get("warning")
                self._append_table_row(
                    str(p.get("supplier_name", "")),
                    str(p.get("iban", "")),
                    amount_str,
                    cust,
                    desc,
                    pdf,
                    disc,
                    str(p.get("status", "ok")),
                    err_cell,
                    invoice_number_meta=inv_meta,
                    warning_raw=str(wr).strip() if wr else None,
                )
            needs_review_invs = [
                (inv, r)
                for inv, r in self._flatten_unique_error_invoices(errors)
                if r == "needs_review"
            ]
            other_errors = [
                (inv, r)
                for inv, r in self._flatten_unique_error_invoices(errors)
                if r != "needs_review"
            ]
            for inv, _reason in needs_review_invs:
                amt = inv.get("amount")
                amount_str = _format_amount_nl(float(amt)) if amt is not None else ""
                cust_r = str(inv.get("customer_number") or "").strip()
                inv_meta_r = str(inv.get("invoice_number") or "").strip()
                desc_r = format_remittance_text(
                    cust_r if cust_r else None,
                    inv_meta_r if inv_meta_r else None,
                    inv.get("description"),
                )
                pdf_r = _pdf_basename_from_dict(inv)
                self._append_table_row(
                    _error_row_supplier(inv),
                    str(inv.get("iban") or ""),
                    amount_str,
                    cust_r,
                    desc_r,
                    pdf_r,
                    _discount_str_from_inv(inv),
                    "needs_review",
                    _nl_error_reason("needs_review"),
                    invoice_number_meta=inv_meta_r,
                )
            for inv, reason in other_errors:
                error_row_count += 1
                amt = inv.get("amount")
                amount_str = _format_amount_nl(float(amt)) if amt is not None else ""
                cust_e = str(inv.get("customer_number") or "").strip()
                inv_meta_e = str(inv.get("invoice_number") or "").strip()
                desc_e = format_remittance_text(
                    cust_e if cust_e else None,
                    inv_meta_e if inv_meta_e else None,
                    inv.get("description"),
                )
                pdf_e = _pdf_basename_from_dict(inv)
                self._append_table_row(
                    _error_row_supplier(inv),
                    str(inv.get("iban") or ""),
                    amount_str,
                    cust_e,
                    desc_e,
                    pdf_e,
                    _discount_str_from_inv(inv),
                    "fout",
                    _nl_error_reason(reason),
                    invoice_number_meta=inv_meta_e,
                )
            self._auto_resize_columns_to_content()
            self._table.setSortingEnabled(True)
            if self._persist_sort_column is not None:
                self._table.sortByColumn(self._persist_sort_column, self._persist_sort_order)
        finally:
            hdr.blockSignals(False)
        if not self._sort_persist_connected:
            hdr.sortIndicatorChanged.connect(self._on_sort_indicator_changed)
            self._sort_persist_connected = True
        self._apply_row_colors()
        self._apply_filter_to_table(self._filter_edit.text())
        return error_row_count

    def _on_reread_pdfs(self) -> None:
        folder: Optional[Path] = self._selected_folder
        if folder is None or not folder.is_dir():
            raw = str(self._settings.get("last_invoice_dir") or "").strip()
            if raw:
                folder = resolve_settings_path(raw, base_dir=APP_BASE)
        if folder is None or not folder.is_dir():
            QMessageBox.warning(
                self,
                "PDF’s",
                "Geen geldige factuurmap. Kies eerst een map via ‘Map selecteren’.",
            )
            return
        self._selected_folder = folder
        self._payment_sources = [self._make_map_folder_source(folder)]
        self._load_payments_from_sources()

    def _on_add_row(self) -> None:
        self._append_table_row("", "", "", "", "", "", "0", "handmatig", "", invoice_number_meta="")
        self._refresh_filter_and_sort_after_row_change()

    def _on_table_context_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        status = self._cell_text(row, PaymentColumn.STATUS).lower()
        menu = QMenu(self)
        if status in ("needs_review", "needs review"):
            action_confirm = menu.addAction("Bevestig leverancier")
            action_confirm.triggered.connect(lambda: self._confirm_review_rows([row]))
            selected = self._selected_table_rows()
            review_selected = [
                r for r in selected
                if self._cell_text(r, PaymentColumn.STATUS).lower() in ("needs_review", "needs review")
            ]
            if len(review_selected) > 1:
                action_all = menu.addAction(f"Bevestig alle geselecteerde ({len(review_selected)})")
                action_all.triggered.connect(lambda: self._confirm_review_rows(review_selected))
        if status not in ("fout",):
            action_fout = menu.addAction("Markeer als fout")
            action_fout.triggered.connect(lambda: self._mark_rows_as_error([row]))
        if not menu.isEmpty():
            menu.exec(self._table.viewport().mapToGlobal(pos))

    def _confirm_review_rows(self, rows: list[int]) -> None:
        for r in rows:
            self._table.setItem(r, PaymentColumn.STATUS, self._item_readonly("reviewed"))
            self._table.setItem(r, PaymentColumn.ERROR, self._item_readonly(""))
        self._apply_row_colors()
        self._set_status(f"{len(rows)} rij(en) handmatig bevestigd.")

    def _mark_rows_as_error(self, rows: list[int]) -> None:
        for r in rows:
            self._table.setItem(r, PaymentColumn.STATUS, self._item_readonly("fout"))
            self._table.setItem(r, PaymentColumn.ERROR, self._item_readonly("Handmatig gemarkeerd als fout."))
        self._apply_row_colors()

    def _strip_iban_mismatch_warning_row(self, r: int) -> None:
        err_it = self._table.item(r, PaymentColumn.ERROR)
        raw = err_it.data(_ROW_WARNING_RAW_ROLE) if err_it else None
        if not raw or "iban_mismatch_supplier" not in str(raw):
            return
        parts = [
            p.strip()
            for p in str(raw).split("|")
            if p.strip() and p.strip() != "iban_mismatch_supplier"
        ]
        new_raw = "|".join(parts)
        new_msg = _nl_payment_warning(new_raw) if new_raw else ""
        new_err = self._item_readonly(new_msg)
        if new_raw:
            new_err.setData(_ROW_WARNING_RAW_ROLE, new_raw)
        self._table.setItem(r, PaymentColumn.ERROR, new_err)

    def _on_sync_selected_to_suppliers(self) -> None:
        rows = self._selected_table_rows()
        if not rows:
            QMessageBox.information(
                self,
                "Leveranciers",
                "Selecteer eerst één of meer rijen in de tabel.",
            )
            return
        db = SupplierDB(path=self._supplier_db_path())
        ok = 0
        failed = 0
        changed = False
        for r in rows:
            name = self._cell_text(r, PaymentColumn.SUPPLIER)
            iban = self._cell_text(r, PaymentColumn.IBAN)
            code = self._cell_text(r, PaymentColumn.CUSTOMER_CODE)
            disc_raw = self._cell_text(r, PaymentColumn.DISCOUNT)
            if not name or not iban:
                failed += 1
                continue
            try:
                d = float(disc_raw.replace(",", ".")) if disc_raw.strip() else 0.0
            except ValueError:
                d = 0.0
            merged = db.merge_or_add_supplier(name, iban, code or None, d)
            updated = db.update_supplier(name, iban=iban, discount=d)
            if merged or updated:
                ok += 1
                self._strip_iban_mismatch_warning_row(r)
                changed = True
            else:
                failed += 1
        if changed:
            self._refresh_filter_and_sort_after_row_change()
        msg = f"Verwerkt: {ok} leverancier(s) toegevoegd of bijgewerkt."
        if failed:
            msg += f" Overgeslagen (ontbrekende naam/IBAN of niet opgeslagen): {failed}."
        QMessageBox.information(self, "Leveranciers", msg)

    def _selected_table_rows(self) -> list[int]:
        n = self._table.rowCount()
        rows = {idx.row() for idx in self._table.selectedIndexes() if 0 <= idx.row() < n}
        return sorted(rows)

    def _refresh_filter_and_sort_after_row_change(self) -> None:
        if self._persist_sort_column is not None:
            self._table.sortByColumn(self._persist_sort_column, self._persist_sort_order)
        self._apply_filter_to_table(self._filter_edit.text())

    def _on_delete_selected_rows(self) -> None:
        selected = self._selected_table_rows()
        if not selected:
            return
        deleted_data: list[list[tuple[int, str]]] = []
        for r in sorted(selected, reverse=True):
            if 0 <= r < self._table.rowCount():
                row_data: list[tuple[int, str]] = []
                for c in range(self._table.columnCount()):
                    it = self._table.item(r, c)
                    row_data.append((c, it.text() if it else ""))
                deleted_data.append(row_data)
                self._table.removeRow(r)
        if deleted_data:
            self._deleted_rows_undo.append(deleted_data[0])
        self._refresh_filter_and_sort_after_row_change()

    def _on_undo_delete(self) -> None:
        if not self._deleted_rows_undo:
            return
        row_data = self._deleted_rows_undo.pop()
        r = self._table.rowCount()
        self._table.insertRow(r)
        for c, text in row_data:
            self._table.setItem(r, c, QTableWidgetItem(text))
        self._refresh_filter_and_sort_after_row_change()

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+O"), self).activated.connect(self._on_select_folder)
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self._on_reread_pdfs)
        QShortcut(QKeySequence("Ctrl+E"), self).activated.connect(self._on_make_xml)
        QShortcut(QKeySequence("Delete"), self).activated.connect(self._on_delete_selected_rows)
        QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self._on_undo_delete)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(
            lambda: self._filter_edit.setFocus()
        )
        QShortcut(QKeySequence("F1"), self).activated.connect(self._on_about)

    def _restore_window_geometry(self) -> None:
        w = self._settings.get("window_width")
        h = self._settings.get("window_height")
        x = self._settings.get("window_x")
        y = self._settings.get("window_y")
        if isinstance(w, int) and isinstance(h, int) and w > 200 and h > 100:
            self.resize(w, h)
        if isinstance(x, int) and isinstance(y, int):
            self.move(x, y)

    def _save_window_geometry(self) -> None:
        geo = self.geometry()
        self._settings["window_width"] = geo.width()
        self._settings["window_height"] = geo.height()
        self._settings["window_x"] = geo.x()
        self._settings["window_y"] = geo.y()
        self._save_column_widths()
        save_settings(self._settings, str(self._settings_path()))

    def closeEvent(self, event) -> None:
        self._save_window_geometry()
        super().closeEvent(event)

    def _log_export(self, xml_path: str, payments: list[dict], total: float) -> None:
        """Append an entry to exports/export_log.json for audit trail."""
        import json
        log_path = self._resolve_export_dir() / "export_log.json"
        try:
            entries: list = []
            if log_path.exists():
                with open(log_path, "r", encoding="utf-8") as f:
                    entries = json.loads(f.read() or "[]")
                if not isinstance(entries, list):
                    entries = []
            entries.append({
                "timestamp": date.today().isoformat(),
                "file": Path(xml_path).name,
                "n_payments": len(payments),
                "total_eur": total,
            })
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(entries, indent=2, ensure_ascii=False))
                f.write("\n")
        except Exception:
            logger.debug("Export log schrijven mislukt", exc_info=True)

    def _on_about(self) -> None:
        QMessageBox.about(
            self,
            "Over PDF2SEPA",
            f"<h2>PDF2SEPA</h2>"
            f"<p>Versie {self.APP_VERSION}</p>"
            f"<p>Converteer PDF-facturen naar SEPA XML (pain.001.001.09) "
            f"voor ING Mijn Zakelijk.</p>",
        )

    def _on_select_folder(self) -> None:
        start = str(self._selected_folder) if self._selected_folder else ""
        path: Optional[str] = QFileDialog.getExistingDirectory(
            self, "Selecteer map met facturen", start
        )
        if not path:
            return
        selected = Path(path).resolve()
        self._selected_folder = selected
        self._persist_invoice_folder(selected)
        self._payment_sources = [self._make_map_folder_source(selected)]
        self._load_payments_from_sources()

    def _cell_text(self, row: int, col: int) -> str:
        it = self._table.item(row, col)
        return (it.text() if it else "").strip()

    def _is_row_blank(self, row: int) -> bool:
        sup = self._cell_text(row, PaymentColumn.SUPPLIER)
        iban = self._cell_text(row, PaymentColumn.IBAN)
        amt = self._cell_text(row, PaymentColumn.AMOUNT)
        desc = self._cell_text(row, PaymentColumn.DESCRIPTION)
        disc = self._cell_text(row, PaymentColumn.DISCOUNT)
        disc_norm = "" if disc in ("0", "0.0", "0,0") else disc
        return not (sup or iban or amt or desc or disc_norm)

    def _payment_dict_from_row(self, row: int) -> dict[str, Any]:
        disc_raw = self._cell_text(row, PaymentColumn.DISCOUNT)
        inv_no = self._get_row_invoice_number(row)
        return {
            "supplier_name": self._cell_text(row, PaymentColumn.SUPPLIER),
            "iban": self._cell_text(row, PaymentColumn.IBAN),
            "amount": _parse_amount_str(self._cell_text(row, PaymentColumn.AMOUNT)),
            "description": self._cell_text(row, PaymentColumn.DESCRIPTION),
            "invoice_number": inv_no,
            "discount": disc_raw if disc_raw else "0",
        }

    def _table_rows_to_payment_dicts(self) -> list[dict[str, Any]]:
        """Lees bewerkte tabel uit naar dicts voor ``generate_xml`` (niet-lege rijen)."""
        rows: list[dict[str, Any]] = []
        for r in range(self._table.rowCount()):
            if self._is_row_blank(r):
                continue
            rows.append(self._payment_dict_from_row(r))
        return rows

    def _clear_row_validation_marks(self) -> None:
        _KEEP = frozenset({"ok", "confirmed", "reviewed", "handmatig", "needs_review", "needs review"})
        for r in range(self._table.rowCount()):
            status = self._cell_text(r, PaymentColumn.STATUS).lower()
            if status not in _KEEP:
                self._table.setItem(r, PaymentColumn.STATUS, self._item_readonly(""))
                self._table.setItem(r, PaymentColumn.ERROR, self._item_readonly(""))

    def _set_row_validation(self, row: int, status: str, error: str) -> None:
        self._table.setItem(row, PaymentColumn.STATUS, self._item_readonly(status))
        self._table.setItem(row, PaymentColumn.ERROR, self._item_readonly(error))

    def _validate_single_payment_row(self, p: dict[str, Any]) -> Optional[str]:
        if not str(p.get("supplier_name") or "").strip():
            return "leverancier is leeg"
        iban_n = clean_iban(str(p.get("iban") or ""))
        if not iban_n or not is_plausible_iban(iban_n):
            return "IBAN ontbreekt of is ongeldig"
        try:
            amt = float(p["amount"])
        except (KeyError, TypeError, ValueError):
            return "bedrag is ongeldig"
        if amt <= 0:
            return "bedrag moet groter zijn dan nul"
        return None

    def _validate_debtor(self) -> Optional[str]:
        self._ensure_debtor_dict()
        return validate_debtor_for_export(self._settings["debtor"])

    def _on_make_xml(self) -> None:
        self._set_status("XML generatie gestart …")
        QApplication.processEvents()

        err_debt = self._validate_debtor()
        if err_debt:
            self._set_status(f"Fout: {err_debt}")
            return

        review_rows: list[int] = []
        for r in range(self._table.rowCount()):
            if self._cell_text(r, PaymentColumn.STATUS).lower() in ("needs_review", "needs review"):
                review_rows.append(r)

        if review_rows:
            answer = QMessageBox.question(
                self,
                "Onbevestigde leveranciers",
                f"{len(review_rows)} rij(en) heeft slechts 1 kenmerk (needs_review).\n\n"
                "Ja = bevestig en neem mee in export.\n"
                "Nee = sla over (rest wordt wel geëxporteerd).",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                for r in review_rows:
                    self._table.setItem(r, PaymentColumn.STATUS, self._item_readonly("reviewed"))
                self._apply_row_colors()

        self._clear_row_validation_marks()
        QApplication.processEvents()

        invalid: list[tuple[int, str]] = []
        payment_dicts: list[dict[str, Any]] = []

        for r in range(self._table.rowCount()):
            if self._is_row_blank(r):
                continue
            status = self._cell_text(r, PaymentColumn.STATUS).lower()
            if status in ("fout", "needs_review", "needs review"):
                continue
            try:
                p = self._payment_dict_from_row(r)
            except ValueError:
                invalid.append((r, "ongeldig bedrag"))
                continue
            msg = self._validate_single_payment_row(p)
            if msg:
                invalid.append((r, msg))
            else:
                payment_dicts.append(p)

        if invalid:
            for r, msg in invalid:
                self._set_row_validation(r, "fout", msg)
            if len(invalid) == 1:
                self._set_status(f"Fout: rij {invalid[0][0] + 1}: {invalid[0][1]}")
            else:
                self._set_status(f"Fout: {len(invalid)} rijen ongeldig (zie Foutmelding-kolom)")
            return

        if not payment_dicts:
            self._set_status("Fout: geen betalingsregels om te exporteren")
            return

        total_amount = sum(p.get("amount", 0) for p in payment_dicts)
        confirm = QMessageBox.question(
            self,
            "Bevestig XML export",
            f"{len(payment_dicts)} betaling(en), totaal EUR {_format_amount_nl(total_amount)}.\n\nDoorgaan?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            self._set_status("XML export geannuleerd.")
            return

        for r in range(self._table.rowCount()):
            if self._is_row_blank(r):
                continue
            self._set_row_validation(r, "ok", "")

        out_dir = self._resolve_export_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        debtor_for_xml: dict[str, Any] = self.get_debtor_dict_for_xml()
        execution_date = date.today().isoformat()

        try:
            abspath = generate_xml(
                payment_dicts,
                debtor_for_xml,
                execution_date,
                str(out_dir),
            )
        except ValueError as e:
            self._set_status(f"Fout: {e}")
            return
        except OSError as e:
            self._set_status(f"Fout: kan bestand niet schrijven ({e})")
            return

        name = Path(abspath).name
        self._set_status(
            f"XML succesvol aangemaakt: {name}\n"
            f"{len(payment_dicts)} betaling(en), totaal EUR {_format_amount_nl(total_amount)}."
        )
        self._log_export(abspath, payment_dicts, total_amount)


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(1100, 560)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
