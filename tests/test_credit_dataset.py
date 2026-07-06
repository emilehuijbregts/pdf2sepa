"""Regression tests for real credit-note PDFs in tests/Credit facturen/."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from logic.credit_classifier import classify_credit_document
from logic.credit_enrichment import enrich_credit_document
from logic.invoice_folder_loader import load_invoice_from_pdf_path

_CREDIT_DIR = Path(__file__).resolve().parent / "Credit facturen"
_EXPECTATIONS_PATH = Path(__file__).resolve().parent / "credit_dataset" / "expectations.json"


def _load_expectations() -> dict:
    return json.loads(_EXPECTATIONS_PATH.read_text(encoding="utf-8"))


def _credit_pdfs() -> list[Path]:
    return sorted(
        p for p in _CREDIT_DIR.iterdir() if p.suffix.lower() == ".pdf"
    )


@pytest.mark.parametrize("pdf_path", _credit_pdfs(), ids=lambda p: p.name)
def test_credit_pdf_detection(pdf_path: Path) -> None:
    expectations = _load_expectations()
    assert pdf_path.name in expectations, f"missing expectations for {pdf_path.name}"

    inv = load_invoice_from_pdf_path(pdf_path)
    text = str(inv.get("raw_text") or "")
    detection = classify_credit_document(text)
    exp = expectations[pdf_path.name]

    assert detection.is_credit is exp["is_credit"]
    if exp["is_credit"]:
        assert detection.confidence >= 50
    else:
        assert detection.confidence < 50

    enriched = enrich_credit_document(inv)
    assert enriched.get("type") == exp["type"]
    assert enriched.get("invoice_number") == exp.get("invoice_number")
    assert enriched.get("referenced_invoice_numbers") == exp.get("referenced_invoice_numbers", [])

    amt_status = (enriched.get("amount_result") or {}).get("status")
    assert amt_status == exp.get("amount_status")

    if exp.get("amount") is not None:
        assert enriched.get("amount") == exp["amount"]
