"""Tests voor universeel FieldResult-model en adapters."""

from __future__ import annotations

from decimal import Decimal

from parser.field_adapters import (
    amount_result_from_field_result,
    field_result_from_amount,
    field_result_from_iban,
    field_result_from_ident,
    field_result_to_legacy_dict,
    iban_result_from_field_result,
    ident_field_result_from_field_result,
    normalize_amount_result_dict,
)
from parser.field_candidates import (
    IdentFieldCandidate,
    IdentFieldResult,
    extract_invoice_number_result,
)
from parser.field_model import CandidateCollection, FieldResult
from parser.pdf_parser import AmountCandidate, AmountResult


class TestAmountRoundtrip:
    def test_amount_result_roundtrip(self):
        ar = AmountResult(
            candidates=[
                AmountCandidate(
                    value=Decimal("1287.29"),
                    source="total_label_payable",
                    confidence=80,
                    context="Totaal te betalen",
                    type="incl",
                ),
                AmountCandidate(
                    value=Decimal("1063.88"),
                    source="total_label_excl",
                    confidence=70,
                    context="Totaal excl",
                    type="excl",
                ),
            ],
            value=Decimal("1287.29"),
            confidence=80,
            source="total_label_payable",
            status="confirmed",
        )
        fr = field_result_from_amount(ar)
        assert fr.field_id == "amount"
        assert fr.selected_value == Decimal("1287.29")
        assert fr.status == "confirmed"
        assert len(fr.candidates) == 2
        assert fr.candidates[0].meta.get("type") == "incl"

        ar2 = amount_result_from_field_result(fr)
        assert ar2.value == ar.value
        assert ar2.status == ar.status
        assert len(ar2.candidates) == 2
        d = ar2.to_dict()
        assert d["selected_amount"] == "1287.29"
        assert d["amount_status"] == "confirmed"

    def test_amount_dict_roundtrip(self):
        raw = {
            "status": "ambiguous",
            "source": "INCL_CONFLICT",
            "value": None,
            "selected_amount": None,
            "confidence": 0,
            "candidates": [
                {
                    "value": "100.00",
                    "source": "total_label_payable",
                    "confidence": 80,
                    "context": "Totaal",
                    "type": "incl",
                }
            ],
        }
        fr = field_result_from_amount(raw)
        assert fr.status == "ambiguous"
        assert fr.is_pickable
        legacy = field_result_to_legacy_dict(fr)
        assert legacy["status"] == "ambiguous"
        assert legacy["amount_status"] == "ambiguous"

    def test_user_selected_preserved(self):
        ar = AmountResult(
            candidates=[
                AmountCandidate(
                    value=Decimal("50.00"),
                    source="USER_PICKED",
                    confidence=95,
                    context="Handmatig",
                )
            ],
            value=Decimal("50.00"),
            confidence=95,
            source="USER_PICKED",
            status="confirmed",
            user_selected=True,
        )
        fr = field_result_from_amount(ar)
        assert fr.user_selected is True
        ar2 = amount_result_from_field_result(fr)
        assert ar2.user_selected is True
        assert ar2.to_dict().get("user_selected") is True


class TestIdentRoundtrip:
    def test_ident_field_result_roundtrip(self):
        ir = IdentFieldResult(
            candidates=[
                IdentFieldCandidate(
                    value="26FC000498",
                    source="factuur_plain",
                    confidence=83,
                    context="Factuur 26FC000498",
                    label="Factuur",
                )
            ],
            value="26FC000498",
            confidence=83,
            source="factuur_plain",
            status="confirmed",
        )
        fr = field_result_from_ident(ir, field_id="invoice_number")
        assert fr.field_id == "invoice_number"
        assert fr.selected_value == "26FC000498"

        ir2 = ident_field_result_from_field_result(fr)
        assert ir2.value == ir.value
        assert ir2.status == ir.status
        d = ir2.to_dict()
        assert d["value"] == "26FC000498"
        assert d["status"] == "confirmed"

    def test_extract_invoice_number_via_adapter(self):
        text = "Factuur 26FC000498\n"
        ir = extract_invoice_number_result(text)
        fr = field_result_from_ident(ir, field_id="invoice_number")
        assert fr.selected_value == "26FC000498"
        legacy = field_result_to_legacy_dict(fr)
        assert legacy["value"] == "26FC000498"


