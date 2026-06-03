"""Unified rendering helpers for field candidates (UI-only).

Rules (stap 5):
- selected_value -> checkmark indicator
- confidence > 80 -> green
- confidence 50-80 -> orange
- missing -> red
"""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QColor


def is_missing_selected_value(selected_value: Any | None) -> bool:
    """Missing is defined as: empty/None selected value."""
    if selected_value is None:
        return True
    return not str(selected_value).strip()


def checkmark_prefix(*, is_selected: bool) -> str:
    return "✓ " if is_selected else ""


def confidence_color(
    confidence: int | None,
    *,
    missing: bool = False,
) -> QColor:
    """Return text color for a candidate/field per step-5 rules."""
    if missing:
        return QColor(180, 0, 0)
    try:
        conf = int(confidence or 0)
    except (TypeError, ValueError):
        conf = 0
    if conf > 80:
        return QColor(0, 128, 0)
    if conf >= 50:
        return QColor(200, 120, 0)
    return QColor(180, 0, 0)

