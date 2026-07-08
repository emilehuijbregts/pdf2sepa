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
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from enum import IntEnum
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple, Optional

from PySide6.QtCore import Qt, QSize, QTimer, QThread
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
from logic.batch_load_pipeline import BatchLoadParams, apply_match_postprocess
from logic.batch_load_types import (
    BatchLoadResult,
    IbanAmbiguityDialogSpec,
    IbanRawUiAnswer,
    IbanRawUiAnswers,
    PreprocessCheckpoint,
)
from logic.iban_resolution_engine import apply_supplier_db_mutations
from logic.invoice_parse_cache import ParsedInvoiceBatchCache, index_invoices_by_source_file
from logic.decision_store import DecisionStore
from logic.decision_store import UserApprovalStore
from logic.diagnostics import (
    build_diagnostics,
    build_invoice_diagnostics_snapshot,
    overlay_field_result,
)
from parser.field_adapters import (
    canonicalize_legacy_result_dict,
    field_result_from_legacy_dict,
    field_result_to_legacy_dict,
)
from parser.field_model import FieldCandidate, FieldId, normalize_field_value
from parser.field_resolver import _values_equal, resolve_field
from parser.resolved_field_apply import apply_resolved_field_result
from logic.profile_learning import (
    can_offer_profile_learning,
    confirm_invoice_fields,
    confirmed_amount_xml,
    profile_field_keys_missing,
    profile_learning_block_reason,
)
from logic.credit_profile_learning import (
    confirm_credit_profile_fields,
    credit_profile_learning_block_reason,
    supplier_key_for_matched_invoice,
)
from ui.diagnostics_dialog import DiagnosticsDialog
from ui.i18n import UiStrings, tr, tr_or_code
from ui.loading_overlay import LoadingOverlay
from ui.field_picker import (
    append_customer_absent_menu_action,
    build_field_candidate_menu,
    filter_amount_menu_candidates,
    filter_iban_menu_candidates,
    picker_eligible,
)
from ui.field_review import (
    CUSTOMER_ABSENT_PICK_SOURCE,
    CUSTOMER_ABSENT_STATE,
    FIELD_REVIEW_SPECS,
    REVIEW_FIELD_IDS,
    candidate_menu_tooltip,
    format_amount_candidate_menu_label,
    format_iban_candidate_menu_label,
    format_ident_candidate_menu_label,
    is_customer_absent_pick,
    make_customer_absent_pick_candidate,
)
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
    normalize_decision,
    now_utc_iso,
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
from logic.credit_enrichment import enrich_credit_documents
from logic.credit_settlement import document_id
from logic.credit_override_apply import make_detach_override, make_reassign_override
from logic.amount_override_apply import apply_amount_overrides
from logic.amount_override_store import AmountOverride, AmountOverrideSession, amount_override_session_fingerprint
from logic.document_type_override_store import (
    DocumentTypeOverrideSession,
    DocumentTypeOverrideStore,
    document_type_override_session_fingerprint,
    make_document_type_override,
)
from logic.credit_override_store import CreditOverrideStore
from logic.engine_cache import SettlementEngineCache
from logic.engine_result import EngineResult
from logic.batch_trace import log_batch_summary
from logic.shadow_mode import run_shadow_validation, shadow_mode_enabled
from logic.payment_engine import (
    batch_requires_settlement,
    calculate_payments,
    calculate_payments_with_overrides,
)
from logic.settlement_export import (
    SettlementExportInput,
    exportable_groups,
    settlement_groups_to_sepa_rows,
    validate_engine_result_for_export,
)
from logic.validation import clean_iban, is_plausible_iban
from logic.paths import read_user_data_root, write_user_data_root
from logic.settings import (
    DEFAULT_SETTINGS,
    apply_legacy_export_dir_migration,
    format_internal_vat_numbers_for_display,
    load_settings,
    merge_debtor_with_defaults,
    resolve_settings_path,
    save_settings,
    sync_debtor_vat_output,
    validate_debtor_for_export,
)
from parser.field_candidates import normalize_internal_vat_numbers_for_storage
from output.sepa_xml import (
    exportable_payments_from_decisions,
    format_batch_export_blocked_message,
    generate_xml,
    validate_export_batch,
)
from parser.pdf_parser import extract_text_strict, format_remittance_text, normalize_amount_decimal
from parser.supplier_db import (
    CUSTOMER_NUMBER_MODE_NONE,
    SupplierDB,
    SupplierDBSnapshot,
    customer_number_authoritative_value,
    customer_number_is_absent_or_none,
    customer_number_mode_from_profile,
    infer_customer_number_mode_from_result,
)
from parser.supplier_matcher import match_suppliers, _db_core_matches
from ui.credit_override_dialog import CreditOverrideDialog
from ui.settlement_badges import settlement_badge_for_group, settlement_badge_nl, _is_credit_only_group
from ui.settlement_expand import (
    SettlementRowKind,
    _ROW_SETTLEMENT_DOC_ID_ROLE,
    _ROW_SETTLEMENT_DOC_TYPE_ROLE,
    _ROW_SETTLEMENT_ROW_KIND_ROLE,
    _ROW_SETTLEMENT_SOURCE_PDF_ROLE,
    _ROW_SETTLEMENT_SUPPLIER_ROLE,
    breakdown_child_rows,
    header_supplier_label,
    mark_group_header_row,
    settlement_group_is_expandable,
    settlement_row_kind,
    vm_from_group,
)
from ui.settlement_inspector import settlement_inspector_lines
from ui.settlement_table import (
    credit_document_ids_from_batch,
    engine_result_views,
    payment_stub_from_group,
    review_documents_as_error_buckets,
    settlement_group_rows,
)
from ui.suppliers_dialog import SuppliersDialog
from ui.workers.invoice_batch_load_worker import InvoiceBatchLoadWorker

logger = logging.getLogger(__name__)

# #region agent log (debug mode - session 3d66a1)
_DEBUG_LOG_3D66A1 = "/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-3d66a1.log"


def _dbg_log_3d66a1(
    *,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any] | None = None,
    run_id: str = "pre-fix",
) -> None:
    try:
        import json
        import time

        payload = {
            "sessionId": "3d66a1",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
            "runId": run_id,
        }
        with open(_DEBUG_LOG_3D66A1, "a", encoding="utf-8") as f:
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
class _CellSnapshot:
    text: str
    tooltip: str
    flags: int
    roles: dict[int, Any]


@dataclass(frozen=True)
class _RowUndoEntry:
    """Single-row undo step (stable row_id, not table index)."""

    row_id: str
    cells: tuple[_CellSnapshot | None, ...]
    session_amount_override: AmountOverride | None = None
    had_session_amount_override: bool = False
    edited_column: int | None = None


@dataclass(frozen=True)
class _TableSnapshot:
    cells: tuple[tuple[_CellSnapshot | None, ...], ...]
    active_run_id: str | None
    session_amount_overrides: dict[str, AmountOverride]


