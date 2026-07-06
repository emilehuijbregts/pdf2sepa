"""Tests for logic/invoice_folder_loader.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from logic.invoice_folder_loader import load_invoices_from_folder, strip_raw_text_from_invoices


def test_load_error_no_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def empty_text(_path: str) -> str:
        return ""

    monkeypatch.setattr(
        "logic.invoice_folder_loader.extract_text_strict",
        empty_text,
    )
    monkeypatch.setattr(
        "logic.pdf_ocr_session.extract_text_from_images",
        lambda _path: "",
    )
    monkeypatch.setattr(
        "logic.pdf_ocr_session.extract_text_force_raster_ocr",
        lambda _path, max_pages=1: "",
    )
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"x")
    out = load_invoices_from_folder(tmp_path)
    assert len(out) == 1
    assert out[0]["load_error"] == "no_text"
    assert out[0]["source_file"] == str(pdf.resolve())
    assert "raw_text" not in out[0]


def test_image_only_pdf_uses_ocr_when_text_layer_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ocr_sample = (
        "IBAN: NL25CITI0266075452\n"
        "Totaal te betalen EUR 10,00\n"
        "Factuurnummer 108895\n"
        "Klantnummer 66167\n"
        "Factuurdatum 29-08-2025\n"
    )

    def empty_text(_path: str) -> str:
        return ""

    monkeypatch.setattr(
        "logic.invoice_folder_loader.extract_text_strict",
        empty_text,
    )
    monkeypatch.setattr(
        "logic.pdf_ocr_session.extract_text_from_images",
        lambda _path: ocr_sample,
    )
    monkeypatch.setattr(
        "logic.pdf_ocr_session.extract_text_force_raster_ocr",
        lambda _path, max_pages=1: "",
    )
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"x")
    out = load_invoices_from_folder(tmp_path)
    assert len(out) == 1
    assert out[0].get("load_error") is None
    assert out[0].get("raw_text") == ""
    assert out[0].get("ocr_text") == ocr_sample.strip()
    assert out[0].get("invoice_number") == "108895"
    assert out[0].get("customer_number") == "66167"


def test_load_error_read_failed_on_extract_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_path: str) -> str:
        raise OSError("locked")

    monkeypatch.setattr(
        "logic.invoice_folder_loader.extract_text_strict",
        boom,
    )
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(b"x")
    out = load_invoices_from_folder(tmp_path)
    assert len(out) == 1
    assert out[0]["load_error"] == "read_failed"


def test_load_error_when_extract_invoice_data_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def ok_text(_path: str) -> str:
        return "some text"

    def boom(_text: str, debtor_iban: str | None = None, debtor_kvk: str | None = None, debtor_vat: str | None = None):
        raise ValueError("parse boom")

    monkeypatch.setattr(
        "logic.invoice_folder_loader.extract_text_strict",
        ok_text,
    )
    monkeypatch.setattr(
        "logic.invoice_folder_loader.extract_invoice_data",
        boom,
    )
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"x")
    out = load_invoices_from_folder(tmp_path)
    assert len(out) == 1
    assert out[0]["load_error"] == "read_failed"


def test_successful_load_parses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sample = (
        "IBAN: NL25CITI0266075452\n"
        "Totaal te betalen EUR 10,00\n"
        "Factuur nr. INV-99\n"
    )

    def fake_strict(_path: str) -> str:
        return sample

    monkeypatch.setattr(
        "logic.invoice_folder_loader.extract_text_strict",
        fake_strict,
    )
    pdf = tmp_path / "ok.pdf"
    pdf.write_bytes(b"x")
    out = load_invoices_from_folder(tmp_path)
    assert len(out) == 1
    assert out[0].get("load_error") is None
    assert out[0].get("iban") == "NL25CITI0266075452"
    assert out[0]["source_file"] == str(pdf.resolve())
    assert out[0].get("raw_text") == sample


def test_strip_raw_text_from_invoices() -> None:
    invs = [{"raw_text": "x", "iban": "NL00"}, {"iban": "NL11"}]
    strip_raw_text_from_invoices(invs)
    assert "raw_text" not in invs[0]
    assert "raw_text" not in invs[1]


def test_skips_hidden_appledouble_pdfs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sample = (
        "IBAN: NL25CITI0266075452\n"
        "Totaal te betalen EUR 10,00\n"
        "Factuur nr. INV-99\n"
    )

    def fake_strict(_path: str) -> str:
        return sample

    monkeypatch.setattr(
        "logic.invoice_folder_loader.extract_text_strict",
        fake_strict,
    )

    # Real invoice + macOS metadata sidecar (must be ignored).
    real_pdf = tmp_path / "Omniplast 3245984_0.pdf"
    sidecar_pdf = tmp_path / "._Omniplast 3245984_0.pdf"
    real_pdf.write_bytes(b"x")
    sidecar_pdf.write_bytes(b"x")

    out = load_invoices_from_folder(tmp_path)
    assert len(out) == 1
    assert Path(str(out[0].get("source_file") or "")).name == "Omniplast 3245984_0.pdf"


def test_ocr_nl_vat_handles_spaced_ocr_suffix() -> None:
    from logic.invoice_folder_loader import _ocr_nl_vat_from_text

    assert _ocr_nl_vat_from_text("NL 8055131 52 BO1 NL15ABNA0591821249") == "NL805513152B01"


def test_supplement_ocr_skipped_for_complete_text_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = (
        "IBAN: NL25CITI0266075452\n"
        "Totaal te betalen EUR 10,00\n"
        "Factuur nr. INV-99\n"
        "Leverancier Wavin Nederland\n"
    )
    supplement_calls = 0

    def fake_strict(_path: str) -> str:
        return sample

    def track_supplement(self) -> str:
        nonlocal supplement_calls
        supplement_calls += 1
        return "unexpected supplement"

    monkeypatch.setattr("logic.invoice_folder_loader.extract_text_strict", fake_strict)
    monkeypatch.setattr("logic.pdf_ocr_session.PdfOcrSession.supplement_text", track_supplement)
    monkeypatch.setattr("logic.pdf_ocr_session.extract_text_from_images", lambda _p: "")
    pdf = tmp_path / "ok.pdf"
    pdf.write_bytes(b"x")
    out = load_invoices_from_folder(tmp_path)
    assert len(out) == 1
    assert out[0].get("load_error") is None
    assert supplement_calls == 0


def test_iban_image_ocr_skipped_for_text_layer_iban(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = (
        "IBAN: NL25CITI0266075452\n"
        "Totaal te betalen EUR 10,00\n"
        "Factuur nr. INV-99\n"
    )
    iban_ocr_calls = 0

    def fake_strict(_path: str) -> str:
        return sample

    def track_ibans(self) -> list[str]:
        nonlocal iban_ocr_calls
        iban_ocr_calls += 1
        return []

    monkeypatch.setattr("logic.invoice_folder_loader.extract_text_strict", fake_strict)
    monkeypatch.setattr("logic.pdf_ocr_session.PdfOcrSession.ibans_from_images", track_ibans)
    monkeypatch.setattr("logic.pdf_ocr_session.extract_text_from_images", lambda _p: "")
    pdf = tmp_path / "ok.pdf"
    pdf.write_bytes(b"x")
    out = load_invoices_from_folder(tmp_path)
    assert len(out) == 1
    assert iban_ocr_calls == 0
    assert out[0].get("iban") == "NL25CITI0266075452"
