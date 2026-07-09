"""Standalone update progress window — visual language aligned with LoadingOverlay."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
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
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

ACCENT = QColor("#4f9cf9")
CARD_BG = QColor("#0c0c18")
WINDOW_BG = QColor("#080810")
TEXT_PRIMARY = "#eeeef4"
TEXT_DIM = "#72728a"


class _MiniSpinner(QWidget):
    """Small rotating arc indicator."""

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
        track_pen = QPen(QColor(255, 255, 255, 22))
        track_pen.setWidthF(2.0)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.drawEllipse(rect)
        arc_pen = QPen(ACCENT)
        arc_pen.setWidthF(2.0)
        arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc_pen)
        start_angle = int((90 - self._angle) * 16)
        painter.drawArc(rect, start_angle, 120 * 16)


class _SparkBar(QWidget):
    """Thin progress track with animated spark."""

    _TRACK_H = 2
    _SPARK_R = 5

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

        track = QPainterPath()
        track.addRoundedRect(0.0, mid_y - h / 2, float(w), h, 1.0, 1.0)
        painter.fillPath(track, QColor(255, 255, 255, 20))

        fill_w = w * self._fill_ratio()
        if fill_w > 0:
            fill = QPainterPath()
            fill.addRoundedRect(0.0, mid_y - h / 2, fill_w, h, 1.0, 1.0)
            painter.fillPath(fill, ACCENT)

        sx = self._spark_x
        sr = float(self._SPARK_R)
        glow = QRadialGradient(QPointF(sx, mid_y), sr)
        glow.setColorAt(0.0, QColor(200, 230, 255, 190))
        glow.setColorAt(0.4, QColor(79, 156, 249, 90))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(glow)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(sx, mid_y), sr, sr)

        painter.setBrush(QColor(235, 248, 255, 255))
        painter.drawEllipse(QPointF(sx, mid_y), 2.5, 2.5)


class UpdateProgressWindow(QWidget):
    """Top-level window shown during the full update flow."""

    def __init__(self, *, version: str = "") -> None:
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setFixedSize(480, 200)
        self._version = version.strip()
        self._closing = False

        self.setStyleSheet(f"background-color: {WINDOW_BG.name()};")

        card = QFrame(self)
        card.setGeometry(0, 0, 480, 200)
        card.setStyleSheet(
            "QFrame {"
            f"  background-color: {CARD_BG.name()};"
            "  border-radius: 12px;"
            "  border: 1px solid rgba(79, 156, 249, 0.18);"
            "}"
        )

        self._spinner = _MiniSpinner(card)
        self._title_label = QLabel("PDF2SEPA bijwerken", card)
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

        separator = QFrame(card)
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("border: none; border-top: 1px solid rgba(255,255,255,0.07);")
        separator.setFixedHeight(1)

        self._status_label = QLabel(card)
        status_font = QFont()
        status_font.setPointSize(10)
        self._status_label.setFont(status_font)
        self._status_label.setStyleSheet(f"color: {TEXT_DIM};")
        self._status_label.setWordWrap(True)

        self._spark_bar = _SparkBar(card)

        self._error_label = QLabel(card)
        self._error_label.setFont(status_font)
        self._error_label.setStyleSheet("color: #e07070;")
        self._error_label.setWordWrap(True)
        self._error_label.hide()

        self._ok_button = QPushButton("OK", card)
        self._ok_button.setFixedWidth(80)
        self._ok_button.hide()
        self._ok_button.clicked.connect(self.close)

        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(self._ok_button)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 22, 28, 22)
        card_layout.setSpacing(0)
        card_layout.addLayout(title_row)
        card_layout.addSpacing(14)
        card_layout.addWidget(separator)
        card_layout.addSpacing(16)
        card_layout.addWidget(self._status_label)
        card_layout.addSpacing(16)
        card_layout.addWidget(self._spark_bar)
        card_layout.addSpacing(12)
        card_layout.addWidget(self._error_label)
        card_layout.addSpacing(8)
        card_layout.addLayout(button_row)

        self._opacity_effect = QGraphicsOpacityEffect(card)
        card.setGraphicsEffect(self._opacity_effect)
        self._opacity_effect.setOpacity(1.0)

        self._center_on_screen()

    def _center_on_screen(self) -> None:
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(
                geo.x() + (geo.width() - self.width()) // 2,
                geo.y() + (geo.height() - self.height()) // 2,
            )

    def show_downloading(self) -> None:
        version_text = f" (versie {self._version})" if self._version else ""
        self._status_label.setText(f"Update downloaden{version_text}…")
        self._spark_bar.set_value(0, 0)
        self._spinner.start_animation()
        self._spark_bar.start_animation()
        self.show()
        self.raise_()
        self.activateWindow()

    def set_download_progress(self, done: int, total: int) -> None:
        self._spark_bar.set_value(done, total)

    def show_installing(self) -> None:
        self._status_label.setText("Update installeren… Dit kan even duren.")
        self._spark_bar.set_value(0, 0)

    def show_restarting(self) -> None:
        self._status_label.setText("PDF2SEPA wordt gestart…")
        self._spark_bar.set_value(0, 0)

    def show_error(self, message: str) -> None:
        self._spinner.stop_animation()
        self._spark_bar.stop_animation()
        self._status_label.setText("Update mislukt")
        self._error_label.setText(message)
        self._error_label.show()
        self._ok_button.show()
        self.show()
        self.raise_()
        self.activateWindow()

    def close_on_success(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._spinner.stop_animation()
        self._spark_bar.stop_animation()
        self.close()