class _PaymentsTableWidget(QTableWidget):
    """Captures undo snapshot once per inline edit (before value changes)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._undo_before_edit: Callable[[int, int], None] | None = None
        self._edit_undo_captured: bool = False
        self._sorting_enabled_before_edit: bool = False

    def set_undo_before_edit(self, callback: Callable[[int, int], None] | None) -> None:
        self._undo_before_edit = callback

    def on_cell_editor_closed(self) -> None:
        """Allow next edit to capture undo; restore sorting if it was on."""
        self._edit_undo_captured = False
        if self._sorting_enabled_before_edit:
            self.setSortingEnabled(True)

    def edit(self, index, trigger, event=None):  # noqa: N802
        # Qt roept edit() bij elke klik aan; alleen capturen als de editor écht opent
        # (return True). De celwaarde is dan nog ongewijzigd — commit volgt pas later.
        started = super().edit(index, trigger, event)
        if (
            started
            and index.isValid()
            and self._undo_before_edit is not None
            and not self._edit_undo_captured
        ):
            self._sorting_enabled_before_edit = self.isSortingEnabled()
            if self._sorting_enabled_before_edit:
                self.setSortingEnabled(False)
            self._undo_before_edit(index.row(), index.column())
            self._edit_undo_captured = True
        return started


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

from logic.runtime_paths import app_root as _app_root

APP_BASE = _app_root()

_SUPPLIER_MATCH_REVIEW_STATUSES = frozenset(
    {"needs_review", "unmatched", "no_hint", "new", "load_failed"}
)

_MATCH_STATUS_TO_ENGINE_REASON: dict[str, str] = {
    "unmatched": "unmatched_supplier",
    "needs_review": "needs_review",
    "no_hint": "no_supplier_hint",
    "new": "unmatched_supplier",
    "load_failed": "pdf_read_failed",
}

def _decision_status_label(status: str) -> str:
    return tr_or_code(f"decision.status.{status}", status)


def _looks_like_internal_code(text: str) -> bool:
    token = str(text or "").strip().split(" — ", 1)[0].strip()
    if not token:
        return False
    for prefix in ("error.reason.", "decision.reason.", "warning."):
        if UiStrings.has(f"{prefix}{token}"):
            return True
    if UiStrings.has(f"validation.row.{token}"):
        return True
    return bool(token) and "_" in token and token == token.lower() and " " not in token


def _translate_error_token(token: str) -> str:
    t = str(token or "").strip()
    if not t:
        return ""
    for prefix in ("error.reason.", "decision.reason.", "warning."):
        key = f"{prefix}{t}"
        if UiStrings.has(key):
            return tr(key)
    val_key = f"validation.row.{t}"
    if UiStrings.has(val_key):
        return tr(val_key)
    if UiStrings.has(t):
        return tr(t)
    if _looks_like_internal_code(t):
        return tr("table.error.unknown")
    return t


def _user_facing_error_text(
    *,
    reason_code: str | None = None,
    reason_detail: str | None = None,
    warnings: str | None = None,
    note: str | None = None,
) -> str:
    parts: list[str] = []
    if warnings:
        for key in [p.strip() for p in str(warnings).split("|") if p.strip()]:
            translated = _translate_error_token(key)
            if translated and translated not in parts:
                parts.append(translated)
    elif reason_code:
        code = str(reason_code).strip()
        if code:
            main = _translate_error_token(code)
            if main:
                parts.append(main)
        detail = str(reason_detail).strip() if reason_detail is not None else ""
        if detail and detail != code:
            if _looks_like_internal_code(detail):
                translated_detail = _translate_error_token(detail)
                if translated_detail and translated_detail not in parts:
                    parts.append(translated_detail)
            elif not _looks_like_internal_code(code):
                parts.append(detail)
    note_s = str(note).strip() if note is not None else ""
    if note_s and note_s not in parts:
        parts.append(note_s)
    return "\n".join(parts)


def _sanitize_table_error_message(msg: str) -> str:
    s = str(msg or "").strip()
    if not s:
        return ""
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    if len(lines) >= 2 and _looks_like_internal_code(lines[0].split(" — ", 1)[0]):
        return "\n".join(lines[1:])
    head = lines[0].split(" — ", 1)[0] if lines else ""
    if head and _looks_like_internal_code(head):
        return _user_facing_error_text(reason_code=head)
    return s


def _decision_reason_text(reason_code: str) -> str:
    return _translate_error_token(reason_code)


def _nl_error_reason(reason: str) -> str:
    return _user_facing_error_text(reason_code=reason)


def _nl_payment_warning(warn: object | None) -> str:
    if not warn:
        return ""
    s = str(warn).strip()
    if not s:
        return ""
    return _user_facing_error_text(warnings=s)


def _display_supplier_name(name: object) -> str:
    s = str(name or "").strip()
    if s == "unknown_supplier":
        return tr("matching.fallback.unknown_supplier")
    return s


def _batch_error_message(code: str) -> str:
    detail = tr(code) if UiStrings.has(code) else code
    return tr("dialog.batch_load.failed.message", detail=detail)

_SIGNAL_LABEL_KEYS: dict[str, str] = {
    "iban": "matching.signal.iban",
    "customer_number": "matching.signal.customer_number",
    "invoice_number": "matching.signal.invoice_number",
    "supplier_hint": "matching.signal.supplier_hint",
    "email_domain": "matching.signal.email_domain",
    "kvk": "matching.signal.kvk",
    "vat": "matching.signal.vat",
    "payment_term": "matching.signal.payment_term",
}


def _signal_label(key: str) -> str:
    return tr(_SIGNAL_LABEL_KEYS.get(key, key))

# For customer_number display and decisions: customer_number_result is ALWAYS
# authoritative when present. Scalar customer_number is display-only fallback
# and must be ignored if result exists (including NONE/absent states).
# Read order: result → profile NONE → scalar (only without result dict).


def _customer_number_none_mode_active(inv: dict[str, Any]) -> bool:
    """Klantnummer is niet van toepassing (user pick of profiel ``customer_number_mode=NONE``)."""
    return customer_number_is_absent_or_none(inv)


def _customer_number_none_mode_from_parts(
    *,
    snap: dict[str, Any] | None = None,
    customer_result: dict[str, Any] | None = None,
) -> bool:
    """NONE-detectie op snapshot + optioneel los resultaat (rij/cache)."""
    probe: dict[str, Any] = {}
    if isinstance(snap, dict):
        probe.update(snap)
    if isinstance(customer_result, dict):
        probe["customer_number_result"] = customer_result
    return _customer_number_none_mode_active(probe)


def _matches_completeness_text(inv: dict[str, Any]) -> str:
    missing: list[str] = []
    if not str(inv.get("invoice_number") or "").strip():
        missing.append(tr("matching.display.missing.invoice_number"))
    if not customer_number_is_absent_or_none(inv) and not customer_number_authoritative_value(inv):
        missing.append(tr("matching.display.missing.customer_number"))
    if not str(inv.get("invoice_date") or "").strip():
        missing.append(tr("matching.display.missing.invoice_date"))
    if not str(inv.get("iban") or "").strip():
        missing.append(tr("matching.display.missing.iban"))
    if not missing:
        return tr("matching.display.complete")
    return tr("matching.display.missing_prefix", fields=", ".join(missing))

def _core_matches_text(inv: dict[str, Any]) -> str:
    source = str(inv.get("supplier_match_source") or "").strip()
    core = inv.get("db_core_matches") or []
    core_clean = [str(x).strip() for x in core if str(x).strip()]
    mi = inv.get("match_info") if isinstance(inv.get("match_info"), dict) else {}
    alias_note = tr("matching.display.alias_note") if mi.get("alias_match") else ""

    db_only = inv.get("supplier_db_traits_not_on_invoice") or []
    db_only_clean = [str(x).strip() for x in db_only if str(x).strip()]
    db_note = ""
    if db_only_clean and len(core_clean) < 2:
        db_note = tr("matching.display.db_note", traits=", ".join(db_only_clean))

    if source == "db_match":
        if len(core_clean) >= 2:
            return tr("matching.display.core_2of2", traits=", ".join(core_clean[:2]))
        if core_clean:
            return tr(
                "matching.display.core_1of2",
                trait=core_clean[0],
                db_note=db_note,
                alias_note=alias_note,
            )
        return tr("matching.display.core_0of2", db_note=db_note, alias_note=alias_note)

    signals = inv.get("match_signals") or []
    signal_set = {str(s).strip() for s in signals if str(s).strip()}
    fallback_order = ["iban", "customer_number", "kvk", "vat", "email_domain"]
    provisional = [k for k in fallback_order if k in signal_set][:2]
    provisional_labels = [_signal_label(k) for k in provisional]
    if len(provisional_labels) >= 2:
        return tr("matching.display.provisional_2of2", labels=" + ".join(provisional_labels))
    if provisional_labels:
        return tr("matching.display.provisional_1of2", label=provisional_labels[0])
    return tr("matching.display.provisional_0of2")

def _pdf_basename_from_dict(d: dict[str, Any]) -> str:
    sf = d.get("_source_file") or d.get("source_file")
    return Path(str(sf)).name if sf else ""


def _stable_payment_row_id(*, supplier: str, invoice_number: str, pdf: str) -> str:
    """Stable row key for DecisionStore: factuur+PDF, niet leveranciersnaam (die kan handmatig wijzigen)."""
    inv = str(invoice_number or "").strip()
    pdf_s = str(pdf or "").strip()
    if pdf_s and pdf_s != "—":
        rid = f"{inv}|{pdf_s}".strip("|")
        if rid:
            return rid
    sup = str(supplier or "").strip()
    return f"{sup}|{inv}|{pdf_s}".strip()


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


def _role_snap_from_snapshot(snapshot: dict[str, Any], key: str) -> Any:
    """Return an independent FIELD slice for Qt role storage.

    Dict values (incl. nested lists like ``candidates``) are always deepcopied.
    Primitives (str/int/None) are returned as-is.
    Callers must treat the return value as owned by the Qt role — not shared
    with the FULL diagnostics snapshot or the render-time snapshot.
    """
    v = snapshot.get(key)
    return deepcopy(v) if isinstance(v, dict) else v


def _freeze_immutable_row_snapshot(inv: dict) -> dict[str, Any]:
    """Immutable row boundary — no shared refs to live inv or nested objects."""
    snapshot = deepcopy(build_invoice_diagnostics_snapshot(inv))
    return deepcopy(snapshot)


def _ident_field_display_from_inv(inv: dict[str, Any], field: str) -> str:
    """Celweergave; ``?`` als parser twijfelt en er kandidaten zijn."""
    if field == "customer_number":
        if customer_number_is_absent_or_none(inv):
            return ""
        cr = inv.get("customer_number_result")
        if isinstance(cr, dict):
            val = customer_number_authoritative_value(inv)
            if val:
                return val
            st = str(cr.get("status") or "").lower()
            cands = cr.get("candidates")
            n_cands = len(cands) if isinstance(cands, list) else 0
            if n_cands and st in ("ambiguous", "tentative", "failed"):
                if st == "ambiguous" or n_cands >= 2:
                    return "?"
            return ""
        return str(inv.get("customer_number") or "").strip()
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


def _iban_field_from_inv(inv: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    disp = _ident_field_display_from_inv(inv, "iban")
    ir = inv.get("iban_result")
    return disp, ir if isinstance(ir, dict) else None


def _remittance_display_from_inv(inv: dict[str, Any]) -> str:
    inv_no = _ident_field_display_from_inv(inv, "invoice_number")
    if _customer_number_none_mode_active(inv):
        return format_remittance_text(
            None,
            None if inv_no in ("", "?") else inv_no,
            None,
        )
    cust = _ident_field_display_from_inv(inv, "customer_number")
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


def build_supplier_sync_payload_from_parts(
    *,
    name: str,
    iban_cell: str,
    customer_code_cell: str,
    discount_raw: str,
    term_raw: str,
    iban_result: dict[str, Any] | None,
    customer_result: dict[str, Any] | None,
    row_snap: dict[str, Any] | None,
    email_dom: str = "",
    kvk_no: str = "",
    vat_no: str = "",
    original_name: str = "",
    supplier_exists: bool = False,
) -> dict[str, Any]:
    """Authoritative supplier write payload from row field state (not OCR fallbacks)."""
    name_s = str(name or "").strip()
    none_mode = _customer_number_none_mode_from_parts(
        snap=row_snap if isinstance(row_snap, dict) else None,
        customer_result=customer_result if isinstance(customer_result, dict) else None,
    )
    iban_user_overridden = bool(
        isinstance(iban_result, dict) and iban_result.get("user_overridden")
    )
    iban = clean_iban(str(iban_cell or ""))
    iban_user_cleared = iban_user_overridden and not iban

    customer_code: str | None = None
    if not none_mode:
        code = str(customer_code_cell or "").strip()
        if code and code != "?":
            customer_code = code

    term_days = _parse_term_days_from_text(term_raw)
    try:
        discount = float(str(discount_raw or "").replace(",", ".")) if str(discount_raw or "").strip() else 0.0
    except ValueError:
        discount = 0.0

    return {
        "name": name_s,
        "original_name": str(original_name or "").strip(),
        "iban": iban,
        "iban_user_cleared": iban_user_cleared,
        "iban_user_overridden": iban_user_overridden,
        "customer_code": customer_code,
        "customer_number_mode": CUSTOMER_NUMBER_MODE_NONE if none_mode else None,
        "none_mode": none_mode,
        "existing_supplier": bool(supplier_exists),
        "discount": discount,
        "term_days": term_days,
        "email_domain": str(email_dom or "").strip() or None,
        "kvk_number": str(kvk_no or "").strip() or None,
        "vat_number": str(vat_no or "").strip() or None,
    }


def patch_authoritative_row_fields_into_invoice(
    inv: dict[str, Any],
    *,
    name: str,
    payload: dict[str, Any],
    iban_result: dict[str, Any] | None,
    customer_result: dict[str, Any] | None,
    field_results: dict[str, dict[str, Any] | None],
    user_overridden_fields: frozenset[str],
) -> None:
    """Inject user-locked field snapshots into parse-cache invoice before rematch.

    Invariant: ``parser.field_authority`` — user_overridden fields are not re-resolved.
    """
    from parser.pdf_parser import build_absent_customer_number_snapshot

    name_s = str(name or "").strip()
    if name_s:
        inv["supplier_hint"] = name_s
        inv["supplier_name"] = name_s

    iban = str(payload.get("iban") or "").strip()
    none_mode = bool(payload.get("none_mode"))
    customer_code = payload.get("customer_code")

    if "iban" in user_overridden_fields and isinstance(iban_result, dict):
        apply_resolved_field_result(inv, "iban", iban_result)
    elif iban:
        inv["iban"] = clean_iban(iban)
    elif payload.get("iban_user_cleared"):
        cleared = dict(iban_result) if isinstance(iban_result, dict) else {}
        cleared.setdefault("value", "")
        cleared.setdefault("selected_value", "")
        cleared["user_overridden"] = True
        cleared["user_selected"] = True
        cleared["status"] = "confirmed"
        cleared["confidence"] = 100
        apply_resolved_field_result(inv, "iban", cleared)

    if none_mode:
        if isinstance(customer_result, dict) and customer_result.get("user_overridden"):
            apply_resolved_field_result(inv, "customer_number", customer_result)
        else:
            absent = build_absent_customer_number_snapshot()
            if isinstance(customer_result, dict):
                for key in ("user_selected", "user_overridden", "source", "override_reason"):
                    if key in customer_result:
                        absent[key] = customer_result[key]
            apply_resolved_field_result(inv, "customer_number", absent)
    elif customer_code:
        inv["customer_number"] = str(customer_code)
        if "customer_number" in user_overridden_fields and isinstance(customer_result, dict):
            apply_resolved_field_result(inv, "customer_number", customer_result)

    if str(payload.get("vat_number") or "").strip():
        inv["vat_number"] = str(payload.get("vat_number") or "").strip()
    if str(payload.get("kvk_number") or "").strip():
        inv["kvk_number"] = str(payload.get("kvk_number") or "").strip()
    if str(payload.get("email_domain") or "").strip():
        inv["email_domain"] = str(payload.get("email_domain") or "").strip()

    for field_id in REVIEW_FIELD_IDS:
        if field_id in ("customer_number", "iban"):
            continue
        if field_id not in user_overridden_fields:
            continue
        snap_fr = field_results.get(field_id)
        if isinstance(snap_fr, dict):
            apply_resolved_field_result(inv, field_id, snap_fr)

    # User confirmed supplier master data via «Voeg toe / update»; payment term from DB
    # may be applied even when automatic matching stays needs_review (e.g. IBAN-only).
    inv["supplier_sync_confirmed"] = True


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
    SETTLEMENT = 15

_ROW_SETTLEMENT_GROUP_ID_ROLE = Qt.ItemDataRole.UserRole + 20
_ROW_SETTLEMENT_STATUS_ROLE = Qt.ItemDataRole.UserRole + 21
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
_ROW_IBAN_RESULT_ROLE = Qt.ItemDataRole.UserRole + 20

_TABLE_SNAPSHOT_ROLES: tuple[Qt.ItemDataRole, ...] = (
    Qt.ItemDataRole.UserRole,
    _ROW_WARNING_RAW_ROLE,
    _ROW_INVOICE_DATE_SOURCE_ROLE,
    _ROW_DATE_MODE_ROLE,
    _ROW_EFFECTIVE_TERM_ROLE,
    _ROW_TERM_TRUSTED_ROLE,
    _ROW_EMAIL_DOMAIN_ROLE,
    _ROW_KVK_NUMBER_ROLE,
    _ROW_VAT_NUMBER_ROLE,
    _ROW_BASE_INCL_ROLE,
    _ROW_BASE_EXCL_ROLE,
    _ROW_DECISION_TRACE_ROLE,
    _ROW_AMOUNT_RESULT_ROLE,
    _ROW_DECISION_ROLE,
    _ROW_ROW_ID_ROLE,
    _ROW_RENDER_HASH_ROLE,
    _ROW_SUPPLIER_ORIGINAL_ROLE,
    _ROW_INVOICE_DIAGNOSTICS_ROLE,
    _ROW_INVOICE_NUMBER_RESULT_ROLE,
    _ROW_CUSTOMER_NUMBER_RESULT_ROLE,
    _ROW_IBAN_RESULT_ROLE,
    _ROW_SETTLEMENT_GROUP_ID_ROLE,
    _ROW_SETTLEMENT_STATUS_ROLE,
    _ROW_SETTLEMENT_DOC_ID_ROLE,
    _ROW_SETTLEMENT_DOC_TYPE_ROLE,
    _ROW_SETTLEMENT_ROW_KIND_ROLE,
    _ROW_SETTLEMENT_SOURCE_PDF_ROLE,
    _ROW_SETTLEMENT_SUPPLIER_ROLE,
    _ROW_INVOICE_META_ROLE,
)

_UNDO_STACK_LIMIT = 5

_READ_ONLY_FLAGS = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


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
_UW_GEGEVENS_XML_HINT_KEY = "dialog.settings.xml_hint"

# (key, label, placeholder, inputMask of None). Alleen deze lijst uitbreiden voor nieuwe debtor-velden.
DEBTOR_FORM_FIELDS: tuple[tuple[str, str, str, str | None], ...] = (
    ("name", "dialog.settings.debtor_name_label", "dialog.settings.debtor_name_placeholder", None),
    ("iban", "dialog.settings.debtor_iban_label", "dialog.settings.debtor_iban_placeholder", None),
    ("bic", "dialog.settings.debtor_bic_label", "dialog.settings.debtor_bic_placeholder", ">XXXXXXXXxxx;_"),
    ("kvk", "dialog.settings.debtor_kvk_label", "dialog.settings.debtor_kvk_placeholder", None),
    ("vat", "dialog.settings.debtor_vat_label", "dialog.settings.debtor_vat_placeholder", None),
)

_DEBTOR_KVK_VAT_TOOLTIP_KEY = "dialog.settings.kvk_vat_tooltip"

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
        return format_internal_vat_numbers_for_display(
            normalize_internal_vat_numbers_for_storage(value)
        )
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


def _format_settlement_child_amount(amount: object) -> str:
    """NL-formatted amount for settlement child rows (engine may use '.' decimals)."""
    raw = str(amount or "").strip()
    if not raw:
        return ""
    try:
        return _format_amount_nl(amount_to_decimal(raw.replace(",", ".")))
    except ValueError:
        return raw


def _error_row_amount_str(inv: dict[str, Any]) -> str:
    amt = inv.get("amount")
    if amt is None:
        return ""
    try:
        dec = amount_to_decimal(amt)
    except ValueError:
        return "?"
    if str(inv.get("type") or "") == "credit_note" and dec > Decimal("0"):
        dec = -dec
    return _format_amount_nl(dec)

def _term_status_label(trusted: bool | None, effective_days: int) -> str:
    if trusted is True:
        return tr("matching.display.term_applied", days=effective_days)
    if trusted is False:
        return tr("matching.display.term_not_applied")
    return tr("matching.display.term_unknown")

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
        self.setWindowTitle(tr("dialog.settings.title"))
        self.setMinimumWidth(560)
        self.resize(560, 520)
        root = QVBoxLayout(self)
        info = QLabel(tr("dialog.settings.intro"))
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
        bbox.addButton(tr("dialog.settings.save"), QDialogButtonBox.ButtonRole.AcceptRole)
        bbox.addButton(tr("dialog.settings.cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        bbox.accepted.connect(self._on_save)
        bbox.rejected.connect(self.reject)
        root.addWidget(bbox)

    def _build_form(self, form: QFormLayout) -> None:
        mw = self.parent()
        for key, label_key, placeholder_key, mask in DEBTOR_FORM_FIELDS:
            edit = QLineEdit()
            edit.setPlaceholderText(tr(placeholder_key))
            edit.setMinimumWidth(300)
            if mask:
                edit.setInputMask(mask)
            elif key == "iban":
                edit.setMaxLength(42)
            elif key == "bic":
                edit.setMaxLength(11)
            elif key == "kvk":
                edit.setMaxLength(12)
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
                    edit.setText(mw.get_debtor_vat_display())
            if key in ("kvk", "vat"):
                edit.setToolTip(tr(_DEBTOR_KVK_VAT_TOOLTIP_KEY))
            else:
                edit.setToolTip(tr(_UW_GEGEVENS_XML_HINT_KEY))
            self._field_edits[key] = edit
            form.addRow(QLabel(tr(label_key)), edit)

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
        self._user_data_dir_edit.setToolTip(tr("dialog.settings.data_dir_tooltip"))
        self._user_data_dir_edit.setStyleSheet("background-color: palette(window);")
        container.addWidget(self._user_data_dir_edit)

        btn = QPushButton(tr("dialog.settings.choose_folder"))
        btn.setFixedWidth(120)
        btn.clicked.connect(self._on_choose_user_data_dir)
        container.addWidget(btn)

        wrapper = QWidget()
        wrapper.setLayout(container)
        form.addRow(QLabel(tr("dialog.settings.data_dir_label")), wrapper)

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
            self, tr("file.pick_data_dir"), start
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
        self._export_dir_edit.setToolTip(tr("dialog.settings.export_dir_tooltip"))
        self._export_dir_edit.setStyleSheet("background-color: palette(window);")
        container.addWidget(self._export_dir_edit)

        btn = QPushButton(tr("dialog.settings.choose_folder"))
        btn.setFixedWidth(120)
        btn.clicked.connect(self._on_choose_export_dir)
        container.addWidget(btn)

        wrapper = QWidget()
        wrapper.setLayout(container)
        form.addRow(QLabel(tr("dialog.settings.export_dir_label")), wrapper)

    def _on_choose_export_dir(self) -> None:
        mw = self.parent()
        if not isinstance(mw, MainWindow):
            return
        start = str(self._selected_export_dir) if self._selected_export_dir else str(mw._resolve_export_dir())
        path: Optional[str] = QFileDialog.getExistingDirectory(self, tr("file.pick_export_dir"), start)
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
                tr("dialog.settings.title"),
                tr("dialog.settings.debtor_save_failed", path=mw._settings_path()),
            )
            return
        if self._selected_export_dir is not None:
            if not mw._persist_export_dir(self._selected_export_dir):
                QMessageBox.warning(
                    self,
                    tr("dialog.settings.title"),
                    tr("dialog.settings.export_dir_save_failed"),
                )
                return
        self.accept()

class MainWindow(QMainWindow):
    """
    Hoofdvenster voor de PDF2SEPA desktop client.

    Biedt mapselectie voor facturen, een bewerkbaar overzicht van betalingen
    en een actie om SEPA XML te genereren.
    """

    APP_VERSION = "1.0.8"

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(tr("window.title"))
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
        self._table: _PaymentsTableWidget
        self._filter_edit: QLineEdit
        self._persist_sort_column: Optional[int] = None
        self._persist_sort_order: Qt.SortOrder = Qt.SortOrder.AscendingOrder
        self._sort_persist_connected: bool = False
        self._undo_stack: list[_RowUndoEntry] = []
        self._pending_undo_snapshot: _RowUndoEntry | None = None
        self._undo_restore_depth: int = 0
        self._undo_batch_depth: int = 0
        self._undo_shortcut_busy: bool = False
        self._diagnostics_action_busy: bool = False
        self._session_date = date.today()
        self._suppress_table_item_changed: bool = False
        self._field_apply_depth: int = 0
        self._is_loading_batch: bool = False
        self._resolver_active: bool = False
        self._decision_store_inner = DecisionStore()
        self._decision_store = GuardedDecisionStore(self._decision_store_inner, is_allowed=lambda: self._resolver_active)
        self._pinned_run_id: str | None = None
        self._active_run_id: str | None = None
        self._approval_store = UserApprovalStore(self._user_data_dir / "user_approvals.json")
        self._override_store = CreditOverrideStore(self._user_data_dir / "credit_overrides.json")
        self._document_type_override_store = DocumentTypeOverrideStore()
        self._session_amount_overrides: dict[str, AmountOverride] = {}
        self._engine_cache = SettlementEngineCache()
        self._matched_invoices: list[dict] = []
        self._expanded_settlement_groups: set[str] = set()
        self._pending_engine_row_ids: set[str] = set()
        self._pending_engine_idempotency: set[str] = set()
        self._rows_requiring_reapproval: set[str] = set()
        self._rows_requiring_reapproval_rows: set[int] = set()
        self._parsed_batch_cache = ParsedInvoiceBatchCache()
        self._engine_result: EngineResult | None = None
        self._decision_table_fingerprint: str | None = None
        self._engine_rerun_timer = QTimer(self)
        self._engine_rerun_timer.setSingleShot(True)
        self._engine_rerun_timer.timeout.connect(self._commit_pending_engine_updates)
        self._batch_load_thread: QThread | None = None
        self._batch_load_worker: InvoiceBatchLoadWorker | None = None
        self._loading_overlay: LoadingOverlay | None = None
        self._btn_folder: QPushButton | None = None
        self._btn_reread: QPushButton | None = None
        self._batch_progress_file = ""
        self._batch_progress_stage = ""
        self._batch_progress_done = 0
        self._batch_progress_total = 0
        self._restore_selected_folder_from_settings()
        self._setup_ui()
        overlay_parent = self.centralWidget() or self
        self._loading_overlay = LoadingOverlay(overlay_parent)
        self._setup_shortcuts()
        self._restore_window_geometry()

    @contextmanager
    def _guard_reentrant_field_apply(self):
        self._field_apply_depth += 1
        prev_suppress = self._suppress_table_item_changed
        self._suppress_table_item_changed = True
        try:
            yield
        finally:
            self._suppress_table_item_changed = prev_suppress
            self._field_apply_depth = max(0, self._field_apply_depth - 1)

    @contextmanager
    def _undo_batch(self, *, source: str = "batch"):
        """Suppress per-cell undo capture during multi-step UI updates."""
        _ = source
        self._undo_batch_depth += 1
        try:
            yield
        finally:
            self._undo_batch_depth = max(0, self._undo_batch_depth - 1)

    def _supplier_db_path(self) -> str:
        return str(self._user_data_dir / "suppliers.json")

    def _batch_key(self) -> str:
        return stable_hash(
            {
                "folder": str(self._selected_folder.resolve()) if self._selected_folder else "",
                "suppliers_path": self._supplier_db_path(),
            }
        )

    def _session_amount_override_session(self) -> AmountOverrideSession | None:
        if not self._session_amount_overrides:
            return None
        return AmountOverrideSession(
            batch_key=self._batch_key(),
            overrides=tuple(self._session_amount_overrides.values()),
            history=(),
        )

    def _upsert_session_amount_override(self, override: AmountOverride) -> None:
        self._session_amount_overrides[override.document_id] = override

    def _remove_session_amount_override(self, document_id: str) -> None:
        self._session_amount_overrides.pop(document_id, None)

    def _clear_session_amount_overrides(self) -> None:
        self._session_amount_overrides.clear()

    def _session_document_type_override_session(self) -> DocumentTypeOverrideSession | None:
        batch_key = self._batch_key()
        all_doc_ids, _ = self._document_ids_in_matched(self._matched_invoices)
        if not all_doc_ids:
            return self._document_type_override_store.load_session(batch_key)
        return self._document_type_override_store.load_applicable_session(batch_key, all_doc_ids)

    def _rematch_with_document_type_overrides(self) -> list[dict] | None:
        warm = self._load_parsed_invoices_warm()
        if warm is None:
            return None
        parsed = deepcopy(warm)
        db = SupplierDB(path=self._supplier_db_path())
        matched = match_suppliers(parsed, db)
        return apply_match_postprocess(
            matched,
            document_type_override_session=self._session_document_type_override_session(),
            strip_raw_text=True,
        )

    def _document_ids_in_matched(self, matched: list[dict]) -> tuple[set[str], set[str]]:
        all_ids: set[str] = set()
        credit_ids: set[str] = set()
        for inv in matched:
            did = document_id({"raw": inv})
            all_ids.add(did)
            if str(inv.get("type") or "") == "credit_note":
                credit_ids.add(did)
        return all_ids, credit_ids

    def _init_expand_state_for_engine(self, engine_result: EngineResult) -> None:
        """Cold-start: keep settlement groups collapsed; user expands manually."""
        _ = engine_result

    def _count_payment_header_rows(self) -> int:
        return sum(
            1
            for r in range(self._table.rowCount())
            if not self._is_row_blank(r) and not self._is_settlement_child_row(r)
        )

    def _compute_engine_result(self, matched: list[dict]) -> EngineResult:
        batch_key = self._batch_key()
        all_doc_ids, credit_doc_ids = self._document_ids_in_matched(matched)
        override_session = self._override_store.load_applicable_session(batch_key, credit_doc_ids)
        amount_session = self._session_amount_override_session()
        # Apply amount overrides to a copy — original _matched_invoices is never mutated.
        effective_matched = apply_amount_overrides(matched, amount_session)
        return self._engine_cache.get_or_compute(
            effective_matched,
            override_session,
            lambda: calculate_payments_with_overrides(
                effective_matched,
                override_session=override_session,
                session_date=self._session_date,
            ),
            amount_override_fingerprint=amount_override_session_fingerprint(amount_session),
            document_type_override_fingerprint=document_type_override_session_fingerprint(
                self._session_document_type_override_session()
            ),
        )

    def _populate_from_engine_result(
        self,
        engine_result: EngineResult,
        matched: list[dict],
    ) -> int:
        """Route table populate: legacy per-invoice vs settlement groups."""
        if engine_result.legacy_payments is not None:
            errors = review_documents_as_error_buckets(engine_result.review_documents)
            n_err = self._populate_table_from_load(
                engine_result.legacy_payments,
                errors,
                matched,
                engine_result=None,
            )
            pipeline = "legacy"
        else:
            n_err = self._populate_table_from_settlement_groups(engine_result, matched)
            pipeline = "settlement"
        payment_rows = self._table.rowCount() - n_err
        log_batch_summary(
            input_invoices=len(matched),
            settlement_groups=len(engine_result.settlement_groups),
            review_documents=len(engine_result.review_documents),
            ui_rows=self._table.rowCount(),
            pipeline=pipeline,
            extra=f"payment_rows≈{payment_rows}",
        )
        if shadow_mode_enabled() and matched:
            run_shadow_validation(matched, engine_result)
        return n_err

    def _cancel_pending_engine_updates(self) -> None:
        """Stop geplande engine-reruns (voorkomt inconsistentie na undo)."""
        self._engine_rerun_timer.stop()
        self._pending_engine_row_ids.clear()
        self._pending_engine_idempotency.clear()

    def _clear_undo_stack(self) -> None:
        self._undo_stack.clear()

    def _rerun_settlement_engine(
        self,
        *,
        focus_doc_ids: set[str] | None = None,
        clear_undo: bool = False,
    ) -> None:
        if not self._matched_invoices:
            return
        self._cancel_pending_engine_updates()
        if clear_undo:
            self._clear_undo_stack()
        prior_expanded = set(self._expanded_settlement_groups)
        self._engine_cache.invalidate("override_changed")
        self._engine_result = self._compute_engine_result(self._matched_invoices)
        self._reconcile_expanded_groups(prior_expanded, focus_doc_ids=focus_doc_ids)
        n_err = self._populate_from_engine_result(self._engine_result, self._matched_invoices)
        self._refresh_export_batch_status_label()
        label_key = "status.settlement_label" if self._engine_result.uses_settlement else "status.payments_label"
        self._set_status(tr("status.settlement_updated", label=tr(label_key), count=n_err))

    def _reconcile_expanded_groups(
        self,
        prior_expanded: set[str],
        *,
        focus_doc_ids: set[str] | None = None,
    ) -> None:
        """Keep valid expand state across engine reruns; auto-expand groups for focus docs."""
        if self._engine_result is None:
            return
        new_gids = {
            str(g.get("group_id") or "")
            for g in self._engine_result.settlement_groups
            if str(g.get("group_id") or "").strip()
            and settlement_group_is_expandable(vm_from_group(g), group=g)
        }
        self._expanded_settlement_groups = prior_expanded & new_gids
        if not focus_doc_ids:
            return
        for group in self._engine_result.settlement_groups:
            gid = str(group.get("group_id") or "").strip()
            if not gid or not settlement_group_is_expandable(vm_from_group(group), group=group):
                continue
            matched = False
            for doc in group.get("member_documents") or []:
                if not isinstance(doc, dict):
                    continue
                did = str(doc.get("document_id") or "").strip()
                raw = doc.get("raw") if isinstance(doc.get("raw"), dict) else {}
                if did in focus_doc_ids or document_id({"raw": raw}) in focus_doc_ids:
                    matched = True
                    break
            if not matched:
                for alloc in group.get("credit_allocation") or []:
                    if not isinstance(alloc, dict):
                        continue
                    cid = str(alloc.get("credit_id") or "").strip()
                    if cid in focus_doc_ids:
                        matched = True
                        break
            if matched:
                self._expanded_settlement_groups.add(gid)

    def _invoice_for_document_id(self, doc_id: str) -> dict[str, Any] | None:
        needle = str(doc_id or "").strip()
        if not needle:
            return None
        needle_base = Path(needle).name
        for inv in self._matched_invoices:
            if document_id({"raw": inv}) == needle:
                return inv
            src = str(inv.get("source_file") or "").strip()
            if src and (src == needle or Path(src).name == needle_base):
                return inv
            if str(inv.get("invoice_number") or "").strip() == needle:
                return inv
            if str(inv.get("source_file") or "").strip() == needle:
                return inv
        return None

    def _document_id_for_table_row(self, row: int) -> str:
        it = self._table.item(row, PaymentColumn.SUPPLIER)
        if it is None:
            return ""
        stored = str(it.data(_ROW_SETTLEMENT_DOC_ID_ROLE) or "").strip()
        if stored:
            return stored
        inv_no = str(it.data(_ROW_INVOICE_META_ROLE) or "").strip()
        if inv_no:
            for inv in self._matched_invoices:
                if str(inv.get("invoice_number") or "").strip() == inv_no:
                    return document_id({"raw": inv})
        pdf = self._cell_text(row, PaymentColumn.PDF).strip()
        if pdf and pdf != "—":
            for inv in self._matched_invoices:
                src = str(inv.get("source_file") or "")
                if src and Path(src).name == Path(pdf).name:
                    return document_id({"raw": inv})
        return ""

    def _credit_note_for_row(self, row: int) -> dict[str, Any] | None:
        """Resolve credit note dict for child rows and detached/review credit rows."""
        it = self._table.item(row, PaymentColumn.SUPPLIER)
        if it is None:
            return None
        stored_doc_id = str(it.data(_ROW_SETTLEMENT_DOC_ID_ROLE) or "").strip()
        if stored_doc_id:
            found = self._invoice_for_document_id(stored_doc_id)
            if found is not None and str(found.get("type") or "") == "credit_note":
                return found
        kind = settlement_row_kind(it)
        if kind == SettlementRowKind.CREDIT_CHILD:
            return self._credit_for_row(row)
        doc_id = self._document_id_for_table_row(row)
        if doc_id:
            found = self._invoice_for_document_id(doc_id)
            if found is not None and str(found.get("type") or "") == "credit_note":
                return found
        inv_no = str(it.data(_ROW_INVOICE_META_ROLE) or "").strip()
        if inv_no:
            supplier = self._cell_text(row, PaymentColumn.SUPPLIER).strip().lower()
            for inv in self._matched_invoices:
                if str(inv.get("type") or "") != "credit_note":
                    continue
                if str(inv.get("invoice_number") or "").strip() != inv_no:
                    continue
                if supplier and supplier not in str(inv.get("supplier_name") or "").lower():
                    continue
                return inv
        return None

    def _invoices_for_supplier(self, supplier_name: str) -> list[dict]:
        key = str(supplier_name or "").strip().lower()
        return [
            inv
            for inv in self._matched_invoices
            if str(inv.get("supplier_name") or "").strip().lower() == key
            and str(inv.get("type") or "invoice") != "credit_note"
        ]

    def _credit_for_row(self, row: int) -> dict[str, Any] | None:
        it = self._table.item(row, PaymentColumn.SUPPLIER)
        kind = settlement_row_kind(it)
        if kind != SettlementRowKind.CREDIT_CHILD:
            return None
        stored_doc_id = str(it.data(_ROW_SETTLEMENT_DOC_ID_ROLE) or "").strip() if it else ""
        if stored_doc_id:
            found = self._invoice_for_document_id(stored_doc_id)
            if found is not None and str(found.get("type") or "") == "credit_note":
                return found
        return None

    def _on_detach_credit_override(self, credit: dict[str, Any]) -> None:
        cid = document_id({"raw": credit})
        self._override_store.upsert_override(
            self._batch_key(),
            make_detach_override(cid),
            history_event={"event": "user_detached", "credit_document_id": cid},
        )
        self._rerun_settlement_engine(focus_doc_ids={cid})

    def _on_reassign_credit_override(self, credit: dict[str, Any]) -> None:
        supplier = str(credit.get("supplier_name") or "")
        dlg = CreditOverrideDialog(
            self,
            credit=credit,
            available_invoices=self._invoices_for_supplier(supplier),
            title="Credit opnieuw koppelen",
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        allocations = dlg.allocations()
        if not allocations:
            return
        self._override_store.upsert_override(
            self._batch_key(),
            make_reassign_override(dlg.credit_document_id, allocations),
            history_event={
                "event": "user_reassigned",
                "credit_document_id": dlg.credit_document_id,
                "invoices": [a.invoice_number for a in allocations],
            },
        )
        self._rerun_settlement_engine()

    def _on_reset_credit_override(self, credit: dict[str, Any]) -> None:
        cid = document_id({"raw": credit})
        self._override_store.clear_credit(self._batch_key(), cid)
        self._rerun_settlement_engine()

    def _on_adjust_amount_override(self, row: int) -> None:
        """Open a dialog for the user to enter a new gross amount for this document.

        Works on both INVOICE_CHILD and CREDIT_CHILD rows via document_id lookup.
        """
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        sup_it = self._table.item(row, PaymentColumn.SUPPLIER)
        if not sup_it:
            return
        doc_id = self._document_id_for_table_row(row)
        if not doc_id:
            QMessageBox.warning(self, tr("dialog.adjust_amount.title"), tr("dialog.adjust_amount.no_doc_id"))
            return

        matched_inv = self._invoice_for_document_id(doc_id)
        if matched_inv is None:
            QMessageBox.warning(self, tr("dialog.adjust_amount.title"), tr("dialog.adjust_amount.doc_not_found", doc_id=doc_id))
            return
        doc_id = document_id({"raw": matched_inv})

        # Determine current amount (check for existing session amount override first)
        existing = self._session_amount_overrides.get(doc_id)
        if existing is not None:
            current = float(existing.new_amount.copy_abs())
        else:
            raw_amt = matched_inv.get("amount_dec")
            if raw_amt is None:
                raw_amt = matched_inv.get("amount")
            current = float(Decimal(str(raw_amt or 0)).copy_abs())

        inv_no = str(matched_inv.get("invoice_number") or doc_id)
        is_credit = str(matched_inv.get("type") or "") == "credit_note"
        new_val, ok = QInputDialog.getDouble(
            self,
            tr("dialog.adjust_amount.title"),
            tr("dialog.adjust_amount.prompt", invoice_number=inv_no),
            value=current,
            min=0.0,
            max=999_999_999.99,
            decimals=2,
        )
        if not ok:
            return

        old_dec = Decimal(str(matched_inv.get("amount_dec") or matched_inv.get("amount") or 0)).copy_abs()
        old_dec = old_dec.quantize(Decimal("0.01"))
        new_dec = Decimal(str(new_val)).copy_abs().quantize(Decimal("0.01"))
        if new_dec == old_dec:
            if existing is not None:
                self._remove_session_amount_override(doc_id)
            self._rerun_settlement_engine()
            return
        if existing is not None and new_dec == existing.new_amount.copy_abs().quantize(Decimal("0.01")):
            return
        override = AmountOverride(
            document_id=doc_id,
            old_amount=old_dec,
            new_amount=new_dec,
            reason="user_adjusted",
            created_at=now_utc_iso(),
        )
        self._upsert_session_amount_override(override)
        self._rerun_settlement_engine()

    def _on_link_credit_to_invoice_row(self, invoice_row: int) -> None:
        """Open reassign dialog for a credit from this group, triggered via an invoice child row.

        If only one credit exists in the group, open the dialog directly.
        If multiple credits exist, show a selection menu so the user picks the
        right credit rather than always defaulting to the first one.
        """
        group = self._settlement_group_for_row(invoice_row)
        if group is None:
            return
        vm = vm_from_group(group)
        credit_lines = [c for c in vm.credits if c.invoice_number and c.invoice_number not in ("Credits", "")]
        if not credit_lines:
            return

        def _find_credit(inv_no: str) -> dict[str, Any] | None:
            return next(
                (
                    inv
                    for inv in self._matched_invoices
                    if str(inv.get("type") or "") == "credit_note"
                    and str(inv.get("invoice_number") or "") == inv_no
                ),
                None,
            )

        if len(credit_lines) == 1:
            credit = _find_credit(credit_lines[0].invoice_number)
            if credit is not None:
                self._on_reassign_credit_override(credit)
            return

        # Multiple credits — let user pick which one to reassign.
        from PySide6.QtWidgets import QMenu
        pick_menu = QMenu(tr("menu.context.choose_credit"), self)
        for cl in credit_lines:
            amt = cl.amount_applied or cl.gross_amount
            label = f"{cl.invoice_number}  ({amt})" if amt else cl.invoice_number
            action = pick_menu.addAction(label)
            action.setData(cl.invoice_number)
        chosen = pick_menu.exec(self._table.viewport().mapToGlobal(
            self._table.visualRect(self._table.model().index(invoice_row, int(PaymentColumn.SUPPLIER))).center()
        ))
        if chosen is None:
            return
        credit = _find_credit(str(chosen.data() or ""))
        if credit is not None:
            self._on_reassign_credit_override(credit)

    def _append_settlement_breakdown_rows(self, group: dict[str, Any]) -> None:
        gid = str(group.get("group_id") or "")
        if gid not in self._expanded_settlement_groups:
            return
        vm = vm_from_group(group)
        for spec in breakdown_child_rows(vm, expanded=True, group=group):
            r = self._table.rowCount()
            self._table.insertRow(r)
            self._apply_settlement_child_row_full(r, spec, gid)

    def _attach_child_row_diagnostics_roles(
        self,
        *,
        snapshot: dict[str, Any],
        doc_id: str,
        sup_it: QTableWidgetItem,
        iban_it: QTableWidgetItem,
        amt_it: QTableWidgetItem,
        cust_it: QTableWidgetItem,
        inv_date_it: QTableWidgetItem,
    ) -> None:
        """Mirror header row diagnostics roles; copy-by-value into Qt roles."""
        diag_snapshot = deepcopy(snapshot)
        sup_it.setData(_ROW_INVOICE_DIAGNOSTICS_ROLE, diag_snapshot)
        inv_meta = str(snapshot.get("invoice_number") or "").strip()
        if inv_meta:
            sup_it.setData(_ROW_INVOICE_META_ROLE, inv_meta)
        inv_snap = _role_snap_from_snapshot(snapshot, "invoice_number_result")
        if inv_snap:
            sup_it.setData(_ROW_INVOICE_NUMBER_RESULT_ROLE, inv_snap)
        sup_original = str(
            snapshot.get("supplier_name") or snapshot.get("supplier_hint") or ""
        ).strip()
        if sup_original:
            sup_it.setData(_ROW_SUPPLIER_ORIGINAL_ROLE, sup_original)
        vat = str(snapshot.get("vat_number") or "").strip()
        if vat:
            sup_it.setData(_ROW_VAT_NUMBER_ROLE, vat)
        kvk = str(snapshot.get("kvk_number") or "").strip()
        if kvk:
            sup_it.setData(_ROW_KVK_NUMBER_ROLE, kvk)
        email = str(snapshot.get("email_domain") or "").strip()
        if email:
            sup_it.setData(_ROW_EMAIL_DOMAIN_ROLE, email)
        if doc_id:
            sup_it.setData(_ROW_SETTLEMENT_DOC_ID_ROLE, doc_id)
        src = str(snapshot.get("source_file") or "").strip()
        if src:
            sup_it.setData(_ROW_SETTLEMENT_SOURCE_PDF_ROLE, src)
        iban_snap = _role_snap_from_snapshot(snapshot, "iban_result")
        if iban_snap:
            iban_it.setData(_ROW_IBAN_RESULT_ROLE, iban_snap)
        amt_snap = _role_snap_from_snapshot(snapshot, "amount_result")
        if amt_snap:
            amt_it.setData(_ROW_AMOUNT_RESULT_ROLE, amt_snap)
        cust_snap = _role_snap_from_snapshot(snapshot, "customer_number_result")
        if cust_snap:
            cust_it.setData(_ROW_CUSTOMER_NUMBER_RESULT_ROLE, cust_snap)
        inv_date_src = snapshot.get("invoice_date_source")
        if inv_date_src is not None:
            inv_date_it.setData(_ROW_INVOICE_DATE_SOURCE_ROLE, inv_date_src)

    def _apply_settlement_child_row_full(self, row: int, spec: dict[str, Any], gid: str) -> None:
        """Render a settlement child row with all payment columns populated (read-only).

        Invoice fields come from a frozen diagnostics snapshot (single source).
        ``spec`` supplies layout/settlement metadata only (kind, styling, amount celtekst).
        WARNING_CHILD rows render only the supplier column.
        """
        from PySide6.QtGui import QBrush, QColor

        kind = spec["kind"]
        is_credit = kind == SettlementRowKind.CREDIT_CHILD
        orange = QBrush(QColor("#b54708"))
        settlement_col = int(PaymentColumn.SETTLEMENT)

        def _ro(text: str) -> QTableWidgetItem:
            return self._item_readonly(str(text or ""))

        # ── Settlement sentinel (group_id linkage, required for every child) ──
        sett_it = _ro("")
        sett_it.setData(_ROW_SETTLEMENT_ROW_KIND_ROLE, int(kind))
        sett_it.setData(_ROW_SETTLEMENT_GROUP_ID_ROLE, gid)
        self._table.setItem(row, settlement_col, sett_it)

        if kind == SettlementRowKind.WARNING_CHILD:
            sup_it = _ro(f"  \u26a0 {spec.get('label', '')}")
            sup_it.setData(_ROW_SETTLEMENT_ROW_KIND_ROLE, int(kind))
            sup_it.setData(_ROW_SETTLEMENT_SUPPLIER_ROLE, str(spec.get("supplier_name") or ""))
            sup_it.setForeground(orange)
            self._table.setItem(row, int(PaymentColumn.SUPPLIER), sup_it)
            return

        doc_id = str(spec.get("document_id") or "").strip()
        inv = self._invoice_for_document_id(doc_id) if doc_id else None
        snapshot: dict[str, Any] | None = None
        if isinstance(inv, dict):
            snapshot = _freeze_immutable_row_snapshot(inv)
            del inv

        doc_type = "credit_note" if is_credit else "invoice"
        meta = spec.get("meta") or {}
        detached = bool(meta.get("detached"))

        # ── Supplier (snapshot only for invoice fields) ───────────────────────
        sup_text = ""
        if snapshot:
            sup_text = _display_supplier_name(
                snapshot.get("supplier_name") or snapshot.get("supplier_hint") or ""
            )
        if is_credit and detached and snapshot:
            inv_no = str(snapshot.get("invoice_number") or "").strip()
            if inv_no:
                sup_text = f"{sup_text}  ·  {inv_no} {tr('matching.display.detached')}"
        sup_it = _ro(sup_text)
        sup_it.setData(_ROW_SETTLEMENT_ROW_KIND_ROLE, int(kind))
        sup_it.setData(_ROW_SETTLEMENT_DOC_TYPE_ROLE, doc_type)
        sup_it.setData(_ROW_SETTLEMENT_SUPPLIER_ROLE, sup_text)
        sup_it.setData(_ROW_SETTLEMENT_GROUP_ID_ROLE, gid)
        if is_credit:
            sup_it.setForeground(orange)
        self._table.setItem(row, int(PaymentColumn.SUPPLIER), sup_it)

        # ── IBAN ──────────────────────────────────────────────────────────────
        iban_disp = str(snapshot.get("iban") or "") if snapshot else ""
        iban_it = _ro(iban_disp)
        self._table.setItem(row, int(PaymentColumn.IBAN), iban_it)

        # ── Amount: settlement presentation celtekst; truth in snapshot role ────
        amount_str = _format_settlement_child_amount(spec.get("amount"))
        amt_it = self._item_amount(amount_str)
        amt_it.setData(_ROW_SETTLEMENT_ROW_KIND_ROLE, int(kind))
        if is_credit:
            amt_it.setForeground(orange)
        self._table.setItem(row, int(PaymentColumn.AMOUNT), amt_it)

        # ── Customer code ─────────────────────────────────────────────────────
        cust = _ident_field_display_from_inv(snapshot, "customer_number") if snapshot else ""
        cust_it = _ro(cust)
        self._table.setItem(row, int(PaymentColumn.CUSTOMER_CODE), cust_it)

        # ── Description ───────────────────────────────────────────────────────
        desc = _remittance_display_from_inv(snapshot) if snapshot else ""
        self._table.setItem(row, int(PaymentColumn.DESCRIPTION), _ro(desc))

        # ── PDF ───────────────────────────────────────────────────────────────
        src = str(snapshot.get("source_file") or "") if snapshot else ""
        pdf_disp = Path(src).name if src else "—"
        self._table.setItem(row, int(PaymentColumn.PDF), _ro(pdf_disp))

        # ── Discount ──────────────────────────────────────────────────────────
        disc = _discount_str_from_inv(snapshot) if snapshot else "0"
        self._table.setItem(row, int(PaymentColumn.DISCOUNT), _ro(disc))

        # ── Invoice date ──────────────────────────────────────────────────────
        inv_date = str(snapshot.get("invoice_date") or "") if snapshot else ""
        inv_disp, inv_sort = self._table_date_display_and_sort(inv_date)
        inv_date_it = self._item_date_cell(inv_disp, inv_sort)
        self._table.setItem(row, int(PaymentColumn.INVOICE_DATE), inv_date_it)

        # ── Status (decision not in snapshot fields) ──────────────────────────
        self._table.setItem(row, int(PaymentColumn.STATUS), _ro("—"))

        # ── INFO (diagnostics entry point; mirror header rows) ─────────────────
        from PySide6.QtGui import QPalette

        info_item = self._item_readonly("🔍")
        info_item.setToolTip(tr("status.diagnostics_tooltip"))
        info_item.setForeground(QApplication.palette().color(QPalette.ColorRole.WindowText))
        self._table.setItem(row, int(PaymentColumn.INFO), info_item)

        if snapshot is not None:
            self._attach_child_row_diagnostics_roles(
                snapshot=snapshot,
                doc_id=doc_id,
                sup_it=sup_it,
                iban_it=iban_it,
                amt_it=amt_it,
                cust_it=cust_it,
                inv_date_it=inv_date_it,
            )

    def _is_settlement_child_row(self, row: int) -> bool:
        kind = settlement_row_kind(self._table.item(row, PaymentColumn.SUPPLIER))
        return kind in (
            SettlementRowKind.INVOICE_CHILD,
            SettlementRowKind.CREDIT_CHILD,
            SettlementRowKind.WARNING_CHILD,
        )

    def _settlement_review_hidden_doc_ids(self, engine_result: EngineResult) -> set[str]:
        """Credits already shown inside a settlement group must not duplicate as review rows."""
        hidden: set[str] = set()
        for g in engine_result.settlement_groups:
            if _is_credit_only_group(g):
                continue
            for doc in g.get("member_documents") or []:
                if not isinstance(doc, dict):
                    continue
                raw = doc.get("raw") if isinstance(doc.get("raw"), dict) else {}
                if str(raw.get("type") or "") != "credit_note":
                    continue
                did = str(doc.get("document_id") or "").strip()
                if did:
                    hidden.add(did)
        return hidden

    def _apply_child_row_amount_override(self, row: int, item: QTableWidgetItem) -> None:
        """Persist gross amount override for a settlement child document (inline edit)."""
        doc_id = self._document_id_for_table_row(row)
        matched_inv = self._invoice_for_document_id(doc_id) if doc_id else None
        if matched_inv is None:
            self._reject_invalid_amount_cell_edit(item, row)
            return
        doc_id = document_id({"raw": matched_inv})
        try:
            new_dec = _parse_amount_str(item.text()).copy_abs().quantize(Decimal("0.01"))
        except ValueError:
            self._reject_invalid_amount_cell_edit(item, row)
            return
        if new_dec <= Decimal("0.00"):
            self._reject_invalid_amount_cell_edit(item, row)
            return
        old_dec = Decimal(str(matched_inv.get("amount_dec") or matched_inv.get("amount") or 0)).copy_abs()
        old_dec = old_dec.quantize(Decimal("0.01"))
        existing = self._session_amount_overrides.get(doc_id)
        if new_dec == old_dec:
            if existing is not None:
                self._remove_session_amount_override(doc_id)
            self._rerun_settlement_engine()
            return
        if existing is not None and new_dec == existing.new_amount.copy_abs().quantize(Decimal("0.01")):
            return
        override = AmountOverride(
            document_id=doc_id,
            old_amount=old_dec,
            new_amount=new_dec,
            reason="user_adjusted",
            created_at=now_utc_iso(),
        )
        self._upsert_session_amount_override(override)
        self._rerun_settlement_engine()

    def _on_table_selection_changed(self) -> None:
        self._refresh_profile_button_state()
        if not hasattr(self, "_decision_inspector") or not self._decision_inspector.isVisible():
            return
        rows = self._selected_table_rows()
        if not rows:
            self._decision_inspector.setPlainText(tr("status.decision_inspector_empty"))
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

        group = self._settlement_group_for_row(vm.row)
        if group:
            lines.extend(settlement_inspector_lines(group))

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
                tr("dialog.settings.title"),
                tr("dialog.settings.data_dir_create_failed"),
            )
            return False
        probe = new_r / ".pdf2sepa_write_test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            QMessageBox.warning(
                dialog_parent,
                tr("dialog.settings.title"),
                tr("dialog.settings.data_dir_no_write"),
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
                tr("dialog.confirm.data_dir_switch.title"),
                tr("dialog.confirm.data_dir_switch.message"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
            if not write_user_data_root(new_r, APP_BASE):
                QMessageBox.warning(
                    dialog_parent,
                    tr("dialog.settings.title"),
                    tr("dialog.settings.bootstrap_failed"),
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
                    tr("dialog.settings.title"),
                    tr("dialog.settings.copy_settings_failed"),
                )
                return False
        if old_sup.exists():
            try:
                shutil.copy2(old_sup, new_sup)
            except OSError:
                QMessageBox.warning(
                    dialog_parent,
                    tr("dialog.settings.title"),
                    tr("dialog.settings.copy_suppliers_failed"),
                )
                return False
        if not write_user_data_root(new_r, APP_BASE):
            QMessageBox.warning(
                dialog_parent,
                tr("dialog.settings.title"),
                tr("dialog.settings.bootstrap_failed"),
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
        self._filter_edit.setPlaceholderText(tr("toolbar.filter_placeholder"))
        self._filter_edit.setMinimumWidth(220)
        self._filter_edit.textChanged.connect(self._on_filter_text_changed)

        row_main = QHBoxLayout()
        row_main.setSpacing(8)

        btn_folder = QPushButton(tr("toolbar.select_folder"))
        btn_folder.clicked.connect(self._on_select_folder)
        _font_primary_button(btn_folder)
        self._btn_folder = btn_folder
        row_main.addWidget(btn_folder, alignment=Qt.AlignmentFlag.AlignLeft)
        btn_reread = QPushButton(tr("toolbar.load_pdfs"))
        btn_reread.clicked.connect(self._on_reread_pdfs)
        _font_primary_button(btn_reread)
        self._btn_reread = btn_reread
        row_main.addWidget(btn_reread, alignment=Qt.AlignmentFlag.AlignLeft)
        btn_xml = QPushButton(tr("toolbar.export_xml"))
        btn_xml.clicked.connect(self._on_make_xml)
        btn_xml.setDefault(True)
        _font_primary_button(btn_xml)
        row_main.addWidget(btn_xml, alignment=Qt.AlignmentFlag.AlignLeft)
        self._btn_xml = btn_xml

        row_main.addSpacing(12)

        btn_add_row = QToolButton()
        btn_add_row.setText("+")
        btn_add_row.setToolTip(tr("toolbar.add_row_tooltip"))
        btn_add_row.clicked.connect(self._on_add_row)
        btn_add_row.setFixedWidth(34)
        row_main.addWidget(btn_add_row, alignment=Qt.AlignmentFlag.AlignLeft)
        btn_del_sel = QToolButton()
        btn_del_sel.setText("\u2212")
        btn_del_sel.setToolTip(tr("toolbar.delete_rows_tooltip"))
        btn_del_sel.clicked.connect(self._on_delete_selected_rows)
        btn_del_sel.setFixedWidth(34)
        row_main.addWidget(btn_del_sel, alignment=Qt.AlignmentFlag.AlignLeft)

        row_main.addStretch(1)

        btn_suppliers = QPushButton(tr("dialog.suppliers.title"))
        btn_suppliers.clicked.connect(self._on_open_suppliers)
        row_main.addWidget(btn_suppliers, alignment=Qt.AlignmentFlag.AlignRight)
        btn_sync_suppliers = QPushButton(tr("toolbar.sync_suppliers"))
        btn_sync_suppliers.setToolTip(tr("toolbar.sync_suppliers_tooltip"))
        btn_sync_suppliers.clicked.connect(self._on_sync_button_clicked)
        row_main.addWidget(btn_sync_suppliers, alignment=Qt.AlignmentFlag.AlignRight)
        self._btn_create_profile = QPushButton(tr("toolbar.create_profile"))
        self._btn_create_profile.setToolTip(tr("toolbar.create_profile_tooltip"))
        self._btn_create_profile.clicked.connect(self._on_create_profile_for_selection)
        self._btn_create_profile.setEnabled(False)
        row_main.addWidget(self._btn_create_profile, alignment=Qt.AlignmentFlag.AlignRight)
        btn_settings = QPushButton()
        btn_settings.setToolTip(tr(_UW_GEGEVENS_XML_HINT_KEY))
        btn_settings.setAccessibleName(tr("toolbar.settings_accessible"))
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
            tr("table.header.supplier"),
            tr("table.header.iban"),
            tr("table.header.amount"),
            tr("table.header.customer_code"),
            tr("table.header.description"),
            tr("table.header.pdf"),
            tr("table.header.discount"),
            tr("table.header.invoice_date"),
            tr("table.header.execution_date"),
            tr("table.header.term"),
            tr("table.header.core_matches"),
            tr("table.header.matches_complete"),
            tr("table.header.status"),
            tr("table.header.error"),
            tr("table.header.info"),
            tr("table.header.settlement"),
        ]
        self._table = _PaymentsTableWidget(0, len(headers))
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
            PaymentColumn.SETTLEMENT: 120,
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
        # Windows-only: selection highlight can be too subtle on tinted rows.
        # Keep the rest of the app OS-native; only adjust table selection colors.
        try:
            import sys

            if sys.platform == "win32":
                self._table.setStyleSheet(
                    """
