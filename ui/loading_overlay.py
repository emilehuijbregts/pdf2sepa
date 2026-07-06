"""Professional loading overlay — Live Activity Log design.

Visual language: minimal dark card, rotating arc indicator next to title,
live log of processing steps (like ChatGPT search activity), thin spark bar.

Components:
- MiniSpinner     : tiny 20 px rotating arc next to title
- ActivityLogWidget: scrolling log of processing steps with fade-in
- SparkBar        : thin 2 px track with glowing spark at leading edge
- LoadingOverlay  : full-window dark overlay containing the card
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.i18n import tr
from ui.i18n.strings import UiStrings

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
ACCENT = QColor("#4f9cf9")
CARD_BG = QColor("#0c0c18")
OVERLAY_BG = QColor(0, 0, 0, 190)
TEXT_PRIMARY = "#eeeef4"
TEXT_DIM = "#4a4a62"
TEXT_DONE = "#72728a"
TEXT_ACTIVE = "#c0d4f0"
CHECK_COLOR = QColor("#3d7a3d")  # subtle green checkmark

_ETA_MIN_SAMPLES = 3


# ---------------------------------------------------------------------------
# MiniSpinner — small rotating arc next to the title
# ---------------------------------------------------------------------------

class MiniSpinner(QWidget):
    """20 × 20 thin rotating arc — subtle AI-thinking cue."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._angle = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self.setFixedSize(20, 20)

    def start_animation(self) -> None:
        self._angle = 0.0
        self._timer.start()

    def stop_animation(self) -> None:
        self._timer.stop()
        self.update()

    def _tick(self) -> None:
        self._angle = (self._angle + 7.0) % 360.0
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        margin = 3
        sz = self.width() - margin * 2
        rect = QRectF(margin, margin, sz, sz)
        # Dim full ring (track)
        track_pen = QPen(QColor(255, 255, 255, 22))
        track_pen.setWidthF(2.0)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.drawEllipse(rect)
        # Bright arc (120 °)
        arc_pen = QPen(ACCENT)
        arc_pen.setWidthF(2.0)
        arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc_pen)
        start_angle = int((90 - self._angle) * 16)   # Qt: 0° = 3 o'clock, CCW
        span_angle = 120 * 16
        painter.drawArc(rect, start_angle, span_angle)


# ---------------------------------------------------------------------------
# ActivityLogWidget — live scrolling log
# ---------------------------------------------------------------------------

@dataclass
class _LogEntry:
    text: str
    alpha: float = 0.0
    alpha_target: float = 1.0
    state: str = "active"          # "active" | "done"
    pulse_phase: float = 0.0
    is_file: bool = False          # True for PDF filenames, False for stage milestones