class TestCandidateCollection:
    def test_from_invoice_dict(self):
        inv = {
            "amount_result": {
                "status": "confirmed",
                "value": "10.00",
                "confidence": 90,
                "source": "total_label_payable",
                "candidates": [],
            },
            "invoice_number_result": {
                "status": "confirmed",
                "value": "INV-1",
                "confidence": 88,
                "source": "label",
                "candidates": [],
            },
        }
        coll = CandidateCollection.from_invoice_dict(inv)
        assert coll.get("amount") is not None
        assert coll.get("amount").selected_value == Decimal("10.00")
        assert coll.get("invoice_number") is not None
        patched = coll.patch_invoice_dict(inv)
        assert patched["amount_result"]["status"] == "confirmed"
        assert patched["invoice_number_result"]["value"] == "INV-1"

    def test_iban_roundtrip_and_collection(self):
        ir = {
            "status": "confirmed",
            "value": "NL20INGB0001234567",
            "confidence": 88,
            "source": "pdf_text",
            "candidates": [
                {
                    "value": "NL20INGB0001234567",
                    "source": "pdf_text",
                    "confidence": 88,
                    "context": "IBAN NL20…",
                },
                {
                    "value": "NL91ABNA0417164300",
                    "source": "pdf_text",
                    "confidence": 72,
                    "context": "other",
                },
            ],
        }
        fr = field_result_from_iban(ir)
        assert fr.field_id == "iban"
        assert fr.selected_value == "NL20INGB0001234567"
        ir2 = iban_result_from_field_result(fr)
        assert ir2.value == ir["value"]
        coll = CandidateCollection.from_invoice_dict({"iban_result": ir})
        assert coll.get("iban") is not None
        patched = coll.patch_invoice_dict({})
        assert patched["iban"] == "NL20INGB0001234567"
        assert patched["iban_result"]["status"] == "confirmed"


class TestResolvedContext:
    def test_amount_resolved_context(self):
        fr = field_result_from_amount(
            {
                "status": "confirmed",
                "value": "100.00",
                "confidence": 90,
                "source": "total_label_payable",
                "user_selected": True,
                "candidates": [
                    {
                        "value": "100.00",
                        "source": "total_label_payable",
                        "confidence": 90,
                        "context": "Totaal EUR 100,00",
                    }
                ],
            }
        )
        assert fr.resolved_context() == "Totaal EUR 100,00"

    def test_ident_resolved_context(self):
        fr = field_result_from_ident(
            {
                "status": "confirmed",
                "value": "8035714",
                "confidence": 95,
                "source": "label",
                "user_selected": True,
                "candidates": [
                    {
                        "value": "8035714",
                        "source": "label",
                        "confidence": 88,
                        "context": "Polisnummer : 8 0 35714",
                        "label": "Polisnummer",
                    }
                ],
            },
            field_id="invoice_number",
        )
        assert fr.resolved_context() == "Polisnummer : 8 0 35714"


class TestHybridMetaRoundtrip:
    def test_hybrid_meta_preserved_on_amount(self):
        raw = {
            "status": "confirmed",
            "value": "100.00",
            "confidence": 90,
            "source": "total_label_payable",
            "candidates": [],
            "user_overridden": True,
            "previous_value": "50.00",
            "override_reason": "user_locked",
            "decision_trace": [
                {"source": "generic", "confidence": 90, "considered": True, "win": True},
            ],
        }
        fr = field_result_from_amount(raw)
        assert fr.user_overridden is True
        assert fr.previous_value == "50.00"
        assert fr.override_reason == "user_locked"
        assert len(fr.decision_trace) == 1
        legacy = field_result_to_legacy_dict(fr)
        assert legacy.get("user_overridden") is True
        assert legacy.get("previous_value") == "50.00"
        assert legacy.get("override_reason") == "user_locked"
        assert len(legacy.get("decision_trace") or []) == 1

    def test_hybrid_meta_defaults(self):
        fr = FieldResult(field_id="invoice_number", status="failed")
        legacy = field_result_to_legacy_dict(fr)
        assert "user_overridden" not in legacy or legacy.get("user_overridden") is False
        assert "decision_trace" not in legacy or not legacy.get("decision_trace")


class TestNormalizeAmountDict:
    def test_normalize_aliases(self):
        n = normalize_amount_result_dict(
            {
                "amount_status": "tentative",
                "selected_amount": "12.50",
                "amount_confidence": 75,
                "source": "total_label_generic",
                "candidates": [],
            }
        )
        assert n["status"] == "tentative"
        assert n["value"] == "12.50"
        assert n["confidence"] == 75
