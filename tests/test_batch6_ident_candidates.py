"""Fase 1: factuur-/klantnummer-kandidaten zichtbaar voor Batch 6 leveranciers."""

from __future__ import annotations

from pathlib import Path

import pytest

from parser.field_candidates import (
    extract_customer_number_result,
    extract_invoice_number_result,
)
from parser.pdf_parser import extract_text_strict

BATCH6 = Path(__file__).resolve().parent / "Batch 6"

_BATCH6_CASES: list[tuple[str, str, str, str]] = [
    ("Rexel 113023143_0.pdf", "113023143", "52111087"),
    ("Roba INV-0396393.PDF", "INV-0396393", "C05630"),
    ("Sanha REG-3461477.pdf", "REG20260000971", "1004563"),
    ("van Walraven Factuur_801083_VP601987.pdf", "VP601987", "801083"),
    ("Samedia R1126096.pdf", "R1126096", "58181"),
    (
        "Ubbink INV_SIN_10567557_101900683_Origineel_0_M.pdf",
        "SIN/10567557",
        "101900683",
    ),
    ("Venttrade Factuur_1100_220_10020159.pdf", "1100/220/10020159", ""),
    ("Van den Borne Factuur_4126VF01369.PDF", "4126VF01369", ""),
    ("Tegeka Factuur93557.pdf", "93557", "10476"),
    ("Vent axia 26801599.PDF", "26801599", "219073"),
]


def _real_candidates(result) -> list:
    return [c for c in result.candidates if str(c.source or "") != "fallback_missing"]


@pytest.mark.parametrize("filename,invoice_number,customer_number", _BATCH6_CASES)
def test_batch6_expected_values_are_candidates(
    filename: str,
    invoice_number: str,
    customer_number: str,
) -> None:
    pdf = BATCH6 / filename
    if not pdf.is_file():
        pytest.skip(f"Missing fixture PDF: {pdf}")
    text = extract_text_strict(str(pdf))
    inv = extract_invoice_number_result(text)
    cust = extract_customer_number_result(text)
    inv_vals = {c.value for c in _real_candidates(inv)}
    cust_vals = {c.value for c in _real_candidates(cust)}
    assert len(inv_vals) >= 1, f"No invoice candidates for {filename}"
    assert invoice_number in inv_vals, f"{invoice_number} not in {inv_vals}"
    if customer_number:
        assert len(cust_vals) >= 1, f"No customer candidates for {filename}"
        assert customer_number in cust_vals, f"{customer_number} not in {cust_vals}"


@pytest.mark.parametrize("filename,invoice_number,customer_number", _BATCH6_CASES)
def test_batch6_expected_values_win(
    filename: str,
    invoice_number: str,
    customer_number: str,
) -> None:
    pdf = BATCH6 / filename
    if not pdf.is_file():
        pytest.skip(f"Missing fixture PDF: {pdf}")
    text = extract_text_strict(str(pdf))
    inv = extract_invoice_number_result(text)
    cust = extract_customer_number_result(text)
    assert inv.value == invoice_number, f"{filename}: got {inv.value!r}"
    if customer_number:
        assert cust.value == customer_number, f"{filename}: got {cust.value!r}"
