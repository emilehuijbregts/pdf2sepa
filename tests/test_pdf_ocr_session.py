"""Tests for logic/pdf_ocr_session.py — single-call OCR orchestration."""

from __future__ import annotations

import pytest

from logic.pdf_ocr_session import (
    PdfOcrSession,
    has_reliable_text_layer_iban,
    merge_supplement_chunks,
    needs_supplement_ocr,
)


def test_merge_supplement_chunks_dedupes() -> None:
    out = merge_supplement_chunks("hello", "hello", "world")
    assert out == "hello\n\nworld"


def test_needs_supplement_ocr_empty_text_layer() -> None:
    assert needs_supplement_ocr({"iban": "x"}, "") is True
    assert needs_supplement_ocr(None, "") is True


def test_needs_supplement_ocr_skips_when_payment_anchors_ok() -> None:
    data = {
        "amount_result": {"status": "confirmed", "candidates": [{"value": "10.00"}]},
        "iban_result": {"status": "confirmed", "value": "NL25CITI0266075452"},
        "iban": "NL25CITI0266075452",
    }
    text = "IBAN: NL25CITI0266075452\nTotaal te betalen EUR 10,00\n"
    assert needs_supplement_ocr(data, text) is False


def test_needs_supplement_ocr_when_amount_failed() -> None:
    data = {
        "amount_result": {"status": "failed", "candidates": []},
        "iban_result": {"status": "confirmed", "value": "NL25CITI0266075452"},
        "iban": "NL25CITI0266075452",
    }
    text = "IBAN: NL25CITI0266075452\n"
    assert needs_supplement_ocr(data, text) is True


def test_needs_supplement_ocr_when_no_text_layer_iban() -> None:
    data = {
        "amount_result": {"status": "confirmed", "candidates": [{"value": "10.00"}]},
        "iban_result": {"status": "failed", "value": None},
    }
    text = "Totaal te betalen EUR 10,00\n"
    assert needs_supplement_ocr(data, text) is True


def test_has_reliable_text_layer_iban() -> None:
    data = {
        "iban_result": {"status": "confirmed", "value": "NL25CITI0266075452"},
        "iban": "NL25CITI0266075452",
    }
    assert has_reliable_text_layer_iban(data, "IBAN NL25CITI0266075452") is True
    assert has_reliable_text_layer_iban(data, "no iban here") is False


def test_pdf_ocr_session_image_text_called_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_extract(path: str) -> str:
        calls.append(path)
        return "img-ocr"

    monkeypatch.setattr(
        "logic.pdf_ocr_session.extract_text_from_images",
        fake_extract,
    )
    session = PdfOcrSession("/tmp/x.pdf")
    assert session.image_text() == "img-ocr"
    assert session.image_text() == "img-ocr"
    assert calls == ["/tmp/x.pdf"]


def test_pdf_ocr_session_supplement_uses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    img_calls = 0
    raster_calls = 0

    def fake_img(_path: str) -> str:
        nonlocal img_calls
        img_calls += 1
        return "from-images"

    def fake_raster(_path: str, *, max_pages: int = 1) -> str:
        nonlocal raster_calls
        raster_calls += 1
        return "from-raster"

    monkeypatch.setattr("logic.pdf_ocr_session.extract_text_from_images", fake_img)
    monkeypatch.setattr("logic.pdf_ocr_session.extract_text_force_raster_ocr", fake_raster)

    session = PdfOcrSession("/tmp/y.pdf")
    sup = session.supplement_text()
    assert "from-images" in sup
    assert "from-raster" in sup
    _ = session.image_text()
    _ = session.raster_text(max_pages=1)
    assert img_calls == 1
    assert raster_calls == 1


def test_pdf_ocr_session_ibans_from_images_uses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    img_calls = 0
    raster_calls = 0

    def fake_img(_path: str) -> str:
        nonlocal img_calls
        img_calls += 1
        return ""

    def fake_raster(_path: str, *, max_pages: int = 1) -> str:
        nonlocal raster_calls
        raster_calls += 1
        return "IBAN NL25CITI0266075452"

    monkeypatch.setattr("logic.pdf_ocr_session.extract_text_from_images", fake_img)
    monkeypatch.setattr("logic.pdf_ocr_session.extract_text_force_raster_ocr", fake_raster)

    session = PdfOcrSession("/tmp/z.pdf")
    ibans = session.ibans_from_images()
    assert ibans == ["NL25CITI0266075452"]
    assert img_calls == 1
    assert raster_calls == 1