QTableWidget::item:selected {
    background-color: #2b78ff;
    color: #ffffff;
}
QTableWidget::item:selected:!active {
    background-color: #2b78ff;
    color: #ffffff;
}
"""
                )
        except Exception:
            pass
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)
        self._table.itemChanged.connect(self._on_table_item_changed)
        self._table.cellClicked.connect(self._on_table_cell_clicked)
        self._table.set_undo_before_edit(self._capture_pending_undo_snapshot)
        self._table.itemDelegate().closeEditor.connect(self._on_table_cell_editor_closed)

        # Table + decision inspector splitter.
        self._decision_inspector = QTextEdit()
        self._decision_inspector.setReadOnly(True)
        self._decision_inspector.setMinimumWidth(240)
        self._decision_inspector.setVisible(False)
        self._decision_inspector.setToolTip(tr("status.decision_inspector_tooltip"))

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

        self._status_label.setWordWrap(False)
        layout.addWidget(self._status_label)

    def _on_table_cell_editor_closed(self, _editor, _hint) -> None:  # noqa: N802
        self._finalize_pending_undo_on_edit_close()
        cur = self._table.currentIndex()
        edited_row_id = self._row_id(cur.row()) if cur.isValid() else ""
        edited_col = cur.column() if cur.isValid() else None
        self._table.on_cell_editor_closed()
        if (
            self._table.isSortingEnabled()
            and self._persist_sort_column is not None
        ):
            self._table.sortByColumn(self._persist_sort_column, self._persist_sort_order)
        # Volg de bewerkte rij na hersortering, anders raakt de gebruiker de rij kwijt.
        if edited_row_id:
            r = self._find_row_by_id(edited_row_id)
            if r is not None:
                self._table.selectRow(r)
                col = edited_col if edited_col is not None and 0 <= edited_col < self._table.columnCount() else int(PaymentColumn.SUPPLIER)
                self._table.setCurrentCell(r, col)
                it = self._table.item(r, col)
                if it is not None:
                    self._table.scrollToItem(it, QAbstractItemView.ScrollHint.PositionAtCenter)

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

    def get_debtor_vat_numbers(self) -> list[str]:
        numbers = self._settings.get("internal_vat_numbers")
        if isinstance(numbers, list):
            return [str(v).strip() for v in numbers if str(v or "").strip()]
        return []

    def get_debtor_vat_display(self) -> str:
        return format_internal_vat_numbers_for_display(self.get_debtor_vat_numbers())

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
        prev_debtor: dict[str, str] = deepcopy(self._settings["debtor"])
        prev_internal_vat = deepcopy(self._settings.get("internal_vat_numbers"))
        template = DEFAULT_SETTINGS["debtor"]
        try:
            for key, raw in updates.items():
                if key not in template:
                    continue
                if key == "vat":
                    numbers = normalize_internal_vat_numbers_for_storage(raw)
                    self._settings["internal_vat_numbers"] = numbers
                    sync_debtor_vat_output(self._settings["debtor"], numbers)
                    continue
                self._settings["debtor"][key] = _normalize_debtor_field(key, raw)
        except Exception:
            self._settings["debtor"] = prev_debtor
            if prev_internal_vat is None:
                self._settings.pop("internal_vat_numbers", None)
            else:
                self._settings["internal_vat_numbers"] = prev_internal_vat
            logger.exception("Instellingen debtor-normalisatie mislukt")
            return False
        if not save_settings(self._settings, str(self._settings_path())):
            self._settings["debtor"] = prev_debtor
            if prev_internal_vat is None:
                self._settings.pop("internal_vat_numbers", None)
            else:
                self._settings["internal_vat_numbers"] = prev_internal_vat
            logger.error("Instellingen opslaan mislukt (save_settings heeft False geretourneerd)")
            return False
        self._invalidate_parsed_batch_cache()
        return True

    def _on_open_settings(self) -> None:
        SettingsDialog(self).exec()

    def _resolve_export_dir(self) -> Path:
        raw: str = str(self._settings.get("export_dir") or "exports")
        return resolve_settings_path(raw, base_dir=self._user_data_dir)

    def _collect_export_batch_previews(self) -> list[dict[str, Any]] | None:
        """Export-preview rijen; ``None`` bij hash-integriteitsfout."""
        previews: list[dict[str, Any]] = []
        for r in range(self._table.rowCount()):
            if self._is_row_blank(r) or self._is_settlement_child_row(r):
                continue
            try:
                self._assert_row_hash_integrity(r)
            except RuntimeError:
                return None
            dec = self._decision_for_row(r)
            try:
                p = self._payment_dict_from_row(r)
                p["decision"] = dec
                previews.append(p)
            except ValueError:
                continue
        return previews

    def _validate_current_export_batch(self):
        previews = self._collect_export_batch_previews()
        if previews is None:
            return None
        exportable = exportable_payments_from_decisions(previews)
        if not exportable:
            return None
        return validate_export_batch(exportable)

    def _count_status_pdf(self) -> int:
        if self._is_loading_batch and self._batch_progress_total > 0:
            return self._batch_progress_total
        if self._batch_progress_total > 0:
            return self._batch_progress_total
        return len(self._matched_invoices)

    def _count_status_issues(self) -> int:
        n = 0
        for r in range(self._table.rowCount()):
            if self._is_row_blank(r) or self._is_settlement_child_row(r):
                continue
            if self._cell_text(r, PaymentColumn.ERROR).strip():
                n += 1
                continue
            if self._decision_for_row(r).get("status") == DECISION_NEEDS_REVIEW:
                n += 1
        return n

    @staticmethod
    def _blocked_export_issue_count(gate) -> int:
        s = gate.summary
        return (
            int(s.get("ambiguous_count", 0))
            + int(s.get("failed_count", 0))
            + int(s.get("invalid_amount_count", 0))
        )

    def _build_status_bar(self) -> str:
        pdf_n = self._count_status_pdf()
        pdf_part = f"📄 {pdf_n} PDF's"

        if self._is_loading_batch:
            return f"{pdf_part} • ⏳ bezig"

        gate = self._validate_current_export_batch()
        if gate is not None and gate.status == "blocked":
            err_n = self._blocked_export_issue_count(gate)
            if err_n <= 0:
                err_n = self._count_status_issues()
            return f"⛔ Export geblokkeerd • {err_n} fouten"

        issues = self._count_status_issues()
        if issues > 0:
            return f"{pdf_part} • ⚠️ {issues} problemen"

        if pdf_n > 0:
            return f"{pdf_part} • 🟢 gereed"

        return pdf_part

    def _set_status(self, text: str = "") -> None:
        _ = text
        self._status_label.setText(self._build_status_bar())

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

    def _is_detached_credit_header_row(self, row: int) -> bool:
        kind = settlement_row_kind(self._table.item(row, PaymentColumn.SUPPLIER))
        if kind != SettlementRowKind.GROUP_HEADER:
            return False
        sett = self._table.item(row, PaymentColumn.SETTLEMENT)
        if sett is None:
            return False
        raw = str(sett.data(_ROW_SETTLEMENT_STATUS_ROLE) or "").strip()
        return raw == "detached"

    def _neutralize_child_row_backgrounds(self) -> None:
        """Child rows: neutral background, preserve foreground (orange credits)."""
        from PySide6.QtGui import QBrush, QPalette
        from PySide6.QtWidgets import QApplication

        neutral = QBrush(QApplication.palette().color(QPalette.ColorRole.Base))
        for r in range(self._table.rowCount()):
            if not self._is_settlement_child_row(r):
                continue
            for c in range(self._table.columnCount()):
                it = self._table.item(r, c)
                if it:
                    it.setBackground(neutral)

    def _apply_row_colors(self) -> None:
        # HARD INVARIANT: never grey; only included/needs_review/excluded.
        prev_suppress = self._suppress_table_item_changed
        blocked = self._table.blockSignals(True)
        self._suppress_table_item_changed = True
        try:
            for r in range(self._table.rowCount()):
                if self._is_row_blank(r):
                    continue
                if self._is_settlement_child_row(r):
                    continue
                dec = self._row_decision(r)
                st = dec.get("status")
                if self._is_detached_credit_header_row(r):
                    color = self._COLOR_ERROR
                elif st == DECISION_INCLUDED:
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
            self._neutralize_child_row_backgrounds()
        finally:
            self._suppress_table_item_changed = prev_suppress
            self._table.blockSignals(blocked)

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

            spec = IbanAmbiguityDialogSpec(
                ambiguity_index=0,
                supplier_name=supplier_name,
                db_iban=db_iban,
                pdf_iban=pdf_iban,
                count=n,
            )
            if self._render_iban_dialog(spec):
                db.update_supplier(supplier_name, iban=pdf_iban)
                for inv in info["invoices"]:
                    generic = field_result_from_legacy_dict(
                        inv.get("iban_result") if isinstance(inv.get("iban_result"), dict) else {},
                        field_id="iban",
                    )
                    generic.user_overridden = True
                    user_pick = FieldCandidate(
                        value=clean_iban(pdf_iban),
                        source="manual",
                        confidence=100,
                        context="iban_mismatch_dialog",
                    )
                    resolved = resolve_field("iban", generic, [], user_pick=user_pick)
                    resolved.resolver_finalized = True
                    apply_resolved_field_result(inv, "iban", field_result_to_legacy_dict(resolved))

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

        def load() -> list[dict]:
            return load_invoices_from_folder(
                selected,
                debtor_iban=debtor_iban,
                debtor_kvk=debtor_kvk,
                debtor_name=self.get_debtor_name() or None,
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

    def _debtor_parse_context(self) -> tuple[str | None, str | None, str | None]:
        numbers = self.get_debtor_vat_numbers()
        internal_vat_fingerprint = ",".join(numbers) if numbers else None
        return (
            self.get_debtor_iban() or None,
            self.get_debtor_kvk() or None,
            internal_vat_fingerprint,
        )

    def _invalidate_parsed_batch_cache(self) -> None:
        self._parsed_batch_cache.clear()

    def _load_parsed_invoices_cold(self) -> list[dict]:
        """PDF-parse + OCR voor de geselecteerde map; werkt parse-cache bij."""
        folder = self._selected_folder
        if folder is None or not folder.is_dir():
            return []
        debtor_iban, debtor_kvk, internal_vat_fingerprint = self._debtor_parse_context()
        invoices = load_invoices_from_folder(
            folder,
            debtor_iban=debtor_iban,
            debtor_kvk=debtor_kvk,
            debtor_name=self.get_debtor_name() or None,
        )
        self._parsed_batch_cache.store(
            folder,
            invoices,
            debtor_iban=debtor_iban,
            debtor_kvk=debtor_kvk,
            debtor_vat=internal_vat_fingerprint,
        )
        return invoices

    def _load_parsed_invoices_warm(self) -> list[dict] | None:
        """Geparste facturen uit cache (geen PDF/OCR)."""
        folder = self._selected_folder
        if folder is None or not folder.is_dir():
            return None
        debtor_iban, debtor_kvk, internal_vat_fingerprint = self._debtor_parse_context()
        return self._parsed_batch_cache.get_parsed_invoices(
            folder,
            debtor_iban=debtor_iban,
            debtor_kvk=debtor_kvk,
            debtor_vat=internal_vat_fingerprint,
        )

    def _field_user_overridden(self, row: int, field_id: FieldId) -> bool:
        snap = self._field_result_snapshot_for_row(row, field_id)
        return bool(isinstance(snap, dict) and snap.get("user_overridden"))

    def _rematch_matched_invoices_from_cache(self) -> list[dict] | None:
        parsed = self._load_parsed_invoices_warm()
        if not parsed:
            return None
        db = SupplierDB(path=self._supplier_db_path())
        return match_suppliers(parsed, db)

    def _payment_for_row_in_lists(
        self, row: int, payments: list[dict], invoices: list[dict]
    ) -> dict | None:
        try:
            p_row = self._payment_dict_from_row(row)
        except ValueError:
            return None
        inv = self._match_inv_for_payment(invoices, p_row)
        if not inv:
            return None
        sf = str(inv.get("source_file") or "").strip()
        sup = str(p_row.get("supplier_name") or "")
        inv_no = str(p_row.get("invoice_number") or "")
        for p in payments:
            if sf and str(p.get("_source_file") or "").strip() == sf:
                return p
            if sup == str(p.get("supplier_name") or "") and inv_no == str(p.get("invoice_number") or ""):
                return p
        return None

    def _apply_rematch_updates_to_row(
        self,
        row: int,
        inv: dict,
        payment: dict | None,
        *,
        all_invoices: list[dict],
    ) -> None:
        """Werk leverancier-/match-velden bij zonder bedrag/IBAN te overschrijven bij user override."""
        sup_it = self._table.item(row, PaymentColumn.SUPPLIER)
        fresh_diag = _diagnostics_snapshot_from_invoice(inv)
        user_overridden = self._row_user_overridden_fields(row)
        if user_overridden and sup_it:
            existing = self._get_row_invoice_diagnostics_snapshot(row)
            if isinstance(existing, dict):
                diag = deepcopy(existing)
                for key in (
                    "supplier_name",
                    "supplier_match_source",
                    "match_info",
                    "db_core_matches",
                    "supplier_db_traits_not_on_invoice",
                    "match_signals",
                    "match_status",
                    "extraction_source",
                    "profile_fields",
                    "supplier_term_trusted",
                    "supplier_payment_term_days_raw",
                ):
                    if key in fresh_diag:
                        diag[key] = fresh_diag[key]
                for field_id in REVIEW_FIELD_IDS:
                    if field_id not in user_overridden:
                        continue
                    if field_id in ("invoice_number", "customer_number"):
                        live = self._ident_field_result_snapshot_for_row(row, field_id)
                    else:
                        live = self._field_result_snapshot_for_row(row, field_id)
                    if isinstance(live, dict):
                        diag = overlay_field_result(diag, field_id, live)
            else:
                diag = fresh_diag
        else:
            diag = fresh_diag
        if sup_it:
            sup_it.setData(_ROW_INVOICE_DIAGNOSTICS_ROLE, diag)
            core_info = _core_matches_text(inv)
            complete_info = _matches_completeness_text(inv)
            sup_it.setToolTip(f"{core_info} | {complete_info}")
            _, inv_meta, _, inv_res, cust_res = self._row_ident_fields_from_inv(inv)
            if inv_meta:
                sup_it.setData(_ROW_INVOICE_META_ROLE, inv_meta)
            if isinstance(inv_res, dict) and not self._field_user_overridden(row, "invoice_number"):
                sup_it.setData(_ROW_INVOICE_NUMBER_RESULT_ROLE, deepcopy(inv_res))
            email_dom = str(inv.get("email_domain") or "")
            kvk_no = str(inv.get("kvk_number") or "")
            vat_no = str(inv.get("vat_number") or "")
            if email_dom:
                sup_it.setData(_ROW_EMAIL_DOMAIN_ROLE, email_dom)
            if kvk_no:
                sup_it.setData(_ROW_KVK_NUMBER_ROLE, kvk_no)
            if vat_no:
                sup_it.setData(_ROW_VAT_NUMBER_ROLE, vat_no)

        cust_it = self._table.item(row, PaymentColumn.CUSTOMER_CODE)
        cust_disp, _, desc_disp, _, cust_res = self._row_ident_fields_from_inv(inv)
        if cust_it and not self._field_user_overridden(row, "customer_number"):
            cust_it.setText(cust_disp)
            if isinstance(cust_res, dict):
                cust_it.setData(_ROW_CUSTOMER_NUMBER_RESULT_ROLE, deepcopy(cust_res))
        desc_it = self._table.item(row, PaymentColumn.DESCRIPTION)
        is_group_header = (
            settlement_row_kind(self._table.item(row, PaymentColumn.SUPPLIER))
            == SettlementRowKind.GROUP_HEADER
        )
        if desc_it and not is_group_header and _customer_number_none_mode_active(inv):
            desc_it.setText(desc_disp)

        disc = (
            self._discount_for_payment([inv], payment)
            if payment
            else _discount_str_from_inv(inv)
        )
        disc_it = self._table.item(row, PaymentColumn.DISCOUNT)
        if disc_it:
            disc_it.setText(disc)

        tr_raw = payment.get("supplier_term_trusted") if payment else inv.get("supplier_term_trusted")
        trusted: bool | None = bool(tr_raw) if isinstance(tr_raw, bool) else None
        if payment is not None:
            eff = int(payment.get("supplier_payment_term_days_effective") or 0)
        else:
            eff = int(inv.get("supplier_payment_term_days_raw") or 0) if trusted else 0
        term_lbl = _term_status_label(trusted, eff)
        term_it = self._table.item(row, PaymentColumn.TERM_HINT)
        if term_it:
            term_it.setText(term_lbl)
            term_it.setData(_ROW_EFFECTIVE_TERM_ROLE, eff)
            if trusted is not None:
                term_it.setData(_ROW_TERM_TRUSTED_ROLE, trusted)

        status_it = self._table.item(row, PaymentColumn.STATUS)
        if status_it:
            status_it.setText(str((payment or inv).get("status") or inv.get("match_status") or ""))

        trace = payment.get("decision_trace") if isinstance(payment, dict) else None
        if isinstance(trace, dict):
            err_it = self._table.item(row, PaymentColumn.ERROR)
            if err_it:
                err_it.setData(_ROW_DECISION_TRACE_ROLE, deepcopy(trace))

        iban_disp, iban_res = _iban_field_from_inv(inv)
        if not self._field_user_overridden(row, "iban"):
            iban_it = self._table.item(row, PaymentColumn.IBAN)
            if iban_it:
                iban_it.setText(iban_disp)
                if isinstance(iban_res, dict):
                    iban_it.setData(_ROW_IBAN_RESULT_ROLE, deepcopy(iban_res))

        if payment and not self._field_user_overridden(row, "amount"):
            inv_match = inv
            base_incl, base_excl = self._payment_base_amounts_for_row(all_invoices, payment, inv_match)
            amt_it = self._table.item(row, PaymentColumn.AMOUNT)
            if amt_it:
                if base_incl is not None:
                    amt_it.setData(_ROW_BASE_INCL_ROLE, str(base_incl))
                if base_excl is not None:
                    amt_it.setData(_ROW_BASE_EXCL_ROLE, str(base_excl))
                ar = inv.get("amount_result")
                if isinstance(ar, dict):
                    amt_it.setData(_ROW_AMOUNT_RESULT_ROLE, deepcopy(ar))

    _SUPPLIER_SYNC_BLOCK_REASONS = frozenset(
        {
            "unmatched_supplier",
            "no_supplier_hint",
            "needs_review",
            REASON_MANUAL_PENDING,
        }
    )

    def _row_user_overridden_fields(self, row: int) -> frozenset[str]:
        return frozenset(
            field_id
            for field_id in REVIEW_FIELD_IDS
            if self._field_user_overridden(row, field_id)
        )

    def _supplier_exists_in_db(self, db: SupplierDB, name: str, *, original_name: str = "") -> bool:
        for lookup in (original_name, name):
            lookup_s = str(lookup or "").strip()
            if lookup_s and db.supplier_exists_by_name(lookup_s):
                return True
        return False

    def _row_field_results_for_patch(self, row: int) -> dict[str, dict[str, Any] | None]:
        out: dict[str, dict[str, Any] | None] = {}
        for field_id in REVIEW_FIELD_IDS:
            if field_id in ("invoice_number", "customer_number"):
                out[field_id] = self._ident_field_result_snapshot_for_row(row, field_id)
            else:
                out[field_id] = self._field_result_snapshot_for_row(row, field_id)
        return out

    def _row_supplier_sync_payload(self, row: int, db: SupplierDB | None = None) -> dict[str, Any]:
        """Authoritative supplier write state from diagnostics/profile row data."""
        sup_it = self._table.item(row, PaymentColumn.SUPPLIER)
        return build_supplier_sync_payload_from_parts(
            name=self._cell_text(row, PaymentColumn.SUPPLIER),
            iban_cell=self._cell_text(row, PaymentColumn.IBAN),
            customer_code_cell=self._cell_text(row, PaymentColumn.CUSTOMER_CODE),
            discount_raw=self._cell_text(row, PaymentColumn.DISCOUNT),
            term_raw=self._cell_text(row, PaymentColumn.TERM_HINT),
            iban_result=self._field_result_snapshot_for_row(row, "iban"),
            customer_result=self._ident_field_result_snapshot_for_row(row, "customer_number"),
            row_snap=self._get_row_invoice_diagnostics_snapshot(row),
            email_dom=str(sup_it.data(_ROW_EMAIL_DOMAIN_ROLE) or "").strip() if sup_it else "",
            kvk_no=str(sup_it.data(_ROW_KVK_NUMBER_ROLE) or "").strip() if sup_it else "",
            vat_no=str(sup_it.data(_ROW_VAT_NUMBER_ROLE) or "").strip() if sup_it else "",
            original_name=str(sup_it.data(_ROW_SUPPLIER_ORIGINAL_ROLE) or "").strip() if sup_it else "",
            supplier_exists=self._supplier_exists_in_db(
                db,
                self._cell_text(row, PaymentColumn.SUPPLIER).strip(),
                original_name=str(sup_it.data(_ROW_SUPPLIER_ORIGINAL_ROLE) or "").strip() if sup_it else "",
            )
            if db is not None
            else False,
        )

    def _patch_table_row_into_invoices(self, row: int, invoices: list[dict]) -> bool:
        """Zet tabel-leverancier/veldstate op de geparste factuur (vóór rematch)."""
        sf = self._resolve_row_source_file(row)
        if not sf:
            return False
        payload = self._row_supplier_sync_payload(row)
        name = str(payload.get("name") or "").strip()
        iban_result = self._field_result_snapshot_for_row(row, "iban")
        customer_result = self._ident_field_result_snapshot_for_row(row, "customer_number")
        user_overridden = self._row_user_overridden_fields(row)
        sf_key = str(Path(sf).resolve())
        for inv in invoices:
            inv_sf = str(inv.get("source_file") or "").strip()
            if not inv_sf:
                continue
            if inv_sf != sf and str(Path(inv_sf).resolve()) != sf_key:
                continue
            patch_authoritative_row_fields_into_invoice(
                inv,
                name=name,
                payload=payload,
                iban_result=iban_result if isinstance(iban_result, dict) else None,
                customer_result=customer_result if isinstance(customer_result, dict) else None,
                field_results=self._row_field_results_for_patch(row),
                user_overridden_fields=user_overridden,
            )
            return True
        return False

    def _patch_warm_invoice_cache_from_table_row(self, row: int) -> bool:
        folder = self._selected_folder
        if folder is None or not folder.is_dir():
            return False
        parsed = self._load_parsed_invoices_warm()
        if not parsed:
            return False
        if not self._patch_table_row_into_invoices(row, parsed):
            return False
        debtor_iban, debtor_kvk, internal_vat_fingerprint = self._debtor_parse_context()
        self._parsed_batch_cache.store(
            folder,
            parsed,
            debtor_iban=debtor_iban,
            debtor_kvk=debtor_kvk,
            debtor_vat=internal_vat_fingerprint,
        )
        return True

    def _decision_after_supplier_sync_confirm(
        self, row: int, engine_dec: dict[str, Any] | None
    ) -> dict[str, Any]:
        if isinstance(engine_dec, dict) and engine_dec.get("status") == DECISION_INCLUDED:
            return dict(normalize_decision(engine_dec))
        try:
            p = self._payment_dict_from_row(row)
        except ValueError:
            if isinstance(engine_dec, dict):
                return dict(normalize_decision(engine_dec))
            return self._missing_decision_payload(row)
        validation_error = self._validate_single_payment_row(p)
        if validation_error:
            if isinstance(engine_dec, dict):
                return dict(normalize_decision(engine_dec))
            return build_decision(
                status=DECISION_NEEDS_REVIEW,
                reason_code="row_validation_failed",
                reason_detail=validation_error,
                editable=True,
                requires_rerun=False,
                causal_inputs=["iban", "amount", "execution_date"],
                input_fields={"row_id": self._row_id(row), "error": validation_error},
            )
        rc = str((engine_dec or {}).get("reason_code") or "")
        if rc and rc not in self._SUPPLIER_SYNC_BLOCK_REASONS:
            if isinstance(engine_dec, dict):
                return dict(normalize_decision(engine_dec))
        rid = self._row_id(row)
        return build_decision(
            status=DECISION_INCLUDED,
            reason_code=REASON_USER_APPROVED,
            reason_detail="supplier_sync_confirmed",
            editable=False,
            requires_rerun=False,
            causal_inputs=["supplier_sync"],
            input_fields={
                "row_id": rid,
                "supplier_name": p.get("supplier_name"),
                "iban": p.get("iban"),
            },
        )

    def _rematch_rows_after_supplier_sync(self, rows: list[int]) -> None:
        """Herkoppel leveranciers uit cache; geen PDF/OCR, tabel blijft intact."""
        parsed = self._load_parsed_invoices_warm()
        if parsed is None:
            matched = self._rematch_matched_invoices_from_cache()
        else:
            parsed_work = deepcopy(parsed)
            for r in rows:
                self._patch_table_row_into_invoices(r, parsed_work)
            db = SupplierDB(path=self._supplier_db_path())
            matched = match_suppliers(parsed_work, db)
            folder = self._selected_folder
            if folder is not None and folder.is_dir():
                debtor_iban, debtor_kvk, internal_vat_fingerprint = self._debtor_parse_context()
                self._parsed_batch_cache.store(
                    folder,
                    parsed_work,
                    debtor_iban=debtor_iban,
                    debtor_kvk=debtor_kvk,
                    debtor_vat=internal_vat_fingerprint,
                )
        if matched is None:
            return

        progress = QProgressDialog(tr("status.rematch_suppliers"), None, 0, 0, self)
        progress.setWindowTitle(tr("status.progress_title"))
        progress.setMinimumDuration(200)
        progress.setValue(0)
        QApplication.processEvents()

        try:
            matched_calc = enrich_credit_documents(deepcopy(matched)) if batch_requires_settlement(matched) else deepcopy(matched)
            engine_result = self._compute_engine_result(matched_calc)
            self._engine_result = engine_result
            self._engine_cache.invalidate("rematch")
            payments, engine_errors = engine_result_views(engine_result)
            inv_index = index_invoices_by_source_file(matched)

            self._suppress_table_item_changed = True
            try:
                for r in rows:
                    if r < 0 or r >= self._table.rowCount() or self._is_row_blank(r):
                        continue
                    sf = self._resolve_row_source_file(r)
                    inv: dict | None = None
                    if sf:
                        inv = inv_index.get(sf) or inv_index.get(str(Path(sf).resolve()))
                    if inv is None:
                        try:
                            p_row = self._payment_dict_from_row(r)
                        except ValueError:
                            continue
                        inv = self._match_inv_for_payment(matched, p_row)
                    if not inv:
                        continue
                    payment = self._payment_for_row_in_lists(r, payments, matched)
                    self._apply_rematch_updates_to_row(
                        r, inv, payment, all_invoices=matched
                    )
            finally:
                self._suppress_table_item_changed = False
            self._apply_row_colors()
            decision_updates: dict[str, dict[str, Any]] = {}
            for r in rows:
                engine_dec = self._decision_from_engine_rematch(
                    r,
                    payments=payments,
                    errors=engine_errors,
                    invoices=matched_calc,
                )
                dec = self._decision_after_supplier_sync_confirm(r, engine_dec)
                rid = self._row_id(r)
                decision_updates[rid] = dec
                self._set_row_decision(r, dec)
            self._commit_decision_map_patch(decision_updates)
            self._pending_engine_row_ids.difference_update(decision_updates.keys())
            self._apply_row_colors()
        finally:
            try:
                progress.close()
            except Exception:
                pass

    def _set_batch_load_ui_busy(self, busy: bool) -> None:
        for btn in (self._btn_folder, self._btn_reread):
            if btn is not None:
                btn.setEnabled(not busy)

    def _build_batch_load_params(
        self,
        *,
        parse_pdfs: bool,
        warm_invoices: list[dict] | None = None,
    ) -> BatchLoadParams | None:
        folder = self._selected_folder
        if folder is None or not folder.is_dir():
            return None
        debtor_iban, debtor_kvk, internal_vat_fingerprint = self._debtor_parse_context()
        warm_tuple = tuple(warm_invoices) if warm_invoices is not None else None
        return BatchLoadParams(
            folder=folder,
            parse_pdfs=parse_pdfs,
            debtor_iban=debtor_iban,
            debtor_kvk=debtor_kvk,
            debtor_vat=internal_vat_fingerprint,
            debtor_name=self.get_debtor_name() or None,
            supplier_db_snapshot=SupplierDBSnapshot.from_path(self._supplier_db_path()),
            session_date=self._session_date,
            batch_key=self._batch_key(),
            amount_override_session=self._session_amount_override_session(),
            document_type_override_session=self._session_document_type_override_session(),
            override_store=self._override_store,
            warm_invoices=warm_tuple,
        )

    def _render_iban_dialog(self, spec: IbanAmbiguityDialogSpec) -> bool:
        answer = QMessageBox.question(
            self,
            tr(f"{spec.key}.title"),
            tr(
                f"{spec.key}.message",
                supplier_name=spec.supplier_name,
                db_iban=spec.db_iban,
                pdf_iban=spec.pdf_iban,
                count=spec.count,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _collect_iban_raw_answers(self, checkpoint: PreprocessCheckpoint) -> IbanRawUiAnswers:
        specs = checkpoint.iban_dialog_specs
        if not specs:
            return IbanRawUiAnswers()
        answers: list[IbanRawUiAnswer] = []
        for spec in specs:
            clicked_yes = self._render_iban_dialog(spec)
            answers.append(IbanRawUiAnswer(ambiguity_index=spec.ambiguity_index, clicked_yes=clicked_yes))
        return IbanRawUiAnswers(answers=tuple(answers))

    def _cleanup_batch_load(self) -> None:
        self._is_loading_batch = False
        self._set_batch_load_ui_busy(False)
        if self._loading_overlay is not None:
            self._loading_overlay.hide_overlay()
        if self._batch_load_thread is not None:
            self._batch_load_thread.quit()
            self._batch_load_thread.wait(5000)
            self._batch_load_thread = None
        self._batch_load_worker = None
        self._set_status()

    def _on_batch_load_progress(self, done: int, total: int, filename: str, stage: str) -> None:
        overlay = self._loading_overlay
        if overlay is None:
            return
        if stage in ("parsing_pdf", "listing_pdfs") and total > 0:
            self._batch_progress_done = done
            self._batch_progress_total = total
        overlay.update_progress(done, total, filename, stage)
        self._set_status()

    def _on_batch_preprocess_finished(self, checkpoint: PreprocessCheckpoint) -> None:
        folder = self._selected_folder
        if folder is not None and folder.is_dir():
            debtor_iban, debtor_kvk, internal_vat_fingerprint = self._debtor_parse_context()
            self._parsed_batch_cache.store(
                folder,
                list(checkpoint.v0.invoices),
                debtor_iban=debtor_iban,
                debtor_kvk=debtor_kvk,
                debtor_vat=internal_vat_fingerprint,
            )
        raw_answers = self._collect_iban_raw_answers(checkpoint)
        worker = self._batch_load_worker
        if worker is None:
            self._cleanup_batch_load()
            return
        worker.resolve_requested.emit(checkpoint, raw_answers)

    def _apply_batch_load_result_atomic(self, result: BatchLoadResult) -> int:
        matched = list(result.v2.invoices)
        engine_result = result.engine_result
        self._matched_invoices = matched
        self._engine_cache.invalidate("reload")
        self._expanded_settlement_groups = set()
        self._engine_result = engine_result
        self._init_expand_state_for_engine(engine_result)
        self._decision_table_fingerprint = None
        n_err_rows = self._populate_from_engine_result(engine_result, matched)
        initial_run_id = str(uuid.uuid4())
        decision_map: dict[str, dict[str, Any]] = {}
        for r in range(self._table.rowCount()):
            if self._is_row_blank(r) or self._is_settlement_child_row(r):
                continue
            decision_map[self._row_id(r)] = self._row_decision(r)
        decision_map = self._normalize_decision_map_row_ids(decision_map)
        batch_key = stable_hash(
            {
                "folder": str(self._selected_folder.resolve()) if self._selected_folder else "",
                "suppliers_path": self._supplier_db_path(),
            }
        )
        persisted = self._approval_store.load_batch(batch_key)
        if persisted:
            for r in range(self._table.rowCount()):
                if self._is_row_blank(r):
                    continue
                rid = self._row_id(r)
                dec = persisted.get(rid) or persisted.get(self._legacy_row_id(r))
                if isinstance(dec, dict):
                    decision_map[rid] = dec
                    self._set_row_decision(r, dec)
        run = self._decision_store.begin_run(
            run_id=initial_run_id,
            input_snapshot_hash=stable_hash(
                {"matched_count": len(matched), "session_date": self._session_date.isoformat()}
            ),
            decision_map=decision_map,
        )
        self._decision_store.commit_run(run.run_id)
        self._pinned_run_id = run.run_id
        self._active_run_id = run.run_id
        self._decision_table_fingerprint = None
        self._sync_decision_store_from_table(force=True)
        # #region agent log
        try:
            import json as _json, time as _time
            with open("/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-c00626.log", "a", encoding="utf-8") as _f:
                _f.write(_json.dumps({"sessionId": "c00626", "hypothesisId": "B", "location": "main_window.py:_apply_batch_load_result_atomic", "message": "about to apply_row_colors", "data": {"row_count": self._table.rowCount()}, "timestamp": int(_time.time() * 1000), "runId": "pre-fix"}) + "\n")
        except Exception:
            pass
        # #endregion
        self._apply_row_colors()
        n_pdf = result.n_raw
        n_pay = self._count_payment_header_rows()
        for name, count in (("Map: " + self._selected_folder.name, n_pdf),) if self._selected_folder else ():
            logger.info("bron %r: %d pdf-facturen", name, count)
        logger.info("betalingsregels: %d, foutregels: %d", n_pay, n_err_rows)
        self._update_load_status_after_load(n_pdf=n_pdf, n_payments=n_pay, n_error_rows=n_err_rows)
        apply_supplier_db_mutations(SupplierDB(path=self._supplier_db_path()), result.v2.pending_db_mutations)
        return n_err_rows

    def _on_batch_load_finished(self, result: BatchLoadResult) -> None:
        try:
            self._apply_batch_load_result_atomic(result)
        except Exception as exc:
            logger.exception("batch load apply failed")
            # #region agent log
            try:
                import json as _json, time as _time
                with open("/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-c00626.log", "a", encoding="utf-8") as _f:
                    _f.write(_json.dumps({"sessionId": "c00626", "hypothesisId": "A", "location": "main_window.py:_on_batch_load_finished", "message": "batch apply exception", "data": {"exc_type": type(exc).__name__, "exc_msg": str(exc)}, "timestamp": int(_time.time() * 1000), "runId": "pre-fix"}) + "\n")
            except Exception:
                pass
            # #endregion
            QMessageBox.warning(
                self,
                tr("dialog.batch_load.failed.title"),
                tr("error.batch.apply_failed", detail=str(exc)),
            )
        finally:
            self._cleanup_batch_load()

    def _on_batch_load_error(self, message: str) -> None:
        logger.error("batch load error: %s", message)
        QMessageBox.warning(
            self,
            tr("dialog.batch_load.failed.title"),
            _batch_error_message(message),
        )
        self._cleanup_batch_load()

    def _start_batch_load(self, *, parse_pdfs: bool) -> None:
        if self._batch_load_thread is not None:
            return
        warm: list[dict] | None = None
        if not parse_pdfs:
            warm = self._load_parsed_invoices_warm()
            if warm is None:
                QMessageBox.warning(
                    self,
                    tr("dialog.batch_load.no_cache.title"),
                    tr("dialog.batch_load.no_cache.message"),
                )
                return
        params = self._build_batch_load_params(parse_pdfs=parse_pdfs, warm_invoices=warm)
        if params is None:
            return
        self._is_loading_batch = True
        self._set_batch_load_ui_busy(True)
        label = tr("overlay.title.parse_pdfs") if parse_pdfs else tr("overlay.title.rematch")
        self._batch_progress_done = 0
        self._batch_progress_total = 0
        self._set_status()
        if self._loading_overlay is not None:
            self._loading_overlay.show_overlay(label)
        worker = InvoiceBatchLoadWorker()
        worker.set_preprocess_params(params)
        thread = QThread(self)
        worker.moveToThread(thread)
        worker.progress.connect(self._on_batch_load_progress_value)
        worker.current_file.connect(self._on_batch_load_progress_file)
        worker.current_stage.connect(self._on_batch_load_progress_stage)
        worker.preprocess_finished.connect(self._on_batch_preprocess_finished)
        worker.finished.connect(self._on_batch_load_finished)
        worker.error.connect(self._on_batch_load_error)
        thread.started.connect(worker.start_preprocess)
        self._batch_load_worker = worker
        self._batch_load_thread = thread
        self._batch_progress_file = ""
        self._batch_progress_stage = ""
        thread.start()

    def _on_batch_load_progress_value(self, done: int, total: int) -> None:
        self._on_batch_load_progress(done, total, self._batch_progress_file, self._batch_progress_stage)

    def _on_batch_load_progress_file(self, filename: str) -> None:
        self._batch_progress_file = filename
        self._on_batch_load_progress(
            self._batch_progress_done,
            self._batch_progress_total,
            filename,
            self._batch_progress_stage,
        )

    def _on_batch_load_progress_stage(self, stage: str) -> None:
        self._batch_progress_stage = stage
        self._on_batch_load_progress(
            self._batch_progress_done,
            self._batch_progress_total,
            self._batch_progress_file,
            stage,
        )

    def _load_payments_from_sources(self, *, parse_pdfs: bool = True) -> None:
        if parse_pdfs:
            self._clear_session_amount_overrides()
            self._undo_stack.clear()
            batch_key = self._batch_key()
            self._override_store.clear_batch(batch_key)
            self._document_type_override_store.clear_batch(batch_key)
            self._approval_store.clear_batch(batch_key)
        self._start_batch_load(parse_pdfs=parse_pdfs)

    def _update_load_status_after_load(
        self,
        *,
        n_pdf: int,
        n_payments: int,
        n_error_rows: int,
    ) -> None:
        _ = (n_payments, n_error_rows)
        if n_pdf > 0:
            self._batch_progress_total = n_pdf
        self._set_status()

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
            if _customer_number_none_mode_active(inv):
                cns = ""
            else:
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

    def _field_result_snapshot_for_row(
        self, row: int, field_id: FieldId
    ) -> dict[str, Any] | None:
        if field_id == "amount":
            return self._amount_result_snapshot_for_row(row)
        if field_id == "iban":
            it = self._table.item(row, PaymentColumn.IBAN)
            if not it:
                return None
            raw = it.data(_ROW_IBAN_RESULT_ROLE)
            if isinstance(raw, dict):
                return deepcopy(raw)
            snap = self._get_row_invoice_diagnostics_snapshot(row)
            if isinstance(snap, dict):
                ir = snap.get("iban_result")
                if isinstance(ir, dict):
                    return deepcopy(ir)
            return None
        if field_id in ("invoice_number", "customer_number"):
            return self._ident_field_result_snapshot_for_row(row, field_id)
        spec = FIELD_REVIEW_SPECS.get(field_id)
        if spec is not None:
            snap = self._get_row_invoice_diagnostics_snapshot(row)
            if isinstance(snap, dict):
                raw = snap.get(spec.result_snapshot_key)
                if isinstance(raw, dict):
                    return deepcopy(raw)
        return None

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

    def _field_picker_eligible(self, row: int, field_id: FieldId) -> bool:
        snap = self._field_result_snapshot_for_row(row, field_id)
        return picker_eligible(snap, field_id=field_id)

    def _show_field_candidate_menu(self, row: int, field_id: FieldId) -> None:
        spec = FIELD_REVIEW_SPECS.get(field_id)
        if spec is None:
            return
        snap = self._field_result_snapshot_for_row(row, field_id)
        if not picker_eligible(snap, field_id=field_id):
            QMessageBox.information(
                self,
                tr(spec.menu_empty_title_key),
                tr(spec.menu_no_candidates_key),
            )
            return
        if field_id == "amount":
            opts = filter_amount_menu_candidates((snap or {}).get("candidates") or [])
            if not opts:
                QMessageBox.information(
                    self,
                    tr(spec.menu_empty_title_key),
                    tr("dialog.field_picker.no_metadata"),
                )
                return
            menu = build_field_candidate_menu(
                self,
                candidates=opts,
                format_label=lambda c: format_amount_candidate_menu_label(
                    c, format_amount_nl=_format_amount_nl
                ),
                on_pick=lambda c: self._apply_field_candidate_pick_to_row(row, field_id, c),
                tooltip_from_candidate=lambda c: candidate_menu_tooltip(c, max_len=72),
            )
        else:
            if field_id == "iban":
                opts = filter_iban_menu_candidates((snap or {}).get("candidates") or [])
                format_label = format_iban_candidate_menu_label
            else:
                opts = [
                    c
                    for c in (snap or {}).get("candidates") or []
                    if isinstance(c, dict) and str(c.get("value") or "").strip()
                ]
                format_label = format_ident_candidate_menu_label
            if field_id == "iban" and not opts:
                QMessageBox.information(
                    self,
                    tr(spec.menu_empty_title_key),
                    tr("dialog.field_picker.no_valid_iban"),
                )
                return
            menu = build_field_candidate_menu(
                self,
                candidates=opts,
                format_label=format_label,
                on_pick=lambda c: self._apply_field_candidate_pick_to_row(row, field_id, c),
                tooltip_from_candidate=candidate_menu_tooltip,
            )
            if menu is not None and field_id == "customer_number":
                append_customer_absent_menu_action(
                    menu,
                    on_pick=lambda: self._apply_field_candidate_pick_to_row(
                        row,
                        field_id,
                        make_customer_absent_pick_candidate(),
                    ),
                )
        if menu is not None:
            menu.exec(QCursor.pos())

    def _apply_field_candidate_pick_to_row(
        self,
        row: int,
        field_id: FieldId,
        cand: dict[str, Any],
    ) -> None:
        spec = FIELD_REVIEW_SPECS.get(field_id)
        if spec is None:
            return
        if field_id == "customer_number" and is_customer_absent_pick(cand):
            # #region agent log (debug mode)
            _dbg_log_3d66a1(
                hypothesis_id="H1",
                location="main_window.py:_apply_field_candidate_pick_to_row",
                message="routing absent pick",
                data={"row": row, "cand_source": str(cand.get("source") or "")},
            )
            # #endregion
            self._apply_customer_absent_pick_to_row(
                row,
                pending_reason="customer_number_absent",
            )
            return
        self._candidate_click_pipeline(
            row,
            field_id,
            cand,
            pending_reason=spec.pick_pending_reason,
        )

    def _candidate_from_ui_dict(
        self,
        field_id: FieldId,
        cand: dict[str, Any],
        *,
        source_fallback: str,
    ) -> FieldCandidate | None:
        conf = int(cand.get("confidence") or 100)
        src = str(cand.get("source") or source_fallback).strip() or source_fallback
        ctx = str(cand.get("context") or "")
        if field_id == "amount":
            raw_v = cand.get("value")
            if raw_v is None:
                return None
            try:
                val = amount_to_decimal(str(raw_v))
            except (TypeError, ValueError):
                return None
            if val <= Decimal("0.00"):
                return None
            return FieldCandidate(value=val, source=src, confidence=conf, context=ctx)
        if field_id == "iban":
            val = clean_iban(str(cand.get("value") or ""))
            if not val or not is_plausible_iban(val):
                return None
            return FieldCandidate(value=val, source=src, confidence=conf, context=ctx)
        if field_id == "customer_number" and is_customer_absent_pick(cand):
            return FieldCandidate(
                value=None,
                source=CUSTOMER_ABSENT_PICK_SOURCE,
                confidence=100,
                context=ctx,
            )
        val = str(cand.get("value") or "").strip()
        if not val:
            return None
        return FieldCandidate(value=val, source=src, confidence=conf, context=ctx)

    def _apply_customer_absent_pick_to_row(
        self,
        row: int,
        *,
        pending_reason: str = "customer_number_absent",
        mark_pending: bool = True,
    ) -> None:
        """Leg vast dat deze leverancier geen klantnummer op de factuur heeft."""
        # #region agent log (debug mode)
        _dbg_log_3d66a1(
            hypothesis_id="H2",
            location="main_window.py:_apply_customer_absent_pick_to_row:entry",
            message="absent pick apply start",
            data={
                "row": row,
                "cell_before": self._cell_text(row, PaymentColumn.CUSTOMER_CODE).strip(),
                "pending_reason": pending_reason,
            },
        )
        # #endregion
        snap = self._field_result_snapshot_for_row(row, "customer_number") or {}
        resolved_dict = dict(snap)
        resolved_dict["value"] = None
        resolved_dict["selected_value"] = None
        resolved_dict["absence_state"] = CUSTOMER_ABSENT_STATE
        resolved_dict["source"] = CUSTOMER_ABSENT_PICK_SOURCE
        resolved_dict["status"] = "confirmed"
        resolved_dict["confidence"] = 100
        resolved_dict["user_selected"] = True
        resolved_dict["user_overridden"] = True
        resolved_dict["candidates"] = []
        resolved_dict["override_reason"] = "user_customer_absent"
        resolved_dict["resolver_finalized"] = True
        resolved_dict = canonicalize_legacy_result_dict(
            resolved_dict,
            field_id="customer_number",
            resolver_finalized=True,
        )
        self._apply_resolved_field_result_to_row(
            row,
            "customer_number",
            resolved_dict,
            pending_reason=pending_reason,
            mark_pending=mark_pending,
        )

    def _resolve_and_apply_field_candidate(
        self,
        row: int,
        field_id: FieldId,
        cand: dict[str, Any],
        *,
        pending_reason: str,
        mark_pending: bool = True,
        skip_undo: bool = False,
    ) -> None:
        # Legacy entry point: keep signature but route through unified pipeline.
        self._candidate_click_pipeline(
            row,
            field_id,
            cand,
            pending_reason=pending_reason,
            mark_pending=mark_pending,
            skip_undo=skip_undo,
        )

    def _candidate_click_pipeline(
        self,
        row: int,
        field_id: FieldId,
        cand: dict[str, Any],
        *,
        pending_reason: str,
        mark_pending: bool = True,
        override_reason: str = "diagnostics_candidate_click",
        skip_undo: bool = False,
    ) -> None:
        """Atomic operation: state update → resolve_field → apply_resolved_field_result → UI refresh."""
        candidate = self._candidate_from_ui_dict(field_id, cand, source_fallback="manual")
        if candidate is None:
            return
        generic_snap = self._field_result_snapshot_for_row(row, field_id) or {}
        generic = field_result_from_legacy_dict(generic_snap, field_id=field_id)

        # Unified user action contract (resolver must be authoritative after this).
        generic.selected_value = candidate.value
        generic.user_selected = True
        generic.user_overridden = True
        generic.override_reason = override_reason

        resolved_fr = resolve_field(field_id, generic, [], user_pick=candidate)
        resolved_fr.resolver_finalized = True
        resolved_dict = field_result_to_legacy_dict(resolved_fr)
        self._apply_resolved_field_result_to_row(
            row,
            field_id,
            resolved_dict,
            pending_reason=pending_reason,
            mark_pending=mark_pending,
        )

    def _build_diagnostics_for_row(self, row: int) -> tuple[dict, bool]:
        snap, limited = self._invoice_diagnostics_snapshot_for_display(row)
        payment = self._payment_dict_from_row(row, require_resolved_amount=False)
        decision = self._decision_for_row(row)
        diag = build_diagnostics(snap, payment=payment, decision=decision)
        # #region agent log (debug mode)
        cust_block = diag.get("customer_number") if isinstance(diag.get("customer_number"), dict) else {}
        _dbg_log_3d66a1(
            hypothesis_id="H5",
            location="main_window.py:_build_diagnostics_for_row",
            message="diagnostics built",
            data={
                "row": row,
                "cell": self._cell_text(row, PaymentColumn.CUSTOMER_CODE).strip(),
                "diag_value_display": str(cust_block.get("value_display") or cust_block.get("value") or ""),
                "diag_status_nl": str(cust_block.get("status_nl") or ""),
                "snap_scalar": str(snap.get("customer_number") or ""),
                "snap_none": customer_number_is_absent_or_none(snap),
            },
        )
        # #endregion
        return diag, limited

    @staticmethod
    def _field_values_equal(field_id: FieldId, a: Any, b: Any) -> bool:
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        return _values_equal(field_id, a, b)

    def _user_pick_for_selected_value(
        self,
        field_id: FieldId,
        generic: Any,
        value: Any,
    ) -> FieldCandidate | None:
        norm = normalize_field_value(field_id, value)
        if norm is None:
            return None
        for c in generic.candidates:
            if _values_equal(field_id, c.value, norm):
                return c
        return FieldCandidate(
            value=norm,
            source="USER_PICKED",
            confidence=100,
            context=str(generic.context or ""),
        )

    def _confirm_selected_fields_for_row(
        self,
        row: int,
        selected_by_field: dict[str, Any],
    ) -> None:
        """Bevestig huidige selectie: user_selected → resolve (gewijzigd) → apply (alle velden)."""
        with self._undo_batch(source="diagnostics_confirm"):
            self._confirm_selected_fields_for_row_impl(row, selected_by_field)

    def _confirm_selected_fields_for_row_impl(
        self,
        row: int,
        selected_by_field: dict[str, Any],
    ) -> None:
        resolved_by_field: dict[FieldId, dict[str, Any]] = {}
        for field_id in REVIEW_FIELD_IDS:
            generic_snap = self._field_result_snapshot_for_row(row, field_id) or {}
            generic = field_result_from_legacy_dict(generic_snap, field_id=field_id)
            raw_target = selected_by_field.get(field_id)
            if field_id == "customer_number" and is_customer_absent_pick(
                raw_target if isinstance(raw_target, dict) else None
            ):
                self._apply_customer_absent_pick_to_row(
                    row,
                    pending_reason="diagnostics_confirm_selection",
                    mark_pending=False,
                )
                continue
            if raw_target is not None:
                target = normalize_field_value(field_id, raw_target)
            else:
                target = normalize_field_value(field_id, generic.selected_value)

            if target is None:
                continue

            changed = not self._field_values_equal(field_id, generic.selected_value, target)
            generic.selected_value = target
            generic.user_selected = True
            generic.user_overridden = True
            generic.override_reason = "diagnostics_confirm_selection"

            if changed:
                user_pick = self._user_pick_for_selected_value(field_id, generic, target)
                resolved_fr = resolve_field(field_id, generic, [], user_pick=user_pick)
                resolved_fr.resolver_finalized = True
                resolved_dict = field_result_to_legacy_dict(resolved_fr)
            else:
                resolved_dict = field_result_to_legacy_dict(generic)
                resolved_dict["user_selected"] = True
                resolved_dict["user_overridden"] = True
                resolved_dict["override_reason"] = "diagnostics_confirm_selection"
                resolved_dict["resolver_finalized"] = True
                resolved_dict = canonicalize_legacy_result_dict(
                    resolved_dict,
                    field_id=field_id,
                    resolver_finalized=True,
                )
            resolved_by_field[field_id] = resolved_dict

        for field_id, resolved_dict in resolved_by_field.items():
            self._apply_resolved_field_result_to_row(
                row,
                field_id,
                resolved_dict,
                pending_reason="diagnostics_confirm_selection",
                mark_pending=False,
            )

        if resolved_by_field:
            self._after_diagnostics_confirm_batch(row)

    def _confirmed_dict_from_selected(
        self,
        row: int,
        selected_by_field: dict[str, Any],
    ) -> dict[str, Any]:
        confirmed: dict[str, Any] = {}

        def _parse_confirmed_amount(raw: Any) -> Decimal | None:
            if raw is None:
                return None
            if isinstance(raw, Decimal):
                return raw if raw > Decimal("0.00") else None
            dec = normalize_amount_decimal(str(raw).strip())
            if dec is not None and dec > Decimal("0.00"):
                return dec
            try:
                parsed = amount_to_decimal(raw)
                return parsed if parsed > Decimal("0.00") else None
            except (TypeError, ValueError):
                return None

        raw_amt = selected_by_field.get("amount")
        if raw_amt is not None and str(raw_amt).strip():
            dec = _parse_confirmed_amount(raw_amt)
            if dec is not None:
                confirmed["amount"] = dec
        if "amount" not in confirmed:
            cell_amt = self._cell_text(row, PaymentColumn.AMOUNT).strip()
            if cell_amt and cell_amt != "?":
                dec = _parse_confirmed_amount(cell_amt)
                if dec is not None:
                    confirmed["amount"] = dec
        inv = str(selected_by_field.get("invoice_number") or "").strip()
        if not inv:
            inv = self._get_row_invoice_number(row)
        if inv:
            confirmed["invoice_number"] = inv
        row_snap = self._get_row_invoice_diagnostics_snapshot(row)
        cust_res = self._ident_field_result_snapshot_for_row(row, "customer_number")
        none_mode = _customer_number_none_mode_from_parts(
            snap=row_snap if isinstance(row_snap, dict) else None,
            customer_result=cust_res if isinstance(cust_res, dict) else None,
        )
        raw_cust = selected_by_field.get("customer_number")
        if none_mode or is_customer_absent_pick(raw_cust if isinstance(raw_cust, dict) else None):
            pass
        elif isinstance(raw_cust, str) and raw_cust.strip():
            confirmed["customer_number"] = raw_cust.strip()
        elif not none_mode:
            cust = self._cell_text(row, PaymentColumn.CUSTOMER_CODE).strip()
            if cust and cust != "?":
                confirmed["customer_number"] = cust

        vat = str(selected_by_field.get("vat_number") or "").strip()
        if vat:
            confirmed["vat_number"] = vat
        kvk = str(selected_by_field.get("kvk_number") or "").strip()
        if kvk:
            confirmed["kvk_number"] = kvk
        dom = str(selected_by_field.get("email_domain") or "").strip()
        if dom:
            confirmed["email_domain"] = dom
        return confirmed

    def _save_profile_from_row(
        self,
        row: int,
        selected_by_field: dict[str, Any],
        *,
        message_parent: QWidget | None = None,
    ) -> None:
        """Sla profiel op via bestaande confirm_invoice_fields-pipeline."""
        msg_parent = message_parent or self
        with self._undo_batch(source="profile_save"):
            self._confirm_selected_fields_for_row_impl(row, selected_by_field)
            if not self._row_can_profile_confirm(row, allow_profile_update=True):
                reason = self._row_profile_block_reason(row, allow_profile_update=True)
                QMessageBox.warning(
                    msg_parent,
                    tr("dialog.profile.save_title"),
                    self._profile_block_tooltip(reason or ""),
                )
                return
            source_file = self._resolve_row_source_file(row)
            if not source_file:
                QMessageBox.warning(
                    msg_parent,
                    tr("dialog.profile.save_title"),
                    tr("dialog.profile.pdf_not_found"),
                )
                return
            snap = self._get_row_invoice_diagnostics_snapshot(row)
            if not isinstance(snap, dict):
                snap = self._minimal_diagnostics_snapshot_from_row(row)
            supplier = self._cell_text(row, PaymentColumn.SUPPLIER).strip()
            try:
                raw_text = extract_text_strict(source_file)
            except Exception as exc:
                QMessageBox.warning(
                    msg_parent,
                    tr("dialog.profile.save_title"),
                    tr("dialog.profile.pdf_read_failed", detail=exc),
                )
                return
            confirmed = self._confirmed_dict_from_selected(row, selected_by_field)
            db = SupplierDB(path=self._supplier_db_path())
            amt_snap = self._amount_result_snapshot_for_row(row)
            inv_snap = self._ident_field_result_snapshot_for_row(row, "invoice_number")
            cust_snap = self._ident_field_result_snapshot_for_row(row, "customer_number")
            iban_snap = self._field_result_snapshot_for_row(row, "iban")
            result = confirm_invoice_fields(
                raw_text=raw_text,
                source_file=source_file,
                supplier_name=supplier,
                confirmed=confirmed,
                db=db,
                save_profile=True,
                iban=self._cell_text(row, PaymentColumn.IBAN).strip() or None,
                amount_result=amt_snap if isinstance(amt_snap, dict) else None,
                invoice_number_result=inv_snap if isinstance(inv_snap, dict) else None,
                customer_number_result=cust_snap if isinstance(cust_snap, dict) else None,
                iban_result=iban_snap if isinstance(iban_snap, dict) else None,
                post_resolve_snapshot=snap if isinstance(snap, dict) else None,
            )
            self._apply_profile_confirm_to_row_impl(
                row,
                result.confirmed,
                profile_saved=result.saved,
                learned_profile=result.profile,
            )
            self._mark_row_pending_engine_update(row, "profile_confirmed")
            self._refresh_export_batch_status_label()
            if result.saved:
                self._rematch_after_profile_saved(row)
            QMessageBox.information(msg_parent, tr("dialog.profile.save_title"), result.message)

    def _row_credit_profile_block_reason(self, row: int) -> str | None:
        credit = self._credit_note_for_row(row)
        snap = self._get_row_invoice_diagnostics_snapshot(row)
        if not isinstance(snap, dict):
            snap = self._minimal_diagnostics_snapshot_from_row(row)
        if credit is not None and str(credit.get("type") or "") == "credit_note":
            snap = dict(snap)
            snap["type"] = "credit_note"
        supplier_key = supplier_key_for_matched_invoice(credit) if isinstance(credit, dict) else None
        gate_snap = dict(snap)
        ms = self._match_status_for_profile_gate(row, snap)
        if ms:
            gate_snap["match_status"] = ms
        return credit_profile_learning_block_reason(
            gate_snap,
            source_file=self._resolve_row_source_file(row),
            supplier_key=supplier_key,
        )

    def _row_can_credit_profile_save(self, row: int) -> bool:
        return self._row_credit_profile_block_reason(row) is None

    def _save_credit_profile_from_row(
        self,
        row: int,
        selected_by_field: dict[str, Any],
    ) -> None:
        """Sla creditprofiel op via expliciete user action (diagnostics)."""
        self._confirm_selected_fields_for_row(row, selected_by_field)
        if not self._row_can_credit_profile_save(row):
            reason = self._row_credit_profile_block_reason(row)
            QMessageBox.warning(
                self,
                tr("dialog.profile.credit_save_title"),
                self._profile_block_tooltip(reason or ""),
            )
            return
        credit = self._credit_note_for_row(row)
        if not isinstance(credit, dict):
            QMessageBox.warning(
                self,
                tr("dialog.profile.credit_save_title"),
                tr("dialog.profile.credit_not_found_detail"),
            )
            return
        supplier_key = supplier_key_for_matched_invoice(credit)
        if not supplier_key:
            QMessageBox.warning(
                self,
                tr("dialog.profile.credit_save_title"),
                tr("dialog.profile.supplier_key_missing_detail"),
            )
            return
        source_file = self._resolve_row_source_file(row)
        if not source_file:
            QMessageBox.warning(
                self,
                tr("dialog.profile.credit_save_title"),
                tr("dialog.profile.pdf_not_found"),
            )
            return
        confirmed = self._confirmed_dict_from_selected(row, selected_by_field)
        try:
            raw_text = extract_text_strict(source_file)
        except Exception as exc:
            QMessageBox.warning(
                self,
                tr("dialog.profile.credit_save_title"),
                tr("dialog.profile.pdf_read_failed", detail=exc),
            )
            return
        db = SupplierDB(path=self._supplier_db_path())
        amt_snap = self._amount_result_snapshot_for_row(row)
        inv_snap = self._ident_field_result_snapshot_for_row(row, "invoice_number")
        result = confirm_credit_profile_fields(
            raw_text=raw_text,
            source_file=source_file,
            supplier_key=supplier_key,
            confirmed=confirmed,
            db=db,
            save_profile=True,
            amount_result=amt_snap if isinstance(amt_snap, dict) else None,
            invoice_number_result=inv_snap if isinstance(inv_snap, dict) else None,
            explicit_user_action=True,
        )
        if result.saved and isinstance(result.profile, dict):
            credit["credit_profile"] = result.profile
            credit["credit_profile_user_override"] = True
            credit["supplier_key"] = supplier_key
        self._mark_row_pending_engine_update(row, "credit_profile_saved")
        self._refresh_export_batch_status_label()
        QMessageBox.information(self, tr("dialog.profile.credit_save_title"), result.message)

    def _apply_resolved_field_result_to_row(
        self,
        row: int,
        field_id: FieldId,
        resolved_dict: dict[str, Any],
        *,
        pending_reason: str,
        mark_pending: bool = True,
    ) -> None:
        sup_it = self._table.item(row, PaymentColumn.SUPPLIER)
        base = self._get_row_invoice_diagnostics_snapshot(row)
        patched = dict(base) if isinstance(base, dict) else self._minimal_diagnostics_snapshot_from_row(row)
        apply_resolved_field_result(patched, field_id, resolved_dict)
        result_key = FIELD_REVIEW_SPECS[field_id].result_snapshot_key
        field_snap_raw = patched.get(result_key)
        if not isinstance(field_snap_raw, dict):
            # #region agent log (debug mode)
            _dbg_log_3d66a1(
                hypothesis_id="H2",
                location="main_window.py:_apply_resolved_field_result_to_row:early_return",
                message="missing field_snap_raw",
                data={"row": row, "field_id": field_id, "result_key": result_key},
            )
            # #endregion
            return
        field_snap = canonicalize_legacy_result_dict(field_snap_raw, field_id=field_id)

        with self._guard_reentrant_field_apply():
            if field_id == "amount":
                amt_it = self._table.item(row, PaymentColumn.AMOUNT)
                if not amt_it:
                    return
                raw_v = field_snap.get("selected_value")
                dec: Decimal | None = None
                if raw_v is not None:
                    try:
                        dec = amount_to_decimal(str(raw_v))
                    except (TypeError, ValueError):
                        dec = None
                if dec is not None and dec > Decimal("0.00"):
                    val_xml = format_eur_xml(dec)
                    amt_it.setText(_format_amount_nl(dec))
                    amt_it.setData(Qt.ItemDataRole.UserRole, val_xml)
                else:
                    amt_it.setText("?")
                    amt_it.setData(Qt.ItemDataRole.UserRole, None)
                amt_it.setData(_ROW_AMOUNT_RESULT_ROLE, deepcopy(field_snap))
                src = str(field_snap.get("source") or "").strip().lower()
                if src == "profile":
                    amt_it.setToolTip(tr("tooltip.amount.profile_confirmed"))
                elif src in {"manual", "user_picked", "user", "picked"}:
                    amt_it.setToolTip(tr("tooltip.amount.manual_pick"))
                else:
                    amt_it.setToolTip("")
                err_prev = self._table.item(row, PaymentColumn.ERROR)
                prev_trace = err_prev.data(_ROW_DECISION_TRACE_ROLE) if err_prev else None
                new_trace = _merge_decision_trace_parsed_amount(
                    prev_trace if isinstance(prev_trace, dict) else None,
                    field_snap,
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
                        input_fields={"row_id": self._row_id(row), "value": field_snap.get("selected_value")},
                    ),
                )
                new_err.setToolTip(self._compose_error_tooltip(error_msg="", decision_trace=new_trace))
                self._table.setItem(row, PaymentColumn.ERROR, new_err)
            elif field_id == "iban":
                iban_it = self._table.item(row, PaymentColumn.IBAN)
                if iban_it:
                    iban_val = str(patched.get("iban") or "").strip()
                    iban_it.setText(iban_val)
                    iban_it.setData(_ROW_IBAN_RESULT_ROLE, deepcopy(field_snap))
            elif field_id == "customer_number":
                cust_it = self._table.item(row, PaymentColumn.CUSTOMER_CODE)
                if cust_it:
                    disp = _ident_field_display_from_inv(patched, "customer_number")
                    cust_it.setText(disp)
                    cust_it.setData(_ROW_CUSTOMER_NUMBER_RESULT_ROLE, deepcopy(field_snap))
                    # #region agent log (debug mode)
                    _dbg_log_3d66a1(
                        hypothesis_id="H2",
                        location="main_window.py:_apply_resolved_field_result_to_row:customer",
                        message="customer cell updated",
                        data={
                            "row": row,
                            "display": disp,
                            "result_source": str(field_snap.get("source") or ""),
                            "absence_state": str(field_snap.get("absence_state") or ""),
                            "scalar_on_patched": str(patched.get("customer_number") or ""),
                            "none_active": customer_number_is_absent_or_none(patched),
                        },
                    )
                    # #endregion
                else:
                    # #region agent log (debug mode)
                    _dbg_log_3d66a1(
                        hypothesis_id="H2",
                        location="main_window.py:_apply_resolved_field_result_to_row:no_cust_it",
                        message="customer_code cell item missing",
                        data={"row": row},
                    )
                    # #endregion
            elif field_id == "invoice_number":
                if sup_it:
                    sup_it.setData(_ROW_INVOICE_META_ROLE, str(patched.get("invoice_number") or ""))
                    sup_it.setData(_ROW_INVOICE_NUMBER_RESULT_ROLE, deepcopy(field_snap))

            if field_id in ("invoice_number", "customer_number"):
                desc_it = self._table.item(row, PaymentColumn.DESCRIPTION)
                if desc_it:
                    desc_it.setText(_remittance_display_from_inv(patched))

            if sup_it:
                sup_it.setData(_ROW_INVOICE_DIAGNOSTICS_ROLE, patched)

        if field_id == "iban":
            iban_val = str(patched.get("iban") or "").strip()
            if iban_val:
                self._rematch_row_supplier_on_iban(row, iban_val)
        if mark_pending:
            self._after_field_user_change(row, field_id, pending_reason)

    def _sync_iban_field_result_and_row_ui(
        self,
        row: int,
        cand: dict[str, Any],
    ) -> None:
        self._resolve_and_apply_field_candidate(
            row,
            "iban",
            cand,
            pending_reason=FIELD_REVIEW_SPECS["iban"].pick_pending_reason,
        )

    def _rematch_row_supplier_on_iban(self, row: int, iban: str) -> None:
        """Lichte rematch na IBAN-keuze; overschrijft tabel-IBAN niet met DB-IBAN."""
        snap = self._get_row_invoice_diagnostics_snapshot(row)
        if not isinstance(snap, dict):
            snap = self._minimal_diagnostics_snapshot_from_row(row)
        iban_clean = clean_iban(iban)
        if not iban_clean:
            return
        try:
            cust_res = self._ident_field_result_snapshot_for_row(row, "customer_number")
            probe = dict(snap)
            if isinstance(cust_res, dict):
                probe["customer_number_result"] = cust_res
            cell_cust = self._cell_text(row, PaymentColumn.CUSTOMER_CODE).strip()
            cust_for_match = customer_number_authoritative_value(
                probe,
                scalar_fallback=cell_cust if cell_cust and cell_cust != "?" else None,
            )
            db = SupplierDB(path=self._supplier_db_path())
            supplier, match_info = db.find_supplier_scored(
                snap.get("supplier_hint") or self._cell_text(row, PaymentColumn.SUPPLIER),
                iban_clean,
                cust_for_match,
                vat_number=snap.get("vat_number"),
                kvk_number=snap.get("kvk_number"),
                email_domain=snap.get("email_domain"),
            )
        except Exception:
            return

        patched = dict(snap)
        current_iban_result = self._field_result_snapshot_for_row(row, "iban") or patched.get("iban_result") or {}
        apply_resolved_field_result(patched, "iban", current_iban_result)
        patched["match_info"] = match_info
        core_matches = _db_core_matches(match_info)
        patched["db_core_matches"] = core_matches
        patched["db_core_match_count"] = len(core_matches)

        iban_mismatch = False
        if supplier:
            sup_iban = clean_iban(str(supplier.get("iban") or ""))
            if sup_iban and iban_clean and sup_iban != iban_clean:
                iban_mismatch = True
            patched["supplier_name"] = str(supplier.get("name") or patched.get("supplier_name") or "")
        patched["iban_mismatch"] = iban_mismatch

        diag_it = self._table.item(row, PaymentColumn.SUPPLIER)
        if diag_it:
            diag_it.setData(_ROW_INVOICE_DIAGNOSTICS_ROLE, patched)

        if supplier and str(supplier.get("name") or "").strip():
            sup_it = self._table.item(row, PaymentColumn.SUPPLIER)
            if sup_it and match_info.get("iban_match"):
                prev_suppress = self._suppress_table_item_changed
                blocked = self._table.blockSignals(True)
                self._suppress_table_item_changed = True
                try:
                    sup_it.setText(str(supplier["name"]))
                finally:
                    self._suppress_table_item_changed = prev_suppress
                    self._table.blockSignals(blocked)

        core_info = _core_matches_text(patched)
        complete_info = _matches_completeness_text(patched)
        sup_it = self._table.item(row, PaymentColumn.SUPPLIER)
        if sup_it:
            sup_it.setToolTip(f"{core_info} | {complete_info}")
        core_it = self._table.item(row, PaymentColumn.CORE_MATCHES)
        if core_it:
            core_it.setText(core_info)
        complete_it = self._table.item(row, PaymentColumn.MATCH_COMPLETE)
        if complete_it:
            complete_it.setText(complete_info)

    def _sync_ident_field_result_and_row_ui(
        self,
        row: int,
        field_id: FieldId,
        cand: dict[str, Any],
    ) -> None:
        if field_id not in ("invoice_number", "customer_number"):
            return
        self._resolve_and_apply_field_candidate(
            row,
            field_id,
            cand,
            pending_reason=FIELD_REVIEW_SPECS[field_id].pick_pending_reason,
        )

    def _patch_row_invoice_diagnostics_field(
        self, row: int, field_id: FieldId, field_snap: dict[str, Any]
    ) -> None:
        if field_id not in FIELD_REVIEW_SPECS:
            return
        diag_it = self._table.item(row, PaymentColumn.SUPPLIER)
        if not diag_it:
            return
        base = diag_it.data(_ROW_INVOICE_DIAGNOSTICS_ROLE)
        if isinstance(base, dict):
            patched = dict(base)
        else:
            patched = self._minimal_diagnostics_snapshot_from_row(row)
        apply_resolved_field_result(patched, field_id, field_snap)
        diag_it.setData(_ROW_INVOICE_DIAGNOSTICS_ROLE, patched)

    def _patch_row_invoice_diagnostics_amount_result(
        self, row: int, amount_snap: dict[str, Any]
    ) -> None:
        self._patch_row_invoice_diagnostics_field(row, "amount", amount_snap)

    def _after_field_user_change(self, row: int, field_id: FieldId, reason: str) -> None:
        self._mark_row_pending_engine_update(row, reason)
        self._refresh_export_batch_status_label()
        self._refresh_profile_button_state()
        self._apply_row_colors()
        self._update_row_render_hash(row)

    def _after_diagnostics_confirm_batch(self, row: int) -> None:
        """Single engine/color refresh after multi-field diagnostics confirm."""
        self._mark_row_pending_engine_update(row, "diagnostics_confirm_selection")
        self._refresh_export_batch_status_label()
        self._refresh_profile_button_state()
        self._apply_row_colors()
        self._update_row_render_hash(row)

    def _get_row_invoice_diagnostics_snapshot(self, row: int) -> dict | None:
        it = self._table.item(row, PaymentColumn.SUPPLIER)
        if not it:
            return None
        v = it.data(_ROW_INVOICE_DIAGNOSTICS_ROLE)
        return v if isinstance(v, dict) else None

    def _invoice_diagnostics_snapshot_for_display(self, row: int) -> tuple[dict, bool]:
        raw = self._get_row_invoice_diagnostics_snapshot(row)
        limited = raw is None
        snap = (
            deepcopy(raw)
            if isinstance(raw, dict)
            else self._minimal_diagnostics_snapshot_from_row(row)
        )
        for field_id in REVIEW_FIELD_IDS:
            live = self._field_result_snapshot_for_row(row, field_id)
            if isinstance(live, dict):
                snap = overlay_field_result(snap, field_id, live)
        return snap, limited

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

    _PROFILE_BLOCK_TOOLTIP_KEYS: dict[str, str] = {
        "no_snapshot": "dialog.profile.block.no_snapshot",
        "match_not_eligible": "dialog.profile.block.match_not_eligible",
        "amount_unresolved": "dialog.profile.block.amount_unresolved",
        "already_profile": "dialog.profile.block.already_profile",
        "no_source_file": "dialog.profile.block.no_source_file",
        "pdf_not_found": "dialog.profile.block.pdf_not_found",
    }

    def _profile_block_tooltip(self, reason: str) -> str:
        key = self._PROFILE_BLOCK_TOOLTIP_KEYS.get(reason)
        if key:
            return tr(key)
        return reason or tr("dialog.profile.unavailable")

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

    def _row_profile_block_reason(self, row: int, *, allow_profile_update: bool = False) -> str | None:
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
            allow_profile_update=allow_profile_update,
        )

    def _row_can_profile_confirm(self, row: int, *, allow_profile_update: bool = False) -> bool:
        return self._row_profile_block_reason(row, allow_profile_update=allow_profile_update) is None

    def _rematch_after_profile_saved(self, row: int) -> None:
        """Her-match batch zodat gewijzigd extractieprofiel direct uit suppliers.json geldt."""
        self._cancel_pending_engine_updates()
        self._clear_undo_stack()
        matched = self._rematch_with_document_type_overrides()
        if matched is None:
            return
        self._matched_invoices = matched
        self._engine_cache.invalidate("profile_saved")
        doc_id = self._document_id_for_table_row(row)
        focus = {doc_id} if doc_id else None
        self._rerun_settlement_engine(focus_doc_ids=focus, clear_undo=True)

    def _refresh_profile_button_state(self) -> None:
        btn = getattr(self, "_btn_create_profile", None)
        if btn is None:
            return
        rows = self._selected_table_rows()
        if len(rows) != 1:
            btn.setEnabled(False)
            btn.setToolTip(tr("toolbar.create_profile_select_one"))
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
            tip_parts = [tr("toolbar.create_profile_tip_confirm")]
            if not self._row_amount_resolved(row):
                tip_parts.insert(0, tr("toolbar.create_profile_tip_amount"))
            if missing:
                btn.setText(tr("toolbar.create_profile_complete"))
                tip_parts.append(tr("toolbar.create_profile_tip_missing", fields=", ".join(missing)))
            else:
                btn.setText(tr("toolbar.create_profile"))
            btn.setToolTip("\n".join(tip_parts))
            return
        btn.setEnabled(False)
        extra = self._profile_block_tooltip(reason)
        btn.setToolTip(tr("toolbar.create_profile_blocked", reason=extra))

    def _on_create_profile_for_selection(self) -> None:
        rows = self._selected_table_rows()
        if not rows:
            QMessageBox.information(
                self,
                tr("dialog.profile.create_title"),
                tr("dialog.profile.select_one_row"),
            )
            return
        if len(rows) > 1:
            QMessageBox.information(
                self,
                tr("dialog.profile.create_title"),
                tr("dialog.profile.select_exactly_one"),
            )
            return
        row = rows[0]
        reason = self._row_profile_block_reason(row)
        if reason is not None:
            QMessageBox.warning(
                self,
                tr("dialog.profile.create_title"),
                self._profile_block_tooltip(reason),
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
        cust_res = self._ident_field_result_snapshot_for_row(row, "customer_number")
        probe = dict(snap) if isinstance(snap, dict) else {}
        if isinstance(cust_res, dict):
            probe["customer_number_result"] = cust_res
        cell_cust = self._cell_text(row, PaymentColumn.CUSTOMER_CODE).strip()
        cust_ph = customer_number_authoritative_value(
            probe,
            scalar_fallback=cell_cust if cell_cust and cell_cust != "?" else None,
        ) or ""
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
        with self._undo_batch(source="profile_confirm_dialog"):
            self._apply_profile_confirm_to_row_impl(
                row,
                result_confirmed,
                profile_saved=profile_saved,
                learned_profile=learned_profile,
            )

    def _apply_profile_confirm_to_row_impl(
        self,
        row: int,
        result_confirmed: dict[str, Any],
        *,
        profile_saved: bool,
        learned_profile: dict[str, Any] | None,
    ) -> None:
        amt_xml = confirmed_amount_xml(result_confirmed)
        if amt_xml:
            self._resolve_and_apply_field_candidate(
                row,
                "amount",
                {
                    "value": amt_xml,
                    "source": "profile",
                    "confidence": 95,
                    "context": "profile_confirm_dialog",
                },
                pending_reason="profile_confirmed_amount",
                mark_pending=False,
            )

        cust = str(result_confirmed.get("customer_number") or "").strip()
        if cust:
            self._resolve_and_apply_field_candidate(
                row,
                "customer_number",
                {
                    "value": cust,
                    "source": "profile",
                    "confidence": 95,
                    "context": "profile_confirm_dialog",
                },
                pending_reason="profile_confirmed_customer_number",
                mark_pending=False,
            )

        inv_no = str(result_confirmed.get("invoice_number") or "").strip()
        if inv_no:
            self._resolve_and_apply_field_candidate(
                row,
                "invoice_number",
                {
                    "value": inv_no,
                    "source": "profile",
                    "confidence": 95,
                    "context": "profile_confirm_dialog",
                },
                pending_reason="profile_confirmed_invoice_number",
                mark_pending=False,
            )

        if profile_saved and learned_profile is not None:
            if customer_number_mode_from_profile(learned_profile) == CUSTOMER_NUMBER_MODE_NONE:
                self._apply_customer_absent_pick_to_row(
                    row,
                    pending_reason="profile_saved_none_mode",
                    mark_pending=False,
                )
            snap = self._get_row_invoice_diagnostics_snapshot(row)
            if isinstance(snap, dict):
                patched = deepcopy(snap)
                patched["extraction_source"] = "profile"
                patched["profile_fields"] = [
                    k for k in ("amount", "invoice_number", "customer_number") if k in learned_profile
                ]
                if customer_number_mode_from_profile(learned_profile) == CUSTOMER_NUMBER_MODE_NONE:
                    patched["extraction_profile"] = dict(learned_profile)
                sup_it = self._table.item(row, PaymentColumn.SUPPLIER)
                if sup_it:
                    sup_it.setData(_ROW_INVOICE_DIAGNOSTICS_ROLE, patched)

    def _on_profile_confirm_row(self, row: int) -> None:
        if row < 0 or row >= self._table.rowCount() or self._is_row_blank(row):
            return
        if not self._row_can_profile_confirm(row):
            reason = self._row_profile_block_reason(row)
            QMessageBox.warning(
                self,
                tr("dialog.profile_confirm.title"),
                self._profile_block_tooltip(reason or ""),
            )
            return
        source_file = self._resolve_row_source_file(row)
        if not source_file:
            QMessageBox.warning(
                self,
                tr("dialog.profile_confirm.title"),
                tr("dialog.profile.pdf_not_found"),
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
                tr("dialog.profile_confirm.title"),
                tr("dialog.profile.pdf_read_failed", detail=exc),
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
            post_resolve_snapshot=snap if isinstance(snap, dict) else None,
        )
        self._apply_profile_confirm_to_row(
            row,
            result.confirmed,
            profile_saved=result.saved,
            learned_profile=result.profile,
        )
        self._mark_row_pending_engine_update(row, "profile_confirmed")
        self._refresh_export_batch_status_label()
        if result.saved:
            self._rematch_after_profile_saved(row)
        QMessageBox.information(self, tr("dialog.profile_confirm.title"), result.message)

    def _minimal_diagnostics_snapshot_from_row(self, row: int) -> dict:
        snap: dict[str, Any] = {}
        pdf = self._cell_text(row, PaymentColumn.PDF).strip()
        if pdf and pdf != "—":
            snap["source_file"] = pdf
        supplier = self._cell_text(row, PaymentColumn.SUPPLIER).strip()
        if supplier:
            snap["supplier_name"] = supplier
        iban = self._cell_text(row, PaymentColumn.IBAN).strip()
        if iban and iban != "?":
            snap["iban"] = iban
        ir = self._field_result_snapshot_for_row(row, "iban")
        if isinstance(ir, dict):
            snap["iban_result"] = ir
        cust_res = self._ident_field_result_snapshot_for_row(row, "customer_number")
        if isinstance(cust_res, dict):
            snap["customer_number_result"] = deepcopy(cust_res)
        cell_cust = self._cell_text(row, PaymentColumn.CUSTOMER_CODE).strip()
        cell_fb = cell_cust if cell_cust and cell_cust != "?" else None
        probe = dict(snap)
        auth = customer_number_authoritative_value(probe, scalar_fallback=cell_fb)
        if customer_number_is_absent_or_none(probe):
            snap.pop("customer_number", None)
        elif auth:
            snap["customer_number"] = auth
        else:
            snap.pop("customer_number", None)
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

    def _set_document_type_from_row(self, row: int, target_type: str) -> bool:
        doc_id = self._document_id_for_table_row(row)
        inv = self._invoice_for_document_id(doc_id) if doc_id else None
        if inv is None:
            try:
                p_row = self._payment_dict_from_row(row)
            except ValueError:
                return False
            inv = self._match_inv_for_payment(self._matched_invoices, p_row)
        if inv is None:
            return False
        if target_type not in ("invoice", "credit_note"):
            return False
        cid = document_id({"raw": inv})
        self._document_type_override_store.upsert_override(
            self._batch_key(),
            make_document_type_override(cid, target_type),  # type: ignore[arg-type]
            history_event={
                "event": "user_set_document_type",
                "document_id": cid,
                "document_type": target_type,
                "at": now_utc_iso(),
            },
        )
        matched = self._rematch_with_document_type_overrides()
        if matched is None:
            QMessageBox.warning(
                self,
                tr("diagnostics.section.general"),
                tr("dialog.batch_load.no_cache.message"),
            )
            return False
        self._matched_invoices = matched
        self._engine_cache.invalidate("document_type_override")
        self._rerun_settlement_engine(focus_doc_ids={cid})
        return True

    def _run_diagnostics_confirm(
        self,
        row_id: str,
        selected: dict[str, Any],
        refresh: Callable[[dict[str, Any]], dict | None],
    ) -> dict | None:
        if self._diagnostics_action_busy:
            return None
        self._diagnostics_action_busy = True
        try:
            row = self._find_row_by_id(row_id)
            if row is None:
                QMessageBox.warning(
                    self,
                    tr("diagnostics.section.general"),
                    tr("dialog.profile.select_one_row"),
                )
                return None
            self._confirm_selected_fields_for_row(row, selected)
            self._set_status(tr("status.diagnostics_selection_confirmed"))
            return refresh(selected)
        except Exception as exc:
            logger.exception("Diagnostics bevestigen mislukt (rij %s)", row_id)
            QMessageBox.critical(
                self,
                tr("diagnostics.section.general"),
                tr("dialog.diagnostics.confirm_failed", detail=exc),
            )
            return None
        finally:
            self._diagnostics_action_busy = False

    def _run_diagnostics_save_profile(
        self,
        row_id: str,
        selected: dict[str, Any],
        dlg: QWidget,
        refresh: Callable[[dict[str, Any]], dict | None],
    ) -> dict | None:
        if self._diagnostics_action_busy:
            return None
        self._diagnostics_action_busy = True
        try:
            row = self._find_row_by_id(row_id)
            if row is None:
                QMessageBox.warning(
                    dlg,
                    tr("dialog.profile.save_title"),
                    tr("dialog.profile.select_one_row"),
                )
                return None
            self._save_profile_from_row(row, selected, message_parent=dlg)
            return refresh(selected)
        except Exception as exc:
            logger.exception("Profiel opslaan mislukt (rij %s)", row_id)
            QMessageBox.critical(
                dlg,
                tr("dialog.profile.save_title"),
                tr("dialog.profile.save_failed", detail=exc),
            )
            return None
        finally:
            self._diagnostics_action_busy = False

    def _run_diagnostics_save_credit(
        self,
        row_id: str,
        selected: dict[str, Any],
        refresh: Callable[[dict[str, Any]], dict | None],
    ) -> dict | None:
        row = self._find_row_by_id(row_id)
        if row is None:
            return None
        self._save_credit_profile_from_row(row, selected)
        return refresh(selected)

    def _open_diagnostics_for_row(self, row: int) -> None:
        try:
            row_id = self._row_id(row)

            def _resolve_row() -> int | None:
                return self._find_row_by_id(row_id)

            def _refresh_diag(_selected: dict[str, Any]) -> dict | None:
                r = _resolve_row()
                if r is None:
                    return None
                return self._build_diagnostics_for_row(r)[0]

            diag, limited = self._build_diagnostics_for_row(row)

            dlg = DiagnosticsDialog(
                diag,
                parent=self,
                on_confirm_selection=lambda selected: (
                    self._run_diagnostics_confirm(row_id, selected, _refresh_diag)
                ),
                on_save_profile=lambda selected: (
                    self._run_diagnostics_save_profile(row_id, selected, dlg, _refresh_diag)
                ),
                on_save_credit_profile=lambda selected: (
                    self._run_diagnostics_save_credit(row_id, selected, _refresh_diag)
                ),
                on_set_document_type=lambda target_type: (
                    _refresh_diag({})
                    if self._set_document_type_from_row(
                        _resolve_row() if _resolve_row() is not None else row,
                        target_type,
                    )
                    else None
                ),
                limited_snapshot=limited,
            )
            dlg.exec()
        except Exception as exc:
            logger.exception("Diagnostics openen mislukt (rij %s)", row)
            QMessageBox.warning(
                self,
                tr("diagnostics.section.general"),
                tr("dialog.diagnostics.open_failed", detail=exc),
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
        iban_result_snapshot: dict[str, Any] | None = None,
        decision: dict[str, Any] | None = None,
        row_id: str | None = None,
        invoice_diagnostics_snapshot: dict | None = None,
        settlement_badge: str = "",
        settlement_group_id: str = "",
        settlement_group_expandable: bool = False,
        settlement_status_engine: str = "",
        document_id_for_row: str = "",
    ) -> None:
        def _field_result_tooltip(result: dict[str, Any] | None) -> str:
            if not isinstance(result, dict):
                return ""
            parts: list[str] = []
            src = str(result.get("source") or "").strip()
            conf = result.get("confidence")
            if src:
                try:
                    c = int(conf or 0)
                except (TypeError, ValueError):
                    c = 0
                parts.append(f"source: {src} ({c}%)")
            ov = str(result.get("override_reason") or "").strip()
            if ov:
                parts.append(f"override: {ov}")
            trace = result.get("decision_trace")
            if isinstance(trace, list):
                for e in trace:
                    if isinstance(e, dict) and str(e.get("kind") or "") == "final":
                        fr = str(e.get("final_decision_reason") or "").strip()
                        if fr:
                            parts.append(f"final: {fr}")
                        break
            return "\n".join(parts).strip()

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
        doc_id_row = str(document_id_for_row or "").strip()
        if doc_id_row:
            sup_item.setData(_ROW_SETTLEMENT_DOC_ID_ROLE, doc_id_row)
        sup_item.setData(
            _ROW_ROW_ID_ROLE,
            row_id
            or _stable_payment_row_id(
                supplier=supplier,
                invoice_number=str(invoice_number_meta or ""),
                pdf=pdf_name,
            ),
        )
        # Keep original supplier name for safe rename during "Voeg toe / update".
        # This prevents update_supplier() from failing when the user corrected the name in the table.
        try:
            original_supplier = supplier.strip()
            if settlement_group_id and original_supplier[:1] in ("▶", "▼"):
                original_supplier = original_supplier[1:].strip()
            if original_supplier:
                sup_item.setData(_ROW_SUPPLIER_ORIGINAL_ROLE, original_supplier)
        except Exception:
            pass
        self._table.setItem(r, PaymentColumn.SUPPLIER, sup_item)
        iban_item = self._item_editable(iban)
        if isinstance(iban_result_snapshot, dict):
            iban_item.setData(_ROW_IBAN_RESULT_ROLE, deepcopy(iban_result_snapshot))
            if iban.strip() == "?":
                base = "Klik om een IBAN-kandidaat te kiezen."
                extra = _field_result_tooltip(iban_result_snapshot)
                iban_item.setToolTip(base + (("\n" + extra) if extra else ""))
        self._table.setItem(r, PaymentColumn.IBAN, iban_item)
        amt_item = self._item_amount(amount_display)
        if base_amount_incl is not None:
            amt_item.setData(_ROW_BASE_INCL_ROLE, format_eur_xml(base_amount_incl))
        if base_amount_excl is not None:
            amt_item.setData(_ROW_BASE_EXCL_ROLE, format_eur_xml(base_amount_excl))
        if isinstance(amount_result_snapshot, dict):
            # Keep export SSOT consistent with what the user sees: if the visible cell amount
            # differs from a non-user-selected snapshot (e.g. discount changed display),
            # promote the visible amount to a user_selected snapshot.
            snap = deepcopy(amount_result_snapshot)
            try:
                cell_dec = amount_to_decimal(amount_display)
            except Exception:
                cell_dec = None
            if cell_dec is not None and not snap.get("user_selected"):
                snap_dec = None
                for key in ("value", "selected_amount"):
                    raw = snap.get(key)
                    if raw is None or not str(raw).strip():
                        continue
                    try:
                        snap_dec = amount_to_decimal(str(raw))
                    except Exception:
                        snap_dec = None
                    break
                if snap_dec is not None and snap_dec != cell_dec:
                    promoted = format_eur_xml(cell_dec)
                    snap = {
                        "status": "confirmed",
                        "amount_status": "confirmed",
                        "user_selected": True,
                        "value": promoted,
                        "selected_amount": promoted,
                        "confidence": 100,
                        "source": "UI_DISPLAY_OVERRIDE",
                        "candidates": [],
                    }
            amt_item.setData(_ROW_AMOUNT_RESULT_ROLE, snap)
            base = "Klik om een voorgesteld bedrag te kiezen (PDF-parser)."
            extra = _field_result_tooltip(snap)
            amt_item.setToolTip(base + (("\n" + extra) if extra else ""))
        self._table.setItem(r, PaymentColumn.AMOUNT, amt_item)
        cust_item = self._item_editable(customer_code)
        if isinstance(customer_number_result_snapshot, dict):
            cust_item.setData(
                _ROW_CUSTOMER_NUMBER_RESULT_ROLE,
                deepcopy(customer_number_result_snapshot),
            )
            if customer_code.strip() == "?":
                base = "Klik om een klantnummer-kandidaat te kiezen."
                extra = _field_result_tooltip(customer_number_result_snapshot)
                cust_item.setToolTip(base + (("\n" + extra) if extra else ""))
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
            inv_it.setToolTip(tr("tooltip.invoice_date.manual"))
        elif invoice_date_source == "parsed":
            inv_it.setToolTip(tr("tooltip.invoice_date.from_pdf"))
        self._table.setItem(r, PaymentColumn.INVOICE_DATE, inv_it)

        ex_disp, ex_sort = self._table_date_display_and_sort(execution_date)
        ex_it = self._item_date_cell(ex_disp, ex_sort)
        ex_it.setData(_ROW_DATE_MODE_ROLE, date_mode)
        if date_mode == "manual":
            ex_it.setToolTip(tr("tooltip.execution_date.manual"))
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
        display_error_msg = _sanitize_table_error_message(error_msg)
        err_item = self._item_readonly(display_error_msg)
        if warning_raw:
            err_item.setData(_ROW_WARNING_RAW_ROLE, warning_raw)
        if decision_trace:
            err_item.setData(_ROW_DECISION_TRACE_ROLE, decision_trace)
        if isinstance(decision, dict):
            err_item.setData(_ROW_DECISION_ROLE, normalize_decision(decision))
        # Debug-proof: always provide a way to see full error text (and trace if present),
        # independent of any debug toggle.
        err_item.setToolTip(
            self._compose_error_tooltip(error_msg=display_error_msg, decision_trace=decision_trace)
        )
        self._table.setItem(r, PaymentColumn.ERROR, err_item)
        info_item = self._item_readonly("🔍")
        info_item.setToolTip(tr("status.diagnostics_tooltip"))
        self._table.setItem(r, PaymentColumn.INFO, info_item)
        sett_item = self._item_readonly(settlement_badge or "—")
        if settlement_group_id:
            sett_item.setData(_ROW_SETTLEMENT_GROUP_ID_ROLE, settlement_group_id)
            sett_item.setData(
                _ROW_SETTLEMENT_STATUS_ROLE,
                settlement_status_engine or settlement_badge,
            )
            if settlement_group_expandable:
                mark_group_header_row(self._table, r, settlement_group_id)
        self._table.setItem(r, PaymentColumn.SETTLEMENT, sett_item)
        self._set_row_decision(r, decision if isinstance(decision, dict) else self._missing_decision_payload(r))

    def _payment_stub_from_group(self, group: dict[str, Any]) -> dict[str, Any]:
        return payment_stub_from_group(group)

    def _settlement_group_for_row(self, row: int) -> dict[str, Any] | None:
        if self._engine_result is None:
            return None
        it = self._table.item(row, PaymentColumn.SETTLEMENT)
        if not it:
            return None
        gid = it.data(_ROW_SETTLEMENT_GROUP_ID_ROLE)
        if not gid:
            return None
        gid_s = str(gid)
        for g in self._engine_result.settlement_groups:
            if str(g.get("group_id") or "") == gid_s:
                return g
        return None

    def _settlement_inspector_lines(self, group: dict[str, Any]) -> list[str]:
        return settlement_inspector_lines(group)

    def _populate_table_from_settlement_groups(
        self,
        engine_result: EngineResult,
        invoices: list[dict],
    ) -> int:
        """Populate table from SSOT settlement_groups."""
        errors = review_documents_as_error_buckets(engine_result.review_documents)
        return self._populate_table_from_load(
            [],
            errors,
            invoices,
            engine_result=engine_result,
        )

    def _populate_table_from_load(
        self,
        payments: list[dict],
        errors: list[dict],
        invoices: list[dict],
        engine_result: EngineResult | None = None,
    ) -> int:
        hdr = self._table.horizontalHeader()
        hdr.blockSignals(True)
        prev_block = self._table.blockSignals(True)
        error_row_count = 0
        try:
            self._suppress_table_item_changed = True
            self._table.setSortingEnabled(False)
            self._table.setRowCount(0)
            self._decision_table_fingerprint = None
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
            if engine_result is not None:
                payment_iter = settlement_group_rows(engine_result)
            else:
                payment_iter = [(p, {}) for p in payments]
            for p, group in payment_iter:
                if engine_result is not None and group and _is_credit_only_group(group):
                    # Credit-only manual_review groups are shown once via review_documents.
                    continue
                amount_str = str(p.get("amount_display") or "").strip()
                if not amount_str:
                    amt = p.get("amount")
                    amount_str = _format_amount_nl(amt) if amt is not None else ""
                err_cell = _nl_payment_warning(p.get("warning"))
                disc = self._discount_for_payment(invoices, p)
                inv_match = self._match_inv_for_payment(invoices, p)
                inv_res_snap = None
                cust_res_snap = None
                if isinstance(inv_match, dict):
                    inv_cust, inv_meta, _inv_desc, inv_res_snap, cust_res_snap = (
                        self._row_ident_fields_from_inv(inv_match)
                    )
                else:
                    inv_cust, inv_meta = self._invoice_fields_for_payment(invoices, p)
                if engine_result is not None and group:
                    cust = str(group.get("customer_number") or p.get("customer_number") or inv_cust or "")
                    inv_meta = str(p.get("invoice_number") or inv_meta or "")
                    desc = str(group.get("description") or p.get("description") or "")
                else:
                    cust = inv_cust
                    desc = format_remittance_text(
                        cust if cust else None,
                        inv_meta if inv_meta else None,
                        p.get("description"),
                    )
                pdf = _pdf_basename_from_dict(p)
                wr = p.get("warning")
                term_trusted_raw = p.get("supplier_term_trusted")
                trusted: bool | None = bool(term_trusted_raw) if term_trusted_raw is not None else None
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
                    if isinstance(inv_match, dict)
                    and isinstance(inv_match.get("amount_result"), dict)
                    else None
                )
                iban_disp, iban_res_snap = _iban_field_from_inv(inv_match or {})
                if not iban_disp:
                    iban_disp = str(p.get("iban", ""))
                gid = str(group.get("group_id") or p.get("settlement_group_id") or "")
                expanded = gid in self._expanded_settlement_groups
                supplier_label = str(p.get("supplier_name", ""))
                group_expandable = False
                if engine_result is not None and group:
                    vm = vm_from_group(group)
                    group_expandable = settlement_group_is_expandable(vm, group=group)
                    supplier_label = header_supplier_label(
                        vm, expanded, expandable=group_expandable, group=group
                    )
                group_badge = ""
                group_status_engine = ""
                if engine_result is not None and group:
                    group_badge = settlement_badge_for_group(group)
                    group_status_engine = (
                        "detached"
                        if group_badge == settlement_badge_nl("detached")
                        else str(group.get("settlement_status") or "")
                    )
                self._append_table_row(
                    supplier_label,
                    iban_disp,
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
                    iban_result_snapshot=iban_res_snap,
                    decision=p.get("decision") if isinstance(p.get("decision"), dict) else None,
                    row_id=str(group.get("group_id") or "")
                    if engine_result is not None and group.get("group_id")
                    else _stable_payment_row_id(
                        supplier=str(p.get("supplier_name") or ""),
                        invoice_number=inv_meta,
                        pdf=pdf,
                    ),
                    invoice_diagnostics_snapshot=_diagnostics_snapshot_from_invoice(inv_match or {}),
                    settlement_badge=group_badge
                    or settlement_badge_nl(
                        str(group.get("settlement_status") or p.get("settlement_status") or "")
                        if group
                        else str(p.get("settlement_status") or "")
                    ),
                    settlement_group_id=gid,
                    settlement_group_expandable=group_expandable,
                    settlement_status_engine=group_status_engine,
                )
                if engine_result is not None and group and group_expandable:
                    self._append_settlement_breakdown_rows(group)
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
            hidden_review_ids = (
                self._settlement_review_hidden_doc_ids(engine_result)
                if engine_result is not None
                else set()
            )
            if hidden_review_ids:
                needs_review_invs = [
                    (inv, r)
                    for inv, r in needs_review_invs
                    if document_id({"raw": inv}) not in hidden_review_ids
                ]
                other_errors = [
                    (inv, r)
                    for inv, r in other_errors
                    if document_id({"raw": inv}) not in hidden_review_ids
                ]
            for inv, _reason in needs_review_invs:
                amount_str = _error_row_amount_str(inv)
                cust_r, inv_meta_r, desc_r, inv_res_r, cust_res_r = self._row_ident_fields_from_inv(inv)
                iban_disp_r, iban_res_r = _iban_field_from_inv(inv)
                pdf_r = _pdf_basename_from_dict(inv)
                inv_dr = str(inv.get("invoice_date") or "").strip()
                src_r = str(inv.get("invoice_date_source") or "missing")
                tr_r = inv.get("supplier_term_trusted")
                trusted_r = bool(tr_r) if isinstance(tr_r, bool) else False
                raw_term_r = int(inv.get("supplier_payment_term_days_raw") or 0)
                eff_r = raw_term_r if trusted_r else 0
                self._append_table_row(
                    _error_row_supplier(inv),
                    iban_disp_r,
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
                    iban_result_snapshot=iban_res_r,
                    decision=inv.get("decision") if isinstance(inv.get("decision"), dict) else None,
                    row_id=_stable_payment_row_id(
                        supplier=_error_row_supplier(inv),
                        invoice_number=inv_meta_r,
                        pdf=pdf_r,
                    ),
                    invoice_diagnostics_snapshot=_diagnostics_snapshot_from_invoice(inv),
                    document_id_for_row=document_id({"raw": inv}),
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
                    amount_str = _error_row_amount_str(inv)
                cust_e, inv_meta_e, desc_e, inv_res_e, cust_res_e = self._row_ident_fields_from_inv(inv)
                iban_disp_e, iban_res_e = _iban_field_from_inv(inv)
                pdf_e = _pdf_basename_from_dict(inv)
                inv_de = str(inv.get("invoice_date") or "").strip()
                src_e = str(inv.get("invoice_date_source") or "missing")
                base_err = _nl_error_reason(reason)
                sig_info = f"{_core_matches_text(inv)} | {_matches_completeness_text(inv)}"
                # #region agent log
                try:
                    import json as _json, time as _time
                    with open("/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-c00626.log", "a", encoding="utf-8") as _f:
                        _f.write(_json.dumps({"sessionId": "c00626", "hypothesisId": "A", "location": "main_window.py:_populate_table_from_load:other_errors", "message": "tr shadow check before term_hint", "data": {"tr_type": type(tr).__name__, "tr_value": str(tr)[:40], "reason": reason}, "timestamp": int(_time.time() * 1000), "runId": "pre-fix"}) + "\n")
                except Exception:
                    pass
                # #endregion
                self._append_table_row(
                    _error_row_supplier(inv),
                    iban_disp_e,
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
                    term_hint=tr("matching.display.term_unknown"),
                    date_mode="direct",
                    invoice_date_source=src_e,
                    amount_result_snapshot=ar_snap
                    if (reason in ("amount_ambiguous", "amount_uncertain") or ambiguous_ar)
                    else None,
                    invoice_number_result_snapshot=inv_res_e,
                    customer_number_result_snapshot=cust_res_e,
                    iban_result_snapshot=iban_res_e,
                    decision=inv.get("decision") if isinstance(inv.get("decision"), dict) else None,
                    row_id=_stable_payment_row_id(
                        supplier=_error_row_supplier(inv),
                        invoice_number=inv_meta_e,
                        pdf=pdf_e,
                    ),
                    invoice_diagnostics_snapshot=_diagnostics_snapshot_from_invoice(inv),
                    document_id_for_row=document_id({"raw": inv}),
                )
            self._auto_resize_columns_to_content()
            if engine_result is not None:
                # Settlement groups: keep header + child row order (sort breaks group cohesion).
                self._table.setSortingEnabled(False)
            else:
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
        if not self._is_loading_batch:
            self._sync_decision_store_from_table(force=True)
            self._apply_row_colors()
        self._apply_filter_to_table(self._filter_edit.text())
        self._refresh_export_batch_status_label()
        self._refresh_profile_button_state()

        return error_row_count

    def _on_reread_pdfs(self) -> None:
        self._invalidate_parsed_batch_cache()
        folder: Optional[Path] = self._selected_folder
        if folder is None or not folder.is_dir():
            raw = str(self._settings.get("last_invoice_dir") or "").strip()
            if raw:
                folder = resolve_settings_path(raw, base_dir=APP_BASE)
        if folder is None or not folder.is_dir():
            QMessageBox.warning(
                self,
                tr("toolbar.load_pdfs"),
                tr("dialog.pdfs.no_valid_folder"),
            )
            return
        self._selected_folder = folder
        self._payment_sources = [self._make_map_folder_source(folder)]
        self._load_payments_from_sources(parse_pdfs=True)

    def _on_reapply_discounts(self, rows: list[int] | None = None) -> None:
        target_rows = [r for r in (rows if rows is not None else self._selected_table_rows()) if r >= 0]
        if not target_rows:
            QMessageBox.information(
                self,
                tr("menu.context.apply_discount"),
                tr("dialog.discount.no_selection"),
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
            # Keep amount_result snapshot aligned with the new discounted amount so export
            # cannot fall back to an old parser snapshot.
            promoted = format_eur_xml(new_amt)
            amt_item.setData(
                _ROW_AMOUNT_RESULT_ROLE,
                {
                    "status": "confirmed",
                    "amount_status": "confirmed",
                    "user_selected": True,
                    "value": promoted,
                    "selected_amount": promoted,
                    "confidence": 100,
                    "source": "DISCOUNT_APPLIED",
                    "candidates": [],
                },
            )
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
        extra = tr("status.discount_no_excl_extra") if skipped and skipped == skipped_no_excl and updated == 0 else ""
        skipped_suffix = tr("status.discount_skipped_suffix", skipped=skipped) if skipped else ""
        self._set_status(tr("status.discount_applied", updated=updated, skipped=skipped_suffix, extra=extra))

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
            tr("matching.display.manual_row"),
            "",
            invoice_number_meta="",
            invoice_date="",
            execution_date=self._session_date.isoformat(),
            term_hint=tr("matching.display.term_unknown"),
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
        is_child = self._is_settlement_child_row(row)
        dec0 = self._decision_for_row(row)
        status = dec0.get("status")
        reason0 = str(dec0.get("reason_code") or "")
        menu = QMenu(self)
        if not is_child:
            if col == int(PaymentColumn.AMOUNT) and self._cell_text(row, PaymentColumn.AMOUNT).strip() == "?":
                snap = self._amount_result_snapshot_for_row(row)
                if picker_eligible(snap, field_id="amount"):
                    act_amt = menu.addAction(tr("menu.context.choose_amount"))
                    act_amt.triggered.connect(
                        lambda checked=False, r=row: self._show_field_candidate_menu(r, "amount")
                    )
            if col == int(PaymentColumn.CUSTOMER_CODE) and self._field_picker_eligible(
                row, "customer_number"
            ):
                act_cust = menu.addAction(tr("menu.context.choose_customer"))
                act_cust.triggered.connect(
                    lambda checked=False, r=row: self._show_field_candidate_menu(
                        r, "customer_number"
                    )
                )
            if col == int(PaymentColumn.IBAN) and self._field_picker_eligible(row, "iban"):
                act_iban = menu.addAction(tr("menu.context.choose_iban"))
                act_iban.triggered.connect(
                    lambda checked=False, r=row: self._show_field_candidate_menu(r, "iban")
                )
            if col in (int(PaymentColumn.DESCRIPTION), int(PaymentColumn.SUPPLIER)) and self._field_picker_eligible(
                row, "invoice_number"
            ):
                act_inv = menu.addAction(tr("menu.context.choose_invoice"))
                act_inv.triggered.connect(
                    lambda checked=False, r=row: self._show_field_candidate_menu(
                        r, "invoice_number"
                    )
                )
            if status == DECISION_NEEDS_REVIEW:
                action_confirm = menu.addAction(tr("menu.context.confirm_invoice"))
                action_confirm.triggered.connect(lambda: self._confirm_review_rows([row]))
                selected = self._selected_table_rows()
                review_selected = [
                    r for r in selected
                    if self._decision_for_row(r).get("status") == DECISION_NEEDS_REVIEW
                ]
                if len(review_selected) > 1:
                    action_all = menu.addAction(tr("menu.context.confirm_all_selected", count=len(review_selected)))
                    action_all.triggered.connect(lambda: self._confirm_review_rows(review_selected))
            if self._row_can_profile_confirm(row):
                action_profile = menu.addAction(tr("menu.context.confirm_profile"))
                action_profile.triggered.connect(lambda: self._on_profile_confirm_row(row))
            if status == DECISION_EXCLUDED and reason0 == REASON_USER_MARKED_ERROR:
                action_restore = menu.addAction(tr("menu.context.restore_ok"))
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
                    action_all_restore = menu.addAction(tr("menu.context.restore_all_selected", count=len(fout_selected)))
                    action_all_restore.triggered.connect(lambda: self._restore_rows_from_error(fout_selected))
            else:
                action_fout = menu.addAction(tr("menu.context.mark_error"))
                action_fout.triggered.connect(lambda: self._mark_rows_as_error([row]))
            if not (status == DECISION_EXCLUDED and reason0 == REASON_USER_MARKED_ERROR):
                menu.addAction(tr("menu.context.pay_direct")).triggered.connect(
                    lambda: self._apply_pay_direct_rows(self._selected_table_rows() or [row])
                )
                menu.addAction(tr("menu.context.pay_due")).triggered.connect(
                    lambda: self._apply_pay_due_rows(self._selected_table_rows() or [row])
                )
                menu.addAction(tr("menu.context.apply_discount")).triggered.connect(
                    lambda: self._on_reapply_discounts(self._selected_table_rows() or [row])
                )
        credit = self._credit_note_for_row(row)
        if credit is not None:
            menu.addSeparator()
            act_detach = menu.addAction(tr("menu.context.detach"))
            act_detach.triggered.connect(lambda checked=False, c=credit: self._on_detach_credit_override(c))
            act_reassign = menu.addAction(tr("menu.context.reassign"))
            act_reassign.triggered.connect(lambda checked=False, c=credit: self._on_reassign_credit_override(c))
            act_reset = menu.addAction(tr("menu.context.reset_override"))
            act_reset.triggered.connect(lambda checked=False, c=credit: self._on_reset_credit_override(c))
        kind = settlement_row_kind(self._table.item(row, PaymentColumn.SUPPLIER))
        if kind == SettlementRowKind.INVOICE_CHILD:
            inv_label = self._cell_text(row, PaymentColumn.SUPPLIER).strip()
            if inv_label and inv_label not in ("Facturen",):
                menu.addSeparator()
                act_link = menu.addAction(tr("menu.context.link_credit"))
                act_link.triggered.connect(lambda checked=False, r=row: self._on_link_credit_to_invoice_row(r))
        doc_id_row = self._document_id_for_table_row(row)
        if doc_id_row and self._invoice_for_document_id(doc_id_row) is not None:
            menu.addSeparator()
            act_amt = menu.addAction(tr("menu.context.adjust_amount"))
            act_amt.triggered.connect(lambda checked=False, r=row: self._on_adjust_amount_override(r))
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
        self._set_status(tr("status.pay_direct", count=len(rows)))
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
                tr("dialog.invoice_date_missing.title"),
                tr("dialog.invoice_date_missing.message", rows=", ".join(str(x) for x in sorted(set(missing)))),
            )
        else:
            self._set_status(tr("status.pay_due", count=len(rows)))
        self._refresh_export_batch_status_label()

    def _confirm_review_rows(self, rows: list[int]) -> None:
        if not rows:
            return
        # Validate rows before allowing force-include.
        invalid_rows: list[int] = []
        for r in rows:
            if r < 0 or r >= self._table.rowCount() or self._is_row_blank(r):
                continue
            try:
                p = self._payment_dict_from_row(r)
            except ValueError:
                invalid_rows.append(r + 1)
                continue
            err = self._validate_single_payment_row(p)
            if err:
                invalid_rows.append(r + 1)
        if invalid_rows:
            amount_hints: list[int] = []
            for r in rows:
                if r < 0 or r >= self._table.rowCount() or self._is_row_blank(r):
                    continue
                try:
                    p = self._payment_dict_from_row(r)
                except ValueError:
                    continue
                err = self._validate_single_payment_row(p)
                if err and "bedrag" in err:
                    amount_hints.append(r + 1)
            extra = ""
            if amount_hints:
                extra = tr("dialog.approve.amount_hint")
            QMessageBox.warning(
                self,
                tr("dialog.approve.blocked_title"),
                tr(
                    "dialog.approve.blocked_message",
                    rows=", ".join(str(x) for x in sorted(set(invalid_rows))),
                    extra=extra,
                ),
            )
            return

        self._engine_rerun_timer.stop()
        for r in rows:
            if r < 0 or r >= self._table.rowCount() or self._is_row_blank(r):
                continue
            rid = self._row_id(r)
            self._pending_engine_row_ids.discard(rid)
            self._rows_requiring_reapproval.discard(rid)
            self._rows_requiring_reapproval_rows.discard(r)

        approved_map: dict[str, dict[str, Any]] = {}
        prev_suppress = self._suppress_table_item_changed
        blocked = self._table.blockSignals(True)
        self._suppress_table_item_changed = True
        try:
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
                self._set_row_decision(r, dec)

            self._commit_decision_map_patch(approved_map)

            batch_key = self._batch_approval_key()
            self._approval_store.upsert_batch(batch_key, approved_map)

            self._apply_row_colors()
            self._set_status(tr("status.approved", count=len(approved_map)))
            self._refresh_export_batch_status_label()
        finally:
            self._suppress_table_item_changed = prev_suppress
            self._table.blockSignals(blocked)

    def _batch_approval_key(self) -> str:
        return stable_hash(
            {
                "folder": str(self._selected_folder.resolve()) if self._selected_folder else "",
                "suppliers_path": self._supplier_db_path(),
            }
        )

    def _revoke_user_approval_for_row(self, row: int) -> None:
        rid = self._row_id(row)
        legacy = self._legacy_row_id(row)
        keys = {rid}
        if legacy != rid:
            keys.add(legacy)
        self._approval_store.remove_from_batch(self._batch_approval_key(), keys)

    def _mark_rows_as_error(self, rows: list[int]) -> None:
        decision_updates: dict[str, dict[str, Any]] = {}
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
            decision_updates[rid] = dec
            self._set_row_decision(r, dec)
        self._commit_decision_map_patch(decision_updates)
        self._apply_row_colors()
        self._refresh_export_batch_status_label()

    def _restore_rows_from_error(self, rows: list[int]) -> None:
        for r in rows:
            if r < 0 or r >= self._table.rowCount() or self._is_row_blank(r):
                continue
            # Restoring is a user action; rerun engine validation for this row.
            self._mark_row_pending_engine_update(r, "restored_from_user_error")
        self._apply_row_colors()
        self._set_status(tr("status.restored", count=len(rows)))
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
        prev_suppress = self._suppress_table_item_changed
        blocked = self._table.blockSignals(True)
        self._suppress_table_item_changed = True
        try:
            self._table.setItem(r, PaymentColumn.ERROR, new_err)
        finally:
            self._suppress_table_item_changed = prev_suppress
            self._table.blockSignals(blocked)

    def _on_sync_button_clicked(self) -> None:
        """Slot wrapper: altijd feedback; vangt stille crashes in de sync-logica af."""
        self._set_status(tr("status.supplier_saving"))
        QApplication.processEvents()
        try:
            self._on_sync_selected_to_suppliers()
        except Exception as exc:
            logger.exception("supplier sync failed")
            QMessageBox.critical(
                self,
                tr("dialog.suppliers.msgbox_title"),
                tr("dialog.suppliers.sync_error", detail=exc),
            )

    def _on_sync_selected_to_suppliers(self) -> None:
        """Voeg toe / update: schrijf leverancier naar DB en herbereken betalingen (zoals voorheen)."""
        rows = self._selected_table_rows()
        if not rows:
            QMessageBox.information(
                self,
                tr("dialog.suppliers.msgbox_title"),
                tr("dialog.suppliers.select_rows"),
            )
            return
        db = SupplierDB(path=self._supplier_db_path())
        ok = 0
        failed = 0
        changed = False
        synced_rows: list[int] = []
        for r in rows:
            payload = self._row_supplier_sync_payload(r, db)
            name = str(payload.get("name") or "").strip()
            iban = str(payload.get("iban") or "").strip()
            existing_supplier = bool(payload.get("existing_supplier"))
            iban_user_cleared = bool(payload.get("iban_user_cleared"))
            if not name:
                failed += 1
                continue
            if not iban and not existing_supplier and not iban_user_cleared:
                failed += 1
                continue

            sup_it = self._table.item(r, PaymentColumn.SUPPLIER)
            original_name = str(payload.get("original_name") or "").strip()

            if original_name and original_name != name:
                renamed = db.rename_supplier(original_name, name, keep_old_as_alias=True)
                if renamed and sup_it:
                    sup_it.setData(_ROW_SUPPLIER_ORIGINAL_ROLE, name)

            merged = False
            if iban:
                merged = db.merge_or_add_supplier(
                    name,
                    iban,
                    payload.get("customer_code"),
                    float(payload.get("discount") or 0.0),
                    default_payment_term_days=payload.get("term_days"),
                    vat_number=payload.get("vat_number"),
                    kvk_number=payload.get("kvk_number"),
                    email_domain=payload.get("email_domain"),
                )
            elif existing_supplier:
                merged = True

            update_kwargs: dict[str, Any] = {
                "iban": iban,
                "discount": float(payload.get("discount") or 0.0),
                "vat_numbers": [payload["vat_number"]] if payload.get("vat_number") else [],
                "kvk_numbers": [payload["kvk_number"]] if payload.get("kvk_number") else [],
                "email_domains": [payload["email_domain"]] if payload.get("email_domain") else [],
            }
            term_days = payload.get("term_days")
            if term_days is not None:
                update_kwargs["default_payment_term_days"] = term_days
            if payload.get("none_mode"):
                update_kwargs["customer_codes"] = []
                update_kwargs["overwrite_customer_codes"] = True

            updated = db.update_supplier(name, **update_kwargs)
            if payload.get("customer_number_mode") == CUSTOMER_NUMBER_MODE_NONE:
                db.set_customer_number_mode(name, CUSTOMER_NUMBER_MODE_NONE)

            if merged or updated:
                ok += 1
                synced_rows.append(r)
                self._strip_iban_mismatch_warning_row(r)
                changed = True
            else:
                failed += 1
        failed_suffix = tr("status.supplier_sync_failed_suffix", failed=failed) if failed else ""
        msg = tr("status.supplier_sync_result", ok=ok, failed_suffix=failed_suffix)
        self._set_status(msg)
        QMessageBox.information(self, tr("dialog.suppliers.msgbox_title"), msg)
        QApplication.processEvents()
        if changed and self._selected_folder:

            def _reload_after_sync() -> None:
                try:
                    self._refresh_filter_and_sort_after_row_change()
                    if self._load_parsed_invoices_warm() is not None:
                        self._rematch_rows_after_supplier_sync(synced_rows)
                    else:
                        self._load_payments_from_sources(parse_pdfs=True)
                except Exception as exc:
                    logger.exception("reload after supplier sync failed")
                    QMessageBox.warning(
                        self,
                        tr("dialog.suppliers.msgbox_title"),
                        tr("dialog.suppliers.reload_failed", detail=exc),
                    )

            QTimer.singleShot(0, _reload_after_sync)

    def _selected_table_rows(self) -> list[int]:
        n = self._table.rowCount()
        rows = {idx.row() for idx in self._table.selectedIndexes() if 0 <= idx.row() < n}
        return sorted(rows)

    def _refresh_filter_and_sort_after_row_change(self, *, allow_sort: bool = True) -> None:
        if allow_sort and self._persist_sort_column is not None:
            self._table.sortByColumn(self._persist_sort_column, self._persist_sort_order)
        self._apply_filter_to_table(self._filter_edit.text())

    def _capture_row_cells(self, row: int) -> tuple[_CellSnapshot | None, ...]:
        row_cells: list[_CellSnapshot | None] = []
        for c in range(self._table.columnCount()):
            it = self._table.item(row, c)
            if it is None:
                row_cells.append(None)
                continue
            roles: dict[int, Any] = {}
            for role in _TABLE_SNAPSHOT_ROLES:
                val = it.data(role)
                if val is not None:
                    roles[int(role)] = deepcopy(val)
            row_cells.append(
                _CellSnapshot(
                    text=it.text(),
                    tooltip=it.toolTip() or "",
                    flags=int(it.flags().value),
                    roles=roles,
                )
            )
        return tuple(row_cells)

    def _find_row_by_id(self, row_id: str) -> int | None:
        target = str(row_id or "").strip()
        if not target:
            return None
        for r in range(self._table.rowCount()):
            if self._row_id(r) == target:
                return r
        return None

    def _capture_table_snapshot(self) -> _TableSnapshot:
        cells: list[tuple[_CellSnapshot | None, ...]] = []
        for r in range(self._table.rowCount()):
            cells.append(self._capture_row_cells(r))
        return _TableSnapshot(
            cells=tuple(cells),
            active_run_id=self._active_run_id,
            session_amount_overrides=dict(self._session_amount_overrides),
        )

    def _restore_row_cells(self, row: int, row_cells: tuple[_CellSnapshot | None, ...]) -> None:
        for c, cell in enumerate(row_cells):
            if cell is None:
                self._table.setItem(row, c, None)
                continue
            it = QTableWidgetItem(cell.text)
            it.setFlags(Qt.ItemFlags(cell.flags))
            if cell.tooltip:
                it.setToolTip(cell.tooltip)
            for role_int, val in cell.roles.items():
                it.setData(Qt.ItemDataRole(role_int), val)
            self._table.setItem(row, c, it)

    def _restore_row_undo(self, entry: _RowUndoEntry) -> int | None:
        row = self._find_row_by_id(entry.row_id)
        if row is None:
            return None
        prev_suppress = self._suppress_table_item_changed
        blocked = self._table.blockSignals(True)
        self._suppress_table_item_changed = True
        # Sorteren uit tijdens restore: anders verplaatst de rij midden in de cel-loop.
        sorting_was_enabled = self._table.isSortingEnabled()
        if sorting_was_enabled:
            self._table.setSortingEnabled(False)
        try:
            self._restore_row_cells(row, entry.cells)
            doc_id = self._document_id_for_table_row(row)
            if doc_id:
                if entry.had_session_amount_override:
                    if entry.session_amount_override is not None:
                        self._session_amount_overrides[doc_id] = entry.session_amount_override
                elif doc_id in self._session_amount_overrides:
                    del self._session_amount_overrides[doc_id]
        finally:
            self._suppress_table_item_changed = prev_suppress
            self._table.blockSignals(blocked)
            if sorting_was_enabled:
                self._table.setSortingEnabled(True)
        self._decision_table_fingerprint = None
        self._apply_row_colors()
        self._refresh_export_batch_status_label()
        self._refresh_profile_button_state()
        row = self._find_row_by_id(entry.row_id)
        if row is not None:
            self._update_row_render_hash(row)
        self._refresh_filter_and_sort_after_row_change(allow_sort=True)
        row = self._find_row_by_id(entry.row_id)
        if row is not None:
            self._table.selectRow(row)
            col = entry.edited_column
            if col is not None and 0 <= col < self._table.columnCount():
                it = self._table.item(row, col)
                if it is not None:
                    self._table.setCurrentCell(row, col)
                    self._table.scrollToItem(it, QAbstractItemView.ScrollHint.PositionAtCenter)
            else:
                it = self._table.item(row, PaymentColumn.SUPPLIER)
                if it is not None:
                    self._table.scrollToItem(it, QAbstractItemView.ScrollHint.PositionAtCenter)
        return row

    def _restore_table_snapshot(self, snap: _TableSnapshot) -> None:
        self._cancel_pending_engine_updates()
        prev_suppress = self._suppress_table_item_changed
        blocked = self._table.blockSignals(True)
        self._suppress_table_item_changed = True
        try:
            self._table.setRowCount(len(snap.cells))
            for r, row_cells in enumerate(snap.cells):
                self._restore_row_cells(r, row_cells)
            self._active_run_id = snap.active_run_id
            self._session_amount_overrides = dict(snap.session_amount_overrides)
        finally:
            self._suppress_table_item_changed = prev_suppress
            self._table.blockSignals(blocked)
        self._decision_table_fingerprint = None
        self._apply_row_colors()
        self._refresh_filter_and_sort_after_row_change(allow_sort=False)
        self._refresh_export_batch_status_label()
        self._refresh_profile_button_state()

    def _capture_pending_undo_snapshot(self, row: int, column: int) -> None:
        """Bewaar rijstaat bij start inline-edit; commit pas bij itemChanged."""
        if self._is_loading_batch or self._undo_restore_depth > 0 or self._undo_batch_depth > 0:
            return
        if row < 0 or row >= self._table.rowCount():
            return
        row_id = self._row_id(row)
        doc_id = self._document_id_for_table_row(row)
        had_override = bool(doc_id and doc_id in self._session_amount_overrides)
        override = (
            deepcopy(self._session_amount_overrides[doc_id])
            if had_override and doc_id
            else None
        )
        self._pending_undo_snapshot = _RowUndoEntry(
            row_id=row_id,
            cells=self._capture_row_cells(row),
            session_amount_override=override,
            had_session_amount_override=had_override,
            edited_column=column,
        )

    def _finalize_pending_undo_on_edit_close(self) -> None:
        """Commit undo als de cel echt gewijzigd is; anders weggooien."""
        pending = self._pending_undo_snapshot
        if pending is None:
            return
        row = self._find_row_by_id(pending.row_id)
        if row is None:
            self._discard_pending_undo_snapshot()
            return
        current = self._capture_row_cells(row)
        if current == pending.cells:
            self._discard_pending_undo_snapshot()
        else:
            self._commit_pending_undo_snapshot()

    def _commit_pending_undo_snapshot(self) -> None:
        if self._pending_undo_snapshot is None:
            return
        if self._is_loading_batch or self._undo_restore_depth > 0 or self._undo_batch_depth > 0:
            self._pending_undo_snapshot = None
            return
        snap = self._pending_undo_snapshot
        self._pending_undo_snapshot = None
        self._undo_stack.append(snap)
        if len(self._undo_stack) > _UNDO_STACK_LIMIT:
            self._undo_stack.pop(0)

    def _discard_pending_undo_snapshot(self) -> None:
        self._pending_undo_snapshot = None

    def _on_undo(self) -> None:
        if self._undo_shortcut_busy:
            return
        if self._table.state() == QAbstractItemView.State.EditingState:
            return
        if not self._undo_stack:
            self._set_status(tr("status.undo_empty"))
            return
        self._undo_shortcut_busy = True
        try:
            entry = self._undo_stack.pop()
            self._undo_restore_depth += 1
            try:
                self._restore_row_undo(entry)
            finally:
                self._undo_restore_depth -= 1
            restored_row = self._find_row_by_id(entry.row_id)
            if restored_row is not None:
                self._set_status(
                    tr(
                        "status.undo_row",
                        supplier=self._cell_text(restored_row, PaymentColumn.SUPPLIER).strip()[:60],
                        amount=self._cell_text(restored_row, PaymentColumn.AMOUNT).strip()[:20],
                    )
                )
            else:
                self._set_status(tr("status.undo"))
        finally:
            self._undo_shortcut_busy = False

    def _on_delete_selected_rows(self) -> None:
        selected = self._selected_table_rows()
        if not selected:
            return
        for r in sorted(selected, reverse=True):
            if 0 <= r < self._table.rowCount():
                self._table.removeRow(r)
        self._refresh_filter_and_sort_after_row_change()
        self._refresh_export_batch_status_label()

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+O"), self).activated.connect(self._on_select_folder)
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self._on_reread_pdfs)
        QShortcut(QKeySequence("Ctrl+E"), self).activated.connect(self._on_make_xml)
        QShortcut(QKeySequence("Delete"), self).activated.connect(self._on_delete_selected_rows)
        undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        undo_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        undo_shortcut.activated.connect(self._on_undo)
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

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._loading_overlay is not None and self._loading_overlay.isVisible():
            self._loading_overlay.fit_to_parent()

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
            tr("app.about.title"),
            tr("app.about.html", version=self.APP_VERSION),
        )

    def _on_select_folder(self) -> None:
        start = str(self._selected_folder) if self._selected_folder else ""
        path: Optional[str] = QFileDialog.getExistingDirectory(
            self, tr("file.pick_invoice_folder"), start
        )
        if not path:
            return
        selected = Path(path).resolve()
        self._invalidate_parsed_batch_cache()
        self._selected_folder = selected
        self._persist_invoice_folder(selected)
        self._payment_sources = [self._make_map_folder_source(selected)]
        self._load_payments_from_sources(parse_pdfs=True)

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
        """Single source for rendering + export: DecisionStore decision or table projection."""
        table_dec = self._row_decision(row)
        table_reason = str(table_dec.get("reason_code") or "")
        active_run_id = self._active_run_id or self._pinned_run_id
        store_dec: dict[str, Any] | None = None
        if active_run_id:
            self._resolver_active = True
            try:
                dec_map = self._decision_store.committed_decision_map(active_run_id)
                raw = dec_map.get(self._row_id(row))
                if isinstance(raw, dict):
                    store_dec = dict(normalize_decision(raw))
            finally:
                self._resolver_active = False
        if isinstance(store_dec, dict):
            return store_dec
        if table_reason and table_reason != REASON_MISSING_DECISION_IN_STORE:
            return table_dec
        return self._missing_decision_payload(row)

    def _set_row_decision(self, row: int, decision: dict[str, Any], *, note: str | None = None) -> None:
        dec = normalize_decision(decision)
        status_label = _decision_status_label(dec["status"])
        reason_code = str(dec.get("reason_code") or "").strip() or REASON_MISSING_DECISION_IN_STORE
        reason_detail = dec.get("reason_detail")
        detail_s = str(reason_detail).strip() if reason_detail is not None else ""
        # For clean UX: when a row is exportable/OK, keep the "foutmelding" column empty.
        # The decision remains available via the stored payload (for debugging/inspection).
        show_message = not (
            dec.get("status") == DECISION_INCLUDED
            and not bool(dec.get("requires_rerun"))
            and not note
        )
        message = (
            _user_facing_error_text(
                reason_code=reason_code,
                reason_detail=detail_s or None,
                note=note,
            )
            if show_message
            else ""
        )
        err_it = self._item_readonly(message)
        err_it.setData(_ROW_DECISION_ROLE, dec)
        err_it.setToolTip(self._compose_error_tooltip(error_msg=message, decision_trace=None))
        prev_suppress = self._suppress_table_item_changed
        blocked = self._table.blockSignals(True)
        self._suppress_table_item_changed = True
        try:
            self._table.setItem(row, PaymentColumn.STATUS, self._item_readonly(status_label))
            self._table.setItem(row, PaymentColumn.ERROR, err_it)
            self._update_row_render_hash(row)
        finally:
            self._suppress_table_item_changed = prev_suppress
            self._table.blockSignals(blocked)

    def _legacy_row_id(self, row: int) -> str:
        sup = self._cell_text(row, PaymentColumn.SUPPLIER)
        inv = self._get_row_invoice_number(row)
        pdf = self._cell_text(row, PaymentColumn.PDF)
        return f"{sup}|{inv}|{pdf}".strip()

    def _normalize_decision_map_row_ids(self, decision_map: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Map oude leverancier|factuur|pdf-sleutels naar stabiele factuur|pdf-sleutels."""
        out = dict(decision_map)
        for r in range(self._table.rowCount()):
            if self._is_row_blank(r) or self._is_settlement_child_row(r):
                continue
            stable = self._row_id(r)
            legacy = self._legacy_row_id(r)
            if legacy in out and legacy != stable:
                if stable not in out:
                    out[stable] = out.pop(legacy)
                else:
                    out.pop(legacy, None)
        return out

    def _row_id(self, row: int) -> str:
        sup_it = self._table.item(row, PaymentColumn.SUPPLIER)
        if sup_it:
            rid = str(sup_it.data(_ROW_ROW_ID_ROLE) or "").strip()
            if rid:
                return rid
        inv = self._get_row_invoice_number(row)
        sup = self._cell_text(row, PaymentColumn.SUPPLIER)
        pdf = self._cell_text(row, PaymentColumn.PDF)
        rid = _stable_payment_row_id(supplier=sup, invoice_number=inv, pdf=pdf)
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
            if self._is_row_blank(r) or self._is_settlement_child_row(r):
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
            self._set_row_decision(
                row,
                mismatch_dec,
                note=tr("decision.note.reload_or_recalculate"),
            )
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

    def _table_rows_for_row_ids(self, row_ids: set[str]) -> list[int]:
        if not row_ids:
            return []
        out: list[int] = []
        for r in range(self._table.rowCount()):
            if self._is_row_blank(r) or self._is_settlement_child_row(r):
                continue
            if self._row_id(r) in row_ids:
                out.append(r)
        return out

    def _decision_from_engine_rematch(
        self,
        row: int,
        *,
        payments: list[dict],
        errors: list[dict],
        invoices: list[dict],
    ) -> dict[str, Any] | None:
        try:
            p_row = self._payment_dict_from_row(row, require_resolved_amount=False)
        except ValueError:
            return None
        payment = self._payment_for_row_in_lists(row, payments, invoices)
        if isinstance(payment, dict):
            dec = payment.get("decision")
            if isinstance(dec, dict):
                return dict(normalize_decision(dec))
        inv = self._match_inv_for_payment(invoices, p_row)
        if isinstance(inv, dict):
            dec = inv.get("decision")
            if isinstance(dec, dict):
                return dict(normalize_decision(dec))
        sf = self._resolve_row_source_file(row)
        pdf = self._cell_text(row, PaymentColumn.PDF).strip()
        sup = str(p_row.get("supplier_name") or "").strip()
        inv_no = str(p_row.get("invoice_number") or "").strip()
        for inv_e, _reason in self._flatten_unique_error_invoices(errors):
            if sf:
                inv_sf = str(inv_e.get("source_file") or "").strip()
                if inv_sf and inv_sf == sf:
                    dec = inv_e.get("decision")
                    if isinstance(dec, dict):
                        return dict(normalize_decision(dec))
            if pdf:
                inv_pdf = str(_pdf_basename_from_dict(inv_e) or "").strip()
                if inv_pdf and inv_pdf == pdf:
                    dec = inv_e.get("decision")
                    if isinstance(dec, dict):
                        return dict(normalize_decision(dec))
            inv_sup = str(
                inv_e.get("supplier_name") or inv_e.get("supplier_hint") or ""
            ).strip()
            inv_meta = str(inv_e.get("invoice_number") or "").strip()
            if sup and inv_sup and sup == inv_sup and (not inv_no or not inv_meta or inv_meta == inv_no):
                dec = inv_e.get("decision")
                if isinstance(dec, dict):
                    return dict(normalize_decision(dec))
        return None

    def _commit_decision_map_patch(
        self,
        decision_updates: dict[str, dict[str, Any]],
        *,
        replace: bool = False,
    ) -> None:
        if not decision_updates:
            return
        # Engine write path (not render resolver): use inner store to avoid guard crash.
        store = self._decision_store_inner
        if replace:
            base_map = self._normalize_decision_map_row_ids(dict(decision_updates))
        else:
            base_map = self._normalize_decision_map_row_ids(
                dict(store.committed_decision_map(self._active_run_id) or {})
            )
            base_map.update(decision_updates)
        run_id = str(uuid.uuid4())
        store.begin_run(
            run_id=run_id,
            input_snapshot_hash=stable_hash({"patch": sorted(decision_updates.keys()), "replace": replace}),
            decision_map=base_map,
        )
        store.commit_run(run_id)
        self._pinned_run_id = run_id
        self._active_run_id = run_id

    def _table_decision_fingerprint(self) -> str:
        parts: list[str] = []
        for r in range(self._table.rowCount()):
            if self._is_row_blank(r) or self._is_settlement_child_row(r):
                continue
            parts.append(f"{self._row_id(r)}:{stable_hash(self._row_decision(r))}")
        return stable_hash(parts)

    def _sync_decision_store_from_table(self, *, force: bool = False) -> None:
        """Sync fresh row decisions from table items into DecisionStore.

        After any repopulate the new decisions live in _ROW_DECISION_ROLE on items
        (set by _set_row_decision inside _append_table_row).  _apply_row_colors reads
        via _row_decision (table projection); DecisionStore is kept in sync for export
        and user-approval flows.  Child rows are excluded — they have no independent
        decision and their styling is managed separately.
        """
        fp = self._table_decision_fingerprint()
        if not force and fp == self._decision_table_fingerprint:
            return
        decision_map: dict[str, dict[str, Any]] = {}
        for r in range(self._table.rowCount()):
            if self._is_row_blank(r) or self._is_settlement_child_row(r):
                continue
            rid = self._row_id(r)
            decision_map[rid] = self._row_decision(r)
        if decision_map:
            self._commit_decision_map_patch(decision_map, replace=True)
            self._decision_table_fingerprint = fp

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
        self._engine_cache.invalidate(f"field_change:{reason}")
        row_id = self._row_id(row)
        prev = self._decision_for_row(row)
        prev_reason = str(prev.get("reason_code") or "")
        if prev_reason == REASON_USER_APPROVED:
            self._rows_requiring_reapproval.add(row_id)
            self._rows_requiring_reapproval_rows.add(row)
            self._revoke_user_approval_for_row(row)
        self._pending_engine_row_ids.add(row_id)
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
        self._commit_decision_map_patch({row_id: pending_decision})
        idempotency = stable_hash({"row_id": row_id, "decision": pending_decision, "reason": reason})
        self._pending_engine_idempotency.add(idempotency)
        self._engine_rerun_timer.start(250)

    def _row_supplier_match_status(self, row: int) -> str:
        snap = self._get_row_invoice_diagnostics_snapshot(row)
        if not isinstance(snap, dict):
            snap = self._minimal_diagnostics_snapshot_from_row(row)
        return self._match_status_for_profile_gate(row, snap)

    def _supplier_match_needs_review(self, row: int) -> bool:
        return self._row_supplier_match_status(row) in _SUPPLIER_MATCH_REVIEW_STATUSES

    def _supplier_review_reason_for_row(self, row: int) -> str:
        ms = self._row_supplier_match_status(row)
        if ms in _MATCH_STATUS_TO_ENGINE_REASON:
            return _MATCH_STATUS_TO_ENGINE_REASON[ms]
        err_it = self._table.item(row, PaymentColumn.ERROR)
        trace = err_it.data(_ROW_DECISION_TRACE_ROLE) if err_it else None
        if isinstance(trace, dict):
            rc = str(trace.get("supplier_match_status") or trace.get("reason_code") or "").strip()
            if rc in _MATCH_STATUS_TO_ENGINE_REASON.values():
                return rc
        return "needs_review"

    def _decision_after_pending_rerun(
        self,
        row: int,
        rid: str,
        *,
        validation_error: str | None,
        amount: Any,
    ) -> dict[str, Any]:
        if validation_error:
            return build_decision(
                status=DECISION_NEEDS_REVIEW,
                reason_code="row_validation_failed",
                reason_detail=validation_error,
                editable=True,
                requires_rerun=False,
                causal_inputs=["iban", "amount", "execution_date"],
                input_fields={"row_id": rid, "error": validation_error},
            )
        needs_reapproval = (
            row in self._rows_requiring_reapproval_rows or rid in self._rows_requiring_reapproval
        )
        if needs_reapproval:
            self._rows_requiring_reapproval_rows.discard(row)
            self._rows_requiring_reapproval.discard(rid)
            return build_decision(
                status=DECISION_NEEDS_REVIEW,
                reason_code=REASON_MANUAL_PENDING,
                reason_detail="user_approval_revoked_after_field_change",
                editable=True,
                requires_rerun=True,
                causal_inputs=["user_approve", "field_change"],
                input_fields={"row_id": rid, "reason": "reapproval_required"},
            )
        if self._supplier_match_needs_review(row):
            reason_code = self._supplier_review_reason_for_row(row)
            return build_decision(
                status=DECISION_NEEDS_REVIEW,
                reason_code=reason_code,
                reason_detail=None,
                editable=True,
                requires_rerun=reason_code in ("needs_review", "unmatched_supplier"),
                causal_inputs=["supplier_match"],
                input_fields={"row_id": rid, "match_status": self._row_supplier_match_status(row)},
            )
        return build_decision(
            status=DECISION_INCLUDED,
            reason_code="included_validated",
            reason_detail=None,
            editable=False,
            requires_rerun=False,
            causal_inputs=["iban", "amount", "execution_date"],
            input_fields={"row_id": rid, "amount": amount},
        )

    def _commit_pending_engine_updates(self) -> None:
        if not self._pending_engine_row_ids:
            return
        row_ids = set(self._pending_engine_row_ids)
        self._pending_engine_row_ids.clear()
        if not self._pending_engine_idempotency:
            return
        self._pending_engine_idempotency.clear()
        rows = self._table_rows_for_row_ids(row_ids)
        if not rows:
            return
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
        decision_map: dict[str, dict[str, Any]] = {
            inp["row_id"]: {
                "status": DECISION_NEEDS_REVIEW,
                "reason_code": REASON_MANUAL_PENDING,
            }
            for inp in inputs
        }
        self._decision_store.begin_run(
            run_id=run_id,
            input_snapshot_hash=snapshot["snapshot_hash"],
            decision_map=decision_map,
        )
        if not schema_result.valid:
            self._decision_store.fail_run(run_id)
            for row in rows:
                rid = self._row_id(row)
                dec = build_decision(
                    status=DECISION_EXCLUDED,
                    reason_code="invalid_engine_input_schema",
                    reason_detail="; ".join(schema_result.errors),
                    editable=True,
                    requires_rerun=True,
                    causal_inputs=["schema"],
                    input_fields={"errors": schema_result.errors, "row_id": rid},
                )
                decision_map[rid] = dec
                self._set_row_decision(row, dec)
            return

        for row in rows:
            rid = self._row_id(row)
            try:
                p = self._payment_dict_from_row(row)
            except ValueError:
                dec = build_decision(
                    status=DECISION_EXCLUDED,
                    reason_code="amount_invalid_format",
                    reason_detail="Ongeldig bedrag",
                    editable=True,
                    requires_rerun=True,
                    causal_inputs=["amount"],
                    input_fields={"row_id": rid},
                )
                decision_map[rid] = dec
                self._set_row_decision(row, dec)
                continue
            validation_error = self._validate_single_payment_row(p)
            dec = self._decision_after_pending_rerun(
                row,
                rid,
                validation_error=validation_error,
                amount=p.get("amount"),
            )
            decision_map[rid] = dec
            self._set_row_decision(row, dec)
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
        if self._is_loading_batch:
            return
        if self._field_apply_depth > 0:
            return
        if self._suppress_table_item_changed:
            return
        col = item.column()
        row = item.row()
        # Read-only kolommen: geen refresh/hash-check (voorkomt oneindige itemChanged-lus op ERROR).
        if col in (
            int(PaymentColumn.ERROR),
            int(PaymentColumn.STATUS),
            int(PaymentColumn.INFO),
            int(PaymentColumn.CORE_MATCHES),
            int(PaymentColumn.MATCH_COMPLETE),
            int(PaymentColumn.PDF),
        ):
            return
        self._commit_pending_undo_snapshot()
        if col == PaymentColumn.INVOICE_DATE:
            item.setData(_ROW_INVOICE_DATE_SOURCE_ROLE, "manual")
            t = item.text().strip()
            iso = parse_ui_date_to_iso(t)
            if iso:
                item.setData(Qt.ItemDataRole.UserRole, iso)
            else:
                item.setData(Qt.ItemDataRole.UserRole, None)
            if t:
                item.setToolTip(tr("tooltip.invoice_date.manual"))
            else:
                item.setToolTip("")
        elif col == PaymentColumn.EXECUTION_DATE:
            self._set_row_date_mode(row, "manual")
            iso = parse_ui_date_to_iso(item.text().strip())
            if iso:
                item.setData(Qt.ItemDataRole.UserRole, iso)
            else:
                item.setData(Qt.ItemDataRole.UserRole, None)
            item.setToolTip(tr("tooltip.execution_date.manual"))
        elif col == PaymentColumn.AMOUNT:
            if self._is_settlement_child_row(row) and self._document_id_for_table_row(row):
                self._apply_child_row_amount_override(row, item)
                return
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
            self._resolve_and_apply_field_candidate(
                row,
                "amount",
                {"value": format_eur_xml(dec), "source": "manual", "confidence": 100},
                pending_reason="amount_changed",
                skip_undo=True,
            )
        elif col == PaymentColumn.IBAN:
            raw = item.text().strip()
            ic = clean_iban(raw)
            if ic and is_plausible_iban(ic):
                self._resolve_and_apply_field_candidate(
                    row,
                    "iban",
                    {"value": ic, "source": "manual", "confidence": 100},
                    pending_reason="iban_changed",
                    skip_undo=True,
                )
            else:
                generic = field_result_from_legacy_dict(
                    self._field_result_snapshot_for_row(row, "iban") or {},
                    field_id="iban",
                )
                resolved = resolve_field("iban", generic, [], user_pick=None)
                resolved.selected_value = None
                resolved.status = "confirmed"
                resolved.confidence = 100
                resolved.user_overridden = True
                resolved.user_selected = True
                resolved.override_reason = "manual_clear"
                resolved.resolver_finalized = True
                self._apply_resolved_field_result_to_row(
                    row,
                    "iban",
                    field_result_to_legacy_dict(resolved),
                    pending_reason="iban_changed",
                )
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
            cust = item.text().strip()
            if cust:
                self._resolve_and_apply_field_candidate(
                    row,
                    "customer_number",
                    {"value": cust, "source": "manual", "confidence": 100},
                    pending_reason="customer_code_changed",
                    skip_undo=True,
                )
            else:
                self._mark_row_pending_engine_update(row, "customer_code_changed")
        elif col == PaymentColumn.TERM_HINT:
            days = _parse_term_days_from_text(item.text())
            if days is not None:
                item.setData(_ROW_EFFECTIVE_TERM_ROLE, days)
        self._refresh_export_batch_status_label()

    def _toggle_settlement_group_expand(self, row: int) -> bool:
        """Toggle expand state for a settlement group header row; repopulate table."""
        gid_it = self._table.item(row, PaymentColumn.SETTLEMENT)
        gid = str(gid_it.data(_ROW_SETTLEMENT_GROUP_ID_ROLE) or "") if gid_it else ""
        if not gid:
            return False
        group = self._settlement_group_for_row(row)
        if group is not None:
            vm = vm_from_group(group)
            if not settlement_group_is_expandable(vm, group=group):
                return False
        if gid in self._expanded_settlement_groups:
            self._expanded_settlement_groups.discard(gid)
        else:
            self._expanded_settlement_groups.add(gid)
        if self._engine_result is not None:
            self._populate_table_from_settlement_groups(
                self._engine_result, self._matched_invoices
            )
        return True

    def _on_table_cell_clicked(self, row: int, column: int) -> None:
        if column == int(PaymentColumn.SETTLEMENT):
            if self._is_settlement_child_row(row):
                group = self._settlement_group_for_row(row)
                if group and self._decision_inspector is not None:
                    vm = self._resolve_row_vm(row)
                    if vm is not None:
                        self._decision_inspector.setPlainText(self._inspector_text_for_vm(vm))
                return
            if self._toggle_settlement_group_expand(row):
                return
            group = self._settlement_group_for_row(row)
            if group and self._decision_inspector is not None:
                vm = self._resolve_row_vm(row)
                if vm is not None:
                    self._decision_inspector.setPlainText(self._inspector_text_for_vm(vm))
            return
        if column == int(PaymentColumn.SUPPLIER):
            sup_it = self._table.item(row, PaymentColumn.SUPPLIER)
            if settlement_row_kind(sup_it) == SettlementRowKind.GROUP_HEADER:
                if self._toggle_settlement_group_expand(row):
                    return
        if column == int(PaymentColumn.INFO):
            self._open_diagnostics_for_row(row)
            return
        if column == int(PaymentColumn.CUSTOMER_CODE):
            if self._cell_text(row, PaymentColumn.CUSTOMER_CODE).strip() == "?":
                self._show_field_candidate_menu(row, "customer_number")
            return
        if column == int(PaymentColumn.IBAN):
            if self._cell_text(row, PaymentColumn.IBAN).strip() == "?":
                self._show_field_candidate_menu(row, "iban")
            return
        if column == int(PaymentColumn.DESCRIPTION):
            if not self._get_row_invoice_number(row) and self._field_picker_eligible(
                row, "invoice_number"
            ):
                self._show_field_candidate_menu(row, "invoice_number")
            elif "?" in self._cell_text(row, PaymentColumn.DESCRIPTION):
                self._show_field_candidate_menu(row, "invoice_number")
            return
        if column != int(PaymentColumn.AMOUNT):
            return
        if self._cell_text(row, PaymentColumn.AMOUNT).strip() != "?":
            return
        self._show_field_candidate_menu(row, "amount")

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

    def _set_amount_row_error_with_trace(self, row: int, message: str) -> None:
        amt_it = self._table.item(row, PaymentColumn.AMOUNT)
        snap = amt_it.data(_ROW_AMOUNT_RESULT_ROLE) if amt_it else None
        err_prev = self._table.item(row, PaymentColumn.ERROR)
        prev_trace = err_prev.data(_ROW_DECISION_TRACE_ROLE) if err_prev else None
        display_message = _sanitize_table_error_message(message) or _user_facing_error_text(
            reason_code="amount_invalid_format"
        )
        err = self._item_readonly(display_message)
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
                error_msg=display_message,
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
            self._item_readonly(_decision_status_label(DECISION_EXCLUDED)),
        )
        self._table.setItem(row, PaymentColumn.ERROR, err)
        self._update_row_render_hash(row)

    def _reject_invalid_amount_cell_edit(self, amt_item: QTableWidgetItem, row: int) -> None:
        """Lege / ongeldige / niet-positieve bedragcel — zelfde pad voor paste en typen."""
        generic = field_result_from_legacy_dict(
            self._amount_result_snapshot_for_row(row) or {},
            field_id="amount",
        )
        resolved = resolve_field("amount", generic, [], user_pick=None)
        resolved.selected_value = None
        resolved.status = "failed"
        resolved.confidence = 0
        resolved.resolver_finalized = True
        self._apply_resolved_field_result_to_row(
            row,
            "amount",
            field_result_to_legacy_dict(resolved),
            pending_reason="amount_changed",
        )
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
        mark_pending: bool = True,
        pending_reason: str = "amount_changed",
    ) -> None:
        """Backward-compatible wrapper routed through resolver -> apply pipeline."""
        cand = picked_candidate if isinstance(picked_candidate, dict) else {}
        source = str(cand.get("source") or amount_source or "manual").strip() or "manual"
        confidence = int(cand.get("confidence") or (95 if amount_source == "profile" else 100))
        self._resolve_and_apply_field_candidate(
            row,
            "amount",
            {
                "value": format_eur_xml(dec),
                "source": source,
                "confidence": confidence,
                "context": str(cand.get("context") or ""),
            },
            pending_reason=pending_reason,
            mark_pending=mark_pending,
        )

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
            row,
            dec,
            from_manual_typing=False,
            picked_candidate=cand,
            pending_reason="amount_picked",
        )

    def _refresh_export_batch_status_label(self) -> None:
        """Batch-export preview: zelfde rijen als export vóór dialogs (geen fout/needs_review)."""
        previews = self._collect_export_batch_previews()
        if previews is None:
            self._btn_xml.setEnabled(False)
            self._set_status()
            return
        exportable = exportable_payments_from_decisions(previews)
        result = validate_export_batch(exportable)
        self._btn_xml.setEnabled(result.status != "blocked")
        self._set_status()

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
        # Defensive export guard: if the visible cell amount differs from a non-user-selected
        # snapshot, persist a promoted user_selected snapshot so future exports can't regress.
        if require_resolved_amount and isinstance(amt_snap, dict) and not amt_snap.get("user_selected"):
            try:
                cell_dec = amount_to_decimal(amt_cell_txt)
            except Exception:
                cell_dec = None
            if cell_dec is not None:
                snap_dec = None
                for key in ("value", "selected_amount"):
                    raw = amt_snap.get(key)
                    if raw is None or not str(raw).strip():
                        continue
                    try:
                        snap_dec = amount_to_decimal(str(raw))
                    except Exception:
                        snap_dec = None
                    break
                if snap_dec is not None and snap_dec != cell_dec:
                    promoted = format_eur_xml(cell_dec)
                    promoted_snap = {
                        "status": "confirmed",
                        "amount_status": "confirmed",
                        "user_selected": True,
                        "value": promoted,
                        "selected_amount": promoted,
                        "confidence": 100,
                        "source": "EXPORT_GUARD_CELL_WINS",
                        "candidates": [],
                    }
                    amt_it = self._table.item(row, PaymentColumn.AMOUNT)
                    if amt_it is not None:
                        amt_it.setData(_ROW_AMOUNT_RESULT_ROLE, deepcopy(promoted_snap))
                    amt_snap = promoted_snap
        supplier_item = self._table.item(row, PaymentColumn.SUPPLIER)
        supplier_name = self._cell_text(row, PaymentColumn.SUPPLIER)
        if supplier_item is not None:
            supplier_name = str(supplier_item.data(_ROW_SUPPLIER_ORIGINAL_ROLE) or supplier_name).strip()
        payment = {
            "row_id": self._row_id(row),
            "supplier_name": supplier_name,
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
            if self._is_row_blank(r) or self._is_settlement_child_row(r):
                continue
            rows.append(self._payment_dict_from_row(r))
        return rows

    def _clear_row_validation_marks(self) -> None:
        _KEEP = frozenset(
            {
                "ok",
                "confirmed",
                "reviewed",
                tr("matching.display.manual_row"),
                "needs_review",
                "needs review",
                _decision_status_label(DECISION_INCLUDED).lower(),
                _decision_status_label(DECISION_NEEDS_REVIEW).lower(),
                _decision_status_label(DECISION_EXCLUDED).lower(),
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
            return tr("validation.row.supplier_empty")
        iban_n = clean_iban(str(p.get("iban") or ""))
        if not iban_n or not is_plausible_iban(iban_n):
            return tr("validation.row.iban_invalid")
        try:
            amt = amount_to_decimal(p["amount"])
        except (KeyError, TypeError, ValueError):
            return tr("validation.row.amount_invalid")
        if amt <= Decimal("0.00"):
            return tr("validation.row.amount_zero")
        ex = str(p.get("execution_date") or "").strip()
        if not ex or not is_valid_iso_date_str(ex):
            return tr("validation.row.execution_date_invalid")
        mode = str(p.get("date_mode") or "direct")
        if mode == "due" and not (p.get("invoice_date") and str(p.get("invoice_date")).strip()):
            return tr("validation.row.invoice_date_required")
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

    def _build_export_batch_summary(self, payments: list[dict[str, Any]]) -> str:
        from collections import defaultdict

        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for p in payments:
            groups[str(p.get("execution_date") or "").strip()].append(p)
        lines: list[str] = [tr("dialog.export.summary_intro"), ""]
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
                label = tr("dialog.export.summary_date_session", date_label=label)
            lines.append(
                tr(
                    "dialog.export.summary_date_line",
                    date_label=label,
                    amount=amt_nl,
                    count=len(plist),
                )
            )
        grand = sum_decimals(all_decs)
        grand_s = format_eur_xml(grand).replace(".", ",")
        lines.append("")
        lines.append(tr("dialog.export.summary_total", amount=grand_s, count=grand_n))
        return "\n".join(lines)

    def _validate_debtor(self) -> Optional[str]:
        self._ensure_debtor_dict()
        return validate_debtor_for_export(self._settings["debtor"])

    def _on_make_xml(self) -> None:
        self._set_status(tr("status.export_started"))
        QApplication.processEvents()

        err_debt = self._validate_debtor()
        if err_debt:
            self._set_status(tr("status.error_prefix", detail=err_debt))
            return

        self._clear_row_validation_marks()
        QApplication.processEvents()

        invalid: list[tuple[int, str]] = []
        row_payment_pairs: list[tuple[int, dict[str, Any]]] = []

        for r in range(self._table.rowCount()):
            if self._is_row_blank(r) or self._is_settlement_child_row(r):
                continue
            dec = self._decision_for_row(r)
            if dec.get("status") != DECISION_INCLUDED or bool(dec.get("requires_rerun")):
                continue
            try:
                p = self._payment_dict_from_row(r)
            except ValueError:
                invalid.append((r, tr("validation.row.invalid_amount_export")))
                continue
            p["decision"] = dec
            mode = str(p.get("date_mode") or "direct")
            if mode == "due" and not (p.get("invoice_date") and str(p.get("invoice_date")).strip()):
                invalid.append((r, tr("validation.row.invoice_date_required")))
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
                self._set_status(tr("status.error_row_single", row=invalid[0][0] + 1, message=invalid[0][1]))
            else:
                self._set_status(tr("status.error_rows_multiple", count=len(invalid)))
            return

        if self._engine_result is None:
            self._set_status(tr("status.no_engine_result"))
            return

        if self._engine_result.uses_settlement:
            override_session = self._override_store.load_session(self._batch_key())
            override_ids = {o.credit_document_id for o in (override_session.overrides if override_session else ())}
            export_errors = validate_engine_result_for_export(
                self._engine_result,
                batch_credit_document_ids=credit_document_ids_from_batch(self._matched_invoices),
                override_credit_document_ids=override_ids,
            )
            if export_errors:
                QMessageBox.critical(
                    self,
                    tr("dialog.export.blocked_title"),
                    tr("dialog.export.blocked_settlement", errors="\n".join(export_errors[:12])),
                )
                self._set_status(tr("status.export_blocked_settlement"))
                return

        if row_payment_pairs:
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

        if self._engine_result.uses_settlement:
            export_input = exportable_groups(self._engine_result)
            assert isinstance(export_input, SettlementExportInput)
            payment_dicts = exportable_payments_from_decisions(
                settlement_groups_to_sepa_rows(export_input.groups)
            )
            if not payment_dicts:
                self._set_status(tr("status.no_settlement_groups"))
                return
        else:
            payment_dicts = exportable_payments_from_decisions([p for _r, p in row_payment_pairs])
            if not payment_dicts:
                self._set_status(tr("status.no_payment_rows"))
                return

        duplicates = self._check_duplicate_payments(payment_dicts)
        if duplicates:
            lines: list[str] = []
            for sup, inv, amt, ts in duplicates[:10]:
                lines.append(tr("dialog.export.duplicate_line", supplier=sup, invoice=inv, amount=_format_amount_nl(amt), timestamp=ts))
            if len(duplicates) > 10:
                lines.append(tr("dialog.export.duplicate_more", count=len(duplicates) - 10))
            detail = "\n".join(lines)
            dup_answer = QMessageBox.warning(
                self,
                tr("dialog.export.duplicate_title"),
                tr("dialog.export.duplicate_message", count=len(duplicates), detail=detail),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if dup_answer != QMessageBox.StandardButton.Yes:
                self._set_status(tr("status.export_cancelled_duplicates"))
                return

        total_amount = sum_decimals([amount_to_decimal(p.get("amount")) for p in payment_dicts])
        summary = self._build_export_batch_summary(payment_dicts)
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
                tr("dialog.export.weekend_title"),
                tr("dialog.export.weekend_message", dates="\n".join(nl_labels)),
            )
        confirm = QMessageBox.question(
            self,
            tr("dialog.export.confirm_title"),
            tr("dialog.export.confirm_message", summary=summary),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            self._set_status(tr("status.export_cancelled"))
            return

        batch_gate = validate_export_batch(payment_dicts)
        if batch_gate.status == "blocked":
            msg = format_batch_export_blocked_message(batch_gate)
            QMessageBox.critical(self, tr("dialog.export.blocked_title"), msg)
            self._set_status(msg)
            return

        for r in range(self._table.rowCount()):
            if self._is_row_blank(r) or self._is_settlement_child_row(r):
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
            self._set_status(tr("status.error_prefix", detail=e))
            return
        except OSError as e:
            self._set_status(tr("status.write_error", detail=e))
            return

        name = Path(abspath).name
        self._set_status(
            tr(
                "status.export_success",
                filename=name,
                count=len(payment_dicts),
                total=_format_amount_nl(total_amount),
            )
        )
        self._log_export(abspath, payment_dicts, total_amount)

def main() -> None:
    from logic.auto_update import offer_update_if_available
    from logic.runtime_paths import app_icon_path
    from parser.profile_strategy_engine import reload_strategy_engine_state

    reload_strategy_engine_state()
    app = QApplication(sys.argv)
    # Auto-update on startup (Windows). Fail-safe: als de update niet start,
    # wordt de app gewoon geopend. Bij een beschikbare update krijgt de
    # gebruiker eerst een duidelijke vraag en uitleg.
    if offer_update_if_available(auto_accept=False):
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(
            None,
            "PDF2SEPA wordt bijgewerkt",
            (
                "PDF2SEPA wordt nu bijgewerkt naar de nieuwste versie.\n\n"
                "De applicatie wordt afgesloten en de updater wordt gestart.\n"
                "Zodra de update klaar is, wordt de nieuwe versie automatisch geopend.\n\n"
                "Sluit dit venster pas wanneer PDF2SEPA opnieuw is gestart."
            ),
        )
        sys.exit(0)
    icon_path = app_icon_path()
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    window.resize(1100, 560)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
