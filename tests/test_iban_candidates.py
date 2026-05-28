"""Tests voor parser/iban_candidates.py."""

from __future__ import annotations

from parser.iban_candidates import (
    collect_iban_candidates_from_ocr,
    collect_iban_candidates_from_text,
    extract_iban_result,
    merge_iban_candidates,
    merge_ocr_into_iban_result,
)
from parser.field_candidates import IdentFieldCandidate

NL1 = "NL20INGB0001234567"
NL2 = "NL91ABNA0417164300"


class TestCollectIbanCandidates:
    def test_single_iban_with_label(self):
        text = f"IBAN: {NL1}\nTotaal 100,00"
        cands = collect_iban_candidates_from_text(text)
        assert len(cands) == 1
        assert cands[0].value == NL1
        assert cands[0].source == "pdf_text"
        assert cands[0].confidence == 88

    def test_debtor_iban_filtered(self):
        text = f"IBAN {NL1}\nIBAN {NL2}"
        cands = collect_iban_candidates_from_text(text, debtor_iban=NL1)
        values = [c.value for c in cands]
        assert NL1 not in values
        assert NL2 in values

    def test_multiple_ibans_ambiguous(self):
        text = f"Rekening {NL1}\nAndere rekening {NL2}"
        result = extract_iban_result(text)
        assert result.status == "ambiguous"
        assert result.value is None
        assert len(result.candidates) >= 2

    def test_ocr_merge_when_no_pdf(self):
        ocr = collect_iban_candidates_from_ocr([NL1], pdf_had_any=False)
        assert ocr[0].confidence == 90
        assert ocr[0].source == "ocr"

    def test_merge_dedupes(self):
        pdf = [IdentFieldCandidate(value=NL1, source="pdf_text", confidence=78, context="")]
        ocr = [IdentFieldCandidate(value=NL1, source="ocr", confidence=82, context="OCR")]
        merged = merge_iban_candidates(pdf, ocr)
        assert len(merged) == 1
        assert merged[0].source == "pdf_text"

    def test_merge_ocr_into_existing_result(self):
        existing = {
            "status": "failed",
            "value": None,
            "candidates": [],
        }
        ir = merge_ocr_into_iban_result(existing, [NL1])
        assert ir.value == NL1
        assert ir.source == "ocr"
        assert len(ir.candidates) == 1
