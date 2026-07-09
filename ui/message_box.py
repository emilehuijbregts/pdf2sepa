"""Localized QMessageBox helpers (ja/nee instead of Qt default Yes/No)."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

from ui.i18n import tr


def ask_yes_no(
    parent: QWidget | None,
    title: str,
    message: str,
    *,
    default_yes: bool = False,
    icon: QMessageBox.Icon = QMessageBox.Icon.Question,
) -> bool:
    """Show a yes/no dialog with localized button labels."""
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(message)
    yes_btn = box.addButton(tr("dialog.button.yes"), QMessageBox.ButtonRole.YesRole)
    no_btn = box.addButton(tr("dialog.button.no"), QMessageBox.ButtonRole.NoRole)
    box.setDefaultButton(yes_btn if default_yes else no_btn)
    box.setEscapeButton(no_btn)
    box.exec()
    return box.clickedButton() == yes_btn
