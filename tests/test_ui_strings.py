"""Tests for ui/i18n UiStrings registry."""

from __future__ import annotations

import pytest

from ui.i18n import MissingTranslationKeyError, UiStrings, tr
from ui.i18n.languages import en, nl
from ui.i18n.strings import _REGISTRIES


@pytest.fixture(autouse=True)
def reset_language() -> None:
    yield
    UiStrings.set_language("nl")


def test_translate_nl_default() -> None:
    assert tr("overlay.stage.parsing_pdf") == "OCR uitvoeren…"


def test_translate_en() -> None:
    UiStrings.set_language("en")
    assert tr("overlay.stage.parsing_pdf") == "Running OCR…"


def test_placeholders_counter() -> None:
    assert tr("overlay.counter.files", done=14, total=82) == "14 / 82 bestanden"


def test_placeholders_eta() -> None:
    assert tr("overlay.eta.seconds", seconds=42) == "~ 42 sec resterend"
    assert tr("overlay.eta.minutes_seconds", minutes=2, seconds=15) == "~ 2 min 15 sec resterend"
    assert tr("overlay.eta.minutes", minutes=5) == "~ 5 min resterend"


def test_fallback_to_nl(monkeypatch: pytest.MonkeyPatch) -> None:
    UiStrings.set_language("en")
    monkeypatch.delitem(_REGISTRIES["en"], "overlay.stage.parsing_pdf")
    assert tr("overlay.stage.parsing_pdf") == "OCR uitvoeren…"


def test_missing_key_raises() -> None:
    with pytest.raises(MissingTranslationKeyError, match="nonexistent.key"):
        tr("nonexistent.key")


def test_has_key() -> None:
    assert UiStrings.has("overlay.stage.parsing_pdf") is True
    assert UiStrings.has("nonexistent.key") is False


def test_set_language_invalid() -> None:
    with pytest.raises(ValueError, match="Unsupported language"):
        UiStrings.set_language("de")


def test_language_roundtrip() -> None:
    UiStrings.set_language("en")
    assert UiStrings.language() == "en"


def test_registry_deterministic() -> None:
    assert set(nl.STRINGS.keys()) == set(en.STRINGS.keys())
    assert len(nl.STRINGS) == len(en.STRINGS)

    UiStrings.set_language("en")
    first = tr("overlay.counter.files", done=14, total=82)
    second = tr("overlay.counter.files", done=14, total=82)
    assert first == second == "14 / 82 files"
