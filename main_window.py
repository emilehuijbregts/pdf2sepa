"""PDF2SEPA desktop client entry: opens the PySide6 main window."""

from __future__ import annotations

import logging
import json
import re
import shutil
import sys
import uuid
import time
from dataclasses import dataclass
from copy import deepcopy
from datetime import date
from decimal import Decimal
from enum import IntEnum
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple, Optional

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QIcon, QKeySequence, QShortcut
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
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from logic.invoice_folder_loader import load_invoices_from_folder, strip_raw_text_from_invoices
from logic.decision_store import DecisionStore
from logic.decision_store import UserApprovalStore
from logic.diagnostics import build_diagnostics, build_invoice_diagnostics_snapshot
from logic.profile_learning import (
    can_offer_profile_learning,
    confirm_invoice_fields,
    confirmed_amount_xml,
    profile_field_keys_missing,
    profile_learning_block_reason,
)
from ui.diagnostics_dialog import DiagnosticsDialog
from ui.profile_confirm_dialog import ProfileConfirmDialog
from logic.payment_decisions import (
    DECISION_EXCLUDED,
    DECISION_INCLUDED,
    DECISION_NEEDS_REVIEW,
    REASON_MISSING_DECISION_IN_STORE,
    REASON_MANUAL_PENDING,
    REASON_RUNTIME_MISMATCH,
    REASON_USER_APPROVED,
    REASON_USER_MARKED_ERROR,
    EngineInputRow,
    EngineInputSchema,
    build_decision,
    build_engine_snapshot,
    decision_reason_text_nl,
    decision_status_label_nl,
    normalize_decision,
    stable_hash,
)
from logic.payment_amounts import (
    amount_to_decimal,
    format_eur_xml,
    incl_amount_to_excl_for_discount,
    normalize_supplier_vat_rate_pct,
    resolved_payment_amount_for_export,
    sum_decimals,
)
from logic.payment_dates import (
    execution_date_for_direct,
    execution_date_for_due,
    format_date_nl_from_iso,
    is_valid_iso_date_str,
    is_weekend,
    parse_iso_date,
    parse_ui_date_to_iso,
)
from logic.payment_engine import calculate_payments
from logic.validation import clean_iban, is_plausible_iban
from logic.paths import read_user_data_root, write_user_data_root
from logic.settings import (
    DEFAULT_SETTINGS,
    apply_legacy_export_dir_migration,
    load_settings,
    merge_debtor_with_defaults,
    resolve_settings_path,
    save_settings,
    validate_debtor_for_export,
)
from output.sepa_xml import (
    exportable_payments_from_decisions,
    format_batch_export_blocked_message,
    generate_xml,
    validate_export_batch,
)
from parser.pdf_parser import extract_text_strict, format_remittance_text
from parser.supplier_db import SupplierDB
from parser.supplier_matcher import match_suppliers
from ui.suppliers_dialog import SuppliersDialog

logger = logging.getLogger(__name__)

# #region agent log (debug mode)
_DEBUG_LOG_PATH = "/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-9b0168.log"
_DEBUG_SESSION_ID = "9b0168"


def _dbg_log(*, hypothesis_id: str, location: str, message: str, data: dict[str, Any] | None = None, run_id: str = "pre-fix") -> None:
    try:
        payload = {
            "sessionId": _DEBUG_SESSION_ID,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
            "runId": run_id,
        }
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return

# #endregion

# #region agent log (debug mode - session 8539bd)
_DEBUG_8539_PATH = "/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-8539bd.log"
_DEBUG_8539_SESSION = "8539bd"


def _dbg8539(
    *,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any] | None = None,
    run_id: str = "pre-fix",
) -> None:
    try:
        payload = {
            "sessionId": _DEBUG_8539_SESSION,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
            "runId": run_id,
        }
        with open(_DEBUG_8539_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return

# #endregion

# #region agent log (debug mode - session a6a30a)
_DEBUG_A6_PATH = "/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-a6a30a.log"
_DEBUG_A6_SESSION = "a6a30a"


def _dbg_a6(*, hypothesis_id: str, location: str, message: str, data: dict[str, Any] | None = None, run_id: str = "ui-run") -> None:
    try:
        payload = {
            "sessionId": _DEBUG_A6_SESSION,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
            "runId": run_id,
        }
        with open(_DEBUG_A6_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return

# #endregion

# Hard guard for decision-store access discipline.
# Debug UI must never affect core rendering/decision resolution behavior.
DECISION_STORE_GUARD_ENABLED = True


@dataclass(frozen=True)
class TraceStepVM:
    rule_name: str
    input_fields_used: list[str]
    outcome: str
    score: str | None = None


@dataclass(frozen=True)
class ResolvedRowViewModel:
    row: int
    row_id: str
    row_data: dict[str, Any]
    decision: dict[str, Any] | None


class GuardedDecisionStore:
    """UI-only proxy: can crash on access outside resolver when guard enabled."""

    def __init__(self, inner: DecisionStore, *, is_allowed: Callable[[], bool]) -> None:
        self._inner = inner
        self._is_allowed = is_allowed

    def _check(self, method: str) -> None:
        if DECISION_STORE_GUARD_ENABLED and not self._is_allowed():
            raise RuntimeError(f"DecisionStore accessed outside resolver: {method}")

    def all_runs(self):
        self._check("all_runs")
        return self._inner.all_runs()

    def get_run(self, run_id: str):
        self._check("get_run")
        return self._inner.get_run(run_id)

    def begin_run(self, **kwargs):
        # begin/commit are part of rerun transaction (not a render read)
        return self._inner.begin_run(**kwargs)

    def commit_run(self, run_id: str, **kwargs):
        return self._inner.commit_run(run_id, **kwargs)

    def fail_run(self, run_id: str):
        return self._inner.fail_run(run_id)

    def committed_decision_map(self, run_id: str | None = None):
        self._check("committed_decision_map")
        return self._inner.committed_decision_map(run_id=run_id)

# region agent log
def _agent_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    try:
        import json, time  # noqa: E401

        payload = {
            "sessionId": "c9cbe4",
            "runId": "pre-fix",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(
            "/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-c9cbe4.log",
            "a",
            encoding="utf-8",
        ) as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
# endregion

APP_BASE = Path(__file__).resolve().parent

_ERROR_REASON_NL: dict[str, str] = {
    "no_supplier_hint": "Geen leveranciersnaam herkend in PDF; voeg een alias toe of vul handmatig in.",
    "unmatched_supplier": "Leverancier niet gevonden in database; controleer IBAN of aliassen.",
    "needs_review": "Slechts 1 kenmerk gevonden; bevestig de leverancier handmatig.",
    "missing_supplier_name": "Interne fout: leveranciersnaam ontbreekt.",
    "missing_amount": "Bedrag ontbreekt of niet leesbaar in PDF.",
    "amount_ambiguous": "Meerdere bedragen gevonden — selecteer het juiste bedrag.",
    "amount_uncertain": "Bedrag niet met voldoende zekerheid uit de PDF af te leiden — controleer het totaal of vul handmatig in.",
    "amount_failed": "Bedragextractie is mislukt; controleer de factuur handmatig.",
    "credit_note_only": "Alleen creditnota’s zonder bijbehorende factuur.",
    "credit_exceeds_available_invoices": "Creditnota past niet bij beschikbare factuurbedragen.",
    "credit_exceeds_invoice_total": "Creditnota’s overschrijden het factuurbedrag.",
    "zero_amount": "Te betalen bedrag is nul na korting/credit.",
    "negative_amount": "Te betalen bedrag is negatief.",
    "missing_iban": "IBAN ontbreekt in PDF of niet ingevuld.",
    "invalid_iban": "IBAN is ongeldig.",
    "pdf_read_failed": "PDF kon niet worden gelezen (bestand beschadigd, versleuteld of geen geldige PDF).",
    "pdf_no_text": "PDF bevat geen uitleesbare tekst (vaak een scan); los dit op in de brondocumenten of voeg tekst toe.",
}

_WARNING_NL: dict[str, str] = {
    "no_excl_vat_amount_discount_skipped": "Geen bedrag excl. BTW; korting niet toegepast.",
    "iban_mismatch_supplier": "IBAN komt niet overeen met bekende leverancier — controleer naam en rekening.",
    "supplier_term_not_applied": "Leverancier niet automatisch bevestigd → betaaltermijn niet toegepast.",
    "missing_invoice_date": "Factuurdatum onbekend; vul handmatig in voor ‘op uiterste datum’.",
    "amount_low_confidence": "Bedrag is onduidelijk (mogelijk verkeerd) — controleer de factuur.",
    "amount_tentative": "Voorlopig bedrag (hoogste betrouwbaarheid) — controleer vóór betaling.",
    "amount_ambiguous": "Meerdere bedragen gevonden — selecteer het juiste bedrag.",
    "amount_uncertain": "Bedrag niet met voldoende zekerheid uit de PDF af te leiden — controleer het totaal of vul handmatig in.",
}

def _nl_error_reason(reason: str) -> str:
    return _ERROR_REASON_NL.get(reason, reason)

_SIGNAL_LABELS: dict[str, str] = {
    "iban": "IBAN",
    "customer_number": "klantnummer",
    "invoice_number": "factuurnummer",
    "supplier_hint": "naam uit factuurtekst",
    "email_domain": "e-maildomein",
    "kvk": "KvK",
    "vat": "BTW-nummer",
    "payment_term": "betalingstermijn",
}

def _matches_completeness_text(inv: dict[str, Any]) -> str:
    missing: list[str] = []
    if not str(inv.get("invoice_number") or "").strip():
        missing.append("factuurnummer")
    if not str(inv.get("customer_number") or "").strip():
        missing.append("klantnummer")
    if not str(inv.get("invoice_date") or "").strip():
        missing.append("factuurdatum")
    if not str(inv.get("iban") or "").strip():
        missing.append("IBAN")
    return "Volledig ✓" if not missing else f"Mist: {', '.join(missing)}"

def _core_matches_text(inv: dict[str, Any]) -> str:
    source = str(inv.get("supplier_match_source") or "").strip()
    core = inv.get("db_core_matches") or []
    core_clean = [str(x).strip() for x in core if str(x).strip()]
    mi = inv.get("match_info") if isinstance(inv.get("match_info"), dict) else {}
    alias_note = " · naam herkend (geen kernkenmerk)" if mi.get("alias_match") else ""

    db_only = inv.get("supplier_db_traits_not_on_invoice") or []
    db_only_clean = [str(x).strip() for x in db_only if str(x).strip()]
    db_note = ""
    if db_only_clean and len(core_clean) < 2:
        db_note = f" · in DB: {', '.join(db_only_clean)} (niet op factuur)"

    if source == "db_match":
        if len(core_clean) >= 2:
            return f"2/2 kernkenmerken ({', '.join(core_clean[:2])})"
        if core_clean:
            return f"1/2 kernkenmerken ({core_clean[0]}) · 2e kenmerk mist op factuur{db_note}{alias_note}"
        return f"0/2 kernkenmerken{db_note}{alias_note}"

    # Voor nieuwe leveranciers: sterke fallback-signalen als voorlopige kernmatch.
    signals = inv.get("match_signals") or []
    signal_set = {str(s).strip() for s in signals if str(s).strip()}
    fallback_order = ["iban", "customer_number", "kvk", "vat", "email_domain"]
    provisional = [k for k in fallback_order if k in signal_set][:2]
    provisional_labels = [_SIGNAL_LABELS.get(k, k) for k in provisional]
    if len(provisional_labels) >= 2:
        return f"2/2 voorlopig ({' + '.join(provisional_labels)})"
    if provisional_labels:
        return f"1/2 voorlopig ({provisional_labels[0]})"
    return "0/2 voorlopig"

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

def _diagnostics_snapshot_from_invoice(inv: dict) -> dict:
    return build_invoice_diagnostics_snapshot(inv if isinstance(inv, dict) else {})


def _ident_field_display_from_inv(inv: dict[str, Any], field: str) -> str:
    """Celweergave; ``?`` als parser twijfelt en er kandidaten zijn."""
    legacy = str(inv.get(field) or "").strip()
    res = inv.get(f"{field}_result")
    if not isinstance(res, dict):
        return legacy
    val = str(res.get("value") or legacy).strip()
    st = str(res.get("status") or "").lower()
    cands = res.get("candidates")
    n_cands = len(cands) if isinstance(cands, list) else 0
    if val:
        return val
    if n_cands and st in ("ambiguous", "tentative", "failed"):
        if st == "ambiguous" or n_cands >= 2:
            return "?"
    return legacy


def _remittance_display_from_inv(inv: dict[str, Any]) -> str:
    cust = _ident_field_display_from_inv(inv, "customer_number")
    inv_no = _ident_field_display_from_inv(inv, "invoice_number")
    return format_remittance_text(
        None if cust in ("", "?") else cust,
        None if inv_no in ("", "?") else inv_no,
        inv.get("description"),
    )


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

def _parse_term_days_from_text(raw: str) -> int | None:
    try:
        s = str(raw or "").strip()
        if not s:
            return 0
        m = re.search(r"-?\d+", s)
        if not m:
            return None
        v = int(m.group(0))
        if v < 0:
            return None
        return v
    except Exception:
        return None

class PaymentColumn(IntEnum):
    """Kolomindices voor de betalingstabel."""

    SUPPLIER = 0
    IBAN = 1
    AMOUNT = 2
    CUSTOMER_CODE = 3
    DESCRIPTION = 4
    PDF = 5
    DISCOUNT = 6
    INVOICE_DATE = 7
    EXECUTION_DATE = 8
    TERM_HINT = 9
    CORE_MATCHES = 10
    MATCH_COMPLETE = 11
    STATUS = 12
    ERROR = 13
    INFO = 14

# Factuurnummer voor SEPA EndToEndId; opgeslagen op leveranciercel (UserRole).
_ROW_INVOICE_META_ROLE = Qt.ItemDataRole.UserRole
# Ruwe warning-code(s) pipe-gescheiden; voor IBAN-bijwerken en tonen na bewerken.
_ROW_WARNING_RAW_ROLE = Qt.ItemDataRole.UserRole + 1
_ROW_INVOICE_DATE_SOURCE_ROLE = Qt.ItemDataRole.UserRole + 2
_ROW_DATE_MODE_ROLE = Qt.ItemDataRole.UserRole + 3
_ROW_EFFECTIVE_TERM_ROLE = Qt.ItemDataRole.UserRole + 4
_ROW_TERM_TRUSTED_ROLE = Qt.ItemDataRole.UserRole + 5
_ROW_EMAIL_DOMAIN_ROLE = Qt.ItemDataRole.UserRole + 6
_ROW_KVK_NUMBER_ROLE = Qt.ItemDataRole.UserRole + 7
_ROW_VAT_NUMBER_ROLE = Qt.ItemDataRole.UserRole + 8
_ROW_BASE_INCL_ROLE = Qt.ItemDataRole.UserRole + 9
_ROW_BASE_EXCL_ROLE = Qt.ItemDataRole.UserRole + 10
_ROW_DECISION_TRACE_ROLE = Qt.ItemDataRole.UserRole + 11
_ROW_AMOUNT_RESULT_ROLE = Qt.ItemDataRole.UserRole + 12
_ROW_DECISION_ROLE = Qt.ItemDataRole.UserRole + 13
_ROW_ROW_ID_ROLE = Qt.ItemDataRole.UserRole + 14
_ROW_RENDER_HASH_ROLE = Qt.ItemDataRole.UserRole + 15
_ROW_SUPPLIER_ORIGINAL_ROLE = Qt.ItemDataRole.UserRole + 16
_ROW_INVOICE_DIAGNOSTICS_ROLE = Qt.ItemDataRole.UserRole + 17
_ROW_INVOICE_NUMBER_RESULT_ROLE = Qt.ItemDataRole.UserRole + 18
_ROW_CUSTOMER_NUMBER_RESULT_ROLE = Qt.ItemDataRole.UserRole + 19

_READ_ONLY_FLAGS = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

_AMOUNT_SOURCE_NL: dict[str, str] = {
    "total_label_payable": "Totaal te betalen",
    "total_label_invoice": "Factuurbedrag",
    "total_label_generic": "Totaal",
    "total_label_excl": "Totaal excl. BTW",
    "total_line_hint": "Totaalregel (fallback)",
    "fallback_last_token": "Laatste bedrag in PDF",
    "INCL_CONFLICT": "Meerdere incl.-bedragen",
    "CONFLICTING_HIGH_CONFIDENCE": "Conflicterende totalen",
}


def _nl_amount_candidate_source(source: str) -> str:
    s = str(source or "").strip()
    return _AMOUNT_SOURCE_NL.get(s, s.replace("_", " ").title() if s else "Bedrag")


def _amount_candidate_type_hint_nl(cand: dict[str, Any]) -> str:
    """Korte tag zodat gemengde incl./excl.-kandidaten in het menu onderscheidbaar zijn."""
    t = str(cand.get("type") or "").strip().lower()
    if t == "incl":
        return ""
    if t == "excl":
        return " [excl. BTW]"
    if t == "vat":
        return " [BTW]"
    if t == "unknown":
        return " [type onbekend]"
    return f" [{t}]" if t else ""


def _compact_parsed_amount_result_for_trace(ar: dict[str, Any]) -> dict[str, Any]:
    cands = ar.get("candidates")
    cand_count = len(cands) if isinstance(cands, list) else 0
    val = ar.get("value")
    if val is None:
        val = ar.get("selected_amount")
    return {
        "status": str(ar.get("status") or ar.get("amount_status") or ""),
        "source": str(ar.get("source") or ""),
        "value": str(val) if val is not None else None,
        "confidence": ar.get("confidence"),
        "candidate_count": cand_count,
    }


def _merge_decision_trace_parsed_amount(existing: dict[str, Any] | None, ar: dict[str, Any]) -> dict[str, Any]:
    base = deepcopy(existing) if isinstance(existing, dict) else {}
    snap = base.get("reconciliation_snapshot")
    if not isinstance(snap, dict):
        snap = {}
    snap["parsed_amount_result"] = _compact_parsed_amount_result_for_trace(ar)
    base["reconciliation_snapshot"] = snap
    return base

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
    (
        "kvk",
        "Uw KvK-nummer:",
        "Alleen cijfers, 7 of 8 posities",
        None,
    ),
    (
        "vat",
        "Uw BTW-nummer:",
        "NL123456789B01",
        None,
    ),
)

_DEBTOR_KVK_VAT_TOOLTIP = (
    "Wordt niet in de SEPA-XML gezet. Wel om uw eigen KvK/BTW op facturen uit te sluiten "
    "bij het herkennen van leveranciers (wordt nooit als leverancier-KvK of -BTW gebruikt)."
)

def _normalize_debtor_field(key: str, value: str) -> str:
    if key == "name":
        return str(value or "").strip()
    if key == "iban":
        return clean_iban(value)
    if key == "bic":
        return "".join(c for c in str(value or "") if c.isalnum()).upper()
    if key == "kvk":
        d = re.sub(r"\D", "", str(value or ""))
        return d if len(d) in (7, 8) else ""
    if key == "vat":
        return re.sub(r"\s+", "", str(value or "")).upper()
    return str(value or "").strip()

class PaymentSource(NamedTuple):
    """Gelabelde factuurbron: naam voor status/UI en loader zonder argumenten."""

    name: str
    load: Callable[[], list[dict]]

def _format_amount_nl(amount: object) -> str:
    try:
        return format_eur_xml(amount_to_decimal(amount)).replace(".", ",")
    except ValueError:
        return "?"

def _term_status_label(trusted: bool | None, effective_days: int) -> str:
    if trusted is True:
        return f"Termijn: {effective_days} dagen (toegepast)"
    if trusted is False:
        return "Termijn niet toegepast (onzekere match)"
    return "—"

def _parse_amount_str(raw: str) -> Decimal:
    s = (raw or "").strip().replace(",", ".")
    if not s:
        raise ValueError("leeg bedrag")
    return amount_to_decimal(s)

class _AmountTableItem(QTableWidgetItem):
    """Tabelcel Bedrag met numerieke sorteer-sleutel in UserRole."""

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, QTableWidgetItem):
            return NotImplemented
        a = self.data(Qt.ItemDataRole.UserRole)
        b = other.data(Qt.ItemDataRole.UserRole)
        if isinstance(a, str) and isinstance(b, str):
            try:
                return amount_to_decimal(a) < amount_to_decimal(b)
            except ValueError:
                return super().__lt__(other)
        return super().__lt__(other)


class _DateTableItem(QTableWidgetItem):
    """Factuur-/betaaldatum met ISO-sorteer-sleutel (UserRole) los van ``dd-mm-jjjj``-tekst."""

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, QTableWidgetItem):
            return NotImplemented
        a = self.data(Qt.ItemDataRole.UserRole)
        b = other.data(Qt.ItemDataRole.UserRole)
        a_ok = isinstance(a, str) and len(a) == 10 and a[4] == "-"
        b_ok = isinstance(b, str) and len(b) == 10 and b[4] == "-"
        if not a_ok and not b_ok:
            return super().__lt__(other)
        if not a_ok:
            return False
        if not b_ok:
            return True
        return str(a) < str(b)


