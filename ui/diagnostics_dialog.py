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

from ui.field_review import (
    CUSTOMER_ABSENT_MENU_LABEL_NL,
    make_customer_absent_pick_candidate,
    is_customer_absent_pick,
)

_DIAG_FIELD_BLOCKS: dict[str, str] = {
    "amount": "amount",
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


def _confidence_color(confidence: int) -> QColor:
    if confidence >= 85:
        return QColor(0, 128, 0)
    if confidence >= 50:
        return QColor(200, 120, 0)
    return QColor(180, 0, 0)


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
        limited_snapshot: bool = False,
    ) -> None:
        super().__init__(parent)
        header = diag.get("header") if isinstance(diag.get("header"), dict) else {}
        supplier_disp = str(header.get("supplier_display") or "").strip() or "—"
        self.setWindowTitle(f"Diagnostics — {supplier_disp}")
        self.setMinimumSize(560, 520)
        self._on_candidate_click = on_candidate_click
        self._on_confirm_selection = on_confirm_selection
        self._on_save_profile = on_save_profile
        self._selected_by_field: dict[str, Any] = {}

        root = QVBoxLayout(self)

        if limited_snapshot:
            banner = QLabel(
                "Herlaad batch voor volledige diagnostics "
                "(parser- en matchgegevens kunnen ontbreken)."
            )
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
        self._confirm_btn = QPushButton("Bevestig selectie")
        self._confirm_btn.clicked.connect(self._on_confirm_selection_clicked)
        action_lay.addWidget(self._confirm_btn)
        self._save_profile_btn = QPushButton("Sla profiel op")
        self._save_profile_btn.clicked.connect(self._on_save_profile_clicked)
        action_lay.addWidget(self._save_profile_btn)
        action_lay.addStretch(1)
        root.addWidget(action_bar)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setText("Sluiten")

        root.addWidget(buttons)
        self.set_diag(diag)

    def set_diag(self, diag: dict, *, restore_scroll_y: int | None = None) -> None:
        """Replace dialog body with a new diagnostics dict (keeps dialog open)."""
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setSpacing(10)

        supplier = diag.get("supplier") if isinstance(diag.get("supplier"), dict) else {}
        amount = diag.get("amount") if isinstance(diag.get("amount"), dict) else {}
        invoice_number = diag.get("invoice_number") if isinstance(diag.get("invoice_number"), dict) else {}
        customer_number = diag.get("customer_number") if isinstance(diag.get("customer_number"), dict) else {}
        iban = diag.get("iban") if isinstance(diag.get("iban"), dict) else {}
        general = diag.get("general") if isinstance(diag.get("general"), dict) else {}
        load_error = general.get("load_error")

        body_lay.addWidget(
            self._section_group(
                title="Leverancier",
                icon=section_icon(
                    needs_attention=bool(supplier.get("needs_attention")),
                    is_error=str(supplier.get("status") or "") == "load_failed",
                ),
                lines=self._supplier_lines(supplier),
            )
        )
        body_lay.addWidget(
            self._section_group(
                title="Bedrag",
                icon=section_icon(
                    needs_attention=bool(amount.get("needs_attention")),
                    is_error=str(amount.get("status") or "") == "failed" or bool(load_error),
                ),
                extra=self._field_candidates_extra(amount, kind="amount", field_id="amount"),
            )
        )
        body_lay.addWidget(
            self._section_group(
                title="Factuur-/polisnummer",
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
                title="Klantnummer",
                icon=section_icon(
                    needs_attention=bool(customer_number.get("needs_attention")),
                    is_error=False,
                ),
                lines=self._simple_value_lines(customer_number),
                extra=self._field_candidates_extra(customer_number, kind="ident", field_id="customer_number"),
            )
        )
        body_lay.addWidget(
            self._section_group(
                title="IBAN",
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
                title="Algemeen",
                icon=section_icon(
                    needs_attention=overall == "needs_review" and not load_error,
                    is_error=bool(load_error) or overall == "error",
                ),
                lines=self._general_lines(general),
            )
        )

        suggestions = diag.get("action_suggestions")
        if isinstance(suggestions, list) and suggestions:
            sug_box = QGroupBox("Actiesuggesties")
            sug_lay = QVBoxLayout(sug_box)
            for s in suggestions:
                txt = str(s or "").strip()
                if txt:
                    sug_lay.addWidget(QLabel(f"• {txt}"))
            body_lay.addWidget(sug_box)

        body_lay.addStretch(1)
        self._scroll.setWidget(body)
        self._sync_selected_from_diag(diag)
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
                self._sync_selected_from_diag(updated)
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

    def _sync_selected_from_diag(self, diag: dict) -> None:
        """Houd lokale selectie in sync met diagnostics-weergave."""
        for field_id, block_key in _DIAG_FIELD_BLOCKS.items():
            block = diag.get(block_key) if isinstance(diag.get(block_key), dict) else {}
            val = self._selected_value_from_block(block)
            if val is not None:
                self._selected_by_field[field_id] = val

    def selected_by_field(self) -> dict[str, Any]:
        return dict(self._selected_by_field)

    def _schedule_set_diag(self, diag: dict, *, scroll_y: int | None = None) -> None:
        """Vernieuw UI na signaal-handler; niet synchroon tijdens itemClicked (Qt-crash)."""
        if scroll_y is None:
            scroll_y = self._scroll_y()

        def _run() -> None:
            self.set_diag(diag, restore_scroll_y=scroll_y)

        QTimer.singleShot(0, _run)

    def _on_confirm_selection_clicked(self) -> None:
        if self._on_confirm_selection is None:
            return
        updated = self._on_confirm_selection(self.selected_by_field())
        if isinstance(updated, dict):
            self._schedule_set_diag(updated)

    def _on_save_profile_clicked(self) -> None:
        if self._on_save_profile is None:
            return
        updated = self._on_save_profile(self.selected_by_field())
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
        st = str(supplier.get("status_nl") or "").strip()
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
            lines.append(f"Match via: {', '.join(str(m) for m in matched)}")
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
            lines.append(f"Bron & overschrijving: {reason_nl}")
        if section.get("user_overridden"):
            lines.append("Gebruiker heeft dit veld handmatig vergrendeld.")
        prev = section.get("previous_value")
        if prev is not None and str(prev).strip():
            lines.append(f"Vorige waarde: {prev}")
        trace = section.get("decision_trace")
        if isinstance(trace, list) and trace:
            lines.append("Beslissing:")
            for entry in trace:
                if not isinstance(entry, dict):
                    continue
                src = str(entry.get("source") or "?")
                try:
                    conf = int(entry.get("confidence") or 0)
                except (TypeError, ValueError):
                    conf = 0
                win = " ← gekozen" if entry.get("win") else ""
                excl = ""
                if entry.get("excluded_reason"):
                    excl = f" ({entry['excluded_reason']})"
                lines.append(f"  • {src} {conf}%{win}{excl}")
        return lines

    @staticmethod
    def _simple_value_lines(section: dict) -> list[str]:
        st = str(section.get("status_nl") or "").strip()
        val = section.get("value")
        lines: list[str] = []
        if st:
            lines.append(st)
        if val:
            lines.append(f"Waarde: {val}")
        lines.extend(DiagnosticsDialog._override_trace_lines(section))
        return lines

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
    ) -> None:
        lw.clear()
        lw.setAutoScroll(False)
        lw.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lw.setObjectName(f"diag_candidates_{field_id}")
        cands = field.get("candidates")
        if not isinstance(cands, list):
            return
        resolved = str(field.get("selected_value") or field.get("value") or "").strip()
        for c in cands:
            if not isinstance(c, dict):
                continue
            disp = str(c.get("value_display") or c.get("value") or "?")
            if kind == "ident":
                lbl = str(c.get("label") or c.get("source_nl") or "")
                try:
                    conf = int(c.get("confidence") or 0)
                except (TypeError, ValueError):
                    conf = 0
                raw_val = str(c.get("value") or "").strip()
                is_resolved = bool(c.get("is_resolved")) or (
                    resolved and (raw_val == resolved or disp == resolved)
                )
                text = f"{disp} — {lbl} ({conf}%)" if lbl else f"{disp} ({conf}%)"
                if is_resolved:
                    text = f"✓ {text}"
                item = QListWidgetItem(text)
                if is_resolved:
                    item.setForeground(QColor(0, 128, 0))
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                else:
                    item.setForeground(_confidence_color(conf))
            else:
                src_nl = str(c.get("source_nl") or c.get("source") or "")
                try:
                    conf = int(c.get("confidence") or 0)
                except (TypeError, ValueError):
                    conf = 0
                item = QListWidgetItem(f"{disp} — {src_nl} ({conf}%)")
                is_resolved = bool(c.get("is_resolved")) or (
                    resolved and str(c.get("value") or "").strip() == resolved
                )
                if is_resolved:
                    item.setText(f"✓ {item.text()}")
                    item.setForeground(QColor(0, 128, 0))
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                else:
                    item.setForeground(_confidence_color(conf))
            preview = c.get("context_preview")
            if preview:
                item.setToolTip(str(preview))
            item.setData(Qt.ItemDataRole.UserRole, c)
            lw.addItem(item)
        lw.itemClicked.connect(
            lambda item, fid=field_id: self._on_candidate_item_clicked(fid, item)
        )
        lw.setMaximumHeight(min(120 + 24 * len(cands), 280))

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
                lay.addWidget(QLabel(st))
            detail = str(field.get("detail_nl") or "").strip()
            if detail:
                lay.addWidget(QLabel(detail))
            vd = str(field.get("value_display") or "").strip()
            if vd:
                lay.addWidget(QLabel(f"Gekozen/weergegeven: {vd}"))
            engine_nl = field.get("engine_reason_nl")
            if engine_nl:
                lay.addWidget(QLabel(str(engine_nl)))
            warnings = field.get("warnings_nl")
            if isinstance(warnings, list):
                for w in warnings:
                    ws = str(w or "").strip()
                    if ws:
                        lay.addWidget(QLabel(ws))
            for line in self._override_trace_lines(field):
                lay.addWidget(QLabel(line))

        cands = field.get("candidates")
        if isinstance(cands, list) and cands:
            lay.addWidget(QLabel("Kandidaten:"))
            lw = QListWidget()
            self._populate_candidate_list(lw, field, kind=kind, field_id=field_id)
            lay.addWidget(lw)
            if field_id == "customer_number":
                absent_btn = QPushButton(CUSTOMER_ABSENT_MENU_LABEL_NL)
                absent_btn.clicked.connect(
                    lambda fid=field_id: self._on_customer_absent_clicked(fid)
                )
                lay.addWidget(absent_btn)

        if lay.count() == 0:
            return None
        return container

    def _on_customer_absent_clicked(self, field_id: str) -> None:
        if self._on_candidate_click is None:
            return
        scroll_y = self._scroll_y()
        self._selected_by_field[field_id] = None
        updated = self._on_candidate_click(
            field_id, make_customer_absent_pick_candidate()
        )
        if isinstance(updated, dict):
            self._apply_diag_pick_refresh(field_id, updated, scroll_y=scroll_y)

    def _on_candidate_item_clicked(self, field_id: str, item: QListWidgetItem) -> None:
        scroll_y = self._scroll_y()
        if self._on_candidate_click is None:
            return
        cand = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(cand, dict):
            return
        if field_id == "customer_number" and is_customer_absent_pick(cand):
            self._on_customer_absent_clicked(field_id)
            return
        raw = cand.get("value")
        if raw is not None and str(raw).strip():
            self._selected_by_field[field_id] = raw
        updated = self._on_candidate_click(field_id, cand)
        if isinstance(updated, dict):
            self._apply_diag_pick_refresh(field_id, updated, scroll_y=scroll_y)
