"""Tests for logic/invoice_folder_loader.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from logic.invoice_folder_loader import load_invoices_from_folder


def test_load_error_no_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def empty_text(_path: str) -> str:
        return ""

    monkeypatch.setattr(
        "logic.invoice_folder_loader.extract_text_strict",
        empty_text,
    )
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"x")
    out = load_invoices_from_folder(tmp_path)
    assert len(out) == 1
    assert out[0]["load_error"] == "no_text"
    assert out[0]["source_file"] == str(pdf.resolve())
    assert "raw_text" not in out[0]


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
        "IBAN: NL91INGB0001234567\n"
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
    assert out[0].get("iban") == "NL91INGB0001234567"
    assert out[0]["source_file"] == str(pdf.resolve())
