"""Optional regression tests against real sample PDFs in `sample_pdfs/`.

These tests are skipped automatically when no PDFs are present. They exist to
lock in fixes for tricky vendor layouts (labels in one line, values in the next,
column/tabular extraction quirks, etc.).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from parser.pdf_parser import extract_invoice_data, extract_text_strict


SAMPLE_DIR = Path(__file__).resolve().parents[1] / "sample_pdfs"

# If you want hard assertions for specific vendor files, add them here once the
# PDFs are available in `sample_pdfs/`.
#
# Key: lowercase substring that must appear in filename.
# Value: dict with any of: amount, invoice_number, customer_number.
EXPECTED_BY_FILENAME_SUBSTRING: dict[str, dict[str, object]] = {
    "cevetech": {"amount": 605.92, "invoice_number": "2602561", "customer_number": "10295"},
    "caleffi": {"amount": 797.35, "invoice_number": "1210001330", "customer_number": "1025995"},
}


@pytest.mark.skipif(not SAMPLE_DIR.exists(), reason="No sample_pdfs/ directory")
def test_sample_pdfs_smoke_parse() -> None:
    pdfs = sorted(p for p in SAMPLE_DIR.glob("*.pdf") if p.is_file())
    if not pdfs:
        pytest.skip("No PDFs in sample_pdfs/")

    for pdf in pdfs:
        text = extract_text_strict(str(pdf))
        assert text.strip(), f"Empty text layer in {pdf.name}"
        d = extract_invoice_data(text)
        # Smoke-level expectations: we should at least find a payable amount.
        assert d.get("amount") is not None, f"Missing amount in {pdf.name}"

        fn = pdf.name.casefold()
        for needle, exp in EXPECTED_BY_FILENAME_SUBSTRING.items():
            if needle not in fn:
                continue
            if "amount" in exp:
                assert d.get("amount") == exp["amount"], f"Unexpected amount in {pdf.name}"
            if "invoice_number" in exp:
                assert d.get("invoice_number") == exp["invoice_number"], f"Unexpected invoice_number in {pdf.name}"
            if "customer_number" in exp:
                assert d.get("customer_number") == exp["customer_number"], f"Unexpected customer_number in {pdf.name}"

