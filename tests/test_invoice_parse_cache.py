"""Tests for logic/invoice_parse_cache.py."""

from __future__ import annotations

from pathlib import Path

from logic.invoice_parse_cache import (
    ParsedInvoiceBatchCache,
    batch_folder_fingerprint,
    index_invoices_by_source_file,
    list_invoice_pdf_paths,
)


def test_list_invoice_pdf_paths_skips_dotfiles(tmp_path: Path) -> None:
    (tmp_path / "a.pdf").write_bytes(b"x")
    (tmp_path / "._a.pdf").write_bytes(b"y")
    (tmp_path / "readme.txt").write_bytes(b"z")
    names = [p.name for p in list_invoice_pdf_paths(tmp_path)]
    assert names == ["a.pdf"]


def test_batch_fingerprint_changes_when_pdf_changes(tmp_path: Path) -> None:
    pdf = tmp_path / "inv.pdf"
    pdf.write_bytes(b"v1")
    fp1 = batch_folder_fingerprint(tmp_path)
    pdf.write_bytes(b"v2")
    fp2 = batch_folder_fingerprint(tmp_path)
    assert fp1 != fp2


def test_parsed_cache_valid_until_pdf_changes(tmp_path: Path) -> None:
    pdf = tmp_path / "inv.pdf"
    pdf.write_bytes(b"x")
    cache = ParsedInvoiceBatchCache()
    inv = [{"source_file": str(pdf.resolve()), "amount": "1"}]
    cache.store(tmp_path, inv)
    assert cache.get_parsed_invoices(tmp_path) is not None
    pdf.write_bytes(b"y")
    assert cache.get_parsed_invoices(tmp_path) is None


def test_index_invoices_by_source_file_resolved(tmp_path: Path) -> None:
    pdf = tmp_path / "inv.pdf"
    inv = {"source_file": str(pdf.resolve())}
    idx = index_invoices_by_source_file([inv])
    assert idx[str(pdf.resolve())] is inv
