"""Tests for OCR reuse in supplier_matcher._try_ocr_upgrade."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parser.supplier_db import SupplierDB
from parser.supplier_matcher import _try_ocr_upgrade


@pytest.fixture
def salo_db(tmp_path: Path) -> SupplierDB:
    data = {
        "suppliers": [
            {
                "name": "SALO B.V.",
                "iban": "NL64ABNA0589033654",
                "discount": 0.0,
                "aliases": ["SALO B.V."],
                "customer_codes": ["3503"],
            }
        ]
    }
    p = tmp_path / "suppliers.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return SupplierDB(path=str(p))


def test_try_ocr_upgrade_reuses_invoice_ocr_text(
    salo_db: SupplierDB, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    extract_calls = 0

    def fake_extract(_path: str) -> str:
        nonlocal extract_calls
        extract_calls += 1
        return "should not be called"

    monkeypatch.setattr("logic.pdf_ocr_session.extract_text_from_images", fake_extract)

    pdf = tmp_path / "inv.pdf"
    pdf.write_bytes(b"x")
    invoice = {
        "source_file": str(pdf),
        "ocr_text": "Factuur SALO B.V. klant 3503 IBAN NL64ABNA0589033654",
        "_ocr_image_text": "",
    }
    supplier = {
        "name": "SALO B.V.",
        "iban": "NL64ABNA0589033654",
        "discount": 0.0,
        "aliases": ["SALO B.V."],
        "customer_codes": ["3503"],
    }
    match_info = {
        "iban_match": False,
        "customer_code_match": False,
        "alias_match": False,
        "fuzzy_match": False,
        "kvk_match": False,
        "vat_match": False,
        "email_domain_match": False,
    }
    status = _try_ocr_upgrade(invoice, supplier, match_info, salo_db)
    assert extract_calls == 0
    assert status == "confirmed"
    assert match_info.get("ocr_confirmed") is True
