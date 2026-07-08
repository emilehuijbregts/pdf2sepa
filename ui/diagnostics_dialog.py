"""Read-only diagnostics popup voor een betalingsrij (PySide6)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui.i18n import UiStrings, tr, tr_or_code
from ui.field_review import (
    CUSTOMER_ABSENT_MENU_LABEL_KEY,
    candidate_menu_tooltip,
    make_customer_absent_pick_candidate,
    is_customer_absent_pick,
)
from ui.field_rendering import checkmark_prefix, confidence_color

# #region agent log (debug mode - session 3d66a1)
_DEBUG_LOG_3D66A1 = "/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-3d66a1.log"


def _dbg_log_3d66a1(
    *,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict | None = None,
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

def _display_text(value: str | None) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    if UiStrings.has(s):
        return tr(s)
    if s.startswith("field.score_label.") and "|" in s:
        key, raw = s.split("|", 1)
        return f"{tr_or_code(key, key)}: {raw}"
    return s


_DIAG_FIELD_BLOCKS: dict[str, str] = {
    "amount": "amount",
    "vat_number": "vat_number",
    "kvk_number": "kvk_number",
    "invoice_date": "invoice_date",
    "email_domain": "email_domain",
    "invoice_number": "invoice_number",
    "customer_number": "customer_number",
    "iban": "iban",
}


def section_icon(*, needs_attention: bool, is_error: bool) -> str:
    if is_error:
        return "❌"
    if needs_attention:
        return "⚠️"
    return "✅"


def _lines_block(lines: list[str]) -> str:
    return "\n".join(line for line in lines if line)


class DiagnosticsDialog(QDialog):
    """Toon gestructureerde diagnostics; wijzigt geen tabel of DecisionStore."""

    def __init__(
        self,
        diag: dict,
        *,
        parent: QWidget | None = None,
        on_candidate_click: Callable[[str, dict], dict | None] | None = None,
        on_confirm_selection: Callable[[dict[str, Any]], dict | None] | None = None,
        on_save_profile: Callable[[dict[str, Any]], dict | None] | None = None,
        on_save_credit_profile: Callable[[dict[str, Any]], dict | None] | None = None,
        on_set_document_type: Callable[[str], dict | None] | None = None,
        limited_snapshot: bool = False,
    ) -> None:
        super().__init__(parent)
        header = diag.get("header") if isinstance(diag.get("header"), dict) else {}
        supplier_disp = str(header.get("supplier_display") or "").strip() or "—"
        if supplier_disp == "unknown_supplier":
            supplier_disp = tr("matching.fallback.unknown_supplier")
        self.setWindowTitle(tr("diagnostics.title", supplier=supplier_disp))
        self.setMinimumSize(560, 520)
        self._on_candidate_click = on_candidate_click
        self._on_confirm_selection = on_confirm_selection
        self._on_save_profile = on_save_profile
        self._on_save_credit_profile = on_save_credit_profile
        self._on_set_document_type = on_set_document_type
        self._diag: dict[str, Any] = {}
        # Local preview selection (no writes). Confirm button commits via callback.
        self._selected_candidates: dict[str, dict[str, Any]] = {}
        self._selected_values: dict[str, Any] = {}
        self._action_busy: bool = False

        root = QVBoxLayout(self)

        if limited_snapshot:
            banner = QLabel(tr("diagnostics.banner.limited"))
            banner.setWordWrap(True)
            banner.setStyleSheet(
                "background-color: #fff3cd; color: #664d03; padding: 8px; border-radius: 4px;"
            )
            root.addWidget(banner)

        pdf_base = str(header.get("pdf_basename") or "").strip()
        hdr_lines = [f"<b>{supplier_disp}</b>"]
        if pdf_base:
            hdr_lines.append(pdf_base)
        hdr_lbl = QLabel("<br/>".join(hdr_lines))
        hdr_lbl.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(hdr_lbl)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        root.addWidget(self._scroll, stretch=1)

        action_bar = QWidget()
        action_lay = QHBoxLayout(action_bar)
        action_lay.setContentsMargins(0, 8, 0, 8)
        self._confirm_btn = QPushButton(tr("diagnostics.button.confirm"))
        self._confirm_btn.clicked.connect(self._on_confirm_selection_clicked)
        action_lay.addWidget(self._confirm_btn)
        self._save_profile_btn = QPushButton(tr("diagnostics.button.save_profile"))
        self._save_profile_btn.clicked.connect(self._on_save_profile_clicked)
        action_lay.addWidget(self._save_profile_btn)
        self._save_credit_profile_btn = QPushButton(tr("diagnostics.button.save_credit_profile"))
        self._save_credit_profile_btn.clicked.connect(self._on_save_credit_profile_clicked)
        action_lay.addWidget(self._save_credit_profile_btn)
        action_lay.addStretch(1)
        root.addWidget(action_bar)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setText(tr("diagnostics.button.close"))

        root.addWidget(buttons)
        self.set_diag(diag)

    def set_diag(self, diag: dict, *, restore_scroll_y: int | None = None) -> None:
        """Replace dialog body with a new diagnostics dict (keeps dialog open)."""
        self._diag = diag if isinstance(diag, dict) else {}
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setSpacing(10)

        supplier = diag.get("supplier") if isinstance(diag.get("supplier"), dict) else {}
        amount = diag.get("amount") if isinstance(diag.get("amount"), dict) else {}
        vat_number = diag.get("vat_number") if isinstance(diag.get("vat_number"), dict) else {}
        kvk_number = diag.get("kvk_number") if isinstance(diag.get("kvk_number"), dict) else {}
        invoice_date = diag.get("invoice_date") if isinstance(diag.get("invoice_date"), dict) else {}
        email_domain = diag.get("email_domain") if isinstance(diag.get("email_domain"), dict) else {}
        invoice_number = diag.get("invoice_number") if isinstance(diag.get("invoice_number"), dict) else {}
        customer_number = diag.get("customer_number") if isinstance(diag.get("customer_number"), dict) else {}
        iban = diag.get("iban") if isinstance(diag.get("iban"), dict) else {}
        general = diag.get("general") if isinstance(diag.get("general"), dict) else {}
        load_error = general.get("load_error")
        doc_type = str(general.get("document_type") or "").strip()
        is_credit_row = doc_type == "credit_note"
        self._save_credit_profile_btn.setVisible(is_credit_row and self._on_save_credit_profile is not None)
        self._save_profile_btn.setVisible(not is_credit_row)

        body_lay.addWidget(
            self._section_group(
                title=tr("diagnostics.section.supplier"),
                icon=section_icon(
                    needs_attention=bool(supplier.get("needs_attention")),
                    is_error=str(supplier.get("status") or "") == "load_failed",
                ),
                lines=self._supplier_lines(supplier),
            )
        )
        body_lay.addWidget(
            self._section_group(
                title=tr("diagnostics.section.amount"),
                icon=section_icon(
                    needs_attention=bool(amount.get("needs_attention")),
                    is_error=str(amount.get("status") or "") == "failed" or bool(load_error),
                ),
                extra=self._field_candidates_extra(amount, kind="amount", field_id="amount"),
            )
        )
        body_lay.addWidget(
            self._section_group(
                title=tr("diagnostics.section.vat"),
                icon=section_icon(
                    needs_attention=bool(vat_number.get("needs_attention")),
                    is_error=False,
                ),
                lines=self._simple_value_lines(vat_number),
                extra=self._field_candidates_extra(vat_number, kind="ident", field_id="vat_number"),
            )
        )
        body_lay.addWidget(
            self._section_group(
                title=tr("diagnostics.section.kvk"),
                icon=section_icon(
                    needs_attention=bool(kvk_number.get("needs_attention")),
                    is_error=False,
                ),
                lines=self._simple_value_lines(kvk_number),
                extra=self._field_candidates_extra(kvk_number, kind="ident", field_id="kvk_number"),
            )
        )
        body_lay.addWidget(
            self._section_group(
                title=tr("diagnostics.section.invoice_date"),
                icon=section_icon(
                    needs_attention=bool(invoice_date.get("needs_attention")),
                    is_error=False,
                ),
                lines=self._simple_value_lines(invoice_date),
                extra=self._field_candidates_extra(invoice_date, kind="ident", field_id="invoice_date"),
            )
        )
        body_lay.addWidget(
            self._section_group(
                title=tr("diagnostics.section.email_domain"),
                icon=section_icon(
                    needs_attention=bool(email_domain.get("needs_attention")),
                    is_error=False,
                ),
                lines=self._simple_value_lines(email_domain),
                extra=self._field_candidates_extra(email_domain, kind="ident", field_id="email_domain"),
            )
        )
        body_lay.addWidget(
            self._section_group(
                title=tr("diagnostics.section.invoice_number"),
                icon=section_icon(
                    needs_attention=bool(invoice_number.get("needs_attention")),
                    is_error=False,
                ),
                lines=self._simple_value_lines(invoice_number),
                extra=self._field_candidates_extra(invoice_number, kind="ident", field_id="invoice_number"),
            )
        )
        body_lay.addWidget(
            self._section_group(
                title=tr("diagnostics.section.customer_number"),
                icon=section_icon(
                    needs_attention=bool(customer_number.get("needs_attention")),
                    is_error=False,
                ),
                lines=self._customer_number_section_lines(
                    customer_number,
                    local_selected=self._selected_values.get("customer_number"),
                ),
                extra=self._field_candidates_extra(customer_number, kind="ident", field_id="customer_number"),
            )
        )
        body_lay.addWidget(
            self._section_group(
                title=tr("diagnostics.section.iban"),
                icon=section_icon(
                    needs_attention=bool(iban.get("needs_attention")),
                    is_error=False,
                ),
                lines=self._iban_lines(iban),
                extra=self._iban_extra(iban),
            )
        )
        overall = str(diag.get("overall_status") or "")
        body_lay.addWidget(
            self._section_group(
                title=tr("diagnostics.section.general"),
                icon=section_icon(
                    needs_attention=overall == "needs_review" and not load_error,
                    is_error=bool(load_error) or overall == "error",
                ),
                lines=self._general_lines(general),
                extra=self._document_type_extra(general),
            )
        )

        suggestions = diag.get("action_suggestions")
        if isinstance(suggestions, list) and suggestions:
            sug_box = QGroupBox(tr("diagnostics.section.suggestions"))
            sug_lay = QVBoxLayout(sug_box)
            for s in suggestions:
                txt = str(s or "").strip()
                if txt:
                    sug_lay.addWidget(QLabel(f"• {txt}"))
            body_lay.addWidget(sug_box)

        body_lay.addStretch(1)
        self._scroll.setWidget(body)
        if restore_scroll_y is not None:
            QTimer.singleShot(0, lambda y=restore_scroll_y: self._restore_scroll_y(y))

    def _scroll_y(self) -> int:
        bar = self._scroll.verticalScrollBar()
        return int(bar.value()) if bar is not None else 0

    def _restore_scroll_y(self, target_y: int, *, attempt: int = 0) -> None:
        bar = self._scroll.verticalScrollBar()
        if bar is None:
            return
        max_y = bar.maximum()
        if max_y == 0 and attempt < 8:
            QTimer.singleShot(
                0,
                lambda y=target_y, n=attempt + 1: self._restore_scroll_y(y, attempt=n),
            )
            return
        bar.setValue(min(target_y, max_y))

    @staticmethod
    def _replace_child_widget(old: QWidget, new: QWidget) -> bool:
        parent = old.parentWidget()
        if parent is None:
            return False
        lay = parent.layout()
        if lay is None:
            return False
        for i in range(lay.count()):
            item = lay.itemAt(i)
            if item is not None and item.widget() is old:
                lay.removeWidget(old)
                old.setParent(None)
                old.deleteLater()
                lay.insertWidget(i, new)
                return True
        return False

    def _refresh_field_extra_inplace(
        self,
        field_id: str,
        block: dict[str, Any],
        *,
        kind: str,
    ) -> bool:
        body = self._scroll.widget()
        if body is None:
            return False
        old_extra = body.findChild(QWidget, f"diag_extra_{field_id}")
        if old_extra is None:
            return False
        new_extra = self._field_extra(block, kind=kind, field_id=field_id)
        if new_extra is None:
            return False
        if not self._replace_child_widget(old_extra, new_extra):
            return False
        return True

    def _apply_diag_pick_refresh(
        self,
        field_id: str,
        updated: dict[str, Any],
        *,
        scroll_y: int,
    ) -> None:
        """UI-vernieuwing na klik; altijd deferred (veilig na itemClicked)."""

        def _run() -> None:
            block_key = _DIAG_FIELD_BLOCKS.get(field_id)
            block = updated.get(block_key) if block_key else None
            kind = "amount" if field_id == "amount" else "ident"
            if isinstance(block, dict) and self._refresh_field_extra_inplace(
                field_id, block, kind=kind
            ):
                self._diag = updated if isinstance(updated, dict) else {}
                self._restore_scroll_y(scroll_y)
                return
            self.set_diag(updated, restore_scroll_y=scroll_y)

        QTimer.singleShot(0, _run)

    @staticmethod
    def _selected_value_from_block(block: dict) -> Any:
        sel = block.get("selected_value")
        if sel is not None and str(sel).strip():
            return sel
        val = block.get("value")
        if val is not None and str(val).strip():
            return val
        return None

    def selected_by_field(self) -> dict[str, Any]:
        diag = self._diag if isinstance(self._diag, dict) else {}
        out: dict[str, Any] = {}
        for field_id, block_key in _DIAG_FIELD_BLOCKS.items():
            if field_id in self._selected_values:
                out[field_id] = self._selected_values[field_id]
                continue
            block = diag.get(block_key) if isinstance(diag.get(block_key), dict) else {}
            val = self._selected_value_from_block(block)
            if val is not None:
                out[field_id] = val
        return out

    def _schedule_set_diag(self, diag: dict, *, scroll_y: int | None = None) -> None:
        """Vernieuw UI na signaal-handler; niet synchroon tijdens itemClicked (Qt-crash)."""
        if scroll_y is None:
            scroll_y = self._scroll_y()

        def _run() -> None:
            self.set_diag(diag, restore_scroll_y=scroll_y)

        QTimer.singleShot(0, _run)

    def _set_action_buttons_enabled(self, enabled: bool) -> None:
        self._confirm_btn.setEnabled(enabled)
        self._save_profile_btn.setEnabled(enabled)
        if hasattr(self, "_save_credit_profile_btn"):
            self._save_credit_profile_btn.setEnabled(enabled)

    def _on_confirm_selection_clicked(self) -> None:
        if self._on_confirm_selection is None or self._action_busy:
            return
        selected = self.selected_by_field()
        self._action_busy = True
        self._set_action_buttons_enabled(False)
        try:
            updated = self._on_confirm_selection(selected)
            if isinstance(updated, dict):
                self._schedule_set_diag(updated)
        finally:
            self._action_busy = False
            self._set_action_buttons_enabled(True)

    def _on_save_profile_clicked(self) -> None:
        if self._on_save_profile is None or self._action_busy:
            return
        selected = self.selected_by_field()
        self._action_busy = True
        self._set_action_buttons_enabled(False)
        try:
            updated = self._on_save_profile(selected)
            if isinstance(updated, dict):
                self._schedule_set_diag(updated)
        finally:
            self._action_busy = False
            self._set_action_buttons_enabled(True)

    def _on_save_credit_profile_clicked(self) -> None:
        if self._on_save_credit_profile is None:
            return
        updated = self._on_save_credit_profile(self.selected_by_field())
        if isinstance(updated, dict):
            self._schedule_set_diag(updated)

    @staticmethod
    def _section_group(
        *,
        title: str,
        icon: str,
        lines: list[str] | None = None,
        extra: QWidget | None = None,
    ) -> QGroupBox:
        box = QGroupBox(f"{icon} {title}")
        lay = QVBoxLayout(box)
        if lines:
            lbl = QLabel(_lines_block(lines))
            lbl.setWordWrap(True)
            lay.addWidget(lbl)
        if extra is not None:
            lay.addWidget(extra)
        return box

    @staticmethod
    def _supplier_lines(supplier: dict) -> list[str]:
        lines: list[str] = []
        st = _display_text(str(supplier.get("status_nl") or ""))
        if st:
            lines.append(st)
        detail = str(supplier.get("detail_nl") or "").strip()
        if detail:
            lines.append(detail)
        name = supplier.get("name")
        if name:
            lines.append(f"Naam: {name}")
        matched = supplier.get("matched_by")
        if isinstance(matched, list) and matched:
            labels = [tr_or_code(f"matching.signal.{m}", str(m)) for m in matched]
            lines.append(tr("diagnostics.label.match_via", matches=", ".join(labels)))
        flags = supplier.get("match_info_flags")
        if isinstance(flags, dict) and flags:
            active = [k for k, v in flags.items() if v]
            if active:
                lines.append(f"Signalen: {', '.join(active)}")
        return lines

    @staticmethod
    def _override_trace_lines(section: dict) -> list[str]:
        lines: list[str] = []
        reason_nl = str(section.get("override_reason_nl") or "").strip()
        if reason_nl:
            lines.append(tr("diagnostics.label.override_source", reason=_display_text(reason_nl)))
        if section.get("user_overridden"):
            lines.append("Gebruiker heeft dit veld handmatig vergrendeld.")
        prev = section.get("previous_value")
        if prev is not None and str(prev).strip():
            lines.append(f"Vorige waarde: {prev}")
        trace = section.get("decision_trace_human")
        if not isinstance(trace, list) or not trace:
            trace = section.get("decision_trace")
        if isinstance(trace, list) and trace:
            lines.append("Beslisspoor:")
            for entry in trace:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("kind") or "") == "final":
                    reason = str(
                        entry.get("final_decision_reason_nl") or ""
                    ).strip()
                    winner = entry.get("winner") if isinstance(entry.get("winner"), dict) else {}
                    w_src = str(
                        winner.get("source_nl") or ""
                    ).strip() or "onbekende bron"
                    w_val = winner.get("value")
                    try:
                        w_conf = int(winner.get("confidence") or 0)
                    except (TypeError, ValueError):
                        w_conf = 0
                    lines.append(
                        f"  • Eindkeuze: {reason or 'Gekozen op basis van beschikbare signalen'} ({w_conf}%)"
                    )
                    if w_val is not None and str(w_val).strip():
                        lines.append(f"    Waarde: {w_val}")
                    lines.append(f"    Bron: {w_src}")
                    continue
                src = str(entry.get("source_nl") or "").strip() or tr("diagnostics.label.source_in_doc")
                try:
                    conf = int(entry.get("confidence") or 0)
                except (TypeError, ValueError):
                    conf = 0
                win = " (gekozen)" if entry.get("win") else ""
                excl = str(
                    entry.get("rejection_reason_nl")
                    or entry.get("rejection_reason")
                    or entry.get("excluded_reason")
                    or ""
                ).strip()
                val = entry.get("value")
                rank = entry.get("rank")
                r_txt = f" #{int(rank)}" if isinstance(rank, int) or str(rank).isdigit() else ""
                v_txt = f" ({val})" if val is not None and str(val).strip() else ""
                lines.append(f"  • {src}{r_txt}: {conf}%{win}{v_txt}")
                if excl and not entry.get("win"):
                    lines.append(f"    Niet gekozen: {excl}")
        return lines

    @staticmethod
    def _winner_candidate(field: dict) -> dict[str, Any] | None:
        cands = field.get("candidates")
        if not isinstance(cands, list):
            return None
        resolved = str(field.get("selected_value") or field.get("value") or "").strip()
        for cand in cands:
            if not isinstance(cand, dict):
                continue
            if cand.get("is_resolved"):
                return cand
            if resolved and str(cand.get("value") or "").strip() == resolved:
                return cand
        return None

    @staticmethod
    def _trace_maps(
        field: dict,
    ) -> tuple[dict[tuple[str, str], str], set[tuple[str, str]]]:
        rej_by_key: dict[tuple[str, str], str] = {}
        win_by_key: set[tuple[str, str]] = set()
        trace = field.get("decision_trace_human")
        if not isinstance(trace, list) or not trace:
            trace = field.get("decision_trace")
        if not isinstance(trace, list):
            return rej_by_key, win_by_key
        for entry in trace:
            if not isinstance(entry, dict) or str(entry.get("kind") or "") == "final":
                continue
            src = str(entry.get("source") or "").strip()
            val = str(entry.get("value") or "").strip()
            if not (src and val):
                continue
            if entry.get("win"):
                win_by_key.add((src, val))
            reason = str(
                entry.get("rejection_reason_nl")
                or ""
            ).strip()
            if reason:
                rej_by_key[(src, val)] = reason
            elif entry.get("rejection_reason") or entry.get("excluded_reason"):
                rej_by_key[(src, val)] = "Niet gekozen omdat een andere kandidaat sterker was"
        return rej_by_key, win_by_key

    @staticmethod
    def _candidate_status_line(cand: dict[str, Any], *, is_resolved: bool, is_selected: bool) -> str:
        disp = str(cand.get("value_display") or cand.get("value") or "?")
        if is_resolved:
            return f"{checkmark_prefix(is_selected=True)}{disp}"
        if is_selected:
            return f"🔵 Preview: {disp}"
        return disp

    @staticmethod
    def _candidate_detail_lines(cand: dict[str, Any], *, conf: int, reason: str = "") -> list[str]:
        lines = [f"Betrouwbaarheid: {conf}%"]
        source = str(cand.get("label") or cand.get("source_nl") or "").strip()
        if source:
            lines.append(f"Bron: {source}")
        hint = str(cand.get("context_hint_nl") or "").strip()
        if hint:
            lines.append(f"Locatie: {hint}")
        method = str(cand.get("extraction_method_nl") or "").strip()
        if method:
            lines.append(method)
        label_reason = str(cand.get("label_reason_nl") or "").strip()
        if label_reason:
            lines.append(label_reason)
        if reason:
            lines.append(f"Niet gekozen: {reason}")
        return lines

    def _why_chosen_lines(self, field: dict, *, field_id: str) -> list[str]:
        lines: list[str] = []
        winner = self._winner_candidate(field)
        if winner is None:
            return lines
        conf = int(winner.get("confidence") or 0)
        lines.append(
            f"• {str(winner.get('value_display') or winner.get('value') or '?')} is nu de keuze ({conf}%)."
        )
        trace = field.get("decision_trace_human")
        if not isinstance(trace, list) or not trace:
            trace = field.get("decision_trace")
        if isinstance(trace, list):
            for entry in trace:
                if isinstance(entry, dict) and str(entry.get("kind") or "") == "final":
                    reason = str(
                        entry.get("final_decision_reason_nl") or ""
                    ).strip()
                    if reason:
                        lines.append(f"• {reason}.")
                    break
        src = str(winner.get("label") or winner.get("source_nl") or "").strip()
        if src:
            lines.append(f"• Bron: {src}.")
        hint = str(winner.get("context_hint_nl") or "").strip()
        if hint:
            lines.append(f"• Waarde stond in de {hint}.")
        method = str(winner.get("extraction_method_nl") or "").strip()
        if method:
            lines.append(f"• {method}.")
        if field_id == "amount":
            override_reason = str(field.get("override_reason_nl") or "").strip()
            if override_reason:
                lines.append(f"• {override_reason}.")
        return lines

    def _why_not_chosen_lines(self, field: dict, selected: dict[str, Any]) -> list[str]:
        lines: list[str] = []
        winner = self._winner_candidate(field)
        selected_val = str(selected.get("value") or "").strip()
        if winner is None or selected_val == str(winner.get("value") or "").strip():
            return lines
        sel_conf = int(selected.get("confidence") or 0)
        win_conf = int(winner.get("confidence") or 0)
        diff = max(win_conf - sel_conf, 0)
        if diff > 0:
            lines.append(
                f"• Lagere betrouwbaarheid ({sel_conf}%) dan de huidige keuze ({win_conf}%, verschil {diff}%)."
            )
        rej_by_key, _ = self._trace_maps(field)
        key = (str(selected.get("source") or "").strip(), selected_val)
        reason = str(rej_by_key.get(key) or "").strip()
        if reason:
            lines.append(f"• {reason}.")
        label_reason = str(selected.get("label_reason_nl") or "").strip()
        if not label_reason:
            lines.append("• Geen sterk labelsignaal gevonden.")
        method = str(selected.get("extraction_method_nl") or "").strip()
        if method:
            lines.append(f"• Alleen via: {method.lower()}.")
        hint = str(selected.get("context_hint_nl") or "").strip()
        if hint:
            lines.append(f"• Gevonden in {hint}.")
        return lines

    @staticmethod
    def _simple_value_lines(section: dict) -> list[str]:
        st = str(section.get("status_nl") or "").strip()
        val = section.get("value_display")
        if val is None:
            val = section.get("value")
        lines: list[str] = []
        if st:
            lines.append(st)
        if val:
            lines.append(f"Waarde: {val}")
        lines.extend(DiagnosticsDialog._override_trace_lines(section))
        return lines

    @staticmethod
    def _customer_number_section_lines(
        section: dict,
        *,
        local_selected: Any = None,
    ) -> list[str]:
        if is_customer_absent_pick(local_selected if isinstance(local_selected, dict) else None):
            lines = ["Geen klantnummer (handmatig gekozen)", "Waarde: Geen klantnummer"]
            lines.extend(DiagnosticsDialog._override_trace_lines(section))
            return lines
        return DiagnosticsDialog._simple_value_lines(section)

    def _iban_extra(self, iban: dict) -> QWidget | None:
        cands = iban.get("candidates")
        if not isinstance(cands, list) or not cands:
            return None
        return self._field_candidates_extra(
            {
                "value": iban.get("value"),
                "selected_value": iban.get("value"),
                "candidates": cands,
            },
            kind="ident",
            field_id="iban",
        )

    @staticmethod
    def _iban_lines(iban: dict) -> list[str]:
        lines: list[str] = []
        st = str(iban.get("status_nl") or "").strip()
        if st:
            lines.append(st)
        masked = str(iban.get("masked_value") or "").strip()
        if masked:
            lines.append(f"IBAN: {masked}")
        all_masked = iban.get("all_ibans_masked")
        if isinstance(all_masked, list) and len(all_masked) > 1:
            lines.append("Alle IBAN's in PDF:")
            for m in all_masked:
                lines.append(f"  • {m}")
        warnings = iban.get("warnings_nl")
        if isinstance(warnings, list):
            for w in warnings:
                ws = str(w or "").strip()
                if ws:
                    lines.append(ws)
        if iban.get("ocr_attempted"):
            lines.append("OCR voor IBAN geprobeerd.")
        ocr_err = iban.get("ocr_error")
        if ocr_err:
            lines.append(f"OCR-fout: {ocr_err}")
        lines.extend(DiagnosticsDialog._override_trace_lines(iban))
        return lines

    def _document_type_extra(self, general: dict) -> QWidget | None:
        if self._on_set_document_type is None or not general.get("can_set_document_type"):
            return None
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        invoice_btn = QPushButton(tr("diagnostics.button.mark_invoice"))
        credit_btn = QPushButton(tr("diagnostics.button.mark_credit_note"))

        def _apply(target_type: str) -> None:
            updated = self._on_set_document_type(target_type)
            if isinstance(updated, dict):
                self.set_diag(updated, restore_scroll_y=self._scroll_y())

        invoice_btn.clicked.connect(lambda: _apply("invoice"))
        credit_btn.clicked.connect(lambda: _apply("credit_note"))
        lay.addWidget(invoice_btn)
        lay.addWidget(credit_btn)
        lay.addStretch(1)
        return wrap

    @staticmethod
    def _general_lines(general: dict) -> list[str]:
        lines: list[str] = []
        load_nl = general.get("load_error_nl")
        if load_nl:
            lines.append(str(load_nl))
        ds = general.get("decision_status")
        if ds:
            lines.append(f"Engine-status: {ds}")
        rc_nl = general.get("decision_reason_nl")
        if rc_nl:
            lines.append(str(rc_nl))
        detail = general.get("decision_reason_detail")
        if detail:
            lines.append(str(detail))
        doc_type = str(general.get("document_type") or "").strip()
        if doc_type:
            key = "diagnostics.document_type.invoice" if doc_type == "invoice" else "diagnostics.document_type.credit_note"
            lines.append(tr("diagnostics.label.document_type", label=tr(key)))
        return lines

    def _field_candidates_extra(
        self,
        field: dict,
        *,
        kind: str = "ident",
        field_id: str,
    ) -> QWidget | None:
        return self._field_extra(field, kind=kind, field_id=field_id)

    def _populate_candidate_list(
        self,
        lw: QListWidget,
        field: dict,
        *,
        kind: str,
        field_id: str,
    ) -> dict[str, Any] | None:
        lw.clear()
        lw.setAutoScroll(False)
        lw.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lw.setObjectName(f"diag_candidates_{field_id}")
        cands = field.get("candidates")
        if not isinstance(cands, list):
            return None
        resolved = str(field.get("selected_value") or field.get("value") or "").strip()
        rej_by_key, win_by_key = self._trace_maps(field)

        selected_val = self._selected_values.get(field_id)
        absent_selected = field_id == "customer_number" and is_customer_absent_pick(
            selected_val if isinstance(selected_val, dict) else None
        )
        selected_cand: dict[str, Any] | None = None
        for c in cands:
            if not isinstance(c, dict):
                continue
            try:
                conf = int(c.get("confidence") or 0)
            except (TypeError, ValueError):
                conf = 0
            raw_val = str(c.get("value") or "").strip()
            is_resolved = (
                not absent_selected
                and (
                    bool(c.get("is_resolved"))
                    or (resolved and raw_val == resolved)
                )
            )
            is_selected = (
                not absent_selected
                and selected_val is not None
                and not isinstance(selected_val, dict)
                and raw_val == str(selected_val).strip()
            )
            if is_selected:
                selected_cand = c
            src_key = str(c.get("source") or "").strip()
            reason = ""
            if src_key and raw_val:
                reason = str(rej_by_key.get((src_key, raw_val)) or "").strip()

            title = self._candidate_status_line(c, is_resolved=is_resolved, is_selected=is_selected)
            details = self._candidate_detail_lines(c, conf=conf, reason=reason if not is_resolved else "")
            item = QListWidgetItem("\n".join([title, *details]))
            if is_resolved:
                item.setForeground(QColor(0, 128, 0))
            elif is_selected:
                item.setForeground(QColor(20, 90, 180))
                item.setBackground(QColor(225, 238, 255))
            elif reason and (src_key, raw_val) not in win_by_key:
                item.setForeground(QColor(130, 110, 70))
            else:
                item.setForeground(confidence_color(conf, missing=False))
            tip = candidate_menu_tooltip(c, max_len=480)
            if tip:
                item.setToolTip(tip)
            item.setData(Qt.ItemDataRole.UserRole, c)
            lw.addItem(item)
        lw.itemClicked.connect(
            lambda item, fid=field_id: self._on_candidate_item_clicked(fid, item)
        )
        lw.setMaximumHeight(min(140 + 40 * len(cands), 360))
        return selected_cand

    def _field_extra(
        self,
        field: dict,
        *,
        kind: str = "ident",
        field_id: str,
    ) -> QWidget | None:
        container = QWidget()
        container.setObjectName(f"diag_extra_{field_id}")
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)

        if kind == "amount":
            st = str(field.get("status_nl") or "").strip()
            if st:
                lay.addWidget(QLabel(_display_text(st)))
            detail = str(field.get("detail_nl") or "").strip()
            if detail:
                lay.addWidget(QLabel(_display_text(detail)))
            vd = str(field.get("value_display") or "").strip()
            if vd:
                lay.addWidget(QLabel(tr("diagnostics.label.displayed", value=vd)))
            engine_nl = field.get("engine_reason_nl")
            if engine_nl:
                lay.addWidget(QLabel(_display_text(str(engine_nl))))
            warnings = field.get("warnings_nl")
            if isinstance(warnings, list):
                for w in warnings:
                    ws = str(w or "").strip()
                    if ws:
                        lay.addWidget(QLabel(_display_text(ws)))
            for line in self._override_trace_lines(field):
                lay.addWidget(QLabel(_display_text(line)))

        cands = field.get("candidates")
        selected_candidate: dict[str, Any] | None = None
        if isinstance(cands, list) and cands:
            lay.addWidget(QLabel(f"<b>{tr('diagnostics.label.candidates')}</b>"))
            lw = QListWidget()
            selected_candidate = self._populate_candidate_list(
                lw, field, kind=kind, field_id=field_id
            )
            lay.addWidget(lw)

        if field_id == "customer_number":
            absent_btn = QPushButton(tr(CUSTOMER_ABSENT_MENU_LABEL_KEY))
            absent_btn.clicked.connect(
                lambda _checked=False, fid=field_id: self._on_customer_absent_clicked(fid)
            )
            lay.addWidget(absent_btn)
            if is_customer_absent_pick(self._selected_values.get("customer_number")):
                lay.addWidget(QLabel(f"<b>{tr('diagnostics.label.chosen_none')}</b>"))

        why_chosen = self._why_chosen_lines(field, field_id=field_id)
        if why_chosen:
            lay.addWidget(QLabel(f"<b>{tr('diagnostics.label.why_chosen')}</b>"))
            for line in why_chosen:
                lay.addWidget(QLabel(_display_text(line)))

        if selected_candidate is not None:
            why_not = self._why_not_chosen_lines(field, selected_candidate)
            if why_not:
                lay.addWidget(QLabel(f"<b>{tr('diagnostics.label.why_not_chosen')}</b>"))
                for line in why_not:
                    lay.addWidget(QLabel(_display_text(line)))

        if lay.count() == 0:
            return None
        return container

    def _on_customer_absent_clicked(self, field_id: str) -> None:
        cand = make_customer_absent_pick_candidate()
        self._selected_values[field_id] = cand
        # #region agent log (debug mode)
        _dbg_log_3d66a1(
            hypothesis_id="H1",
            location="ui/diagnostics_dialog.py:_on_customer_absent_clicked",
            message="absent button preview only",
            data={
                "field_id": field_id,
                "cand_absent": bool(cand.get("absent")),
            },
            run_id="preview-only",
        )
        # #endregion
        self._schedule_set_diag(self._diag)

    def _on_candidate_item_clicked(self, field_id: str, item: QListWidgetItem) -> None:
        cand = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(cand, dict):
            return
        if field_id == "customer_number" and is_customer_absent_pick(cand):
            self._on_customer_absent_clicked(field_id)
            return
        self._selected_values[field_id] = cand.get("value")
        self._schedule_set_diag(self._diag)
