from __future__ import annotations

from parser.field_candidates import (
    extract_customer_number_result,
    extract_email_domain_result,
    extract_invoice_date_result,
    extract_invoice_number_result,
    extract_kvk_number_result,
    extract_vat_number_result,
)


def test_ident_fields_always_have_at_least_one_candidate_when_missing() -> None:
    text = ""
    for fn in (
        extract_invoice_number_result,
        extract_customer_number_result,
        extract_vat_number_result,
        extract_kvk_number_result,
        extract_invoice_date_result,
        extract_email_domain_result,
    ):
        res = fn(text)
        assert len(res.candidates) >= 1
        # explicit missing candidate
        assert any(str(c.source or "") == "fallback_missing" for c in res.candidates)