class ActivityLogWidget(QWidget):
    """Scrolling log of processing steps; newest entry at the bottom."""

    _MAX_VISIBLE = 6
    _LINE_H = 22
    _ALPHA_SPEED = 0.10   # per tick
    _DOT_R = 3.5          # radius of active dot
    _INDENT = 22          # left margin for icon + text

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[_LogEntry] = []
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self.setFixedHeight(self._MAX_VISIBLE * self._LINE_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def start_animation(self) -> None:
        self._timer.start()

    def stop_animation(self) -> None:
        self._timer.stop()

    def clear(self) -> None:
        self._entries.clear()
        self.update()

    def add_entry(self, text: str, *, is_file: bool = False) -> None:
        """Mark the current active entry as done, then append a new active entry."""
        for entry in self._entries:
            if entry.state == "active":
                entry.state = "done"
                entry.alpha_target = 0.42
        self._entries.append(_LogEntry(text=text, is_file=is_file))
        # Trim entries that will never be visible again (keep a comfortable buffer)
        if len(self._entries) > self._MAX_VISIBLE + 4:
            self._entries = self._entries[-(self._MAX_VISIBLE + 4):]
        self.update()

    def mark_all_done(self) -> None:
        for entry in self._entries:
            if entry.state == "active":
                entry.state = "done"
                entry.alpha_target = 0.42
        self.update()

    def _tick(self) -> None:
        changed = False
        for entry in self._entries:
            diff = entry.alpha_target - entry.alpha
            if abs(diff) > 0.005:
                entry.alpha = min(1.0, max(0.0, entry.alpha + diff * self._ALPHA_SPEED))
                changed = True
            else:
                entry.alpha = entry.alpha_target
            if entry.state == "active":
                entry.pulse_phase += 0.14
                changed = True
        if changed:
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw the last _MAX_VISIBLE entries, newest at the bottom
        visible = self._entries[-self._MAX_VISIBLE:]
        # Pad so newest is always at the bottom slot
        start_y = max(0, (self._MAX_VISIBLE - len(visible))) * self._LINE_H

        font = self.font()
        fm = QFontMetrics(font)
        max_text_w = self.width() - self._INDENT - 4

        for i, entry in enumerate(visible):
            y = start_y + i * self._LINE_H
            mid_y = y + self._LINE_H / 2.0
            painter.setOpacity(entry.alpha)

            if entry.state == "active":
                # Pulsing dot
                pulse = (math.sin(entry.pulse_phase) + 1.0) / 2.0
                dot_col = QColor(ACCENT)
                dot_col.setAlpha(int(140 + 115 * pulse))
                painter.setBrush(dot_col)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(
                    QPointF(self._DOT_R + 2, mid_y),
                    self._DOT_R,
                    self._DOT_R,
                )
                # Text in accent-ish colour
                painter.setPen(QColor(TEXT_ACTIVE))
            else:
                # Tiny checkmark
                check_pen = QPen(QColor(70, 170, 80, 180))
                check_pen.setWidthF(1.3)
                check_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                check_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(check_pen)
                # Draw a small ✓ (3 points)
                cx, cy = 4.0, mid_y
                painter.drawLine(QPointF(cx, cy), QPointF(cx + 3, cy + 3))
                painter.drawLine(QPointF(cx + 3, cy + 3), QPointF(cx + 8, cy - 3))
                painter.setPen(QColor(TEXT_DONE))

            elided = fm.elidedText(entry.text, Qt.TextElideMode.ElideMiddle, max_text_w)
            painter.drawText(
                self._INDENT,
                int(y),
                max_text_w,
                self._LINE_H,
                Qt.AlignmentFlag.AlignVCenter,
                elided,
            )

        painter.setOpacity(1.0)


# ---------------------------------------------------------------------------
# SparkBar — thin progress track with a glowing spark
# ---------------------------------------------------------------------------

class SparkBar(QWidget):
    """2 px progress track with a glowing spark at the leading edge."""

    _TRACK_H = 2
    _SPARK_R = 9

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._done = 0
        self._total = 0
        self._spark_x = 0.0
        self._spark_dir = 1
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self.setFixedHeight(self._SPARK_R * 2 + 2)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def start_animation(self) -> None:
        self._spark_x = 0.0
        self._spark_dir = 1
        self._timer.start()

    def stop_animation(self) -> None:
        self._timer.stop()

    def set_value(self, done: int, total: int) -> None:
        self._done = max(0, done)
        self._total = max(0, total)
        self.update()

    def _fill_ratio(self) -> float:
        if self._total <= 0:
            return 1.0
        return 0.0 if self._done <= 0 else min(1.0, self._done / self._total)

    def _tick(self) -> None:
        w = max(1, self.width())
        if self._total <= 0:
            self._spark_x += 4.5 * self._spark_dir
            if self._spark_x >= w - 2:
                self._spark_dir = -1
                self._spark_x = float(w - 2)
            elif self._spark_x <= 2:
                self._spark_dir = 1
                self._spark_x = 2.0
        else:
            target = w * self._fill_ratio()
            self._spark_x += (target - self._spark_x) * 0.10
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        mid_y = self.height() / 2.0
        h = float(self._TRACK_H)

        # Track
        track = QPainterPath()
        track.addRoundedRect(0.0, mid_y - h / 2, float(w), h, 1.0, 1.0)
        painter.fillPath(track, QColor(255, 255, 255, 20))

        # Fill
        fill_w = w * self._fill_ratio()
        if fill_w > 0:
            fill = QPainterPath()
            fill.addRoundedRect(0.0, mid_y - h / 2, fill_w, h, 1.0, 1.0)
            painter.fillPath(fill, ACCENT)

        # Glow
        sx = self._spark_x
        sr = float(self._SPARK_R)
        glow = QRadialGradient(QPointF(sx, mid_y), sr)
        glow.setColorAt(0.0, QColor(200, 230, 255, 190))
        glow.setColorAt(0.4, QColor(79, 156, 249, 90))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(glow)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(sx, mid_y), sr, sr)

        # Core
        painter.setBrush(QColor(235, 248, 255, 255))
        painter.drawEllipse(QPointF(sx, mid_y), 2.5, 2.5)


