"""i18n tests for loading overlay widgets."""

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


def _make_overlay(qapp) -> LoadingOverlay:
    parent = QWidget()
    overlay = LoadingOverlay(parent)
    overlay._keepalive_parent = parent
    return overlay


def test_overlay_nl_texts(qapp) -> None:
    UiStrings.set_language("nl")
    overlay = _make_overlay(qapp)
    overlay.show_overlay(tr("overlay.title.parse_pdfs"))
    overlay.update_progress(14, 82, "Factuur_018.pdf", "parsing_pdf")

    assert overlay._title_label.text() == tr("overlay.title.parse_pdfs")
    assert overlay._stage_label.text() == tr("overlay.stage.parsing_pdf")
    assert overlay._counter_label.text() == tr("overlay.counter.files", done=14, total=82)


def test_overlay_en_texts(qapp) -> None:
    UiStrings.set_language("en")
    overlay = _make_overlay(qapp)
    overlay.show_overlay(tr("overlay.title.parse_pdfs"))
    overlay.update_progress(14, 82, "Invoice_018.pdf", "parsing_pdf")

    assert overlay._title_label.text() == "Loading PDFs…"
    assert overlay._stage_label.text() == "Running OCR…"
    assert overlay._counter_label.text() == "14 / 82 files"


def test_overlay_en_rematch_title(qapp) -> None:
    UiStrings.set_language("en")
    overlay = _make_overlay(qapp)
    overlay.show_overlay(tr("overlay.title.rematch"))
    overlay.update_progress(0, 1, "", "matching_suppliers")

    assert overlay._title_label.text() == "Recalculating payments…"
    assert overlay._stage_label.text() == "Matching suppliers…"


def test_unknown_stage_fallback(qapp) -> None:
    UiStrings.set_language("en")
    overlay = _make_overlay(qapp)
    overlay.show_overlay(tr("overlay.title.parse_pdfs"))
    overlay.update_progress(0, 0, "", "custom_stage")

    assert overlay._stage_label.text() == "custom stage"


def test_eta_almost_done_when_complete(qapp, monkeypatch: pytest.MonkeyPatch) -> None:
    UiStrings.set_language("nl")
    overlay = _make_overlay(qapp)
    monkeypatch.setattr("ui.loading_overlay.time.monotonic", lambda: 1010.0)
    overlay._current_stage = "parsing_pdf"
    overlay._current_done = 82
    overlay._current_total = 82
    overlay._parse_start = 1000.0
    overlay._update_eta()

    assert overlay._eta_label.text() == tr("overlay.eta.almost_done")


def test_eta_almost_done_within_five_seconds(qapp, monkeypatch: pytest.MonkeyPatch) -> None:
    UiStrings.set_language("nl")
    overlay = _make_overlay(qapp)
    monkeypatch.setattr("ui.loading_overlay.time.monotonic", lambda: 1010.0)
    overlay._current_stage = "parsing_pdf"
    overlay._current_done = 10
    overlay._current_total = 15
    overlay._parse_start = 1000.0
    overlay._update_eta()

    assert overlay._eta_label.text() == tr("overlay.eta.almost_done")


def test_eta_seconds(qapp, monkeypatch: pytest.MonkeyPatch) -> None:
    UiStrings.set_language("nl")
    overlay = _make_overlay(qapp)
    monkeypatch.setattr("ui.loading_overlay.time.monotonic", lambda: 1030.0)
    overlay._current_stage = "parsing_pdf"
    overlay._current_done = 10
    overlay._current_total = 20
    overlay._parse_start = 1000.0
    overlay._update_eta()

    assert overlay._eta_label.text() == tr("overlay.eta.seconds", seconds=30)


def test_eta_minutes_seconds(qapp, monkeypatch: pytest.MonkeyPatch) -> None:
    UiStrings.set_language("nl")
    overlay = _make_overlay(qapp)
    monkeypatch.setattr("ui.loading_overlay.time.monotonic", lambda: 1010.0)
    overlay._current_stage = "parsing_pdf"
    overlay._current_done = 10
    overlay._current_total = 85
    overlay._parse_start = 1000.0
    overlay._update_eta()

    assert overlay._eta_label.text() == tr("overlay.eta.minutes_seconds", minutes=1, seconds=15)


def test_eta_minutes(qapp, monkeypatch: pytest.MonkeyPatch) -> None:
    UiStrings.set_language("nl")
    overlay = _make_overlay(qapp)
    monkeypatch.setattr("ui.loading_overlay.time.monotonic", lambda: 1010.0)
    overlay._current_stage = "parsing_pdf"
    overlay._current_done = 10
    overlay._current_total = 70
    overlay._parse_start = 1000.0
    overlay._update_eta()

    assert overlay._eta_label.text() == tr("overlay.eta.minutes", minutes=1)


def test_eta_en_seconds(qapp, monkeypatch: pytest.MonkeyPatch) -> None:
    UiStrings.set_language("en")
    overlay = _make_overlay(qapp)
    monkeypatch.setattr("ui.loading_overlay.time.monotonic", lambda: 1030.0)
    overlay._current_stage = "parsing_pdf"
    overlay._current_done = 10
    overlay._current_total = 20
    overlay._parse_start = 1000.0
    overlay._update_eta()

    assert overlay._eta_label.text() == "~ 30 sec remaining"
