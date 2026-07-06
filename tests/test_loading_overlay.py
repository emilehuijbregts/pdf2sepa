"""Tests for loading overlay widgets."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QWidget

from ui.i18n import UiStrings, tr
from ui.loading_overlay import LoadingOverlay


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def reset_language() -> None:
    yield
    UiStrings.set_language("nl")


def test_stage_labels_cover_pipeline_stages() -> None:
    expected = {
        "listing_pdfs",
        "parsing_pdf",
        "deduplicating",
        "matching_suppliers",
        "enriching_credits",
        "computing_payments",
    }
    for stage in expected:
        assert UiStrings.has(f"overlay.stage.{stage}")


def test_loading_overlay_update_progress(qapp) -> None:
    parent = QWidget()
    overlay = LoadingOverlay(parent)
    overlay.show_overlay(tr("overlay.title.parse_pdfs"))
    overlay.update_progress(14, 82, "Factuur_018.pdf", "parsing_pdf")

    # Info label should show counter text
    counter_text = tr("overlay.counter.files", done=14, total=82)
    assert counter_text in overlay._info_label.text()

    # Activity log should contain the stage label and the filename
    log_texts = [e.text for e in overlay._log._entries]
    assert any(tr("overlay.stage.parsing_pdf") in t for t in log_texts)
    assert any("Factuur_018.pdf" in t for t in log_texts)

    overlay.hide_overlay()
    overlay._finish_hide()


def test_loading_overlay_indeterminate_stage(qapp) -> None:
    parent = QWidget()
    overlay = LoadingOverlay(parent)
    overlay.show_overlay(tr("overlay.title.rematch"))
    overlay.update_progress(0, 1, "", "matching_suppliers")

    # Log should have an entry for the stage
    log_texts = [e.text for e in overlay._log._entries]
    assert any(tr("overlay.stage.matching_suppliers") in t for t in log_texts)

    # No counter shown for indeterminate stages
    assert overlay._info_label.isHidden()

    overlay.hide_overlay()
    overlay._finish_hide()