class SettingsDialog(QDialog):
    """Dialoog voor SEPA debtor-gegevens; formuliervelden komen uit ``DEBTOR_FORM_FIELDS``."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Instellingen")
        self.setMinimumWidth(560)
        self.resize(560, 520)
        root = QVBoxLayout(self)
        info = QLabel(
            "Deze gegevens zijn nodig voor correcte SEPA-export. "
            "KvK en BTW zijn alleen voor herkenning op facturen (eigen nummers worden nooit als leverancier gebruikt)."
        )
        info.setWordWrap(True)
        root.addWidget(info)
        form = QFormLayout()
        root.addLayout(form)
        self._field_edits: dict[str, QLineEdit] = {}
        self._selected_user_data_dir: Optional[Path] = None
        self._selected_export_dir: Optional[Path] = None
        self._build_form(form)
        self._build_user_data_dir_row(form)
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
            elif key == "kvk":
                edit.setMaxLength(12)
            elif key == "vat":
                edit.setMaxLength(20)
            if isinstance(mw, MainWindow):
                if key == "name":
                    edit.setText(mw.get_debtor_name())
                elif key == "iban":
                    edit.setText(mw.get_debtor_iban())
                elif key == "bic":
                    edit.setText(mw.get_debtor_bic())
                elif key == "kvk":
                    edit.setText(mw.get_debtor_kvk())
                elif key == "vat":
                    edit.setText(mw.get_debtor_vat())
            if key in ("kvk", "vat"):
                edit.setToolTip(_DEBTOR_KVK_VAT_TOOLTIP)
            else:
                edit.setToolTip(_UW_GEGEVENS_XML_HINT)
            self._field_edits[key] = edit
            form.addRow(QLabel(label_text), edit)

    def _build_user_data_dir_row(self, form: QFormLayout) -> None:
        mw = self.parent()
        ud_path = None
        if isinstance(mw, MainWindow):
            ud_path = mw._user_data_dir

        container = QVBoxLayout()
        container.setSpacing(4)

        self._user_data_dir_edit = QLineEdit()
        self._user_data_dir_edit.setReadOnly(True)
        self._user_data_dir_edit.setMinimumWidth(300)
        self._user_data_dir_edit.setText(str(ud_path) if ud_path else "")
        self._user_data_dir_edit.setToolTip(
            "Instellingen (settings.json) en leveranciersdatabase (suppliers.json). "
            "Kies bijvoorbeeld een map op de server (UNC-pad)."
        )
        self._user_data_dir_edit.setStyleSheet("background-color: palette(window);")
        container.addWidget(self._user_data_dir_edit)

        btn = QPushButton("Kies map…")
        btn.setFixedWidth(120)
        btn.clicked.connect(self._on_choose_user_data_dir)
        container.addWidget(btn)

        wrapper = QWidget()
        wrapper.setLayout(container)
        form.addRow(QLabel("Gegevensmap:"), wrapper)

    def _on_choose_user_data_dir(self) -> None:
        mw = self.parent()
        if not isinstance(mw, MainWindow):
            return
        start = (
            str(self._selected_user_data_dir)
            if self._selected_user_data_dir
            else str(mw._user_data_dir)
        )
        path: Optional[str] = QFileDialog.getExistingDirectory(
            self, "Selecteer gegevensmap (instellingen & leveranciers)", start
        )
        if not path:
            return
        selected = Path(path).resolve()
        self._selected_user_data_dir = selected
        self._user_data_dir_edit.setText(str(selected))

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
        if self._selected_user_data_dir is not None:
            if not mw._apply_user_data_directory(self._selected_user_data_dir, dialog_parent=self):
                return
        updates = {key: self._field_edits[key].text() for key in self._field_edits}
        if not mw._apply_debtor_and_save(updates):
            QMessageBox.warning(
                self,
                "Instellingen",
                "Uw gegevens konden niet worden opgeslagen. Controleer schrijfrechten op "
                f"{mw._settings_path()}.",
            )
            return
        if self._selected_export_dir is not None:
            if not mw._persist_export_dir(self._selected_export_dir):
                QMessageBox.warning(
                    self,
                    "Instellingen",
                    "De exportmap kon niet worden opgeslagen. Controleer schrijfrechten in de "
                    "gegevensmap (settings.json).",
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
        self._user_data_dir: Path = read_user_data_root(APP_BASE)
        self._settings: dict[str, Any] = load_settings(str(self._settings_path()))
        self._ensure_debtor_dict()
        if apply_legacy_export_dir_migration(
            self._settings, user_data_dir=self._user_data_dir, app_base=APP_BASE
        ):
            save_settings(self._settings, str(self._settings_path()))
        self._selected_folder: Optional[Path] = None
        self._payment_sources: list[PaymentSource] = []
        self._status_label = QLabel("")
        self._table: QTableWidget
        self._filter_edit: QLineEdit
        self._persist_sort_column: Optional[int] = None
        self._persist_sort_order: Qt.SortOrder = Qt.SortOrder.AscendingOrder
        self._sort_persist_connected: bool = False
        self._deleted_rows_undo: list[list[tuple[int, str]]] = []
        self._session_date = date.today()
        self._suppress_table_item_changed: bool = False
        self._is_loading_batch: bool = False
        self._resolver_active: bool = False
        self._decision_store_inner = DecisionStore()
        self._decision_store = GuardedDecisionStore(self._decision_store_inner, is_allowed=lambda: self._resolver_active)
        self._pinned_run_id: str | None = None
        self._active_run_id: str | None = None
        self._approval_store = UserApprovalStore(self._user_data_dir / "user_approvals.json")
        self._pending_engine_rows: set[int] = set()
        self._pending_engine_idempotency: set[str] = set()
        self._engine_rerun_timer = QTimer(self)
        self._engine_rerun_timer.setSingleShot(True)
        self._engine_rerun_timer.timeout.connect(self._commit_pending_engine_updates)
        self._restore_selected_folder_from_settings()
        self._setup_ui()
        self._setup_shortcuts()
        self._restore_window_geometry()

    def _supplier_db_path(self) -> str:
        return str(self._user_data_dir / "suppliers.json")

    def _on_table_selection_changed(self) -> None:
        self._refresh_profile_button_state()
        if not hasattr(self, "_decision_inspector") or not self._decision_inspector.isVisible():
            return
        rows = self._selected_table_rows()
        if not rows:
            self._decision_inspector.setPlainText("Selecteer een rij om details te zien.")
            return
        r = rows[0]
        vm = self._resolve_row_vm(r)
        self._decision_inspector.setPlainText(self._inspector_text_for_vm(vm))

    def _inspector_text_for_vm(self, vm: ResolvedRowViewModel) -> str:
        # No causal claims: only show classified state + engine outputs + dominance-friendly fields.
        err_it = self._table.item(vm.row, PaymentColumn.ERROR)
        trace_payload = err_it.data(_ROW_DECISION_TRACE_ROLE) if err_it else None
        trace_steps = self._trace_steps_from_decision_trace(trace_payload)
        flags: list[str] = []

        if vm.decision is not None:
            if not str(vm.decision.get("reason_code") or "").strip():
                flags.append("reason_missing")
            if vm.decision.get("reason_detail") is None:
                flags.append("reason_detail_missing")
        if trace_steps and trace_steps[0].rule_name == "UNKNOWN_TRACE_MISSING":
            flags.append("trace_missing")

        lines: list[str] = []
        lines.append(f"row={vm.row}")
        lines.append(f"row_id={vm.row_id}")
        lines.append("")
        lines.append(f"active_run_id={self._active_run_id or ''}")
        lines.append("")

        if vm.decision:
            reason_code = str(vm.decision.get("reason_code") or "").strip() or "UNKNOWN_REASON_MISSING_FROM_ENGINE"
            reason_detail = vm.decision.get("reason_detail")
            if reason_detail is None or not str(reason_detail).strip():
                reason_detail_s = "UNKNOWN_REASON_MISSING_FROM_ENGINE"
            else:
                reason_detail_s = str(reason_detail)
            lines.append(f"decision.status={vm.decision.get('status')}")
            lines.append(f"reason_code={reason_code}")
            lines.append(f"reason_detail={reason_detail_s}")
            lines.append(f"requires_rerun={bool(vm.decision.get('requires_rerun'))}")
            lines.append(f"editable={bool(vm.decision.get('editable'))}")
            causal = vm.decision.get("causal_inputs") or []
            if isinstance(causal, list) and causal:
                lines.append(f"causal_inputs={', '.join([str(x) for x in causal])}")
        else:
            lines.append("decision=<none>")

        if flags:
            lines.append("")
            lines.append(f"flags={', '.join(flags)}")

        lines.append("")
        lines.append("row_data:")
        for k in ("supplier_name", "iban", "amount", "invoice_number", "customer_code", "execution_date", "pdf"):
            lines.append(f"  {k}={vm.row_data.get(k,'')}")

        lines.append("")
        lines.append("decision_trace.steps:")
        for i, st in enumerate(trace_steps, start=1):
            parts = [f"{i}. rule_name={st.rule_name}", f"outcome={st.outcome}"]
            if st.score is not None:
                parts.append(f"score={st.score}")
            if st.input_fields_used:
                parts.append(f"inputs={','.join(st.input_fields_used)}")
            lines.append("  " + " | ".join(parts))
        return "\n".join(lines)

    def _settings_path(self) -> Path:
        return self._user_data_dir / "settings.json"

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

    def _apply_user_data_directory(self, new_dir: Path, *, dialog_parent: QWidget) -> bool:
        """Wijzig gegevensmap: bootstrap, kopieer of laad bestaande bestanden, herbouw ``_settings``."""
        new_r = new_dir.resolve()
        if new_r == self._user_data_dir.resolve():
            return True
        try:
            new_r.mkdir(parents=True, exist_ok=True)
        except OSError:
            QMessageBox.warning(
                dialog_parent,
                "Instellingen",
                "De gegevensmap kon niet worden aangemaakt.",
            )
            return False
        probe = new_r / ".pdf2sepa_write_test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            QMessageBox.warning(
                dialog_parent,
                "Instellingen",
                "Geen schrijfrecht op de gekozen gegevensmap.",
            )
            return False

        old = self._user_data_dir
        old_settings = old / "settings.json"
        old_sup = old / "suppliers.json"
        new_settings = new_r / "settings.json"
        new_sup = new_r / "suppliers.json"

        if new_settings.exists() or new_sup.exists():
            answer = QMessageBox.question(
                dialog_parent,
                "Gegevensmap",
                "De gekozen map bevat al settings.json en/of suppliers.json.\n\n"
                "Overschakelen en die bestanden gebruiken? "
                "(Daarna worden de velden in dit venster alsnog naar die map opgeslagen.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
            if not write_user_data_root(new_r, APP_BASE):
                QMessageBox.warning(
                    dialog_parent,
                    "Instellingen",
                    "Kon het pad naar de gegevensmap niet bewaren (bootstrap-bestand).",
                )
                return False
            self._user_data_dir = new_r
            self._settings = load_settings(str(self._settings_path()))
            self._ensure_debtor_dict()
            return True

        if old_settings.exists():
            try:
                shutil.copy2(old_settings, new_settings)
            except OSError:
                QMessageBox.warning(
                    dialog_parent,
                    "Instellingen",
                    "Kon settings.json niet naar de nieuwe gegevensmap kopiëren.",
                )
                return False
        if old_sup.exists():
            try:
                shutil.copy2(old_sup, new_sup)
            except OSError:
                QMessageBox.warning(
                    dialog_parent,
                    "Instellingen",
                    "Kon suppliers.json niet naar de nieuwe gegevensmap kopiëren.",
                )
                return False
        if not write_user_data_root(new_r, APP_BASE):
            QMessageBox.warning(
                dialog_parent,
                "Instellingen",
                "Kon het pad naar de gegevensmap niet bewaren (bootstrap-bestand).",
            )
            return False
        self._user_data_dir = new_r
        self._settings = load_settings(str(self._settings_path()))
        self._ensure_debtor_dict()
        if apply_legacy_export_dir_migration(
            self._settings, user_data_dir=self._user_data_dir, app_base=APP_BASE
        ):
            save_settings(self._settings, str(self._settings_path()))
        return True

    def _persist_export_dir(self, folder: Path) -> bool:
        folder = folder.resolve()
        try:
            rel = folder.relative_to(self._user_data_dir)
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
        self._btn_xml = btn_xml
        self._batch_status_label = QLabel("Batch status: VALID")
        row_main.addWidget(self._batch_status_label, alignment=Qt.AlignmentFlag.AlignLeft)

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
        self._btn_create_profile = QPushButton("Profiel aanmaken")
        self._btn_create_profile.setToolTip(
            "Stap 1: Voeg toe / update (leverancier in database).\n"
            "Stap 2: Kies het juiste bedrag (klik op ? of Diagnostics).\n"
            "Stap 3: Bevestig factuurnummer/klantnummer en leer extractieprofiel."
        )
        self._btn_create_profile.clicked.connect(self._on_create_profile_for_selection)
        self._btn_create_profile.setEnabled(False)
        row_main.addWidget(self._btn_create_profile, alignment=Qt.AlignmentFlag.AlignRight)
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
            "Factuurdatum",
            "Betaaldatum",
            "Betaaltermijn",
            "Kernmatches",
            "Matches compleet",
            "Status",
            "Foutmelding",
            "Info",
        ]
        self._table = QTableWidget(0, len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        # ERROR column must remain readable: prefer horizontal scroll + tooltips over eliding.
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._table.setWordWrap(False)
        hdr = self._table.horizontalHeader()
        # Requested UI order: show "Foutmelding" before "Status" (visual swap only).
        # Keep logical column indices stable to avoid breaking data bindings.
        try:
            err_vi = hdr.visualIndex(PaymentColumn.ERROR)
            st_vi = hdr.visualIndex(PaymentColumn.STATUS)
            if err_vi != -1 and st_vi != -1 and err_vi > st_vi:
                hdr.moveSection(err_vi, st_vi)
        except Exception:
            # If Qt refuses (shouldn't), keep the default order rather than crashing.
            pass
        self._DEFAULT_COL_WIDTHS = {
            PaymentColumn.SUPPLIER: 160,
            PaymentColumn.IBAN: 180,
            PaymentColumn.AMOUNT: 90,
            PaymentColumn.CUSTOMER_CODE: 100,
            PaymentColumn.DESCRIPTION: 220,
            PaymentColumn.PDF: 120,
            PaymentColumn.DISCOUNT: 55,
            PaymentColumn.INVOICE_DATE: 100,
            PaymentColumn.EXECUTION_DATE: 100,
            PaymentColumn.TERM_HINT: 200,
            PaymentColumn.CORE_MATCHES: 260,
            PaymentColumn.MATCH_COMPLETE: 220,
            PaymentColumn.STATUS: 80,
            PaymentColumn.ERROR: 420,
            PaymentColumn.INFO: 44,
        }
        for col in range(len(headers)):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        # Do not force-stretch the last column; it interferes with user resizing of the ERROR column.
        hdr.setStretchLastSection(False)
        self._restore_column_widths()
        hdr.sectionHandleDoubleClicked.connect(
            lambda idx: self._table.resizeColumnToContents(idx)
        )
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)
        self._table.itemChanged.connect(self._on_table_item_changed)
        self._table.cellClicked.connect(self._on_table_cell_clicked)

        # Table + decision inspector splitter.
        self._decision_inspector = QTextEdit()
        self._decision_inspector.setReadOnly(True)
        self._decision_inspector.setMinimumWidth(240)
        self._decision_inspector.setVisible(False)
        self._decision_inspector.setToolTip("Beslissingsinspecteur: selecteer een rij.")

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._table)
        splitter.addWidget(self._decision_inspector)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, stretch=1)

        self._table.itemSelectionChanged.connect(self._on_table_selection_changed)

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

    def get_debtor_kvk(self) -> str:
        self._ensure_debtor_dict()
        return str(self._settings["debtor"].get("kvk") or "").strip()

    def get_debtor_vat(self) -> str:
        self._ensure_debtor_dict()
        return str(self._settings["debtor"].get("vat") or "").strip().upper()

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
        return resolve_settings_path(raw, base_dir=self._user_data_dir)

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
            key: str | None = format_eur_xml(_parse_amount_str(amount_display))
        except ValueError:
            key = None
        it.setData(Qt.ItemDataRole.UserRole, key)
        return it

    @staticmethod
    def _table_date_display_and_sort(raw: str) -> tuple[str, str | None]:
        iso = parse_ui_date_to_iso((raw or "").strip())
        if iso:
            return format_date_nl_from_iso(iso), iso
        s = (raw or "").strip()
        if not s:
            return "", None
        return s, None

    @staticmethod
    def _item_date_cell(display: str, sort_iso: str | None) -> _DateTableItem:
        it = _DateTableItem()
        it.setText(display)
        if sort_iso:
            it.setData(Qt.ItemDataRole.UserRole, sort_iso)
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
        inv_dt = self._cell_text(row, PaymentColumn.INVOICE_DATE).casefold()
        ex_dt = self._cell_text(row, PaymentColumn.EXECUTION_DATE).casefold()
        term = self._cell_text(row, PaymentColumn.TERM_HINT).casefold()
        core_matches = self._cell_text(row, PaymentColumn.CORE_MATCHES).casefold()
        match_complete = self._cell_text(row, PaymentColumn.MATCH_COMPLETE).casefold()
        return (
            needle in supplier
            or needle in description
            or needle in pdf
            or needle in cust
            or needle in inv_dt
            or needle in ex_dt
            or needle in term
            or needle in core_matches
            or needle in match_complete
        )

    def _apply_filter_to_table(self, filter_text: str) -> None:
        needle = filter_text.strip().casefold()
        for r in range(self._table.rowCount()):
            self._table.setRowHidden(r, bool(needle) and not self._row_matches_filter(r, needle))

    # Slightly stronger green tint for better visual distinction (especially on some displays).
    _COLOR_CONFIRMED = QColor(200, 255, 200)
    _COLOR_NEEDS_REVIEW = QColor(255, 248, 200)
    _COLOR_AMOUNT_TENTATIVE = QColor(214, 232, 255)
    _COLOR_ERROR = QColor(255, 220, 220)
    _TEXT_COLOR_ON_TINT = QColor(0, 0, 0)

    def _apply_row_colors(self) -> None:
        # HARD INVARIANT: never grey; only included/needs_review/excluded.
        # #region agent log (debug mode)
        try:
            counts = {"included": 0, "needs_review": 0, "excluded": 0, "other": 0}
            for _r in range(self._table.rowCount()):
                if self._is_row_blank(_r):
                    continue
                _st = (self._decision_for_row(_r) or {}).get("status")
                if _st in counts:
                    counts[str(_st)] += 1
                else:
                    counts["other"] += 1
            _dbg_log(
                hypothesis_id="D",
                location="main_window.py:_apply_row_colors",
                message="apply_row_colors summary",
                data={
                    "counts": counts,
                    "color_confirmed_rgb": [self._COLOR_CONFIRMED.red(), self._COLOR_CONFIRMED.green(), self._COLOR_CONFIRMED.blue()],
                    "color_needs_review_rgb": [self._COLOR_NEEDS_REVIEW.red(), self._COLOR_NEEDS_REVIEW.green(), self._COLOR_NEEDS_REVIEW.blue()],
                },
                run_id="pre-fix",
            )
        except Exception:
            pass
        # #endregion
        for r in range(self._table.rowCount()):
            if self._is_row_blank(r):
                continue
            dec = self._decision_for_row(r)
            st = dec.get("status")
            if st == DECISION_INCLUDED:
                color = self._COLOR_CONFIRMED
            elif st == DECISION_NEEDS_REVIEW:
                # Engine-driven UX nuance: unknown supplier (but otherwise parse OK)
                # should stand out separately from other review reasons.
                rc = str(dec.get("reason_code") or "")
                color = self._COLOR_AMOUNT_TENTATIVE if rc == "unmatched_supplier" else self._COLOR_NEEDS_REVIEW
            elif st == DECISION_EXCLUDED:
                color = self._COLOR_ERROR
            else:
                color = self._COLOR_NEEDS_REVIEW
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
        self._refresh_export_batch_status_label()

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

    def _resolve_iban_mismatches(self, invoices: list[dict], db: SupplierDB) -> None:
        """Prompt user when PDF IBAN differs from DB IBAN.

        DB IBAN is already set on each invoice by the matcher.  This dialog
        lets the user choose to *update* the DB to the PDF IBAN, or keep
        the verified DB IBAN (default / recommended).
        """
        MismatchKey = tuple[str, str]
        mismatches: dict[MismatchKey, dict] = {}

        for inv in invoices:
            if not inv.get("iban_mismatch"):
                continue
            sup = str(inv.get("supplier_name") or "")
            pdf_iban = str(inv.get("pdf_iban") or "")
            if not sup or not pdf_iban:
                continue
            key: MismatchKey = (sup, pdf_iban)
            if key not in mismatches:
                mismatches[key] = {
                    "supplier_name": sup,
                    "db_iban": str(inv.get("iban") or ""),
                    "pdf_iban": pdf_iban,
                    "invoices": [],
                }
            mismatches[key]["invoices"].append(inv)

        if not mismatches:
            return

        for info in mismatches.values():
            supplier_name = info["supplier_name"]
            db_iban = info["db_iban"]
            pdf_iban = info["pdf_iban"]
            n = len(info["invoices"])

            answer = QMessageBox.question(
                self,
                "IBAN-afwijking gedetecteerd",
                f"Leverancier: {supplier_name}\n"
                f"Aantal facturen: {n}\n\n"
                f"Database IBAN:\t{db_iban}\n"
                f"Factuur IBAN:\t{pdf_iban}\n\n"
                "De database-IBAN wordt standaard gebruikt (aanbevolen).\n"
                "Wil je de database bijwerken naar het factuur-IBAN?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                db.update_supplier(supplier_name, iban=pdf_iban)
                for inv in info["invoices"]:
                    inv["iban"] = pdf_iban

            for inv in info["invoices"]:
                inv.pop("iban_mismatch", None)

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
        debtor_kvk = self.get_debtor_kvk() or None
        debtor_vat = self.get_debtor_vat() or None

        def load() -> list[dict]:
            return load_invoices_from_folder(
                selected,
                debtor_iban=debtor_iban,
                debtor_kvk=debtor_kvk,
                debtor_vat=debtor_vat,
            )

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
        _dbg_log(
            hypothesis_id="A",
            location="main_window.py:_load_payments_from_sources:entry",
            message="load start",
            data={"has_selected_folder": bool(self._selected_folder)},
        )
        self._is_loading_batch = True
        progress: QProgressDialog | None = None
        try:
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
            strip_raw_text_from_invoices(matched)
            _agent_log(
                "H1",
                "main_window.py:_load_payments_from_sources",
                "matched invoices summary",
                {
                    "count": int(len(matched)),
                    "sample": [
                        {
                            "supplier_name": str(inv.get("supplier_name") or ""),
                            "match_status": str(inv.get("match_status") or ""),
                            "db_core_match_count": int(inv.get("db_core_match_count") or 0),
                            "invoice_number": str(inv.get("invoice_number") or ""),
                            "invoice_date_present": bool(str(inv.get("invoice_date") or "").strip()),
                            "iban_present": bool(str(inv.get("iban") or "").strip()),
                        }
                        for inv in matched[:8]
                        if isinstance(inv, dict)
                    ],
                },
            )
            self._resolve_iban_mismatches(matched, db)
            payments, errors = calculate_payments(matched, session_date=self._session_date)

            self._enrich_payments_with_source_files(payments, matched)
            n_err_rows = self._populate_table_from_load(payments, errors, matched)
            _dbg_log(
                hypothesis_id="A",
                location="main_window.py:_load_payments_from_sources:post_populate",
                message="table populated",
                data={
                    "rowCount": int(self._table.rowCount()),
                    "n_err_rows": int(n_err_rows),
                    "suppress_item_changed": bool(self._suppress_table_item_changed),
                    "tableSignalsBlocked": bool(self._table.signalsBlocked()),
                },
            )
            initial_run_id = str(uuid.uuid4())
            decision_map: dict[str, dict[str, Any]] = {}
            for r in range(self._table.rowCount()):
                if self._is_row_blank(r):
                    continue
                decision_map[self._row_id(r)] = self._row_decision(r)

            # Apply persisted approvals for this batch before committing the initial run.
            batch_key = stable_hash(
                {
                    "folder": str(self._selected_folder.resolve()) if self._selected_folder else "",
                    "suppliers_path": self._supplier_db_path(),
                }
            )
            persisted = self._approval_store.load_batch(batch_key)
            _dbg_log(
                hypothesis_id="C",
                location="main_window.py:_load_payments_from_sources:persisted",
                message="loaded persisted approvals",
                data={"count": int(len(persisted))},
            )
            _dbg_a6(
                hypothesis_id="UI1",
                location="main_window.py:_load_payments_from_sources:persisted",
                message="loaded persisted approvals (a6 session)",
                data={
                    "batch_key": batch_key,
                    "count": int(len(persisted)),
                    "user_data_dir": str(self._user_data_dir),
                },
            )
            if persisted:
                for r in range(self._table.rowCount()):
                    if self._is_row_blank(r):
                        continue
                    rid = self._row_id(r)
                    dec = persisted.get(rid)
                    if isinstance(dec, dict):
                        decision_map[rid] = dec
                        self._set_row_decision(r, dec)
            run = self._decision_store.begin_run(
                run_id=initial_run_id,
                input_snapshot_hash=stable_hash({"matched_count": len(matched), "session_date": self._session_date.isoformat()}),
                decision_map=decision_map,
            )
            self._decision_store.commit_run(run.run_id)
            self._pinned_run_id = run.run_id
            self._active_run_id = run.run_id
            # Ensure colors reflect committed decisions (initial table paint may have happened before run commit).
            self._apply_row_colors()
            try:
                targets = {"aluned 502601306.pdf", "bauder 24065433.pdf"}
                rows: list[dict[str, Any]] = []
                for rr in range(self._table.rowCount()):
                    if self._is_row_blank(rr):
                        continue
                    pdf = str(self._cell_text(rr, PaymentColumn.PDF) or "").strip()
                    if pdf.casefold() not in targets:
                        continue
                    dec = self._decision_for_row(rr) or {}
                    rows.append(
                        {
                            "row": int(rr),
                            "row_id": self._row_id(rr),
                            "pdf": pdf,
                            "status_cell": str(self._cell_text(rr, PaymentColumn.STATUS) or ""),
                            "decision_status": str(dec.get("status") or ""),
                            "reason_code": str(dec.get("reason_code") or ""),
                            "requires_rerun": bool(dec.get("requires_rerun")) if isinstance(dec, dict) else None,
                        }
                    )
                _dbg_a6(
                    hypothesis_id="UI2",
                    location="main_window.py:_load_payments_from_sources:post_commit",
                    message="resolved UI rows for target PDFs after apply_row_colors",
                    data={"rows": rows},
                )
            except Exception:
                pass
            try:
                reasons: dict[str, int] = {}
                for _rid, _dec in decision_map.items():
                    rc = str((_dec or {}).get("reason_code") or "")
                    reasons[rc] = reasons.get(rc, 0) + 1
                _dbg_log(
                    hypothesis_id="A",
                    location="main_window.py:_load_payments_from_sources:committed",
                    message="initial run committed",
                    data={"reason_counts": reasons, "active_run_id_set": bool(self._active_run_id)},
                )
            except Exception:
                pass

            n_pdf = len(all_raw)
            n_pay = len(payments)
            for name, count in per_source_counts:
                logger.info("bron %r: %d pdf-facturen", name, count)
            logger.info("betalingsregels: %d, foutregels: %d", n_pay, n_err_rows)
            self._update_load_status_after_load(
                n_pdf=n_pdf, n_payments=n_pay, n_error_rows=n_err_rows
            )
        finally:
            # Only now allow itemChanged to trigger pending validation.
            self._is_loading_batch = False
            try:
                if progress is not None:
                    progress.close()
            except Exception:
                pass

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

    def _match_inv_for_payment(self, invoices: list[dict], payment: dict) -> dict | None:
        sup = str(payment.get("supplier_name") or "")
        inv_no_pay = str(payment.get("invoice_number") or "")
        for inv in invoices:
            if str(inv.get("supplier_name") or "") != sup:
                continue
            if inv_no_pay and str(inv.get("invoice_number") or "") != inv_no_pay:
                continue
            return inv
        return None

    def _payment_base_amounts_for_row(
        self, invoices: list[dict], payment: dict, inv_match: dict | None
    ) -> tuple[Decimal | None, Decimal | None]:
        """Bepaal basisbedragen (incl/excl) voor lokale korting-herberekening per rij."""
        if not inv_match:
            return None, None
        try:
            inv_amount = amount_to_decimal(inv_match.get("amount"))
        except ValueError:
            return None, None
        vat_rate = normalize_supplier_vat_rate_pct(inv_match.get("supplier_vat_rate", 21))
        credit_numbers = {
            str(x).strip()
            for x in (payment.get("credit_notes_applied") or [])
            if str(x).strip()
        }
        if not credit_numbers:
            base_incl = inv_amount
            base_excl = incl_amount_to_excl_for_discount(base_incl, vat_rate)
            return base_incl, base_excl

        supplier = str(payment.get("supplier_name") or "")
        credits: list[dict[str, Any]] = []
        for inv in invoices:
            if str(inv.get("supplier_name") or "") != supplier:
                continue
            inv_no = str(inv.get("invoice_number") or "").strip()
            if inv_no and inv_no in credit_numbers:
                credits.append(inv)
        credit_incl = Decimal("0")
        for c in credits:
            try:
                credit_incl += amount_to_decimal(c.get("amount"))
            except ValueError:
                return None, None
        base_incl = (inv_amount - credit_incl).quantize(Decimal("0.01"))
        base_excl = incl_amount_to_excl_for_discount(base_incl, vat_rate)
        return base_incl, base_excl

    @staticmethod
    def _parse_discount_pct(raw: str) -> Decimal:
        s = (raw or "").strip().replace(",", ".")
        if not s:
            return Decimal("0.00")
        return amount_to_decimal(s)

    def _get_row_invoice_number(self, row: int) -> str:
        it = self._table.item(row, PaymentColumn.SUPPLIER)
        if not it:
            return ""
        v = it.data(_ROW_INVOICE_META_ROLE)
        return str(v).strip() if v is not None else ""

    @staticmethod
    def _row_ident_fields_from_inv(inv: dict | None) -> tuple[str, str, str, dict[str, Any] | None, dict[str, Any] | None]:
        """(klantcel, factuur-meta, omschrijving, invoice_result, customer_result)."""
        base = inv if isinstance(inv, dict) else {}
        cust = _ident_field_display_from_inv(base, "customer_number")
        inv_disp = _ident_field_display_from_inv(base, "invoice_number")
        meta = "" if inv_disp == "?" else inv_disp
        desc = _remittance_display_from_inv(base)
        ir = base.get("invoice_number_result")
        cr = base.get("customer_number_result")
        return (
            cust,
            meta,
            desc,
            ir if isinstance(ir, dict) else None,
            cr if isinstance(cr, dict) else None,
        )

    def _ident_field_result_snapshot_for_row(self, row: int, field: str) -> dict[str, Any] | None:
        if field == "invoice_number":
            it = self._table.item(row, PaymentColumn.SUPPLIER)
            role = _ROW_INVOICE_NUMBER_RESULT_ROLE
        elif field == "customer_number":
            it = self._table.item(row, PaymentColumn.CUSTOMER_CODE)
            role = _ROW_CUSTOMER_NUMBER_RESULT_ROLE
        else:
            return None
        if not it:
            return None
        raw = it.data(role)
        return deepcopy(raw) if isinstance(raw, dict) else None

    @staticmethod
    def _ident_field_picker_eligible(snap: dict[str, Any] | None) -> bool:
        if not isinstance(snap, dict):
            return False
        cands = snap.get("candidates")
        if not isinstance(cands, list) or not cands:
            return False
        st = str(snap.get("status") or "").lower()
        if st == "ambiguous":
            return True
        if st in ("tentative", "failed") and len(cands) >= 2:
            return True
        return len(cands) >= 2

    def _show_ident_field_candidate_menu(self, row: int, field: str) -> None:
        snap = self._ident_field_result_snapshot_for_row(row, field)
        if not self._ident_field_picker_eligible(snap):
            title = "Factuur-/polisnummer" if field == "invoice_number" else "Klantnummer"
            QMessageBox.information(
                self,
                title,
                "Geen meerdere parser-kandidaten om uit te kiezen.",
            )
            return
        cands = [c for c in (snap or {}).get("candidates") or [] if isinstance(c, dict)]
        menu = QMenu(self)
        for cand in cands:
            val = str(cand.get("value") or "").strip()
            if not val:
                continue
            label = str(cand.get("label") or cand.get("source") or "kandidaat").strip()
            conf = cand.get("confidence")
            text = f"{val} — {label}"
            if conf is not None:
                text += f" ({int(conf)}%)"
            ctx = str(cand.get("context") or "")
            act = menu.addAction(text)
            if ctx:
                act.setToolTip(ctx[:200])
            act.triggered.connect(
                lambda checked=False, r=row, f=field, c=cand: self._apply_ident_field_pick_to_row(r, f, c)
            )
        if menu.isEmpty():
            return
        menu.exec(QCursor.pos())

    def _apply_ident_field_pick_to_row(
        self,
        row: int,
        field: str,
        cand: dict[str, Any],
    ) -> None:
        val = str(cand.get("value") or "").strip()
        if not val:
            return
        snap = self._ident_field_result_snapshot_for_row(row, field) or {}
        snap = deepcopy(snap)
        snap["value"] = val
        snap["status"] = "confirmed"
        snap["user_selected"] = True
        snap["confidence"] = int(cand.get("confidence") or snap.get("confidence") or 95)
        snap["source"] = str(cand.get("source") or "USER_PICKED")
        self._suppress_table_item_changed = True
        try:
            if field == "customer_number":
                it = self._table.item(row, PaymentColumn.CUSTOMER_CODE)
                if it:
                    it.setText(val)
                    it.setData(_ROW_CUSTOMER_NUMBER_RESULT_ROLE, snap)
                cust_for_desc = val
                inv_for_desc = self._get_row_invoice_number(row)
            else:
                sup_it = self._table.item(row, PaymentColumn.SUPPLIER)
                if sup_it:
                    sup_it.setData(_ROW_INVOICE_META_ROLE, val)
                    sup_it.setData(_ROW_INVOICE_NUMBER_RESULT_ROLE, snap)
                cust_for_desc = self._cell_text(row, PaymentColumn.CUSTOMER_CODE).strip()
                if cust_for_desc == "?":
                    cust_for_desc = ""
                inv_for_desc = val
            desc_it = self._table.item(row, PaymentColumn.DESCRIPTION)
            if desc_it:
                desc_it.setText(
                    format_remittance_text(
                        cust_for_desc or None,
                        inv_for_desc or None,
                        None,
                    )
                )
            diag_it = self._table.item(row, PaymentColumn.SUPPLIER)
            if diag_it:
                diag = diag_it.data(_ROW_INVOICE_DIAGNOSTICS_ROLE)
                if isinstance(diag, dict):
                    patched = dict(diag)
                    patched[field] = val
                    patched[f"{field}_result"] = snap
                    diag_it.setData(_ROW_INVOICE_DIAGNOSTICS_ROLE, patched)
        finally:
            self._suppress_table_item_changed = False
        self._mark_row_pending_engine_update(row, f"{field}_picked")
        self._refresh_profile_button_state()

    def _get_row_invoice_diagnostics_snapshot(self, row: int) -> dict | None:
        it = self._table.item(row, PaymentColumn.SUPPLIER)
        if not it:
            return None
        v = it.data(_ROW_INVOICE_DIAGNOSTICS_ROLE)
        return v if isinstance(v, dict) else None

    def _resolve_row_source_file(self, row: int) -> str | None:
        """Absoluut PDF-pad: eerst geselecteerde factuurmap + PDF-kolom, dan snapshot-pad."""
        pdf_name = self._cell_text(row, PaymentColumn.PDF).strip()
        if pdf_name and pdf_name != "—" and self._selected_folder:
            candidate = self._selected_folder / Path(pdf_name).name
            if candidate.is_file():
                return str(candidate.resolve())
        snap = self._get_row_invoice_diagnostics_snapshot(row)
        if isinstance(snap, dict):
            sf = str(snap.get("source_file") or "").strip()
            if sf:
                p = Path(sf)
                if p.is_file():
                    return str(p.resolve())
                if self._selected_folder and not p.is_absolute():
                    alt = self._selected_folder / p.name
                    if alt.is_file():
                        return str(alt.resolve())
        return None

    def _match_status_for_profile_gate(self, row: int, snap: dict) -> str:
        ms = str(snap.get("match_status") or "").strip()
        if ms:
            return ms
        err_it = self._table.item(row, PaymentColumn.ERROR)
        trace = err_it.data(_ROW_DECISION_TRACE_ROLE) if err_it else None
        if isinstance(trace, dict):
            return str(trace.get("supplier_match_status") or "").strip()
        return ""

    _PROFILE_BLOCK_TOOLTIPS: dict[str, str] = {
        "no_snapshot": "Geen factuurgegevens op deze rij — herlaad de batch.",
        "match_not_eligible": "Leverancier eerst bevestigen: Voeg toe / update en PDF's opnieuw inlezen.",
        "amount_unresolved": "Kies eerst het juiste bedrag (klik op ? in kolom Bedrag).",
        "already_profile": "Extractieprofiel is al compleet voor deze leverancier.",
        "no_source_file": "PDF-pad ontbreekt — selecteer de factuurmap.",
        "pdf_not_found": "PDF-bestand niet gevonden in de geselecteerde map.",
    }

    def _row_amount_resolved(self, row: int) -> bool:
        """True als de rij een parseerbaar bedrag heeft (geen '?')."""
        txt = self._cell_text(row, PaymentColumn.AMOUNT).strip()
        if not txt or txt == "?":
            ar = self._amount_result_snapshot_for_row(row)
            if isinstance(ar, dict):
                st = str(ar.get("status") or ar.get("amount_status") or "").strip().lower()
                if st in ("confirmed", "tentative"):
                    raw_v = ar.get("value") or ar.get("selected_amount")
                    if raw_v is not None:
                        try:
                            amount_to_decimal(str(raw_v))
                            return True
                        except (TypeError, ValueError):
                            pass
            return False
        try:
            amount_to_decimal(txt)
            return True
        except (TypeError, ValueError):
            return False

    def _stored_profile_for_row(self, row: int) -> dict | None:
        supplier = self._cell_text(row, PaymentColumn.SUPPLIER).strip()
        if not supplier:
            return None
        try:
            db = SupplierDB(path=self._supplier_db_path())
            ep = db.get_extraction_profile(supplier)
            return ep if isinstance(ep, dict) else None
        except Exception:
            return None

    def _row_profile_block_reason(self, row: int) -> str | None:
        snap = self._get_row_invoice_diagnostics_snapshot(row)
        if not isinstance(snap, dict):
            snap = self._minimal_diagnostics_snapshot_from_row(row)
        gate_snap = dict(snap)
        ms = self._match_status_for_profile_gate(row, snap)
        if ms:
            gate_snap["match_status"] = ms
        return profile_learning_block_reason(
            gate_snap,
            source_file=self._resolve_row_source_file(row),
            amount_resolved=self._row_amount_resolved(row),
            stored_profile=self._stored_profile_for_row(row),
        )

    def _row_can_profile_confirm(self, row: int) -> bool:
        return self._row_profile_block_reason(row) is None

    def _refresh_profile_button_state(self) -> None:
        btn = getattr(self, "_btn_create_profile", None)
        if btn is None:
            return
        rows = self._selected_table_rows()
        if len(rows) != 1:
            btn.setEnabled(False)
            btn.setToolTip(
                "Selecteer precies één rij.\n"
                "Stap 1: Voeg toe / update → Stap 2: bedrag kiezen → Stap 3: profiel aanmaken."
            )
            return
        row = rows[0]
        reason = self._row_profile_block_reason(row)
        snap = self._get_row_invoice_diagnostics_snapshot(row)
        if not isinstance(snap, dict):
            snap = self._minimal_diagnostics_snapshot_from_row(row)
        # #region agent log
        _dbg8539(
            hypothesis_id="H1-H5",
            location="main_window.py:_refresh_profile_button_state",
            message="profile_button_state",
            data={
                "row": row,
                "reason": reason,
                "enabled": reason is None,
                "amount_resolved": self._row_amount_resolved(row),
                "match_status": self._match_status_for_profile_gate(row, snap),
                "extraction_source": str(snap.get("extraction_source") or ""),
                "source_file_resolved": bool(self._resolve_row_source_file(row)),
                "pdf_cell": self._cell_text(row, PaymentColumn.PDF).strip()[:80],
            },
            run_id="post-fix",
        )
        # #endregion
        if reason is None:
            btn.setEnabled(True)
            stored = self._stored_profile_for_row(row)
            missing = profile_field_keys_missing(stored)
            tip_parts = [
                "Bevestig bedrag, factuurnummer en klantnummer; sla extractieprofiel op.",
            ]
            if not self._row_amount_resolved(row):
                tip_parts.insert(
                    0,
                    "Tip: kies eerst het juiste bedrag (klik op ?) of vul het in de dialog.",
                )
            if missing:
                btn.setText("Profiel aanvullen")
                tip_parts.append("Ontbrekend in profiel: " + ", ".join(missing) + ".")
            else:
                btn.setText("Profiel aanmaken")
            btn.setToolTip("\n".join(tip_parts))
            return
        btn.setEnabled(False)
        extra = self._PROFILE_BLOCK_TOOLTIPS.get(reason, reason)
        btn.setToolTip(f"Profiel aanmaken (nog niet mogelijk): {extra}")

    def _on_create_profile_for_selection(self) -> None:
        rows = self._selected_table_rows()
        if not rows:
            QMessageBox.information(
                self,
                "Profiel aanmaken",
                "Selecteer eerst één rij in de tabel.",
            )
            return
        if len(rows) > 1:
            QMessageBox.information(
                self,
                "Profiel aanmaken",
                "Selecteer precies één rij.",
            )
            return
        row = rows[0]
        reason = self._row_profile_block_reason(row)
        if reason is not None:
            QMessageBox.warning(
                self,
                "Profiel aanmaken",
                self._PROFILE_BLOCK_TOOLTIPS.get(reason, reason),
            )
            return
        self._on_profile_confirm_row(row)

    def _profile_confirm_placeholders(self, row: int, snap: dict) -> dict[str, str]:
        amount_ph = ""
        ar = snap.get("amount_result") if isinstance(snap.get("amount_result"), dict) else None
        if isinstance(ar, dict):
            raw_v = ar.get("value") or ar.get("selected_amount")
            if raw_v is not None:
                try:
                    amount_ph = _format_amount_nl(amount_to_decimal(str(raw_v)))
                except (TypeError, ValueError):
                    amount_ph = str(raw_v)
        if not amount_ph:
            cell_amt = self._cell_text(row, PaymentColumn.AMOUNT).strip()
            if cell_amt:
                amount_ph = cell_amt
        inv_ph = str(snap.get("invoice_number") or "").strip()
        if not inv_ph:
            inv_ph = self._get_row_invoice_number(row)
        cust_ph = str(snap.get("customer_number") or "").strip()
        if not cust_ph:
            cust_ph = str(snap.get("pdf_customer_number") or "").strip()
        if not cust_ph:
            cust_ph = self._cell_text(row, PaymentColumn.CUSTOMER_CODE).strip()
        return {
            "amount": amount_ph,
            "invoice": inv_ph,
            "customer": cust_ph,
        }

    def _apply_profile_confirm_to_row(
        self,
        row: int,
        result_confirmed: dict[str, Any],
        *,
        profile_saved: bool,
        learned_profile: dict[str, Any] | None,
    ) -> None:
        """Werk tabelcellen bij na bevestiging (geen engine-commit)."""
        self._suppress_table_item_changed = True
        try:
            amt_xml = confirmed_amount_xml(result_confirmed)
            if amt_xml:
                try:
                    dec = amount_to_decimal(amt_xml)
                    self._sync_amount_result_and_row_ui(
                        row,
                        dec,
                        from_manual_typing=False,
                        picked_candidate=None,
                        amount_source="profile",
                    )
                except (TypeError, ValueError):
                    pass

            cust = str(result_confirmed.get("customer_number") or "").strip()
            if cust:
                cust_it = self._table.item(row, PaymentColumn.CUSTOMER_CODE)
                if cust_it:
                    cust_it.setText(cust)

            inv_no = str(result_confirmed.get("invoice_number") or "").strip()
            if inv_no:
                sup_it = self._table.item(row, PaymentColumn.SUPPLIER)
                if sup_it:
                    sup_it.setData(_ROW_INVOICE_META_ROLE, inv_no)

            desc = format_remittance_text(
                cust or None,
                inv_no or None,
                None,
            )
            if desc:
                desc_it = self._table.item(row, PaymentColumn.DESCRIPTION)
                if desc_it:
                    desc_it.setText(desc)

            if profile_saved and learned_profile is not None:
                snap = self._get_row_invoice_diagnostics_snapshot(row)
                if isinstance(snap, dict):
                    patched = deepcopy(snap)
                    patched["extraction_source"] = "profile"
                    field_keys = [
                        k
                        for k in ("amount", "invoice_number", "customer_number")
                        if k in learned_profile
                    ]
                    patched["profile_fields"] = field_keys
                    if inv_no:
                        patched["invoice_number"] = inv_no
                        patched["invoice_number_result"] = {
                            "status": "confirmed",
                            "value": inv_no,
                            "confidence": 95,
                            "source": "profile",
                            "candidates": [],
                        }
                        inv_it = self._table.item(row, PaymentColumn.SUPPLIER)
                        if inv_it:
                            inv_it.setData(
                                _ROW_INVOICE_NUMBER_RESULT_ROLE,
                                deepcopy(patched["invoice_number_result"]),
                            )
                    if cust:
                        patched["customer_number"] = cust
                        patched["customer_number_result"] = {
                            "status": "confirmed",
                            "value": cust,
                            "confidence": 95,
                            "source": "profile",
                            "candidates": [],
                        }
                    sup_it = self._table.item(row, PaymentColumn.SUPPLIER)
                    if sup_it:
                        sup_it.setData(_ROW_INVOICE_DIAGNOSTICS_ROLE, patched)
        finally:
            self._suppress_table_item_changed = False

    def _on_profile_confirm_row(self, row: int) -> None:
        if row < 0 or row >= self._table.rowCount() or self._is_row_blank(row):
            return
        if not self._row_can_profile_confirm(row):
            reason = self._row_profile_block_reason(row)
            QMessageBox.warning(
                self,
                "Factuurgegevens bevestigen",
                self._PROFILE_BLOCK_TOOLTIPS.get(reason or "", reason or "Niet beschikbaar voor deze rij."),
            )
            return
        source_file = self._resolve_row_source_file(row)
        if not source_file:
            QMessageBox.warning(
                self,
                "Factuurgegevens bevestigen",
                "PDF-bestand niet gevonden. Selecteer de factuurmap opnieuw of herlaad de batch.",
            )
            return
        snap = self._get_row_invoice_diagnostics_snapshot(row)
        if not isinstance(snap, dict):
            snap = self._minimal_diagnostics_snapshot_from_row(row)
        placeholders = self._profile_confirm_placeholders(row, snap)
        supplier = self._cell_text(row, PaymentColumn.SUPPLIER).strip()
        amt_initial = placeholders["amount"]
        dlg = ProfileConfirmDialog(
            supplier_name=supplier,
            amount_initial=amt_initial,
            amount_placeholder=placeholders["amount"],
            invoice_initial=placeholders["invoice"],
            invoice_placeholder=placeholders["invoice"],
            customer_initial=placeholders["customer"],
            customer_placeholder=placeholders["customer"],
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            raw_text = extract_text_strict(source_file)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Factuurgegevens bevestigen",
                f"PDF kon niet worden gelezen:\n{exc}",
            )
            return
        db = SupplierDB(path=self._supplier_db_path())
        amt_snap = self._amount_result_snapshot_for_row(row)
        inv_snap = self._ident_field_result_snapshot_for_row(row, "invoice_number")
        cust_snap = self._ident_field_result_snapshot_for_row(row, "customer_number")
        result = confirm_invoice_fields(
            raw_text=raw_text,
            source_file=source_file,
            supplier_name=supplier,
            confirmed=dlg.get_confirmed(),
            db=db,
            save_profile=dlg.save_profile,
            iban=self._cell_text(row, PaymentColumn.IBAN).strip() or None,
            amount_result=amt_snap if isinstance(amt_snap, dict) else None,
            invoice_number_result=inv_snap if isinstance(inv_snap, dict) else None,
            customer_number_result=cust_snap if isinstance(cust_snap, dict) else None,
        )
        self._apply_profile_confirm_to_row(
            row,
            result.confirmed,
            profile_saved=result.saved,
            learned_profile=result.profile,
        )
        self._mark_row_pending_engine_update(row, "profile_confirmed")
        self._refresh_export_batch_status_label()
        QMessageBox.information(self, "Factuurgegevens bevestigen", result.message)

    def _minimal_diagnostics_snapshot_from_row(self, row: int) -> dict:
        snap: dict[str, Any] = {}
        pdf = self._cell_text(row, PaymentColumn.PDF).strip()
        if pdf and pdf != "—":
            snap["source_file"] = pdf
        supplier = self._cell_text(row, PaymentColumn.SUPPLIER).strip()
        if supplier:
            snap["supplier_name"] = supplier
        iban = self._cell_text(row, PaymentColumn.IBAN).strip()
        if iban:
            snap["iban"] = iban
        cust = self._cell_text(row, PaymentColumn.CUSTOMER_CODE).strip()
        if cust:
            snap["customer_number"] = cust
        inv_no = self._get_row_invoice_number(row)
        if inv_no:
            snap["invoice_number"] = inv_no
        ar = self._amount_result_snapshot_for_row(row)
        if isinstance(ar, dict):
            snap["amount_result"] = ar
        return snap

    def _amount_result_snapshot_for_row(self, row: int) -> dict[str, Any] | None:
        """Parser-snapshot: bedragcel, anders invoice-diagnostics op leverancierscel."""
        amt_it = self._table.item(row, PaymentColumn.AMOUNT)
        if amt_it:
            raw = amt_it.data(_ROW_AMOUNT_RESULT_ROLE)
            if isinstance(raw, dict):
                return deepcopy(raw)
        snap = self._get_row_invoice_diagnostics_snapshot(row)
        if isinstance(snap, dict):
            ar = snap.get("amount_result")
            if isinstance(ar, dict):
                return deepcopy(ar)
        return None

    def _open_diagnostics_for_row(self, row: int) -> None:
        snap = self._get_row_invoice_diagnostics_snapshot(row)
        limited = snap is None
        if limited:
            snap = self._minimal_diagnostics_snapshot_from_row(row)
        try:
            payment = self._payment_dict_from_row(row, require_resolved_amount=False)
            decision = self._decision_for_row(row)
            diag = build_diagnostics(snap or {}, payment=payment, decision=decision)
            profile_eligible = self._row_can_profile_confirm(row)
            dlg = DiagnosticsDialog(
                diag,
                parent=self,
                on_pick_amount=lambda: self._on_table_cell_clicked(row, int(PaymentColumn.AMOUNT)),
                limited_snapshot=limited,
                profile_confirm_eligible=profile_eligible,
                on_profile_confirm=lambda: self._on_profile_confirm_row(row)
                if profile_eligible
                else None,
            )
            dlg.exec()
        except Exception as exc:
            logger.exception("Diagnostics openen mislukt (rij %s)", row)
            QMessageBox.warning(
                self,
                "Diagnostics",
                f"Kon diagnostics niet openen:\n{exc}",
            )

    def _decision_trace_debug_enabled(self) -> bool:
        raw = self._settings.get("decision_trace_debug")
        return bool(raw) or logger.isEnabledFor(logging.DEBUG)

    @staticmethod
    def _decision_trace_tooltip(trace: dict[str, Any]) -> str:
        try:
            pretty = json.dumps(trace, ensure_ascii=False, indent=2)
        except Exception:
            return "decision_trace: <onleesbaar>"
        return f"decision_trace\n{pretty}"

    @staticmethod
    def _compose_error_tooltip(*, error_msg: str, decision_trace: dict[str, Any] | None) -> str:
        parts: list[str] = []
        em = (error_msg or "").strip()
        if em:
            parts.append(em)
        if isinstance(decision_trace, dict) and decision_trace:
            if parts:
                parts.append("")
            parts.append(MainWindow._decision_trace_tooltip(decision_trace))
        return "\n".join(parts)

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
        supplier_tooltip: str = "",
        core_matches_info: str = "",
        match_complete_info: str = "",
        email_domain: str = "",
        kvk_number: str = "",
        vat_number: str = "",
        invoice_number_meta: str = "",
        warning_raw: str | None = None,
        invoice_date: str = "",
        execution_date: str = "",
        term_hint: str = "—",
        date_mode: str = "direct",
        invoice_date_source: str = "missing",
        effective_term_days: int = 0,
        supplier_term_trusted: bool | None = None,
        base_amount_incl: Decimal | None = None,
        base_amount_excl: Decimal | None = None,
        decision_trace: dict[str, Any] | None = None,
        amount_result_snapshot: dict[str, Any] | None = None,
        invoice_number_result_snapshot: dict[str, Any] | None = None,
        customer_number_result_snapshot: dict[str, Any] | None = None,
        decision: dict[str, Any] | None = None,
        row_id: str | None = None,
        invoice_diagnostics_snapshot: dict | None = None,
    ) -> None:
        r = self._table.rowCount()
        self._table.insertRow(r)
        sup_item = self._item_editable(supplier)
        if supplier_tooltip:
            sup_item.setToolTip(supplier_tooltip)
        if email_domain:
            sup_item.setData(_ROW_EMAIL_DOMAIN_ROLE, email_domain)
        if kvk_number:
            sup_item.setData(_ROW_KVK_NUMBER_ROLE, kvk_number)
        if vat_number:
            sup_item.setData(_ROW_VAT_NUMBER_ROLE, vat_number)
        if invoice_number_meta:
            sup_item.setData(_ROW_INVOICE_META_ROLE, invoice_number_meta)
        if invoice_diagnostics_snapshot is not None:
            sup_item.setData(_ROW_INVOICE_DIAGNOSTICS_ROLE, invoice_diagnostics_snapshot)
        sup_item.setData(
            _ROW_ROW_ID_ROLE,
            row_id or f"{supplier}|{invoice_number_meta}|{pdf_name}",
        )
        # Keep original supplier name for safe rename during "Voeg toe / update".
        # This prevents update_supplier() from failing when the user corrected the name in the table.
        try:
            if supplier.strip():
                sup_item.setData(_ROW_SUPPLIER_ORIGINAL_ROLE, supplier.strip())
        except Exception:
            pass
        self._table.setItem(r, PaymentColumn.SUPPLIER, sup_item)
        self._table.setItem(r, PaymentColumn.IBAN, self._item_editable(iban))
        amt_item = self._item_amount(amount_display)
        if base_amount_incl is not None:
            amt_item.setData(_ROW_BASE_INCL_ROLE, format_eur_xml(base_amount_incl))
        if base_amount_excl is not None:
            amt_item.setData(_ROW_BASE_EXCL_ROLE, format_eur_xml(base_amount_excl))
        if isinstance(amount_result_snapshot, dict):
            amt_item.setData(_ROW_AMOUNT_RESULT_ROLE, deepcopy(amount_result_snapshot))
            amt_item.setToolTip("Klik om een voorgesteld bedrag te kiezen (PDF-parser).")
        self._table.setItem(r, PaymentColumn.AMOUNT, amt_item)
        cust_item = self._item_editable(customer_code)
        if isinstance(customer_number_result_snapshot, dict):
            cust_item.setData(
                _ROW_CUSTOMER_NUMBER_RESULT_ROLE,
                deepcopy(customer_number_result_snapshot),
            )
            if customer_code.strip() == "?":
                cust_item.setToolTip("Klik om een klantnummer-kandidaat te kiezen.")
        self._table.setItem(r, PaymentColumn.CUSTOMER_CODE, cust_item)
        if isinstance(invoice_number_result_snapshot, dict):
            sup_item.setData(
                _ROW_INVOICE_NUMBER_RESULT_ROLE,
                deepcopy(invoice_number_result_snapshot),
            )
            if not str(invoice_number_meta or "").strip() and customer_code != "?":
                sup_item.setToolTip(
                    (sup_item.toolTip() or "")
                    + ("\n" if sup_item.toolTip() else "")
                    + "Klik op Omschrijving om factuur-/polisnummer te kiezen."
                )
        self._table.setItem(r, PaymentColumn.DESCRIPTION, self._item_editable(description))
        pdf_disp = pdf_name if pdf_name.strip() else "—"
        self._table.setItem(r, PaymentColumn.PDF, self._item_readonly(pdf_disp))
        self._table.setItem(r, PaymentColumn.DISCOUNT, self._item_editable(discount))

        inv_disp, inv_sort = self._table_date_display_and_sort(invoice_date)
        inv_it = self._item_date_cell(inv_disp, inv_sort)
        inv_it.setData(_ROW_INVOICE_DATE_SOURCE_ROLE, invoice_date_source)
        if invoice_date_source == "manual" and invoice_date.strip():
            inv_it.setToolTip("Handmatig ingesteld — factuurdatum")
        elif invoice_date_source == "parsed":
            inv_it.setToolTip("Uit PDF geëxtraheerd")
        self._table.setItem(r, PaymentColumn.INVOICE_DATE, inv_it)

        ex_disp, ex_sort = self._table_date_display_and_sort(execution_date)
        ex_it = self._item_date_cell(ex_disp, ex_sort)
        ex_it.setData(_ROW_DATE_MODE_ROLE, date_mode)
        if date_mode == "manual":
            ex_it.setToolTip("Handmatig ingesteld — betaaldatum")
        self._table.setItem(r, PaymentColumn.EXECUTION_DATE, ex_it)

        # Allow manual entry of supplier payment term even for rows that need review
        # or are marked as error; the term is supplier master data and should never
        # be blocked by PDF parsing/matching status.
        term_it = self._item_editable(term_hint)
        term_it.setData(_ROW_EFFECTIVE_TERM_ROLE, int(effective_term_days))
        if supplier_term_trusted is not None:
            term_it.setData(_ROW_TERM_TRUSTED_ROLE, supplier_term_trusted)
        self._table.setItem(r, PaymentColumn.TERM_HINT, term_it)
        self._table.setItem(r, PaymentColumn.CORE_MATCHES, self._item_readonly(core_matches_info))
        self._table.setItem(r, PaymentColumn.MATCH_COMPLETE, self._item_readonly(match_complete_info))

        status_item = self._item_readonly(status)
        self._table.setItem(r, PaymentColumn.STATUS, status_item)
        err_item = self._item_readonly(error_msg)
        if warning_raw:
            err_item.setData(_ROW_WARNING_RAW_ROLE, warning_raw)
        if decision_trace:
            err_item.setData(_ROW_DECISION_TRACE_ROLE, decision_trace)
        if isinstance(decision, dict):
            err_item.setData(_ROW_DECISION_ROLE, normalize_decision(decision))
        # Debug-proof: always provide a way to see full error text (and trace if present),
        # independent of any debug toggle.
        err_item.setToolTip(self._compose_error_tooltip(error_msg=error_msg, decision_trace=decision_trace))
        self._table.setItem(r, PaymentColumn.ERROR, err_item)
        info_item = self._item_readonly("🔍")
        info_item.setToolTip("Diagnostics — wat ging er goed of mis?")
        self._table.setItem(r, PaymentColumn.INFO, info_item)
        self._set_row_decision(r, decision if isinstance(decision, dict) else self._missing_decision_payload(r))

    def _populate_table_from_load(
        self,
        payments: list[dict],
        errors: list[dict],
        invoices: list[dict],
    ) -> int:
        hdr = self._table.horizontalHeader()
        hdr.blockSignals(True)
        prev_block = self._table.blockSignals(True)
        error_row_count = 0
        try:
            self._suppress_table_item_changed = True
            self._table.setSortingEnabled(False)
            self._table.setRowCount(0)
            try:
                targets = {"aluned 502601306.pdf", "bauder 24065433.pdf"}
                pay_targets = []
                for p in payments:
                    pdf = str(_pdf_basename_from_dict(p) or "").strip()
                    if pdf.casefold() not in targets:
                        continue
                    dec = p.get("decision") if isinstance(p.get("decision"), dict) else {}
                    pay_targets.append(
                        {
                            "pdf": pdf,
                            "p_status": str(p.get("status") or ""),
                            "decision_status": str((dec or {}).get("status") or ""),
                            "reason_code": str((dec or {}).get("reason_code") or ""),
                            "requires_rerun": bool((dec or {}).get("requires_rerun")) if isinstance(dec, dict) else None,
                        }
                    )
                err_targets = []
                for inv, reason in self._flatten_unique_error_invoices(errors):
                    pdf = str(_pdf_basename_from_dict(inv) or "").strip()
                    if pdf.casefold() not in targets:
                        continue
                    dec = inv.get("decision") if isinstance(inv.get("decision"), dict) else {}
                    err_targets.append(
                        {
                            "pdf": pdf,
                            "bucket_reason": str(reason),
                            "match_status": str(inv.get("match_status") or ""),
                            "decision_status": str((dec or {}).get("status") or ""),
                            "reason_code": str((dec or {}).get("reason_code") or ""),
                            "reason_detail": str((dec or {}).get("reason_detail") or ""),
                            "requires_rerun": bool((dec or {}).get("requires_rerun")) if isinstance(dec, dict) else None,
                        }
                    )
                _dbg_a6(
                    hypothesis_id="UI3",
                    location="main_window.py:_populate_table_from_load:inputs",
                    message="target PDFs presence in payments/errors before table append",
                    data={
                        "payments_count": int(len(payments)),
                        "errors_count": int(len(errors)),
                        "pay_targets": pay_targets,
                        "err_targets": err_targets,
                    },
                )
            except Exception:
                pass
            for p in payments:
                amount_str = str(p.get("amount_display") or "").strip()
                if not amount_str:
                    amt = p.get("amount")
                    amount_str = _format_amount_nl(amt) if amt is not None else ""
                err_cell = _nl_payment_warning(p.get("warning"))
                disc = self._discount_for_payment(invoices, p)
                inv_match = self._match_inv_for_payment(invoices, p)
                cust, inv_meta, desc, inv_res_snap, cust_res_snap = self._row_ident_fields_from_inv(
                    inv_match if isinstance(inv_match, dict) else None
                )
                if not cust and not inv_meta:
                    cust, inv_meta = self._invoice_fields_for_payment(invoices, p)
                    desc = format_remittance_text(
                        cust if cust else None,
                        inv_meta if inv_meta else None,
                        p.get("description"),
                    )
                    inv_res_snap = None
                    cust_res_snap = None
                pdf = _pdf_basename_from_dict(p)
                wr = p.get("warning")
                tr = p.get("supplier_term_trusted")
                trusted: bool | None = bool(tr) if tr is not None else None
                eff_term = int(p.get("supplier_payment_term_days_effective") or 0)
                inv_d = str(p.get("invoice_date") or "").strip()
                inv_src = str(p.get("invoice_date_source") or "missing")
                ex_d = str(p.get("execution_date") or "").strip() or self._session_date.isoformat()
                mode = str(p.get("date_mode") or "direct")
                term_lbl = _term_status_label(trusted, eff_term)
                base_incl, base_excl = self._payment_base_amounts_for_row(invoices, p, inv_match)
                core_info = _core_matches_text(inv_match or {})
                complete_info = _matches_completeness_text(inv_match or {})
                email_dom = str((inv_match or {}).get("email_domain") or "")
                kvk_no = str((inv_match or {}).get("kvk_number") or "")
                vat_no = str((inv_match or {}).get("vat_number") or "")
                amt_snap = (
                    inv_match.get("amount_result")
                    if isinstance(inv_match.get("amount_result"), dict)
                    else None
                )
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
                    supplier_tooltip=f"{core_info} | {complete_info}",
                    core_matches_info=core_info,
                    match_complete_info=complete_info,
                    email_domain=email_dom,
                    kvk_number=kvk_no,
                    vat_number=vat_no,
                    invoice_number_meta=inv_meta,
                    warning_raw=str(wr).strip() if wr else None,
                    invoice_date=inv_d,
                    execution_date=ex_d,
                    term_hint=term_lbl,
                    date_mode=mode,
                    invoice_date_source=inv_src,
                    effective_term_days=eff_term,
                    supplier_term_trusted=trusted,
                    base_amount_incl=base_incl,
                    base_amount_excl=base_excl,
                    decision_trace=p.get("decision_trace")
                    if isinstance(p.get("decision_trace"), dict)
                    else None,
                    amount_result_snapshot=amt_snap,
                    invoice_number_result_snapshot=inv_res_snap,
                    customer_number_result_snapshot=cust_res_snap,
                    decision=p.get("decision") if isinstance(p.get("decision"), dict) else None,
                    row_id=f"{str(p.get('supplier_name') or '')}|{inv_meta}|{pdf}",
                    invoice_diagnostics_snapshot=_diagnostics_snapshot_from_invoice(inv_match or {}),
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
                amount_str = _format_amount_nl(amt) if amt is not None else ""
                cust_r, inv_meta_r, desc_r, inv_res_r, cust_res_r = self._row_ident_fields_from_inv(inv)
                pdf_r = _pdf_basename_from_dict(inv)
                inv_dr = str(inv.get("invoice_date") or "").strip()
                src_r = str(inv.get("invoice_date_source") or "missing")
                tr_r = inv.get("supplier_term_trusted")
                trusted_r = bool(tr_r) if isinstance(tr_r, bool) else False
                raw_term_r = int(inv.get("supplier_payment_term_days_raw") or 0)
                eff_r = raw_term_r if trusted_r else 0
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
                    supplier_tooltip=f"{_core_matches_text(inv)} | {_matches_completeness_text(inv)}",
                    core_matches_info=_core_matches_text(inv),
                    match_complete_info=_matches_completeness_text(inv),
                    email_domain=str(inv.get("email_domain") or ""),
                    kvk_number=str(inv.get("kvk_number") or ""),
                    vat_number=str(inv.get("vat_number") or ""),
                    invoice_number_meta=inv_meta_r,
                    invoice_date=inv_dr,
                    execution_date=self._session_date.isoformat(),
                    term_hint=_term_status_label(trusted_r, eff_r),
                    date_mode="direct",
                    invoice_date_source=src_r,
                    effective_term_days=eff_r,
                    supplier_term_trusted=trusted_r,
                    amount_result_snapshot=inv.get("amount_result")
                    if isinstance(inv.get("amount_result"), dict)
                    else None,
                    invoice_number_result_snapshot=inv_res_r,
                    customer_number_result_snapshot=cust_res_r,
                    decision=inv.get("decision") if isinstance(inv.get("decision"), dict) else None,
                    row_id=f"{_error_row_supplier(inv)}|{inv_meta_r}|{pdf_r}",
                    invoice_diagnostics_snapshot=_diagnostics_snapshot_from_invoice(inv),
                )
            for inv, reason in other_errors:
                error_row_count += 1
                amt = inv.get("amount")
                ar_snap = inv.get("amount_result") if isinstance(inv.get("amount_result"), dict) else None
                ar_status = (
                    str((ar_snap or {}).get("status") or (ar_snap or {}).get("amount_status") or "")
                    .strip()
                    .lower()
                )
                ambiguous_ar = ar_status in ("ambiguous", "uncertain") and isinstance(ar_snap, dict)
                if reason in ("amount_ambiguous", "amount_uncertain") or ambiguous_ar:
                    _snap = ar_snap if isinstance(ar_snap, dict) else {}
                    _ac = _snap.get("candidates")
                    _n = len(_ac) if isinstance(_ac, list) else -1
                    _agent_log(
                        "H3",
                        "main_window.py:_populate_table_from_load",
                        "error row amount_ambiguous snapshot",
                        {
                            "engine_reason": reason,
                            "candidate_count": _n,
                            "ar_status": str(_snap.get("status") or ""),
                            "ar_source": str(_snap.get("source") or ""),
                        },
                    )
                    amount_str = "?"
                else:
                    amount_str = _format_amount_nl(amt) if amt is not None else ""
                cust_e, inv_meta_e, desc_e, inv_res_e, cust_res_e = self._row_ident_fields_from_inv(inv)
                pdf_e = _pdf_basename_from_dict(inv)
                inv_de = str(inv.get("invoice_date") or "").strip()
                src_e = str(inv.get("invoice_date_source") or "missing")
                base_err = _nl_error_reason(reason)
                sig_info = f"{_core_matches_text(inv)} | {_matches_completeness_text(inv)}"
                self._append_table_row(
                    _error_row_supplier(inv),
                    str(inv.get("iban") or ""),
                    amount_str,
                    cust_e,
                    desc_e,
                    pdf_e,
                    _discount_str_from_inv(inv),
                    "fout",
                    base_err,
                    supplier_tooltip=sig_info,
                    core_matches_info=_core_matches_text(inv),
                    match_complete_info=_matches_completeness_text(inv),
                    email_domain=str(inv.get("email_domain") or ""),
                    kvk_number=str(inv.get("kvk_number") or ""),
                    vat_number=str(inv.get("vat_number") or ""),
                    invoice_number_meta=inv_meta_e,
                    invoice_date=inv_de,
                    execution_date=self._session_date.isoformat(),
                    term_hint="—",
                    date_mode="direct",
                    invoice_date_source=src_e,
                    amount_result_snapshot=ar_snap
                    if (reason in ("amount_ambiguous", "amount_uncertain") or ambiguous_ar)
                    else None,
                    invoice_number_result_snapshot=inv_res_e,
                    customer_number_result_snapshot=cust_res_e,
                    decision=inv.get("decision") if isinstance(inv.get("decision"), dict) else None,
                    row_id=f"{_error_row_supplier(inv)}|{inv_meta_e}|{pdf_e}",
                    invoice_diagnostics_snapshot=_diagnostics_snapshot_from_invoice(inv),
                )
            self._auto_resize_columns_to_content()
            self._table.setSortingEnabled(True)
            if self._persist_sort_column is not None:
                self._table.sortByColumn(self._persist_sort_column, self._persist_sort_order)
        finally:
            self._table.blockSignals(prev_block)
            hdr.blockSignals(False)
            self._suppress_table_item_changed = False
        if not self._sort_persist_connected:
            hdr.sortIndicatorChanged.connect(self._on_sort_indicator_changed)
            self._sort_persist_connected = True
        self._apply_row_colors()
        self._apply_filter_to_table(self._filter_edit.text())
        self._refresh_export_batch_status_label()
        self._refresh_profile_button_state()

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

    def _on_reapply_discounts(self, rows: list[int] | None = None) -> None:
        target_rows = [r for r in (rows if rows is not None else self._selected_table_rows()) if r >= 0]
        if not target_rows:
            QMessageBox.information(
                self,
                "Korting toepassen",
                "Selecteer eerst één of meer rijen.",
            )
            return
        updated = 0
        skipped = 0
        skipped_no_excl = 0
        for r in target_rows:
            skip_reason = None
            if r >= self._table.rowCount():
                skipped += 1
                skip_reason = "row_out_of_range"
                _agent_log("H3", "main_window.py:_on_reapply_discounts", "row skipped", {"row": r, "reason": skip_reason})
                continue
            dec = self._row_decision(r)
            if dec.get("status") != DECISION_INCLUDED:
                skipped += 1
                skip_reason = "status_blocked"
                _agent_log(
                    "H3",
                    "main_window.py:_on_reapply_discounts",
                    "row skipped",
                    {"row": r, "reason": skip_reason, "status": dec.get("status")},
                )
                continue
            amt_item = self._table.item(r, PaymentColumn.AMOUNT)
            if not amt_item:
                skipped += 1
                skip_reason = "missing_amount_item"
                _agent_log("H3", "main_window.py:_on_reapply_discounts", "row skipped", {"row": r, "reason": skip_reason})
                continue
            base_incl_raw = amt_item.data(_ROW_BASE_INCL_ROLE)
            base_excl_raw = amt_item.data(_ROW_BASE_EXCL_ROLE)
            if base_incl_raw is None:
                skipped += 1
                skip_reason = "missing_base_incl"
                _agent_log(
                    "H3",
                    "main_window.py:_on_reapply_discounts",
                    "row skipped",
                    {"row": r, "reason": skip_reason, "base_excl_raw_present": base_excl_raw is not None},
                )
                continue
            try:
                base_incl = amount_to_decimal(base_incl_raw)
                base_excl = amount_to_decimal(base_excl_raw) if base_excl_raw is not None else None
                disc_pct = self._parse_discount_pct(self._cell_text(r, PaymentColumn.DISCOUNT))
            except Exception:
                skipped += 1
                skip_reason = "parse_error"
                _agent_log(
                    "H3",
                    "main_window.py:_on_reapply_discounts",
                    "row skipped",
                    {"row": r, "reason": skip_reason, "base_incl_raw": str(base_incl_raw), "base_excl_raw": str(base_excl_raw)},
                )
                continue
            discount_amt = amount_to_decimal(0)
            if base_excl is not None:
                discount_amt = (base_excl * amount_to_decimal(disc_pct) / amount_to_decimal("100")).quantize(
                    Decimal("0.01")
                )
            elif disc_pct > 0:
                skipped += 1
                skipped_no_excl += 1
                skip_reason = "no_base_excl_for_discount"
                _agent_log(
                    "H3",
                    "main_window.py:_on_reapply_discounts",
                    "row skipped",
                    {"row": r, "reason": skip_reason, "disc_pct": str(disc_pct), "base_incl": str(base_incl)},
                )
                continue
            new_amt = (base_incl - discount_amt).quantize(Decimal("0.01"))
            if new_amt <= Decimal("0"):
                skipped += 1
                skip_reason = "new_amount_non_positive"
                _agent_log(
                    "H3",
                    "main_window.py:_on_reapply_discounts",
                    "row skipped",
                    {"row": r, "reason": skip_reason, "new_amt": str(new_amt)},
                )
                continue
            amt_item.setText(_format_amount_nl(new_amt))
            amt_item.setData(Qt.ItemDataRole.UserRole, format_eur_xml(new_amt))
            self._mark_row_pending_engine_update(r, "discount_reapplied")
            updated += 1
            _agent_log(
                "H3",
                "main_window.py:_on_reapply_discounts",
                "row updated",
                {
                    "row": r,
                    "disc_pct": str(disc_pct),
                    "base_incl": str(base_incl),
                    "base_excl_present": base_excl is not None,
                    "base_excl": str(base_excl) if base_excl is not None else None,
                    "discount_amt": str(discount_amt),
                    "new_amt": str(new_amt),
                },
            )
        self._refresh_filter_and_sort_after_row_change()
        extra = ""
        if skipped and skipped == skipped_no_excl and updated == 0:
            extra = " (Geen bedrag excl. BTW gevonden → korting niet toepasbaar.)"
        self._set_status(
            f"Korting toegepast op {updated} rij(en) zonder facturen opnieuw te laden."
            + (f" Overgeslagen: {skipped}." if skipped else "")
            + extra
        )

    def _on_add_row(self) -> None:
        self._suppress_table_item_changed = True
        self._append_table_row(
            "",
            "",
            "",
            "",
            "",
            "",
            "0",
            "handmatig",
            "",
            invoice_number_meta="",
            invoice_date="",
            execution_date=self._session_date.isoformat(),
            term_hint="—",
            date_mode="direct",
            invoice_date_source="missing",
            decision=build_decision(
                status=DECISION_NEEDS_REVIEW,
                reason_code=REASON_MANUAL_PENDING,
                reason_detail="Nieuwe handmatige rij",
                editable=True,
                requires_rerun=True,
                causal_inputs=["row_creation"],
                input_fields={"source": "manual"},
            ),
        )
        self._suppress_table_item_changed = False
        self._refresh_filter_and_sort_after_row_change()
        self._refresh_export_batch_status_label()

    def _on_table_context_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        col = self._table.columnAt(pos.x())
        dec0 = self._decision_for_row(row)
        status = dec0.get("status")
        reason0 = str(dec0.get("reason_code") or "")
        menu = QMenu(self)
        if col == int(PaymentColumn.AMOUNT) and self._cell_text(row, PaymentColumn.AMOUNT).strip() == "?":
            sub = menu.addMenu("Kies bedrag…")
            snap = self._amount_result_snapshot_for_row(row)
            if isinstance(snap, dict):
                for cand in snap.get("candidates") or []:
                    if not isinstance(cand, dict):
                        continue
                    raw_v = cand.get("value")
                    try:
                        disp = _format_amount_nl(raw_v) if raw_v is not None else "?"
                    except Exception:
                        disp = str(raw_v or "?")
                    label = f"{disp} — {_nl_amount_candidate_source(str(cand.get('source') or ''))}"
                    act = sub.addAction(label)
                    act.triggered.connect(
                        lambda checked=False, r=row, c=cand: self._apply_amount_candidate_pick_to_row(r, c)
                    )
        if col == int(PaymentColumn.CUSTOMER_CODE) and self._ident_field_picker_eligible(
            self._ident_field_result_snapshot_for_row(row, "customer_number")
        ):
            sub_c = menu.addMenu("Kies klantnummer…")
            for cand in (self._ident_field_result_snapshot_for_row(row, "customer_number") or {}).get(
                "candidates"
            ) or []:
                if not isinstance(cand, dict):
                    continue
                val = str(cand.get("value") or "").strip()
                if not val:
                    continue
                act = sub_c.addAction(f"{val} — {cand.get('label') or 'kandidaat'}")
                act.triggered.connect(
                    lambda checked=False, r=row, c=cand: self._apply_ident_field_pick_to_row(
                        r, "customer_number", c
                    )
                )
        if col in (int(PaymentColumn.DESCRIPTION), int(PaymentColumn.SUPPLIER)) and self._ident_field_picker_eligible(
            self._ident_field_result_snapshot_for_row(row, "invoice_number")
        ):
            sub_i = menu.addMenu("Kies factuur-/polisnummer…")
            for cand in (self._ident_field_result_snapshot_for_row(row, "invoice_number") or {}).get(
                "candidates"
            ) or []:
                if not isinstance(cand, dict):
                    continue
                val = str(cand.get("value") or "").strip()
                if not val:
                    continue
                act = sub_i.addAction(f"{val} — {cand.get('label') or 'kandidaat'}")
                act.triggered.connect(
                    lambda checked=False, r=row, c=cand: self._apply_ident_field_pick_to_row(
                        r, "invoice_number", c
                    )
                )
        if status == DECISION_NEEDS_REVIEW:
            action_confirm = menu.addAction("Bevestig factuur")
            action_confirm.triggered.connect(lambda: self._confirm_review_rows([row]))
            selected = self._selected_table_rows()
            review_selected = [
                r for r in selected
                if self._decision_for_row(r).get("status") == DECISION_NEEDS_REVIEW
            ]
            if len(review_selected) > 1:
                action_all = menu.addAction(f"Bevestig alle geselecteerde ({len(review_selected)})")
                action_all.triggered.connect(lambda: self._confirm_review_rows(review_selected))
        if self._row_can_profile_confirm(row):
            action_profile = menu.addAction("Bevestig factuurgegevens…")
            action_profile.triggered.connect(lambda: self._on_profile_confirm_row(row))
        if status == DECISION_EXCLUDED and reason0 == REASON_USER_MARKED_ERROR:
            action_restore = menu.addAction("Herstel naar OK")
            action_restore.triggered.connect(lambda: self._restore_rows_from_error([row]))
            selected = self._selected_table_rows()
            fout_selected = [
                r for r in selected
                if (
                    self._decision_for_row(r).get("status") == DECISION_EXCLUDED
                    and str(self._decision_for_row(r).get("reason_code") or "") == REASON_USER_MARKED_ERROR
                )
            ]
            if len(fout_selected) > 1:
                action_all_restore = menu.addAction(f"Herstel alle geselecteerde ({len(fout_selected)})")
                action_all_restore.triggered.connect(lambda: self._restore_rows_from_error(fout_selected))
        else:
            action_fout = menu.addAction("Markeer als fout")
            action_fout.triggered.connect(lambda: self._mark_rows_as_error([row]))
        if not (status == DECISION_EXCLUDED and reason0 == REASON_USER_MARKED_ERROR):
            menu.addAction("Betaal direct (sessiedatum)").triggered.connect(
                lambda: self._apply_pay_direct_rows(self._selected_table_rows() or [row])
            )
            menu.addAction("Betaal op uiterste betaaldatum").triggered.connect(
                lambda: self._apply_pay_due_rows(self._selected_table_rows() or [row])
            )
            menu.addAction("Korting toepassen op geselecteerde rij(en)").triggered.connect(
                lambda: self._on_reapply_discounts(self._selected_table_rows() or [row])
            )
        if not menu.isEmpty():
            menu.exec(self._table.viewport().mapToGlobal(pos))

    def _apply_pay_direct_rows(self, rows: list[int]) -> None:
        self._suppress_table_item_changed = True
        for r in rows:
            if r < 0 or r >= self._table.rowCount():
                continue
            it = self._table.item(r, PaymentColumn.EXECUTION_DATE)
            if not it:
                continue
            it.setData(_ROW_DATE_MODE_ROLE, "direct")
            sess = self._session_date.isoformat()
            it.setText(format_date_nl_from_iso(sess))
            it.setData(Qt.ItemDataRole.UserRole, sess)
            it.setToolTip("")
        self._suppress_table_item_changed = False
        self._set_status(f"{len(rows)} rij(en): betalingsmodus direct (sessiedatum).")
        self._refresh_export_batch_status_label()

    def _apply_pay_due_rows(self, rows: list[int]) -> None:
        missing: list[int] = []
        self._suppress_table_item_changed = True
        for r in rows:
            if r < 0 or r >= self._table.rowCount():
                continue
            inv_txt = self._cell_text(r, PaymentColumn.INVOICE_DATE).strip()
            if not inv_txt:
                missing.append(r + 1)
                continue
            term_it = self._table.item(r, PaymentColumn.TERM_HINT)
            try:
                eff = int(term_it.data(_ROW_EFFECTIVE_TERM_ROLE)) if term_it else 0
            except (TypeError, ValueError):
                eff = 0
            inv_iso = parse_ui_date_to_iso(inv_txt)
            if inv_iso is None:
                missing.append(r + 1)
                continue
            ex = execution_date_for_due(inv_iso, eff, self._session_date)
            if ex is None:
                missing.append(r + 1)
                continue
            it = self._table.item(r, PaymentColumn.EXECUTION_DATE)
            if it:
                it.setData(_ROW_DATE_MODE_ROLE, "due")
                it.setText(format_date_nl_from_iso(ex))
                it.setData(Qt.ItemDataRole.UserRole, ex)
                it.setToolTip("")
        self._suppress_table_item_changed = False
        if missing:
            QMessageBox.warning(
                self,
                "Factuurdatum ontbreekt",
                "Voor ‘op uiterste betaaldatum’ is een factuurdatum verplicht.\n"
                f"Rij(en): {', '.join(str(x) for x in sorted(set(missing)))}",
            )
        else:
            self._set_status(f"{len(rows)} rij(en): modus uiterste betaaldatum toegepast.")
        self._refresh_export_batch_status_label()

    def _confirm_review_rows(self, rows: list[int]) -> None:
        if not rows:
            return
        # Validate rows before allowing force-include.
        invalid_rows: list[int] = []
        for r in rows:
            if r < 0 or r >= self._table.rowCount() or self._is_row_blank(r):
                continue
            p = self._payment_dict_from_row(r)
            err = self._validate_single_payment_row(p)
            if err:
                invalid_rows.append(r + 1)
        if invalid_rows:
            amount_hints: list[int] = []
            for r in rows:
                if r < 0 or r >= self._table.rowCount() or self._is_row_blank(r):
                    continue
                p = self._payment_dict_from_row(r)
                err = self._validate_single_payment_row(p)
                if err and "bedrag" in err:
                    amount_hints.append(r + 1)
            extra = ""
            if amount_hints:
                extra = (
                    "\n\nVoor bedrag '?': klik op de cel Bedrag en kies het juiste totaal, "
                    "of open Info (🔍) → Bedrag kiezen. Daarna eventueel «Profiel aanmaken»."
                )
            QMessageBox.warning(
                self,
                "Goedkeuren niet mogelijk",
                "Eén of meer rijen zijn nog niet exporteerbaar.\n\n"
                "Corrigeer eerst de velden (bijv. IBAN, bedrag, betaaldatum) en probeer opnieuw.\n"
                f"Rij(en): {', '.join(str(x) for x in sorted(set(invalid_rows)))}"
                f"{extra}",
            )
            return

        base_run_id = self._active_run_id or self._pinned_run_id
        base_map = dict(self._decision_store.committed_decision_map(base_run_id)) if base_run_id else {}
        approved_map: dict[str, dict[str, Any]] = {}
        for r in rows:
            if r < 0 or r >= self._table.rowCount() or self._is_row_blank(r):
                continue
            rid = self._row_id(r)
            dec = build_decision(
                status=DECISION_INCLUDED,
                reason_code=REASON_USER_APPROVED,
                reason_detail="context_menu_approve",
                editable=False,
                requires_rerun=False,
                causal_inputs=["user_approve"],
                input_fields={
                    "row_id": rid,
                    "supplier_name": self._cell_text(r, PaymentColumn.SUPPLIER),
                    "iban": self._cell_text(r, PaymentColumn.IBAN),
                    "amount": self._cell_text(r, PaymentColumn.AMOUNT),
                    "invoice_number": self._get_row_invoice_number(r),
                },
            )
            approved_map[rid] = dict(dec)
            base_map[rid] = dict(dec)
            self._set_row_decision(r, dec)

        run_id = str(uuid.uuid4())
        run = self._decision_store.begin_run(
            run_id=run_id,
            input_snapshot_hash=stable_hash({"action": "user_approve", "base_run_id": base_run_id or "", "rows": sorted(approved_map.keys())}),
            decision_map=base_map,
        )
        self._decision_store.commit_run(run.run_id)
        self._pinned_run_id = run.run_id
        self._active_run_id = run.run_id

        # Persist approvals for this batch.
        batch_key = stable_hash(
            {
                "folder": str(self._selected_folder.resolve()) if self._selected_folder else "",
                "suppliers_path": self._supplier_db_path(),
            }
        )
        self._approval_store.upsert_batch(batch_key, approved_map)

        self._apply_row_colors()
        self._set_status(f"{len(approved_map)} rij(en) handmatig goedgekeurd.")
        self._refresh_export_batch_status_label()

    def _mark_rows_as_error(self, rows: list[int]) -> None:
        for r in rows:
            if r < 0 or r >= self._table.rowCount() or self._is_row_blank(r):
                continue
            rid = self._row_id(r)
            dec = build_decision(
                status=DECISION_EXCLUDED,
                reason_code=REASON_USER_MARKED_ERROR,
                reason_detail="context_menu_mark_error",
                editable=True,
                requires_rerun=False,
                causal_inputs=["user_mark_error"],
                input_fields={"row_id": rid},
            )
            self._set_row_decision(r, dec)
        self._apply_row_colors()
        self._refresh_export_batch_status_label()

    def _restore_rows_from_error(self, rows: list[int]) -> None:
        for r in rows:
            if r < 0 or r >= self._table.rowCount() or self._is_row_blank(r):
                continue
            # Restoring is a user action; rerun engine validation for this row.
            self._mark_row_pending_engine_update(r, "restored_from_user_error")
        self._apply_row_colors()
        self._set_status(f"{len(rows)} rij(en) hersteld naar OK.")
        self._refresh_export_batch_status_label()

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
        new_err.setToolTip(new_msg)
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
            term_raw = self._cell_text(r, PaymentColumn.TERM_HINT)
            status_raw = self._cell_text(r, PaymentColumn.STATUS)
            sup_it = self._table.item(r, PaymentColumn.SUPPLIER)
            original_name = (
                str(sup_it.data(_ROW_SUPPLIER_ORIGINAL_ROLE) or "").strip() if sup_it else ""
            )
            email_dom = str(sup_it.data(_ROW_EMAIL_DOMAIN_ROLE) or "").strip() if sup_it else ""
            kvk_no = str(sup_it.data(_ROW_KVK_NUMBER_ROLE) or "").strip() if sup_it else ""
            vat_no = str(sup_it.data(_ROW_VAT_NUMBER_ROLE) or "").strip() if sup_it else ""
            if not name or not iban:
                failed += 1
                continue

            # If the supplier name was corrected in the table, rename the supplier record first.
            if original_name and original_name.strip() != name.strip():
                renamed = db.rename_supplier(original_name, name, keep_old_as_alias=True)
                if renamed and sup_it:
                    sup_it.setData(_ROW_SUPPLIER_ORIGINAL_ROLE, name.strip())

            term_days = _parse_term_days_from_text(term_raw)
            # TERM_HINT is a human-facing label and may not contain a number (e.g. "—" or
            # "Termijn niet toegepast ..."). Missing/unknown term must never block syncing.
            try:
                d = float(disc_raw.replace(",", ".")) if disc_raw.strip() else 0.0
            except ValueError:
                d = 0.0
            merged = db.merge_or_add_supplier(
                name,
                iban,
                code or None,
                d,
                default_payment_term_days=term_days,
                vat_number=vat_no or None,
                kvk_number=kvk_no or None,
                email_domain=email_dom or None,
            )
            update_kwargs: dict[str, Any] = {
                "iban": iban,
                "discount": d,
                "vat_numbers": [vat_no] if vat_no else [],
                "kvk_numbers": [kvk_no] if kvk_no else [],
                "email_domains": [email_dom] if email_dom else [],
            }
            if term_days is not None:
                update_kwargs["default_payment_term_days"] = term_days
            updated = db.update_supplier(name, **update_kwargs)
            if merged or updated:
                ok += 1
                self._strip_iban_mismatch_warning_row(r)
                changed = True
            else:
                failed += 1
        if changed:
            self._refresh_filter_and_sort_after_row_change()
            if self._payment_sources:
                self._load_payments_from_sources()
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
        self._refresh_export_batch_status_label()

    def _on_undo_delete(self) -> None:
        if not self._deleted_rows_undo:
            return
        row_data = self._deleted_rows_undo.pop()
        r = self._table.rowCount()
        self._table.insertRow(r)
        for c, text in row_data:
            self._table.setItem(r, c, QTableWidgetItem(text))
        self._refresh_filter_and_sort_after_row_change()
        self._refresh_export_batch_status_label()

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

    def _export_log_path(self) -> Path:
        return self._resolve_export_dir() / "export_log.json"

    def _read_export_log(self) -> list[dict]:
        import json
        log_path = self._export_log_path()
        try:
            if not log_path.exists():
                return []
            with open(log_path, "r", encoding="utf-8") as f:
                entries = json.loads(f.read() or "[]")
            return entries if isinstance(entries, list) else []
        except Exception:
            logger.debug("Export log lezen mislukt", exc_info=True)
            return []

    def _log_export(self, xml_path: str, payments: list[dict], total: Decimal) -> None:
        """Append an entry to exports/export_log.json (audit trail; bevat o.a. leverancier/factuur)."""
        import json
        from collections import defaultdict

        from logic.settings import atomic_write

        batches_map: dict[str, list[dict]] = defaultdict(list)
        for p in payments:
            batches_map[str(p.get("execution_date") or "").strip()].append(p)
        batches_out: list[dict] = []
        for ex in sorted(batches_map.keys()):
            if not ex:
                continue
            plist = batches_map[ex]
            decs: list[Decimal] = []
            for x in plist:
                try:
                    decs.append(amount_to_decimal(x.get("amount")))
                except ValueError:
                    continue
            batches_out.append({
                "execution_date": ex,
                "n_tx": len(plist),
                "total_eur": format_eur_xml(sum_decimals(decs)),
            })

        log_path = self._export_log_path()
        pay_lines: list[dict[str, str]] = []
        for p in payments:
            try:
                pay_lines.append({
                    "supplier": str(p.get("supplier_name") or ""),
                    "invoice": str(p.get("invoice_number") or ""),
                    "amount": format_eur_xml(amount_to_decimal(p.get("amount"))),
                    "execution_date": str(p.get("execution_date") or ""),
                })
            except ValueError:
                continue
        try:
            entries = self._read_export_log()
            entries.append({
                "timestamp": date.today().isoformat(),
                "file": Path(xml_path).name,
                "n_payments": len(payments),
                "n_batches": len(batches_out),
                "total_eur": format_eur_xml(total),
                "batches": batches_out,
                "payments": pay_lines,
            })
            text = json.dumps(entries, indent=2, ensure_ascii=False) + "\n"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(log_path, text)
        except Exception:
            logger.debug("Export log schrijven mislukt", exc_info=True)

    def _check_duplicate_payments(
        self, payment_dicts: list[dict[str, Any]]
    ) -> list[tuple[str, str, Decimal, str]]:
        """Check payment_dicts against export log for previously exported invoices.

        Returns list of (supplier, invoice_number, amount, export_date) for duplicates.
        """
        entries = self._read_export_log()
        exported: dict[tuple[str, str, str], str] = {}
        for entry in entries:
            ts = str(entry.get("timestamp") or "")
            for p in entry.get("payments") or []:
                if not isinstance(p, dict):
                    continue
                try:
                    key = (
                        str(p.get("supplier") or "").strip().lower(),
                        str(p.get("invoice") or "").strip(),
                        format_eur_xml(amount_to_decimal(p.get("amount"))),
                    )
                except ValueError:
                    continue
                if key[1]:
                    exported[key] = ts

        duplicates: list[tuple[str, str, Decimal, str]] = []
        for p in payment_dicts:
            sup = str(p.get("supplier_name") or "").strip().lower()
            inv = str(p.get("invoice_number") or "").strip()
            try:
                amt = amount_to_decimal(p.get("amount"))
            except ValueError:
                continue
            if not inv:
                continue
            key = (sup, inv, format_eur_xml(amt))
            if key in exported:
                duplicates.append((
                    str(p.get("supplier_name") or ""),
                    inv,
                    amt,
                    exported[key],
                ))
        return duplicates

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

    def _row_decision(self, row: int) -> dict[str, Any]:
        err_it = self._table.item(row, PaymentColumn.ERROR)
        raw = err_it.data(_ROW_DECISION_ROLE) if err_it else None
        if isinstance(raw, dict):
            return dict(normalize_decision(raw))
        return self._missing_decision_payload(row)

    def _missing_decision_payload(self, row: int) -> dict[str, Any]:
        return dict(
            build_decision(
                status=DECISION_NEEDS_REVIEW,
                reason_code=REASON_MISSING_DECISION_IN_STORE,
                reason_detail=None,
                editable=True,
                requires_rerun=True,
                causal_inputs=["decision_store"],
                input_fields={"row_id": self._row_id(row)},
            )
        )

    def _decision_for_row(self, row: int) -> dict[str, Any]:
        """Single source for rendering + export: DecisionStore decision or safe missing default."""
        vm = self._resolve_row_vm(row)
        if isinstance(vm.decision, dict):
            return dict(normalize_decision(vm.decision))
        return self._missing_decision_payload(row)

    def _set_row_decision(self, row: int, decision: dict[str, Any], *, note: str | None = None) -> None:
        dec = normalize_decision(decision)
        status_label = decision_status_label_nl(dec["status"])
        reason_code = str(dec.get("reason_code") or "").strip() or REASON_MISSING_DECISION_IN_STORE
        reason_detail = dec.get("reason_detail")
        detail_s = str(reason_detail).strip() if reason_detail is not None else ""
        raw_line = f"{reason_code} — {detail_s}" if detail_s else reason_code
        nl_line = decision_reason_text_nl(reason_code)
        if not nl_line.strip():
            nl_line = "—"
        # For clean UX: when a row is exportable/OK, keep the "foutmelding" column empty.
        # The decision remains available via the stored payload and tooltip (for debugging/inspection).
        show_message = not (
            dec.get("status") == DECISION_INCLUDED
            and not bool(dec.get("requires_rerun"))
            and not note
        )
        message = (raw_line + "\n" + nl_line) if show_message else ""
        if note:
            message = message + "\n" + str(note)
        err_it = self._item_readonly(message)
        err_it.setData(_ROW_DECISION_ROLE, dec)
        tooltip_msg = (raw_line + "\n" + nl_line) if not note else (raw_line + "\n" + nl_line + "\n" + str(note))
        err_it.setToolTip(self._compose_error_tooltip(error_msg=tooltip_msg, decision_trace=None))
        # #region agent log (debug mode)
        try:
            if not show_message:
                _dbg_log(
                    hypothesis_id="E",
                    location="main_window.py:_set_row_decision",
                    message="cleared error column for included/exportable decision",
                    data={"row": int(row), "reason_code": reason_code},
                    run_id="pre-fix",
                )
        except Exception:
            pass
        # #endregion
        self._table.setItem(row, PaymentColumn.STATUS, self._item_readonly(status_label))
        self._table.setItem(row, PaymentColumn.ERROR, err_it)
        self._update_row_render_hash(row)

    def _row_id(self, row: int) -> str:
        sup_it = self._table.item(row, PaymentColumn.SUPPLIER)
        if sup_it:
            rid = str(sup_it.data(_ROW_ROW_ID_ROLE) or "").strip()
            if rid:
                return rid
        inv = self._get_row_invoice_number(row)
        sup = self._cell_text(row, PaymentColumn.SUPPLIER)
        pdf = self._cell_text(row, PaymentColumn.PDF)
        rid = f"{sup}|{inv}|{pdf}".strip()
        if sup_it:
            sup_it.setData(_ROW_ROW_ID_ROLE, rid)
        return rid

    def _trace_steps_from_decision_trace(self, trace: Any) -> list[TraceStepVM]:
        if not isinstance(trace, dict):
            return [TraceStepVM(rule_name="UNKNOWN_TRACE_MISSING", input_fields_used=[], outcome="unknown", score=None)]
        steps = trace.get("steps")
        if not isinstance(steps, list) or not steps:
            return [TraceStepVM(rule_name="UNKNOWN_TRACE_MISSING", input_fields_used=[], outcome="unknown", score=None)]
        out: list[TraceStepVM] = []
        for st in steps:
            if not isinstance(st, dict):
                continue
            rule = str(st.get("rule_name") or st.get("rule") or "UNKNOWN").strip() or "UNKNOWN"
            inputs_used = st.get("input_fields_used") or st.get("inputs") or st.get("fields") or []
            if not isinstance(inputs_used, list):
                inputs_used = []
            outcome = str(st.get("outcome") or st.get("result") or st.get("pass_fail") or "unknown").strip() or "unknown"
            score = st.get("score")
            score_s = str(score) if score is not None and str(score).strip() else None
            out.append(
                TraceStepVM(
                    rule_name=rule,
                    input_fields_used=[str(x) for x in inputs_used if str(x).strip()],
                    outcome=outcome,
                    score=score_s,
                )
            )
        return out or [TraceStepVM(rule_name="UNKNOWN_TRACE_MISSING", input_fields_used=[], outcome="unknown", score=None)]

    def _resolve_row_vm(self, row: int) -> ResolvedRowViewModel:
        """Single render gate: resolve everything needed for UI from one object."""
        row_id = self._row_id(row)
        row_data = {
            "supplier_name": self._cell_text(row, PaymentColumn.SUPPLIER),
            "iban": self._cell_text(row, PaymentColumn.IBAN),
            "amount": self._cell_text(row, PaymentColumn.AMOUNT),
            "invoice_number": self._get_row_invoice_number(row),
            "customer_code": self._cell_text(row, PaymentColumn.CUSTOMER_CODE),
            "description": self._cell_text(row, PaymentColumn.DESCRIPTION),
            "execution_date": self._cell_text(row, PaymentColumn.EXECUTION_DATE),
            "pdf": self._cell_text(row, PaymentColumn.PDF),
        }

        active_run_id = self._active_run_id or self._pinned_run_id
        decision: dict[str, Any] | None = None
        if active_run_id:
            self._resolver_active = True
            try:
                dec_map = self._decision_store.committed_decision_map(active_run_id)
                raw = dec_map.get(row_id)
                if isinstance(raw, dict):
                    decision = dict(normalize_decision(raw))
            finally:
                self._resolver_active = False

        return ResolvedRowViewModel(
            row=row,
            row_id=row_id,
            row_data=row_data,
            decision=decision,
        )

    def _resolve_all_row_vms(self) -> list[ResolvedRowViewModel]:
        vms: list[ResolvedRowViewModel] = []
        for r in range(self._table.rowCount()):
            if self._is_row_blank(r):
                continue
            vms.append(self._resolve_row_vm(r))
        return vms

    def _row_render_hash(self, row: int) -> str:
        decision = self._row_decision(row)
        payload = {
            "row_id": self._row_id(row),
            "supplier": self._cell_text(row, PaymentColumn.SUPPLIER),
            "iban": self._cell_text(row, PaymentColumn.IBAN),
            "amount": self._cell_text(row, PaymentColumn.AMOUNT),
            "invoice_number": self._get_row_invoice_number(row),
            "decision": decision,
        }
        return stable_hash(payload)

    def _update_row_render_hash(self, row: int) -> None:
        err_it = self._table.item(row, PaymentColumn.ERROR)
        if err_it:
            err_it.setData(_ROW_RENDER_HASH_ROLE, self._row_render_hash(row))

    def _assert_row_hash_integrity(self, row: int) -> None:
        err_it = self._table.item(row, PaymentColumn.ERROR)
        if not err_it:
            return
        expected = str(err_it.data(_ROW_RENDER_HASH_ROLE) or "").strip()
        if not expected:
            self._update_row_render_hash(row)
            return
        actual = self._row_render_hash(row)
        if actual != expected:
            mismatch_dec = build_decision(
                status=DECISION_EXCLUDED,
                reason_code=REASON_RUNTIME_MISMATCH,
                reason_detail="UI row hash mismatch with stored decision state",
                editable=False,
                requires_rerun=True,
                causal_inputs=["row_state", "decision_store"],
                input_fields={"expected": expected, "actual": actual, "row_id": self._row_id(row)},
            )
            self._set_row_decision(row, mismatch_dec, note="Herlaad of herbereken deze rij.")
            raise RuntimeError(f"UI/engine mismatch detected for row {row}")

    def _is_row_blank(self, row: int) -> bool:
        sup = self._cell_text(row, PaymentColumn.SUPPLIER)
        iban = self._cell_text(row, PaymentColumn.IBAN)
        amt = self._cell_text(row, PaymentColumn.AMOUNT)
        desc = self._cell_text(row, PaymentColumn.DESCRIPTION)
        disc = self._cell_text(row, PaymentColumn.DISCOUNT)
        disc_norm = "" if disc in ("0", "0.0", "0,0") else disc
        return not (sup or iban or amt or desc or disc_norm)

    def _cell_date_mode(self, row: int) -> str:
        it = self._table.item(row, PaymentColumn.EXECUTION_DATE)
        if not it:
            return "direct"
        v = it.data(_ROW_DATE_MODE_ROLE)
        s = str(v).strip().lower() if v is not None else "direct"
        if s in ("direct", "due", "manual"):
            return s
        return "direct"

    def _set_row_date_mode(self, row: int, mode: str) -> None:
        it = self._table.item(row, PaymentColumn.EXECUTION_DATE)
        if it:
            it.setData(_ROW_DATE_MODE_ROLE, mode)

    def build_engine_input_from_row(self, row_id: str) -> EngineInputRow:
        for r in range(self._table.rowCount()):
            if self._row_id(r) != row_id:
                continue
            p = self._payment_dict_from_row(r)
            return EngineInputRow(
                row_id=row_id,
                supplier_name=str(p.get("supplier_name") or ""),
                iban=str(p.get("iban") or ""),
                amount=str(p.get("amount") or ""),
                invoice_number=str(p.get("invoice_number") or ""),
                customer_code=str(self._cell_text(r, PaymentColumn.CUSTOMER_CODE) or ""),
                description=str(p.get("description") or ""),
                execution_date=str(p.get("execution_date") or ""),
                invoice_date=(str(p.get("invoice_date") or "") or None),
                date_mode=str(p.get("date_mode") or "direct"),
                discount=str(p.get("discount") or "0"),
                amount_result=deepcopy(p.get("amount_result") or {}),
                decision_trace=deepcopy(p.get("decision_trace") or {}),
                supplier_match_status=str((p.get("decision_trace") or {}).get("supplier_match_status") or ""),
                source_file=str(p.get("_source_file") or ""),
            )
        raise ValueError(f"row not found for row_id: {row_id}")

    def _mark_row_pending_engine_update(self, row: int, reason: str) -> None:
        _dbg_log(
            hypothesis_id="B",
            location="main_window.py:_mark_row_pending_engine_update",
            message="pending set",
            data={
                "row": int(row),
                "reason": str(reason),
                "suppress": bool(self._suppress_table_item_changed),
                "tableSignalsBlocked": bool(self._table.signalsBlocked()),
            },
        )
        self._pending_engine_rows.add(row)
        pending_decision = build_decision(
            status=DECISION_NEEDS_REVIEW,
            reason_code=REASON_MANUAL_PENDING,
            reason_detail=reason,
            editable=True,
            requires_rerun=True,
            causal_inputs=["amount", "iban", "supplier_name", "customer_code"],
            input_fields={"row_id": self._row_id(row), "reason": reason},
        )
        self._set_row_decision(row, pending_decision)
        idempotency = stable_hash({"row_id": self._row_id(row), "decision": pending_decision, "reason": reason})
        self._pending_engine_idempotency.add(idempotency)
        self._engine_rerun_timer.start(250)

    def _commit_pending_engine_updates(self) -> None:
        if not self._pending_engine_rows:
            return
        rows = sorted(self._pending_engine_rows)
        self._pending_engine_rows.clear()
        if not self._pending_engine_idempotency:
            return
        self._pending_engine_idempotency.clear()
        self._rerun_engine_for_rows(rows)

    def _rerun_engine_for_rows(self, rows: list[int]) -> None:
        inputs: list[EngineInputRow] = []
        for row in rows:
            if row < 0 or row >= self._table.rowCount() or self._is_row_blank(row):
                continue
            rid = self._row_id(row)
            inputs.append(self.build_engine_input_from_row(rid))
        if not inputs:
            return

        supplier_db_hash = stable_hash({"supplier_db": self._supplier_db_path()})
        cfg_hash = stable_hash({"settings": self._settings})
        runtime_hash = stable_hash({"session_date": self._session_date.isoformat()})
        snapshot = build_engine_snapshot(
            invoices=inputs,
            supplier_db_hash=supplier_db_hash,
            config_hash=cfg_hash,
            runtime_context_hash=runtime_hash,
            engine_version="decision-model-v1",
        )
        schema_result = EngineInputSchema.validate(snapshot)
        run_id = str(uuid.uuid4())
        self._decision_store.begin_run(
            run_id=run_id,
            input_snapshot_hash=snapshot["snapshot_hash"],
            decision_map={inp["row_id"]: {"status": DECISION_NEEDS_REVIEW, "reason_code": REASON_MANUAL_PENDING} for inp in inputs},
        )
        if not schema_result.valid:
            self._decision_store.fail_run(run_id)
            for row in rows:
                self._set_row_decision(
                    row,
                    build_decision(
                        status=DECISION_EXCLUDED,
                        reason_code="invalid_engine_input_schema",
                        reason_detail="; ".join(schema_result.errors),
                        editable=True,
                        requires_rerun=True,
                        causal_inputs=["schema"],
                        input_fields={"errors": schema_result.errors, "row_id": self._row_id(row)},
                    ),
                )
            return

        for row in rows:
            try:
                p = self._payment_dict_from_row(row)
            except ValueError:
                self._set_row_decision(
                    row,
                    build_decision(
                        status=DECISION_EXCLUDED,
                        reason_code="amount_invalid_format",
                        reason_detail="Ongeldig bedrag",
                        editable=True,
                        requires_rerun=True,
                        causal_inputs=["amount"],
                        input_fields={"row_id": self._row_id(row)},
                    ),
                )
                continue
            validation_error = self._validate_single_payment_row(p)
            if validation_error:
                self._set_row_decision(
                    row,
                    build_decision(
                        status=DECISION_NEEDS_REVIEW,
                        reason_code="row_validation_failed",
                        reason_detail=validation_error,
                        editable=True,
                        requires_rerun=False,
                        causal_inputs=["iban", "amount", "execution_date"],
                        input_fields={"row_id": self._row_id(row), "error": validation_error},
                    ),
                )
            else:
                self._set_row_decision(
                    row,
                    build_decision(
                        status=DECISION_INCLUDED,
                        reason_code="included_validated",
                        reason_detail=None,
                        editable=False,
                        requires_rerun=False,
                        causal_inputs=["iban", "amount", "execution_date"],
                        input_fields={"row_id": self._row_id(row), "amount": p.get("amount")},
                    ),
                )
            try:
                self._assert_row_hash_integrity(row)
            except RuntimeError:
                pass
        self._decision_store.commit_run(run_id)
        self._pinned_run_id = run_id
        self._active_run_id = run_id
        self._apply_row_colors()
        self._refresh_export_batch_status_label()

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        _dbg_log(
            hypothesis_id="B",
            location="main_window.py:_on_table_item_changed:entry",
            message="item changed",
            data={
                "suppress": bool(self._suppress_table_item_changed),
                "col": int(item.column()),
                "row": int(item.row()),
                "tableSignalsBlocked": bool(self._table.signalsBlocked()),
                "is_loading_batch": bool(self._is_loading_batch),
            },
        )
        if self._is_loading_batch:
            return
        if self._suppress_table_item_changed:
            return
        col = item.column()
        row = item.row()
        if col == PaymentColumn.INVOICE_DATE:
            item.setData(_ROW_INVOICE_DATE_SOURCE_ROLE, "manual")
            t = item.text().strip()
            iso = parse_ui_date_to_iso(t)
            if iso:
                item.setData(Qt.ItemDataRole.UserRole, iso)
            else:
                item.setData(Qt.ItemDataRole.UserRole, None)
            if t:
                item.setToolTip("Handmatig ingesteld — factuurdatum")
            else:
                item.setToolTip("")
        elif col == PaymentColumn.EXECUTION_DATE:
            self._set_row_date_mode(row, "manual")
            iso = parse_ui_date_to_iso(item.text().strip())
            if iso:
                item.setData(Qt.ItemDataRole.UserRole, iso)
            else:
                item.setData(Qt.ItemDataRole.UserRole, None)
            item.setToolTip("Handmatig ingesteld — betaaldatum")
        elif col == PaymentColumn.AMOUNT:
            t = item.text().strip()
            if not t:
                self._reject_invalid_amount_cell_edit(item, row)
                return
            try:
                dec = _parse_amount_str(t)
            except ValueError:
                self._reject_invalid_amount_cell_edit(item, row)
                return
            if dec <= Decimal("0.00"):
                self._reject_invalid_amount_cell_edit(item, row)
                return
            self._sync_amount_result_and_row_ui(
                row, dec, from_manual_typing=True, picked_candidate=None
            )
            self._mark_row_pending_engine_update(row, "amount_changed")
        elif col == PaymentColumn.IBAN:
            self._mark_row_pending_engine_update(row, "iban_changed")
        elif col == PaymentColumn.SUPPLIER:
            # Capture original supplier name once for rows created/edited manually.
            # Used by "Voeg toe / update" to rename suppliers in suppliers.json.
            try:
                orig = str(item.data(_ROW_SUPPLIER_ORIGINAL_ROLE) or "").strip()
                now = (item.text() or "").strip()
                if not orig and now:
                    item.setData(_ROW_SUPPLIER_ORIGINAL_ROLE, now)
            except Exception:
                pass
            self._mark_row_pending_engine_update(row, "supplier_changed")
        elif col == PaymentColumn.CUSTOMER_CODE:
            self._mark_row_pending_engine_update(row, "customer_code_changed")
        self._refresh_export_batch_status_label()

    def _on_table_cell_clicked(self, row: int, column: int) -> None:
        if column == int(PaymentColumn.INFO):
            self._open_diagnostics_for_row(row)
            return
        if column == int(PaymentColumn.CUSTOMER_CODE):
            if self._cell_text(row, PaymentColumn.CUSTOMER_CODE).strip() == "?":
                self._show_ident_field_candidate_menu(row, "customer_number")
            return
        if column == int(PaymentColumn.DESCRIPTION):
            if not self._get_row_invoice_number(row) and self._ident_field_picker_eligible(
                self._ident_field_result_snapshot_for_row(row, "invoice_number")
            ):
                self._show_ident_field_candidate_menu(row, "invoice_number")
            elif "?" in self._cell_text(row, PaymentColumn.DESCRIPTION):
                self._show_ident_field_candidate_menu(row, "invoice_number")
            return
        if column != int(PaymentColumn.AMOUNT):
            return
        amt_it = self._table.item(row, PaymentColumn.AMOUNT)
        if not amt_it:
            return
        if self._cell_text(row, PaymentColumn.AMOUNT).strip() != "?":
            return
        snap = amt_it.data(_ROW_AMOUNT_RESULT_ROLE)
        if not isinstance(snap, dict):
            return
        st = str(snap.get("status") or snap.get("amount_status") or "").strip().lower()
        if st not in ("ambiguous", "tentative", "failed"):
            return
        cands = snap.get("candidates")
        if not isinstance(cands, list) or not cands:
            QMessageBox.information(
                self,
                "Bedrag kiezen",
                "Er zijn geen parser-kandidaten om uit te kiezen.",
            )
            return
        all_opts: list[dict[str, Any]] = [c for c in cands if isinstance(c, dict)]
        incl_opts = [c for c in all_opts if str(c.get("type") or "").lower() == "incl"]
        # Eerder: ``incl if incl else all`` — één incl.-kandidaat maakte ``incl`` truthy waardoor
        # alle andere (excl./unknown) vielen en len(opts)==1: geen menu, wel "?" + fouttekst.
        if len(incl_opts) >= 2:
            opts = incl_opts
            _branch = "incl_only"
        else:
            opts = all_opts
            _branch = "all_candidates"
        _agent_log(
            "H2",
            "main_window.py:_on_table_cell_clicked",
            "amount picker options",
            {
                "raw_cands_len": len(cands) if isinstance(cands, list) else -1,
                "dict_cands_len": len(all_opts),
                "incl_opts_len": len(incl_opts),
                "menu_opts_len": len(opts),
                "branch": _branch,
                "incl_distinct_values": len(
                    {str(c.get("value")) for c in incl_opts if isinstance(c, dict)}
                ),
            },
        )
        if not opts:
            QMessageBox.information(
                self,
                "Bedrag kiezen",
                "De parserkandidaten hebben geen bruikbare metadata (verwacht dicts).",
            )
            return
        menu = QMenu(self)
        for cand in opts:
            raw_v = cand.get("value")
            try:
                disp = _format_amount_nl(raw_v) if raw_v is not None else "?"
            except Exception:
                disp = str(raw_v or "?")
            label = (
                f"{disp} — {_nl_amount_candidate_source(str(cand.get('source') or ''))}"
                f"{_amount_candidate_type_hint_nl(cand)}"
            )
            conf = cand.get("confidence")
            if conf is not None:
                label += f" ({int(conf)}%)"
            ctx = str(cand.get("context") or "")
            if len(ctx) > 72:
                ctx = ctx[:69] + "..."
            act = menu.addAction(label)
            act.setToolTip(ctx)
            act.triggered.connect(
                lambda checked=False, r=row, c=cand: self._apply_amount_candidate_pick_to_row(r, c)
            )
        menu.exec(QCursor.pos())

    @staticmethod
    def _amount_candidates_include_decimal(cands: list[Any], dec: Decimal) -> bool:
        for c in cands:
            if not isinstance(c, dict):
                continue
            raw = c.get("value")
            if raw is None:
                continue
            try:
                if amount_to_decimal(str(raw)) == dec:
                    return True
            except ValueError:
                continue
        return False

    def _mark_amount_snapshot_failed(self, amt_item: QTableWidgetItem) -> None:
        """amount_result → failed; geen stille match met oude confirmed snapshot bij ongeldige cel."""
        snap_raw = amt_item.data(_ROW_AMOUNT_RESULT_ROLE)
        if isinstance(snap_raw, dict):
            snap: dict[str, Any] = deepcopy(snap_raw)
            pv = snap.get("value") if snap.get("value") is not None else snap.get("selected_amount")
            if pv is not None and "original_value" not in snap:
                snap["original_value"] = str(pv)
            snap["status"] = "failed"
            snap["amount_status"] = "failed"
            snap["user_selected"] = False
            snap["value"] = None
            snap["selected_amount"] = None
        else:
            snap = {
                "status": "failed",
                "amount_status": "failed",
                "user_selected": False,
                "candidates": [],
            }
        amt_item.setData(_ROW_AMOUNT_RESULT_ROLE, snap)

    def _set_amount_row_error_with_trace(self, row: int, message: str) -> None:
        amt_it = self._table.item(row, PaymentColumn.AMOUNT)
        snap = amt_it.data(_ROW_AMOUNT_RESULT_ROLE) if amt_it else None
        err_prev = self._table.item(row, PaymentColumn.ERROR)
        prev_trace = err_prev.data(_ROW_DECISION_TRACE_ROLE) if err_prev else None
        err = self._item_readonly(message)
        if isinstance(snap, dict):
            err.setData(
                _ROW_DECISION_TRACE_ROLE,
                _merge_decision_trace_parsed_amount(
                    prev_trace if isinstance(prev_trace, dict) else None,
                    snap,
                ),
            )
        tr = err.data(_ROW_DECISION_TRACE_ROLE)
        err.setToolTip(
            self._compose_error_tooltip(
                error_msg=message,
                decision_trace=tr if isinstance(tr, dict) else None,
            )
        )
        err.setData(
            _ROW_DECISION_ROLE,
            build_decision(
                status=DECISION_EXCLUDED,
                reason_code="amount_invalid_format",
                reason_detail=message,
                editable=True,
                requires_rerun=True,
                causal_inputs=["amount"],
                input_fields={"row_id": self._row_id(row)},
            ),
        )
        self._table.setItem(
            row,
            PaymentColumn.STATUS,
            self._item_readonly(decision_status_label_nl(DECISION_EXCLUDED)),
        )
        self._table.setItem(row, PaymentColumn.ERROR, err)
        self._update_row_render_hash(row)

    def _reject_invalid_amount_cell_edit(self, amt_item: QTableWidgetItem, row: int) -> None:
        """Lege / ongeldige / niet-positieve bedragcel — zelfde pad voor paste en typen."""
        self._suppress_table_item_changed = True
        amt_item.setData(Qt.ItemDataRole.UserRole, None)
        self._mark_amount_snapshot_failed(amt_item)
        self._suppress_table_item_changed = False
        self._set_amount_row_error_with_trace(row, "Ongeldig bedrag")
        self._apply_row_colors()
        self._refresh_export_batch_status_label()

    def _sync_amount_result_and_row_ui(
        self,
        row: int,
        dec: Decimal,
        *,
        from_manual_typing: bool,
        picked_candidate: dict[str, Any] | None = None,
        amount_source: str | None = None,
    ) -> None:
        """Werk amount_result-snapshot, trace en bedragcel bij na handmatige invoer of kandidaatkeuze."""
        amt_it = self._table.item(row, PaymentColumn.AMOUNT)
        if not amt_it:
            return
        snap_raw = amt_it.data(_ROW_AMOUNT_RESULT_ROLE)
        snap: dict[str, Any] = deepcopy(snap_raw) if isinstance(snap_raw, dict) else {}
        raw_c = snap.get("candidates")
        if isinstance(raw_c, list):
            cands: list[Any] = [deepcopy(c) for c in raw_c if isinstance(c, dict)]
        else:
            cands = []

        prev_str = snap.get("value")
        if prev_str is None:
            prev_str = snap.get("selected_amount")
        if prev_str is not None and "original_value" not in snap:
            snap["original_value"] = str(prev_str)

        val_xml = format_eur_xml(dec)
        snap["value"] = val_xml
        snap["selected_amount"] = val_xml
        snap["status"] = "confirmed"
        snap["amount_status"] = "confirmed"
        snap["user_selected"] = True

        if amount_source == "profile":
            snap["confidence"] = 95
            snap["source"] = "profile"
            snap["amount_status"] = "confirmed"
        elif picked_candidate is not None:
            snap["confidence"] = int(picked_candidate.get("confidence") or snap.get("confidence") or 0)
            src = str(picked_candidate.get("source") or snap.get("source") or "").strip()
            snap["source"] = src.upper() if src else str(snap.get("source") or "")
        else:
            snap["confidence"] = 100
            snap["source"] = "MANUAL"

        if from_manual_typing:
            if not self._amount_candidates_include_decimal(cands, dec):
                cands.append({
                    "value": val_xml,
                    "source": "manual",
                    "confidence": 100,
                    "type": "incl",
                })
        snap["candidates"] = cands

        self._suppress_table_item_changed = True
        amt_it.setText(_format_amount_nl(dec))
        amt_it.setData(Qt.ItemDataRole.UserRole, val_xml)
        amt_it.setData(_ROW_AMOUNT_RESULT_ROLE, snap)
        if amount_source == "profile":
            amt_it.setToolTip("Bevestigd via extractieprofiel")
        elif picked_candidate is not None:
            amt_it.setToolTip("Handmatig gekozen uit parserkandidaten")
        else:
            amt_it.setToolTip("Handmatig ingevoerd bedrag")
        err_prev = self._table.item(row, PaymentColumn.ERROR)
        prev_trace = err_prev.data(_ROW_DECISION_TRACE_ROLE) if err_prev else None
        new_trace = _merge_decision_trace_parsed_amount(
            prev_trace if isinstance(prev_trace, dict) else None,
            snap,
        )
        new_err = self._item_readonly("")
        new_err.setData(_ROW_DECISION_TRACE_ROLE, new_trace)
        new_err.setData(
            _ROW_DECISION_ROLE,
            build_decision(
                status=DECISION_NEEDS_REVIEW,
                reason_code=REASON_MANUAL_PENDING,
                reason_detail="Amount changed in UI",
                editable=True,
                requires_rerun=True,
                causal_inputs=["amount"],
                input_fields={"row_id": self._row_id(row), "value": val_xml},
            ),
        )
        new_err.setToolTip(self._compose_error_tooltip(error_msg="", decision_trace=new_trace))
        self._table.setItem(row, PaymentColumn.ERROR, new_err)
        self._table.setItem(
            row,
            PaymentColumn.STATUS,
            self._item_readonly(decision_status_label_nl(DECISION_NEEDS_REVIEW)),
        )
        self._suppress_table_item_changed = False
        self._update_row_render_hash(row)
        self._apply_row_colors()
        self._refresh_export_batch_status_label()
        self._refresh_profile_button_state()

    def _apply_amount_candidate_pick_to_row(self, row: int, cand: dict[str, Any]) -> None:
        """Gebruiker kiest een parser-kandidaat: amount_result → confirmed, rij exporteerbaar."""
        amt_it = self._table.item(row, PaymentColumn.AMOUNT)
        if not amt_it:
            return
        if not isinstance(amt_it.data(_ROW_AMOUNT_RESULT_ROLE), dict):
            return
        raw_v = cand.get("value")
        if raw_v is None:
            return
        try:
            dec = amount_to_decimal(str(raw_v))
        except (TypeError, ValueError):
            return
        if dec <= Decimal("0.00"):
            return
        self._sync_amount_result_and_row_ui(
            row, dec, from_manual_typing=False, picked_candidate=cand
        )
        self._refresh_profile_button_state()

    def _refresh_export_batch_status_label(self) -> None:
        """Batch-export preview: zelfde rijen als export vóór dialogs (geen fout/needs_review)."""
        previews: list[dict[str, Any]] = []
        for r in range(self._table.rowCount()):
            if self._is_row_blank(r):
                continue
            try:
                self._assert_row_hash_integrity(r)
            except RuntimeError as e:
                self._set_status(f"Fout: {e}")
                return
            dec = self._decision_for_row(r)
            try:
                p = self._payment_dict_from_row(r)
                p["decision"] = dec
                previews.append(p)
            except ValueError:
                continue
        exportable = exportable_payments_from_decisions(previews)
        result = validate_export_batch(exportable)
        label_map = {"valid": "VALID", "warning": "WARNING", "blocked": "BLOCKED"}
        self._batch_status_label.setText(f"Batch status: {label_map[result.status]}")
        self._btn_xml.setEnabled(result.status != "blocked")

    def _payment_dict_from_row(
        self, row: int, *, require_resolved_amount: bool = True
    ) -> dict[str, Any]:
        disc_raw = self._cell_text(row, PaymentColumn.DISCOUNT)
        inv_no = self._get_row_invoice_number(row)
        inv_dt_cell = self._table.item(row, PaymentColumn.INVOICE_DATE)
        inv_src = inv_dt_cell.data(_ROW_INVOICE_DATE_SOURCE_ROLE) if inv_dt_cell else "missing"
        if inv_src is None:
            inv_src = "missing"
        term_it = self._table.item(row, PaymentColumn.TERM_HINT)
        eff_raw = term_it.data(_ROW_EFFECTIVE_TERM_ROLE) if term_it else 0
        try:
            eff_term = int(eff_raw) if eff_raw is not None else 0
        except (TypeError, ValueError):
            eff_term = 0
        tr_raw = term_it.data(_ROW_TERM_TRUSTED_ROLE) if term_it else None
        inv_date_txt = self._cell_text(row, PaymentColumn.INVOICE_DATE).strip()
        inv_iso = parse_ui_date_to_iso(inv_date_txt)
        ex_txt = self._cell_text(row, PaymentColumn.EXECUTION_DATE).strip()
        ex_iso = parse_ui_date_to_iso(ex_txt)
        amt_cell_txt = self._cell_text(row, PaymentColumn.AMOUNT).strip()
        amt_snap = self._amount_result_snapshot_for_row(row)
        if require_resolved_amount:
            amount_val = resolved_payment_amount_for_export(
                amount_cell_text=amt_cell_txt,
                amount_result=amt_snap,
            )
        else:
            amount_val = None
        payment = {
            "row_id": self._row_id(row),
            "supplier_name": self._cell_text(row, PaymentColumn.SUPPLIER),
            "iban": self._cell_text(row, PaymentColumn.IBAN),
            "amount": amount_val,
            "description": self._cell_text(row, PaymentColumn.DESCRIPTION),
            "invoice_number": inv_no,
            "discount": disc_raw if disc_raw else "0",
            "invoice_date": inv_iso,
            "invoice_date_source": str(inv_src),
            "execution_date": ex_iso or "",
            "date_mode": self._cell_date_mode(row),
            "supplier_payment_term_days_effective": eff_term,
            "supplier_term_trusted": tr_raw if tr_raw is not None else None,
            "decision": self._decision_for_row(row),
        }
        err_it = self._table.item(row, PaymentColumn.ERROR)
        trace_raw = err_it.data(_ROW_DECISION_TRACE_ROLE) if err_it else None
        if isinstance(amt_snap, dict):
            payment["amount_result"] = amt_snap
            payment["decision_trace"] = _merge_decision_trace_parsed_amount(
                trace_raw if isinstance(trace_raw, dict) else None,
                amt_snap,
            )
        elif isinstance(trace_raw, dict):
            payment["decision_trace"] = deepcopy(trace_raw)
        return payment

    def _table_rows_to_payment_dicts(self) -> list[dict[str, Any]]:
        """Lees bewerkte tabel uit naar dicts voor ``generate_xml`` (niet-lege rijen)."""
        rows: list[dict[str, Any]] = []
        for r in range(self._table.rowCount()):
            if self._is_row_blank(r):
                continue
            rows.append(self._payment_dict_from_row(r))
        return rows

    def _clear_row_validation_marks(self) -> None:
        _KEEP = frozenset(
            {
                "ok",
                "confirmed",
                "reviewed",
                "handmatig",
                "needs_review",
                "needs review",
                decision_status_label_nl(DECISION_INCLUDED).lower(),
                decision_status_label_nl(DECISION_NEEDS_REVIEW).lower(),
                decision_status_label_nl(DECISION_EXCLUDED).lower(),
            }
        )
        for r in range(self._table.rowCount()):
            status = self._cell_text(r, PaymentColumn.STATUS).lower()
            if status not in _KEEP:
                self._table.setItem(r, PaymentColumn.STATUS, self._item_readonly(""))
                it = self._item_readonly("")
                it.setToolTip("")
                self._table.setItem(r, PaymentColumn.ERROR, it)

    def _set_row_validation(self, row: int, status: str, error: str) -> None:
        # UI-only validation marker: must not overwrite engine decisions.
        msg = (error or "").strip()
        it = self._item_readonly(msg)
        it.setToolTip(msg)
        self._table.setItem(row, PaymentColumn.ERROR, it)

    def _validate_single_payment_row(self, p: dict[str, Any]) -> Optional[str]:
        if not str(p.get("supplier_name") or "").strip():
            return "leverancier is leeg"
        iban_n = clean_iban(str(p.get("iban") or ""))
        if not iban_n or not is_plausible_iban(iban_n):
            return "IBAN ontbreekt of is ongeldig"
        try:
            amt = amount_to_decimal(p["amount"])
        except (KeyError, TypeError, ValueError):
            return "bedrag is ongeldig"
        if amt <= Decimal("0.00"):
            return "bedrag moet groter zijn dan nul"
        ex = str(p.get("execution_date") or "").strip()
        if not ex or not is_valid_iso_date_str(ex):
            return "betaaldatum ontbreekt of ongeldig (dd-mm-jjjj of jjjj-mm-dd)"
        mode = str(p.get("date_mode") or "direct")
        if mode == "due" and not (p.get("invoice_date") and str(p.get("invoice_date")).strip()):
            return "factuurdatum verplicht voor ‘op uiterste betaaldatum’"
        return None

    def _finalize_payment_for_export(self, p: dict[str, Any]) -> None:
        """Vul execution_date vlak vóór XML; manual blijft ongemoeid."""
        mode = str(p.get("date_mode") or "direct")
        if mode == "manual":
            return
        if mode == "direct":
            p["execution_date"] = execution_date_for_direct(self._session_date)
            return
        if mode == "due":
            ex = execution_date_for_due(
                p.get("invoice_date"),
                int(p.get("supplier_payment_term_days_effective") or 0),
                self._session_date,
            )
            if ex is None:
                raise ValueError("due-modus zonder geldige factuurdatum")
            p["execution_date"] = ex

    def _build_export_batch_summary_nl(self, payments: list[dict[str, Any]]) -> str:
        from collections import defaultdict

        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for p in payments:
            groups[str(p.get("execution_date") or "").strip()].append(p)
        lines: list[str] = ["Je staat op het punt te exporteren:", ""]
        all_decs: list[Decimal] = []
        grand_n = 0
        for ex in sorted(groups.keys()):
            if not ex:
                continue
            plist = groups[ex]
            decs: list[Decimal] = []
            for x in plist:
                try:
                    decs.append(amount_to_decimal(x.get("amount")))
                except ValueError:
                    continue
            all_decs.extend(decs)
            total = sum_decimals(decs)
            grand_n += len(plist)
            amt_nl = format_eur_xml(total).replace(".", ",")
            ex_d = parse_iso_date(ex)
            label = (
                f"{ex_d.day:02d}-{ex_d.month:02d}-{ex_d.year}"
                if ex_d
                else ex
            )
            if ex == self._session_date.isoformat():
                label = f"{label} (sessie)"
            lines.append(f"{label}: €{amt_nl} — {len(plist)} betaling(en)")
        grand = sum_decimals(all_decs)
        grand_s = format_eur_xml(grand).replace(".", ",")
        lines.append("")
        lines.append(f"Totaal: €{grand_s} — {grand_n} betaling(en)")
        return "\n".join(lines)

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

        self._clear_row_validation_marks()
        QApplication.processEvents()

        invalid: list[tuple[int, str]] = []
        row_payment_pairs: list[tuple[int, dict[str, Any]]] = []

        for r in range(self._table.rowCount()):
            if self._is_row_blank(r):
                continue
            dec = self._decision_for_row(r)
            if dec.get("status") != DECISION_INCLUDED or bool(dec.get("requires_rerun")):
                continue
            try:
                p = self._payment_dict_from_row(r)
            except ValueError:
                invalid.append((r, "ongeldig bedrag"))
                continue
            p["decision"] = dec
            mode = str(p.get("date_mode") or "direct")
            if mode == "due" and not (p.get("invoice_date") and str(p.get("invoice_date")).strip()):
                invalid.append((r, "factuurdatum verplicht voor ‘op uiterste betaaldatum’"))
                continue
            try:
                self._finalize_payment_for_export(p)
            except ValueError as e:
                invalid.append((r, str(e)))
                continue
            msg = self._validate_single_payment_row(p)
            if msg:
                invalid.append((r, msg))
            else:
                row_payment_pairs.append((r, p))

        if invalid:
            for r, msg in invalid:
                self._set_row_validation(r, "fout", msg)
            if len(invalid) == 1:
                self._set_status(f"Fout: rij {invalid[0][0] + 1}: {invalid[0][1]}")
            else:
                self._set_status(f"Fout: {len(invalid)} rijen ongeldig (zie Foutmelding-kolom)")
            return

        if not row_payment_pairs:
            self._set_status("Fout: geen betalingsregels om te exporteren")
            return

        self._suppress_table_item_changed = True
        for r, p in row_payment_pairs:
            if self._cell_date_mode(r) != "manual":
                it = self._table.item(r, PaymentColumn.EXECUTION_DATE)
                if it:
                    ex_iso = str(p.get("execution_date") or "").strip()
                    it.setText(format_date_nl_from_iso(ex_iso))
                    if ex_iso and parse_iso_date(ex_iso):
                        it.setData(Qt.ItemDataRole.UserRole, ex_iso)
        self._suppress_table_item_changed = False
        self._refresh_export_batch_status_label()

        payment_dicts = exportable_payments_from_decisions([p for _r, p in row_payment_pairs])

        duplicates = self._check_duplicate_payments(payment_dicts)
        if duplicates:
            lines: list[str] = []
            for sup, inv, amt, ts in duplicates[:10]:
                lines.append(f"  {sup}  |  {inv}  |  EUR {_format_amount_nl(amt)}  (export {ts})")
            if len(duplicates) > 10:
                lines.append(f"  … en nog {len(duplicates) - 10} andere")
            detail = "\n".join(lines)
            dup_answer = QMessageBox.warning(
                self,
                "Mogelijke dubbele betalingen",
                f"{len(duplicates)} factuur/facturen zijn al eerder geëxporteerd:\n\n"
                f"{detail}\n\n"
                "Wil je toch doorgaan?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if dup_answer != QMessageBox.StandardButton.Yes:
                self._set_status("XML export geannuleerd (dubbele betalingen).")
                return

        total_amount = sum_decimals([amount_to_decimal(p.get("amount")) for p in payment_dicts])
        summary = self._build_export_batch_summary_nl(payment_dicts)
        weekend_hits: set[str] = set()
        for p in payment_dicts:
            exd = parse_iso_date(str(p.get("execution_date") or ""))
            if exd and is_weekend(exd):
                weekend_hits.add(str(p.get("execution_date") or ""))
        if weekend_hits:
            nl_labels = [
                format_date_nl_from_iso(h) or h
                for h in sorted(weekend_hits)
            ]
            QMessageBox.warning(
                self,
                "Betaaldatum weekend",
                "Eén of meer betaaldatums vallen in het weekend; de bank kan deze verschuiven.\n\n"
                + "\n".join(nl_labels),
            )
        confirm = QMessageBox.question(
            self,
            "Bevestig XML export",
            f"{summary}\n\nDoorgaan?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            self._set_status("XML export geannuleerd.")
            return

        batch_gate = validate_export_batch(payment_dicts)
        if batch_gate.status == "blocked":
            msg = format_batch_export_blocked_message(batch_gate)
            QMessageBox.critical(self, "Export geblokkeerd", msg)
            self._set_status(msg)
            return

        for r in range(self._table.rowCount()):
            if self._is_row_blank(r):
                continue
            if self._decision_for_row(r).get("status") == DECISION_INCLUDED:
                self._set_row_validation(r, "ok", "")

        out_dir = self._resolve_export_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        debtor_for_xml: dict[str, Any] = self.get_debtor_dict_for_xml()

        try:
            abspath = generate_xml(
                payment_dicts,
                debtor_for_xml,
                str(out_dir),
                run_id=self._pinned_run_id,
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