# ---------------------------------------------------------------------------
# LoadingOverlay
# ---------------------------------------------------------------------------

class LoadingOverlay(QWidget):
    """Non-modal full-window loading overlay with Live Activity Log card."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.hide()

        # Internal state
        self._parse_start: float | None = None
        self._current_done = 0
        self._current_total = 0
        self._current_stage = ""
        self._last_stage = ""
        self._last_filename = ""
        self._hide_animation: QPropertyAnimation | None = None
        self._fade_in_animation: QPropertyAnimation | None = None

        # ── Card ────────────────────────────────────────────────────────────
        self._card = QFrame(self)
        self._card.setFixedWidth(480)
        self._card.setStyleSheet(
            "QFrame {"
            f"  background-color: {CARD_BG.name()};"
            "  border-radius: 12px;"
            "  border: 1px solid rgba(79, 156, 249, 0.18);"
            "}"
        )

        self._opacity_effect = QGraphicsOpacityEffect(self._card)
        self._opacity_effect.setOpacity(0.0)
        self._card.setGraphicsEffect(self._opacity_effect)

        # ── Title row: spinner + label ───────────────────────────────────────
        self._spinner = MiniSpinner(self._card)
        self._title_label = QLabel(self._card)
        title_font = QFont()
        title_font.setPointSize(13)
        title_font.setWeight(QFont.Weight.DemiBold)
        self._title_label.setFont(title_font)
        self._title_label.setStyleSheet(f"color: {TEXT_PRIMARY};")

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title_row.addWidget(self._spinner)
        title_row.addWidget(self._title_label)
        title_row.addStretch()

        # ── Separator line ────────────────────────────────────────────────
        separator = QFrame(self._card)
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("border: none; border-top: 1px solid rgba(255,255,255,0.07);")
        separator.setFixedHeight(1)

        # ── Activity log ──────────────────────────────────────────────────
        self._log = ActivityLogWidget(self._card)
        log_font = QFont()
        log_font.setPointSize(10)
        self._log.setFont(log_font)

        # ── Spark bar ─────────────────────────────────────────────────────
        self._spark_bar = SparkBar(self._card)

        # ── Info line ─────────────────────────────────────────────────────
        self._info_label = QLabel(self._card)
        self._info_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px;")
        self._info_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._info_label.hide()

        # ── Card layout ───────────────────────────────────────────────────
        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(28, 22, 28, 22)
        card_layout.setSpacing(0)
        card_layout.addLayout(title_row)
        card_layout.addSpacing(14)
        card_layout.addWidget(separator)
        card_layout.addSpacing(12)
        card_layout.addWidget(self._log)
        card_layout.addSpacing(16)
        card_layout.addWidget(self._spark_bar)
        card_layout.addSpacing(8)
        card_layout.addWidget(self._info_label)

    # ── Public API ───────────────────────────────────────────────────────────

    def fit_to_parent(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self._place_card()

    def show_overlay(self, title: str) -> None:
        self._parse_start = None
        self._current_done = 0
        self._current_total = 0
        self._current_stage = ""
        self._last_stage = ""
        self._last_filename = ""
        self._title_label.setText(title)
        self._info_label.hide()
        self._log.clear()
        self._spark_bar.set_value(0, 0)
        self.fit_to_parent()
        self.show()
        self.raise_()
        self._spinner.start_animation()
        self._log.start_animation()
        self._spark_bar.start_animation()
        self._opacity_effect.setOpacity(0.0)
        if self._hide_animation is not None:
            self._hide_animation.stop()
        if self._fade_in_animation is not None:
            self._fade_in_animation.stop()
        fade_in = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        fade_in.setDuration(200)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade_in.finished.connect(lambda: setattr(self, "_fade_in_animation", None))
        fade_in.start()
        self._fade_in_animation = fade_in

    def hide_overlay(self) -> None:
        self._spinner.stop_animation()
        self._log.stop_animation()
        self._spark_bar.stop_animation()
        if not self.isVisible():
            return
        if self._hide_animation is not None:
            self._hide_animation.stop()
        fade_out = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        fade_out.setDuration(160)
        fade_out.setStartValue(self._opacity_effect.opacity())
        fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
        fade_out.finished.connect(self._finish_hide)
        fade_out.start()
        self._hide_animation = fade_out

    def update_progress(self, done: int, total: int, filename: str, stage: str) -> None:
        if stage and stage != self._last_stage:
            self._last_stage = stage
            self._current_stage = stage
            self._last_filename = ""
            stage_key = f"overlay.stage.{stage}"
            label = tr(stage_key) if UiStrings.has(stage_key) else stage.replace("_", " ")
            self._log.add_entry(label, is_file=False)

        if stage == "parsing_pdf" and filename and filename != self._last_filename:
            self._last_filename = filename
            self._log.add_entry(filename, is_file=True)

        if self._current_stage in ("parsing_pdf", "listing_pdfs") and total > 0:
            self._current_done = done
            self._current_total = total

        # Spark bar
        show_counter = (
            self._current_stage in ("parsing_pdf", "listing_pdfs")
            and self._current_total > 0
        )
        if show_counter:
            self._spark_bar.set_value(self._current_done, self._current_total)
            counter_text = tr(
                "overlay.counter.files",
                done=self._current_done,
                total=self._current_total,
            )
            eta_text = self._eta_text()
            if eta_text:
                self._info_label.setText(f"{counter_text}  ·  {eta_text}")
            else:
                self._info_label.setText(counter_text)
            self._info_label.show()
        else:
            self._spark_bar.set_value(0, 0)
            self._info_label.hide()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _eta_text(self) -> str:
        if self._current_stage != "parsing_pdf" or self._current_total <= 0:
            return ""
        if self._current_done >= _ETA_MIN_SAMPLES and self._parse_start is None:
            self._parse_start = time.monotonic()
        if self._parse_start is None or self._current_done < _ETA_MIN_SAMPLES:
            return ""
        remaining = self._current_total - self._current_done
        if remaining <= 0:
            return tr("overlay.eta.almost_done")
        elapsed = time.monotonic() - self._parse_start
        if elapsed <= 0:
            return ""
        secs = int(elapsed / self._current_done * remaining)
        if secs <= 5:
            return tr("overlay.eta.almost_done")
        if secs < 60:
            return tr("overlay.eta.seconds", seconds=secs)
        minutes, seconds = divmod(secs, 60)
        if seconds >= 30:
            minutes += 1
            seconds = 0
        if seconds:
            return tr("overlay.eta.minutes_seconds", minutes=minutes, seconds=seconds)
        return tr("overlay.eta.minutes", minutes=minutes)

    def _finish_hide(self) -> None:
        self.hide()
        self._opacity_effect.setOpacity(0.0)

    def _place_card(self) -> None:
        card_x = max(0, (self.width() - self._card.width()) // 2)
        card_y = max(0, (self.height() - self._card.sizeHint().height()) // 2)
        self._card.move(card_x, card_y)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), OVERLAY_BG)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_card()
