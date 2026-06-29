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
        assert result.status in {"confirmed", "tentative"}
        assert result.value in {NL1, NL2}
        assert len(result.candidates) >= 2

    def test_multi_bank_footer_prefers_bnp_over_peppol_line(self):
        """PGB: meerdere bank-IBAN's in footer; BNP wint over Peppol/ING-regel."""
        text = (
            "BTW-nummer : BE 0425.888.396 RPR GENT "
            "KBC IBAN BE50 4459 6389 4118 BIC KREDBEBB "
            "BNP IBAN BE78 2900 1606 0086 BIC GEBABEBB\n"
            "Peppol ID : 9925:BE0425888396 ING IBAN BE98 3900 3232 4293 "
            "BIC BBRUBEBB BEL IBAN BE30 5645 1378 2011 BIC GKCCBEBB 1 / 12\n"
        )
        result = extract_iban_result(text)
        assert result.value == "BE78290016060086"

    def test_pipe_separated_accounts_prefers_first(self):
        """Feyts: twee rekeningen zonder IBAN-label — eerste wint."""
        text = (
            "KvK 14039954 l BTW NL008438602B01 l "
            "ING NL38 INGB 0005 6088 46 l NL 15 RABO 0108 3871 00\n"
        )
        result = extract_iban_result(text)
        assert result.value == "NL38INGB0005608846"

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

    def test_cross_line_labeled_iban_is_detected(self):
        text = "IBAN:\nNL07 RABO 0375 2943 84\nTotaal 100,00"
        result = extract_iban_result(text)
        assert result.value == "NL07RABO0375294384"
        assert any(c.value == "NL07RABO0375294384" for c in result.candidates)

    def test_invalid_mod97_iban_is_filtered_out(self):
        text = "IBAN: NL00 RABO 0375 2943 84"
        cands = collect_iban_candidates_from_text(text)
        assert cands == []

    def test_ocr_is_merged_even_when_pdf_candidate_exists(self):
        existing = {
            "status": "confirmed",
            "value": NL2,
            "source": "pdf_text",
            "candidates": [
                {
                    "value": NL2,
                    "source": "pdf_text",
                    "confidence": 88,
                    "context": "IBAN: NL91ABNA0417164300",
                    "label": "IBAN",
                }
            ],
        }
        ir = merge_ocr_into_iban_result(existing, [NL1])
        vals = {c.value for c in ir.candidates}
        assert NL2 in vals
        assert NL1 in vals
