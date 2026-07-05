"""Golden regression for standalone credit notes (losse creditnota's).

Contract (hard): per PDF in tests/credit_dataset/pdfs/:
- supplier_name + match_status (leveranciersmatch)
- invoice_number (factuurnummer van de losse regel)
- amount (bedrag van de losse regel)
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from logic.credit_enrichment import enrich_credit_document
from logic.golden_dataset import money_to_str, normalize_text
from logic.invoice_folder_loader import load_invoice_from_pdf_path
from logic.paths import read_user_data_root
from parser.supplier_db import SupplierDB
from parser.supplier_matcher import match_suppliers

_APP_BASE = Path(__file__).resolve().parents[1]
_PDF_DIR = Path(__file__).resolve().parent / "credit_dataset" / "pdfs"
_EXPECTATIONS_PATH = (
    Path(__file__).resolve().parent / "credit_dataset" / "standalone_expectations.json"
)


def _load_expectations() -> dict[str, dict]:
    data = json.loads(_EXPECTATIONS_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("standalone_expectations.json must be a JSON object")
    return data


def _standalone_pdfs() -> list[Path]:
    return sorted(p for p in _PDF_DIR.iterdir() if p.suffix.lower() == ".pdf")


def _supplier_db() -> SupplierDB:
    user_data = read_user_data_root(_APP_BASE)
    return SupplierDB(path=str(user_data / "suppliers.json"))


@pytest.fixture(scope="module")
def expectations() -> dict[str, dict]:
    return _load_expectations()


@pytest.mark.parametrize("pdf_path", _standalone_pdfs(), ids=lambda p: p.name)
def test_standalone_credit_golden(pdf_path: Path, expectations: dict[str, dict]) -> None:
    assert pdf_path.name in expectations, f"missing golden expectations for {pdf_path.name}"
    exp = expectations[pdf_path.name]

    inv = load_invoice_from_pdf_path(pdf_path)
    enriched = enrich_credit_document(inv)
    matched = match_suppliers([enriched], _supplier_db())
    assert len(matched) == 1
    row = matched[0]

    assert normalize_text(row.get("supplier_name")) == normalize_text(exp["supplier_name"])
    assert normalize_text(row.get("match_status")) == normalize_text(exp["match_status"])
    assert normalize_text(row.get("invoice_number")) == normalize_text(exp["invoice_number"])

    expected_amount = str(Decimal(str(exp["amount"])).quantize(Decimal("0.01")))
    actual_amount = money_to_str(row.get("amount"))
    assert actual_amount == expected_amount, (
        f"amount mismatch for {pdf_path.name}: expected {expected_amount}, got {actual_amount}"
    )


def test_standalone_expectations_cover_all_pdfs(expectations: dict[str, dict]) -> None:
    pdf_names = {p.name for p in _standalone_pdfs()}
    assert set(expectations) == pdf_names
    assert len(pdf_names) == 15
