"""Read-only diagnostics popup voor een betalingsrij (PySide6)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


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
        on_pick_amount: Callable[[], None] | None = None,
        limited_snapshot: bool = False,
        profile_confirm_eligible: bool = False,
        on_profile_confirm: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        header = diag.get("header") if isinstance(diag.get("header"), dict) else {}
        supplier_disp = str(header.get("supplier_display") or "").strip() or "—"
        self.setWindowTitle(f"Diagnostics — {supplier_disp}")
        self.setMinimumSize(560, 520)
        self._on_pick_amount = on_pick_amount
        self._on_profile_confirm = on_profile_confirm

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

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
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
                extra=self._amount_extra(amount),
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
                extra=self._ident_field_extra(invoice_number),
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
                extra=self._ident_field_extra(customer_number),
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
        scroll.setWidget(body)
        root.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setText("Sluiten")

        pick_visible = (
            str(amount.get("status") or "") == "ambiguous"
            and isinstance(amount.get("candidates"), list)
            and len(amount.get("candidates") or []) > 0
        )
        if pick_visible and on_pick_amount is not None:
            pick_btn = QPushButton("Bedrag kiezen")
            pick_btn.clicked.connect(self._on_pick_amount_clicked)
            buttons.addButton(pick_btn, QDialogButtonBox.ButtonRole.ActionRole)

        if profile_confirm_eligible and on_profile_confirm is not None:
            profile_btn = QPushButton("Bevestig factuurgegevens…")
            profile_btn.clicked.connect(self._on_profile_confirm_clicked)
            buttons.addButton(profile_btn, QDialogButtonBox.ButtonRole.ActionRole)

        root.addWidget(buttons)

    def _on_pick_amount_clicked(self) -> None:
        self.accept()
        if self._on_pick_amount is not None:
            self._on_pick_amount()

    def _on_profile_confirm_clicked(self) -> None:
        self.accept()
        if self._on_profile_confirm is not None:
            self._on_profile_confirm()

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
    def _simple_value_lines(section: dict) -> list[str]:
        st = str(section.get("status_nl") or "").strip()
        val = section.get("value")
        lines: list[str] = []
        if st:
            lines.append(st)
        if val:
            lines.append(f"Waarde: {val}")
        return lines

    def _iban_extra(self, iban: dict) -> QWidget | None:
        cands = iban.get("candidates")
        if not isinstance(cands, list) or not cands:
            all_masked = iban.get("all_ibans_masked")
            if isinstance(all_masked, list) and all_masked:
                cands = [
                    {
                        "value": m,
                        "value_display": m,
                        "source_nl": "Gevonden",
                        "confidence": 90,
                    }
                    for m in all_masked
                    if str(m or "").strip()
                ]
        if not cands:
            return None
        return self._ident_field_extra({"value": iban.get("masked_value"), "candidates": cands})

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

    def _ident_field_extra(self, field: dict) -> QWidget | None:
        cands = field.get("candidates")
        if not isinstance(cands, list) or not cands:
            return None
        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("Kandidaten:"))
        lw = QListWidget()
        resolved = str(field.get("value") or "").strip()
        for c in cands:
            if not isinstance(c, dict):
                continue
            val = str(c.get("value_display") or c.get("value") or "?")
            lbl = str(c.get("label") or c.get("source_nl") or "")
            try:
                conf = int(c.get("confidence") or 0)
            except (TypeError, ValueError):
                conf = 0
            is_resolved = bool(c.get("is_resolved")) or (
                resolved and val == resolved
            )
            text = f"{val} — {lbl} ({conf}%)" if lbl else f"{val} ({conf}%)"
            if is_resolved:
                text = f"✓ {text}"
            item = QListWidgetItem(text)
            if is_resolved:
                item.setForeground(QColor(0, 128, 0))
            else:
                item.setForeground(_confidence_color(conf))
            if c.get("context_preview"):
                item.setToolTip(str(c.get("context_preview")))
            lw.addItem(item)
        lw.setMaximumHeight(min(120 + 24 * len(cands), 280))
        lay.addWidget(lw)
        return container

    def _amount_extra(self, amount: dict) -> QWidget | None:
        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)

        st = str(amount.get("status_nl") or "").strip()
        if st:
            lay.addWidget(QLabel(st))
        detail = str(amount.get("detail_nl") or "").strip()
        if detail:
            lay.addWidget(QLabel(detail))
        vd = str(amount.get("value_display") or "").strip()
        if vd:
            lay.addWidget(QLabel(f"Gekozen/weergegeven: {vd}"))

        engine_nl = amount.get("engine_reason_nl")
        if engine_nl:
            lay.addWidget(QLabel(str(engine_nl)))
        warnings = amount.get("warnings_nl")
        if isinstance(warnings, list):
            for w in warnings:
                ws = str(w or "").strip()
                if ws:
                    lay.addWidget(QLabel(ws))

        cands = amount.get("candidates")
        if isinstance(cands, list) and cands:
            lay.addWidget(QLabel("Kandidaten:"))
            lw = QListWidget()
            for c in cands:
                if not isinstance(c, dict):
                    continue
                disp = str(c.get("value_display") or c.get("value") or "?")
                src_nl = str(c.get("source_nl") or c.get("source") or "")
                try:
                    conf = int(c.get("confidence") or 0)
                except (TypeError, ValueError):
                    conf = 0
                label = f"{disp} — {src_nl} ({conf}%)"
                item = QListWidgetItem(label)
                item.setForeground(_confidence_color(conf))
                preview = c.get("context_preview")
                if preview:
                    item.setToolTip(str(preview))
                lw.addItem(item)
            lw.setMaximumHeight(min(120 + 24 * len(cands), 280))
            lay.addWidget(lw)

        if lay.count() == 0:
            return None
        return container
