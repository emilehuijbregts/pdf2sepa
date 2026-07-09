"""Tests for localized yes/no message boxes."""

from __future__ import annotations

import pytest

from ui.i18n import UiStrings, tr


@pytest.fixture(autouse=True)
def reset_language() -> None:
    yield
    UiStrings.set_language("nl")


def test_yes_no_button_labels_nl() -> None:
    assert tr("dialog.button.yes") == "Ja"
    assert tr("dialog.button.no") == "Nee"


def test_yes_no_button_labels_en() -> None:
    UiStrings.set_language("en")
    assert tr("dialog.button.yes") == "Yes"
    assert tr("dialog.button.no") == "No"
