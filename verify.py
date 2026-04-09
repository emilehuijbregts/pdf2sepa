"""
E2E verify voor PDF2SEPA: Module 1 (parser), Module 2 (supplier matching),
Module 3 (payment engine), Module 4 (uitgebreide payment-scenario's), Module 4b (payment simulaties),
Module 5 (XML generator).
Voer uit met: python verify.py
Exitcode: 0 = alles goed, 1 = iets fout.
"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
from contextlib import redirect_stdout
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from lxml import etree

from output.sepa_xml import NS, generate_xml


def _record(results: list[bool], ok: bool, message: str) -> None:
    icon = "✅" if ok else "❌"
    print(f"{icon} {message}")
    results.append(ok)


def _run_parser_checks(results: list[bool]) -> None:
    print()
    print("[ Parser — invoice dict ]")
    try:
        from parser.pdf_parser import extract_invoice_data
    except Exception as e:
        _record(results, False, f"Parser import FAIL ({e.__class__.__name__})")
        return

    sample_text = "Subtotaal EUR 100,00\nTotaal EUR 121,00"
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            d = extract_invoice_data(sample_text)
    except Exception as e:
        _record(results, False, f"extract_invoice_data crash ({e.__class__.__name__})")
        return

    _record(results, d.get("amount") == 121.0, "amount correct")
    _record(results, d.get("amount_excl_vat") == 100.0, "amount_excl_vat correct")

    try:
        from parser.pdf_parser import extract_amount_excl_vat

        excl_netto = extract_amount_excl_vat("Totaal netto goederenwaarde 9,99")
        _record(results, excl_netto == 9.99, "excl BTW — Totaal netto goederenwaarde")
        excl_goed = extract_amount_excl_vat("Netto goederenbedrag: 252,72")
        _record(results, excl_goed == 252.72, "excl BTW — Netto goederenbedrag")
    except Exception as e:
        _record(results, False, f"extract_amount_excl_vat netto labels ({e.__class__.__name__})")

    try:
        from parser.pdf_parser import format_remittance_text

        rem = format_remittance_text("1012146", "7012254003", None)
        ok_rem = rem == "1012146 / 7012254003" and ".pdf" not in rem
        _record(results, ok_rem, "format_remittance_text — alleen klant/factuur")
    except Exception as e:
        _record(results, False, f"format_remittance_text ({e.__class__.__name__})")


def _run_supplier_checks(results: list[bool]) -> None:
    print()
    print("[ Supplier Matching ]")
    try:
        from parser.supplier_db import SupplierDB
        from parser.supplier_matcher import match_suppliers
    except Exception as e:
        _record(results, False, f"Supplier modules import FAIL ({e.__class__.__name__})")
        return

    payload = {
        "suppliers": [
            {
                "name": "ING Bank B.V.",
                "iban": "NL13TEST0123456789",
                "aliases": [],
                "discount": 5.0,
            }
        ]
    }

    tmp_path: str | None = None
    out: dict | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            tmp.write(json.dumps(payload, ensure_ascii=False))
            tmp_path = tmp.name
        db = SupplierDB(path=tmp_path)
        invoice = {
            "supplier_name": "ING Bank B.V.",
            "iban": "NL13TEST0123456789",
        }
        out = match_suppliers([invoice], db)[0]
    except Exception as e:
        _record(results, False, f"Supplier matching crash ({e.__class__.__name__}: {e})")
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

    if out is None:
        return

    _record(results, "match_status" in out, "match_status aanwezig")
    _record(results, out.get("supplier_name") == "ING Bank B.V.", "supplier_name correct")
    _record(results, "discount" in out and out.get("discount") == 5.0, "discount aanwezig")

    # Klantcode-match (stap 4) + IBAN uit database vullen
    tmp_cc: str | None = None
    try:
        payload_cc = {
            "suppliers": [
                {
                    "name": "Klantcode BV",
                    "iban": "NL62TEST0987654321",
                    "aliases": ["Klantcode BV"],
                    "discount": 1.5,
                    "customer_codes": ["424242"],
                }
            ]
        }
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            tmp.write(json.dumps(payload_cc, ensure_ascii=False))
            tmp_cc = tmp.name
        db_cc = SupplierDB(path=tmp_cc)
        inv_cc = {
            "supplier_hint": None,
            "iban": None,
            "customer_number": "424242",
        }
        out_cc = match_suppliers([inv_cc], db_cc)[0]
        _record(
            results,
            out_cc.get("supplier_name") == "Klantcode BV"
            and out_cc.get("iban") == "NL62TEST0987654321"
            and out_cc.get("match_status") in ("matched", "needs_review", "confirmed"),
            "match via klantcode + IBAN uit DB",
        )
    except Exception as e:
        _record(results, False, f"Klantcode-match crash ({e.__class__.__name__}: {e})")
    finally:
        if tmp_cc:
            try:
                Path(tmp_cc).unlink(missing_ok=True)
            except Exception:
                pass


def _run_payment_checks(results: list[bool]) -> None:
    print()
    print("[ Payment Engine ]")
    try:
        from logic.payment_engine import calculate_payments
    except Exception as e:
        _record(results, False, f"payment_engine import FAIL ({e.__class__.__name__})")
        return

    # Scenario 1 — normale factuur
    try:
        inv1 = {
            "supplier_name": "Test BV",
            "match_status": "matched",
            "amount": 121.0,
            "amount_excl_vat": 100.0,
            "discount": 10,
            "iban": "NL13TEST0123456789",
            "type": "invoice",
            "invoice_number": "INV-1",
            "description": "test",
        }
        pay1, err1 = calculate_payments([inv1])
        ok1 = (
            len(pay1) == 1
            and pay1[0].get("amount") == 111.0
        )
        _record(results, ok1, "normal invoice correct")
    except Exception as e:
        _record(results, False, f"Scenario 1 crash ({e.__class__.__name__})")

    # Scenario 2 — credit correct gekoppeld (discount 0 → 200 - 50 = 150)
    try:
        base = {
            "supplier_name": "Test BV",
            "match_status": "matched",
            "iban": "NL13TEST0123456789",
            "discount": 0,
        }
        inv2a = {
            **base,
            "amount": 200.0,
            "amount_excl_vat": 165.29,
            "type": "invoice",
            "invoice_number": "INV-2",
            "description": "a",
        }
        inv2b = {
            **base,
            "amount": 50.0,
            "type": "credit_note",
            "invoice_number": "CR-1",
            "description": "c",
        }
        pay2, err2 = calculate_payments([inv2a, inv2b])
        ok2 = len(err2) == 0 and len(pay2) == 1 and pay2[0].get("amount") == 150.0
        _record(results, ok2, "credit applied correct")
    except Exception as e:
        _record(results, False, f"Scenario 2 crash ({e.__class__.__name__})")

    # Scenario 3 — credit groter dan factuur
    try:
        base = {
            "supplier_name": "Test BV Over",
            "match_status": "matched",
            "iban": "NL78TEST0999999999",
            "discount": 0,
        }
        inv3a = {
            **base,
            "amount": 200.0,
            "type": "invoice",
            "invoice_number": "INV-3",
            "description": "a",
        }
        inv3b = {
            **base,
            "amount": 300.0,
            "type": "credit_note",
            "invoice_number": "CR-2",
            "description": "c",
        }
        pay3, err3 = calculate_payments([inv3a, inv3b])
        reasons = [e.get("reason") for e in err3]
        ok3 = "credit_exceeds_available_invoices" in reasons and len(pay3) == 0
        _record(results, ok3, "credit overflow blocked")
    except Exception as e:
        _record(results, False, f"Scenario 3 crash ({e.__class__.__name__})")


def _run_module4_payment_checks(results: list[bool]) -> None:
    print()
    print("[ Module 4 — Payment Engine ]")
    try:
        from logic.payment_engine import calculate_payments
    except Exception as e:
        _record(results, False, f"Module 4 payment_engine import FAIL ({e.__class__.__name__})")
        return

    # Test 1 — Factuur zonder creditnota, met korting
    try:
        invoice = {
            "amount": 121.0,
            "amount_excl_vat": 100.0,
            "discount": 2.0,
            "type": "invoice",
            "supplier_name": "ING Bank B.V.",
            "match_status": "matched",
            "iban": "NL13TEST0123456789",
            "invoice_number": "INV001",
            "description": "Testfactuur 1",
        }
        payments, errors = calculate_payments([invoice])
        expected_amount = 119.0
        _record(results, payments[0]["amount"] == expected_amount, "Test 1 — payment amount")
        _record(results, len(errors) == 0, "Test 1 — errors empty")
    except Exception as e:
        _record(results, False, f"Test 1 crash ({e.__class__.__name__})")

    # Test 2 — Factuur met gekoppelde creditnota
    try:
        invoice = {
            "amount": 121.0,
            "amount_excl_vat": 100.0,
            "discount": 2.0,
            "type": "invoice",
            "supplier_name": "ING Bank B.V.",
            "match_status": "matched",
            "iban": "NL13TEST0123456789",
            "invoice_number": "INV002",
            "description": "Testfactuur 2",
        }
        credit = {
            "amount": 60.50,
            "amount_excl_vat": 50.0,
            "discount": 2.0,
            "type": "credit_note",
            "supplier_name": "ING Bank B.V.",
            "match_status": "matched",
            "invoice_number": "CRD001",
            "description": "Credit 1",
        }
        payments, errors = calculate_payments([invoice, credit])
        expected_amount = 59.50
        _record(results, payments[0]["amount"] == expected_amount, "Test 2 — payment amount after credit")
        _record(
            results,
            payments[0]["credit_notes_applied"] == ["CRD001"],
            "Test 2 — credit_notes_applied correct",
        )
        _record(results, len(errors) == 0, "Test 2 — errors empty")
    except Exception as e:
        _record(results, False, f"Test 2 crash ({e.__class__.__name__})")

    # Test 3 — Twee facturen dezelfde leverancier → twee aparte betalingen
    try:
        invoice_a = {
            "amount": 121.0,
            "amount_excl_vat": 100.0,
            "discount": 0,
            "type": "invoice",
            "supplier_name": "ING Bank B.V.",
            "match_status": "matched",
            "iban": "NL13TEST0123456789",
            "invoice_number": "INV003",
            "description": "A",
        }
        invoice_b = {
            "amount": 60.50,
            "amount_excl_vat": 50.0,
            "discount": 0,
            "type": "invoice",
            "supplier_name": "ING Bank B.V.",
            "match_status": "matched",
            "iban": "NL13TEST0123456789",
            "invoice_number": "INV004",
            "description": "B",
        }
        payments, errors = calculate_payments([invoice_a, invoice_b])
        _record(results, len(payments) == 2, "Test 3 — two payments")
        _record(
            results,
            payments[0]["amount"] == 121.0 and payments[1]["amount"] == 60.50,
            "Test 3 — amounts correct",
        )
        _record(results, len(errors) == 0, "Test 3 — errors empty")
    except Exception as e:
        _record(results, False, f"Test 3 crash ({e.__class__.__name__})")

    # Test 4 — Alleen creditnota
    try:
        credit_only = {
            "amount": 50.0,
            "type": "credit_note",
            "supplier_name": "ING Bank B.V.",
            "match_status": "matched",
            "invoice_number": "CRD002",
            "description": "Credit Only",
        }
        payments, errors = calculate_payments([credit_only])
        _record(results, len(payments) == 0, "Test 4 — no payments")
        _record(
            results,
            any(e["reason"] == "credit_note_only" for e in errors),
            "Test 4 — credit_note_only error",
        )
    except Exception as e:
        _record(results, False, f"Test 4 crash ({e.__class__.__name__})")

    # Test 5 — Onbekende leverancier
    try:
        invoice_unknown = {
            "amount": 121.0,
            "amount_excl_vat": 100.0,
            "discount": 2.0,
            "type": "invoice",
            "supplier_name": "UNKNOWN",
            "match_status": "unmatched",
            "iban": "NL13TEST0123456789",
            "invoice_number": "INV005",
            "description": "Unknown Supplier",
        }
        payments, errors = calculate_payments([invoice_unknown])
        _record(results, len(payments) == 0, "Test 5 — no payments for unknown supplier")
        _record(
            results,
            any(e["reason"] == "unmatched_supplier" for e in errors),
            "Test 5 — unmatched_supplier error",
        )
    except Exception as e:
        _record(results, False, f"Test 5 crash ({e.__class__.__name__})")

    # Test 6 — amount_excl_vat ontbreekt → warning
    try:
        invoice_no_excl = {
            "amount": 121.0,
            "amount_excl_vat": None,
            "discount": 2.0,
            "type": "invoice",
            "supplier_name": "ING Bank B.V.",
            "match_status": "matched",
            "iban": "NL13TEST0123456789",
            "invoice_number": "INV006",
            "description": "No Excl VAT",
        }
        payments, errors = calculate_payments([invoice_no_excl])
        _record(results, payments[0]["amount"] == 121.0, "Test 6 — payment amount with missing amount_excl_vat")
        _record(
            results,
            payments[0]["warning"] == "no_excl_vat_amount_discount_skipped",
            "Test 6 — warning set correctly",
        )
    except Exception as e:
        _record(results, False, f"Test 6 crash ({e.__class__.__name__})")

    # Test 6b — IBAN afwijkend van leveranciersdatabase
    try:
        invoice_iban_mm = {
            "amount": 121.0,
            "amount_excl_vat": 100.0,
            "discount": 0,
            "type": "invoice",
            "supplier_name": "ING Bank B.V.",
            "match_status": "matched",
            "iban": "NL13TEST0123456789",
            "invoice_number": "INV006B",
            "description": "IBAN mismatch",
            "iban_mismatch": True,
        }
        payments_b, _err_b = calculate_payments([invoice_iban_mm])
        _record(
            results,
            payments_b[0].get("warning") == "iban_mismatch_supplier",
            "Test 6b — iban_mismatch warning",
        )
        _record(results, payments_b[0].get("iban_mismatch") is True, "Test 6b — iban_mismatch flag on payment")
    except Exception as e:
        _record(results, False, f"Test 6b crash ({e.__class__.__name__})")

    # Test 7 — IBAN: syntactisch ongeldig vs. niet-NL (DE/BE/FR) ok
    try:
        invoice_bad_iban = {
            "amount": 121.0,
            "amount_excl_vat": 100.0,
            "discount": 0,
            "type": "invoice",
            "supplier_name": "ING Bank B.V.",
            "match_status": "matched",
            "iban": "GEEN_IBAN",
            "invoice_number": "INV007",
            "description": "Invalid IBAN",
        }
        payments, errors = calculate_payments([invoice_bad_iban])
        _record(
            results,
            len([e for e in errors if e.get("reason") in ["missing_iban", "invalid_iban"]]) == 1,
            "Test 7 — invalid IBAN error for garbage",
        )
        _record(results, len(payments) == 0, "Test 7 — no payments due to invalid IBAN")

        base = {
            "amount": 100.0,
            "amount_excl_vat": 82.64,
            "discount": 0,
            "type": "invoice",
            "match_status": "matched",
        }
        foreign = [
            {
                **base,
                "supplier_name": "Supplier DE",
                "iban": "DE89370400440532013000",
                "invoice_number": "INV007DE",
                "description": "DE",
            },
            {
                **base,
                "supplier_name": "Supplier BE",
                "iban": "BE68539007547034",
                "invoice_number": "INV007BE",
                "description": "BE",
            },
            {
                **base,
                "supplier_name": "Supplier FR",
                "iban": "FR7630006000011234567890189",
                "invoice_number": "INV007FR",
                "description": "FR",
            },
        ]
        pay_f, err_f = calculate_payments(foreign)
        _record(results, len(pay_f) == 3, "Test 7 — payments for DE/BE/FR IBANs")
        _record(
            results,
            not any(e.get("reason") == "invalid_iban" for e in err_f),
            "Test 7 — no invalid_iban for syntactically valid foreign IBANs",
        )
    except Exception as e:
        _record(results, False, f"Test 7 crash ({e.__class__.__name__})")


def _run_module4b_payment_simulations(results: list[bool]) -> None:
    print("\n[ Module 4b — Payment Engine Simulaties ]")
    try:
        from logic.payment_engine import calculate_payments
    except Exception as e:
        _record(results, False, f"Module 4b payment_engine import FAIL ({e.__class__.__name__})")
        return

    try:
        scenarios = []

        scenarios.append(
            {
                "name": "A - Factuur zonder credit",
                "invoices": [
                    {
                        "invoice_number": "F001",
                        "type": "invoice",
                        "amount": 200.0,
                        "amount_excl_vat": 100.0,
                        "discount": 10.0,
                        "match_status": "matched",
                        "supplier_name": "Test BV",
                        "iban": "NL13TEST0123456789",
                    }
                ],
                "expected_payments": [190.0],
                "expected_errors": [],
            }
        )

        scenarios.append(
            {
                "name": "B - Factuur + credit",
                "invoices": [
                    {
                        "invoice_number": "F002",
                        "type": "invoice",
                        "amount": 300.0,
                        "amount_excl_vat": 200.0,
                        "discount": 5.0,
                        "match_status": "matched",
                        "supplier_name": "Test BV",
                        "iban": "NL13TEST0123456789",
                    },
                    {
                        "invoice_number": "CRD001",
                        "type": "credit_note",
                        "amount": 50.0,
                        "amount_excl_vat": 30.0,
                        "discount": 0.0,
                        "match_status": "matched",
                        "supplier_name": "Test BV",
                        "iban": "NL13TEST0123456789",
                    },
                ],
                # 300 - 50 - ((200 - 30) * 5%) = 241.5
                "expected_payments": [241.5],
                "expected_credit_notes": [["CRD001"]],
                "expected_errors": [],
            }
        )

        scenarios.append(
            {
                "name": "C - Credit overflow",
                "invoices": [
                    {
                        "invoice_number": "F003",
                        "type": "invoice",
                        "amount": 100.0,
                        "amount_excl_vat": 80.0,
                        "discount": 0.0,
                        "match_status": "matched",
                        "supplier_name": "Test BV",
                        "iban": "NL13TEST0123456789",
                    },
                    {
                        "invoice_number": "CRD002",
                        "type": "credit_note",
                        "amount": 150.0,
                        "amount_excl_vat": 120.0,
                        "discount": 0.0,
                        "match_status": "matched",
                        "supplier_name": "Test BV",
                        "iban": "NL13TEST0123456789",
                    },
                ],
                "expected_payments": [],
                "expected_errors": ["credit_exceeds_available_invoices"],
            }
        )

        scenarios.append(
            {
                "name": "D - Twee facturen",
                "invoices": [
                    {
                        "invoice_number": "F004",
                        "type": "invoice",
                        "amount": 200.0,
                        "amount_excl_vat": 150.0,
                        "discount": 0.0,
                        "match_status": "matched",
                        "supplier_name": "Test BV",
                        "iban": "NL13TEST0123456789",
                    },
                    {
                        "invoice_number": "F005",
                        "type": "invoice",
                        "amount": 120.0,
                        "amount_excl_vat": 100.0,
                        "discount": 0.0,
                        "match_status": "matched",
                        "supplier_name": "Test BV",
                        "iban": "NL13TEST0123456789",
                    },
                ],
                "expected_payments": [200.0, 120.0],
                "expected_errors": [],
            }
        )

        scenarios.append(
            {
                "name": "E - Alleen creditnota",
                "invoices": [
                    {
                        "invoice_number": "CRD003",
                        "type": "credit_note",
                        "amount": 50.0,
                        "amount_excl_vat": 50.0,
                        "discount": 0.0,
                        "match_status": "matched",
                        "supplier_name": "Test BV",
                        "iban": "NL13TEST0123456789",
                    }
                ],
                "expected_payments": [],
                "expected_errors": ["credit_note_only"],
            }
        )

        scenarios.append(
            {
                "name": "F - Geen amount_excl_vat",
                "invoices": [
                    {
                        "invoice_number": "F006",
                        "type": "invoice",
                        "amount": 150.0,
                        "amount_excl_vat": None,
                        "discount": 10.0,
                        "match_status": "matched",
                        "supplier_name": "Test BV",
                        "iban": "NL13TEST0123456789",
                    }
                ],
                "expected_payments": [150.0],
                "expected_warnings": ["no_excl_vat_amount_discount_skipped"],
                "expected_errors": [],
            }
        )

        scenarios.append(
            {
                "name": "G - Internationale IBAN",
                "invoices": [
                    {
                        "invoice_number": "F007",
                        "type": "invoice",
                        "amount": 200.0,
                        "amount_excl_vat": 100.0,
                        "discount": 5.0,
                        "match_status": "matched",
                        "supplier_name": "DE GmbH",
                        "iban": "DE89370400440532013000",
                    }
                ],
                "expected_payments": [195.0],
                "expected_errors": [],
            }
        )

        for sc in scenarios:
            try:
                payments, errors = calculate_payments(sc["invoices"])
                ok = True
                msg = sc["name"]

                expected = sc.get("expected_payments", [])
                actual = [p["amount"] for p in payments]
                if actual != expected:
                    ok = False
                    msg += f" | Payments mismatch: expected {expected} got {actual}"

                expected_errs = sc.get("expected_errors", [])
                actual_errs = [e["reason"] for e in errors]
                if sorted(actual_errs) != sorted(expected_errs):
                    ok = False
                    msg += f" | Errors mismatch: expected {expected_errs} got {actual_errs}"

                expected_warnings = sc.get("expected_warnings", [])
                actual_warnings = [p.get("warning") for p in payments if p.get("warning")]
                if sorted(actual_warnings) != sorted(expected_warnings):
                    ok = False
                    msg += f" | Warnings mismatch: expected {expected_warnings} got {actual_warnings}"

                expected_cn = sc.get("expected_credit_notes", [])
                actual_cn = [p.get("credit_notes_applied", []) for p in payments]
                if expected_cn and actual_cn != expected_cn:
                    ok = False
                    msg += f" | credit_notes_applied mismatch: expected {expected_cn} got {actual_cn}"

                _record(results, ok, msg)
            except Exception as e:
                _record(results, False, f"{sc['name']} crash ({type(e).__name__}: {e})")

    except Exception as e:
        _record(results, False, f"Module 4b crash ({type(e).__name__}: {e})")


def _run_module5_xml_generator_checks(results: list[bool]) -> None:
    print()
    print("[ Module 5 — XML Generator ]")

    def qn(local: str) -> str:
        return f"{{{NS}}}{local}"

    def _dec2(s: str) -> Decimal:
        return Decimal(str(s)).quantize(Decimal("0.01"))

    tmp_dir = tempfile.mkdtemp()
    try:
        debtor = {
            "name": "Test Bedrijf BV",
            "iban": "NL02ABNA0123456789",
            "bic": "ABNANL2A",
        }
        exec_dt = (date.today() + timedelta(days=1)).isoformat()

        # Test 1 — basisgeneratie
        payment_a = {
            "supplier_name": "Test Leverancier A",
            "iban": "NL20INGB0001234567",
            "amount": 100.00,
            "description": "REF001",
            "invoice_number": "INV001",
            "execution_date": exec_dt,
        }
        payment_b = {
            "supplier_name": "Test Leverancier B",
            "iban": "NL25CITI0266075452",
            "amount": 57.00,
            "description": "REF002",
            "invoice_number": "INV002",
            "execution_date": exec_dt,
        }
        try:
            out_path = generate_xml(
                [payment_a, payment_b],
                debtor,
                output_dir=tmp_dir,
            )
        except Exception as e:
            _record(results, False, f"Test 1 — generate_xml crash ({e.__class__.__name__})")
            out_path = None

        if out_path is not None:
            _record(results, Path(out_path).is_file(), "Test 1 — bestand bestaat op pad")
            root = None
            try:
                tree = etree.parse(out_path)
                root = tree.getroot()
                _record(results, True, "Test 1 — geldige XML (lxml parse)")
            except Exception as e:
                _record(results, False, f"Test 1 — XML parse FAIL ({e.__class__.__name__})")

            if root is not None:
                ns_ok = root.tag == qn("Document") and root.nsmap.get(None) == NS
                _record(results, ns_ok, "Test 1 — namespace exact pain.001.001.09")

                txs = root.findall(f".//{qn('CdtTrfTxInf')}")
                n_found = len(txs)
                _record(results, n_found == 2, "Test 1 — aantal CdtTrfTxInf = 2")

                instd = [el.text for el in root.findall(f".//{qn('InstdAmt')}")]
                _record(results, instd == ["100.00", "57.00"], "Test 1 — InstdAmt 100.00 en 57.00")
                xml_sum = sum((_dec2(x) for x in instd if x is not None), start=Decimal("0.00"))
                xml_sum_str = f"{xml_sum:.2f}"

                grp_nb = root.find(f".//{qn('GrpHdr')}/{qn('NbOfTxs')}")
                pmt_nb = root.find(f".//{qn('PmtInf')}/{qn('NbOfTxs')}")
                _record(
                    results,
                    grp_nb is not None and grp_nb.text == str(n_found),
                    "Test 1 — GrpHdr NbOfTxs matcht CdtTrfTxInf",
                )
                _record(
                    results,
                    pmt_nb is not None and pmt_nb.text == str(n_found),
                    "Test 1 — PmtInf NbOfTxs matcht CdtTrfTxInf",
                )

                grp_cs = root.find(f".//{qn('GrpHdr')}/{qn('CtrlSum')}")
                pmt_cs = root.find(f".//{qn('PmtInf')}/{qn('CtrlSum')}")
                _record(
                    results,
                    grp_cs is not None and grp_cs.text == xml_sum_str,
                    "Test 1 — GrpHdr CtrlSum matcht som InstdAmt",
                )
                _record(
                    results,
                    pmt_cs is not None and pmt_cs.text == xml_sum_str,
                    "Test 1 — PmtInf CtrlSum matcht som InstdAmt",
                )

                # PmtTpInf/SvcLvl/Cd exact "SEPA"
                cd = root.find(f".//{qn('PmtInf')}/{qn('PmtTpInf')}/{qn('SvcLvl')}/{qn('Cd')}")
                _record(results, cd is not None and cd.text == "SEPA", "Test 1 — SvcLvl/Cd = SEPA")

                # ChrgBr verplicht SLEV
                ch = root.find(f".//{qn('PmtInf')}/{qn('ChrgBr')}")
                _record(results, ch is not None and ch.text == "SLEV", "Test 1 — ChrgBr = SLEV")

                reqd_dt = root.find(f".//{qn('ReqdExctnDt')}/{qn('Dt')}")
                _record(
                    results,
                    reqd_dt is not None and (reqd_dt.text or "").strip() == exec_dt,
                    "Test 1 — ReqdExctnDt/Dt is execution_date",
                )

                dbic = root.find(f".//{qn('PmtInf')}/{qn('DbtrAgt')}/{qn('FinInstnId')}/{qn('BICFI')}")
                _record(results, dbic is not None and (dbic.text or "").strip() == "ABNANL2A", "Test 1 — DbtrAgt/BICFI aanwezig")

                # InstrId + EndToEndId per tx, Ustrd <= 140
                pmt_ids = root.findall(f".//{qn('CdtTrfTxInf')}/{qn('PmtId')}")
                ok_ids = True
                for pid in pmt_ids:
                    instr = pid.find(qn("InstrId"))
                    e2e = pid.find(qn("EndToEndId"))
                    if instr is None or not (instr.text or "").strip():
                        ok_ids = False
                        break
                    if e2e is None or not (e2e.text or "").strip():
                        ok_ids = False
                        break
                _record(results, ok_ids and len(pmt_ids) == n_found, "Test 1 — InstrId en EndToEndId aanwezig per tx")

                ustrd_elems = root.findall(f".//{qn('RmtInf')}/{qn('Ustrd')}")
                _record(
                    results,
                    len(ustrd_elems) == n_found and all(len((u.text or "")) <= 140 for u in ustrd_elems),
                    "Test 1 — Ustrd aanwezig en <= 140",
                )

        # Test 2 — CtrlSum precisie
        try:
            p2 = [
                {
                    "supplier_name": "Precisie A",
                    "iban": "NL20INGB0001234567",
                    "amount": 10.10,
                    "description": "P1",
                    "invoice_number": "P1",
                    "execution_date": exec_dt,
                },
                {
                    "supplier_name": "Precisie B",
                    "iban": "NL25CITI0266075452",
                    "amount": 20.20,
                    "description": "P2",
                    "invoice_number": "P2",
                    "execution_date": exec_dt,
                },
                {
                    "supplier_name": "Precisie C",
                    "iban": "NL02ABNA0123456789",
                    "amount": 30.30,
                    "description": "P3",
                    "invoice_number": "P3",
                    "execution_date": exec_dt,
                },
            ]
            path2 = generate_xml(p2, debtor, output_dir=tmp_dir)
            t2 = etree.parse(path2)
            r2 = t2.getroot()
            cs2 = r2.findall(f".//{qn('CtrlSum')}")
            _record(
                results,
                len(cs2) > 0 and all(el.text == "60.60" for el in cs2),
                "Test 2 — CtrlSum precisie \"60.60\"",
            )
        except Exception as e:
            _record(results, False, f"Test 2 crash ({e.__class__.__name__})")

        # Test 3 — speciale tekens
        umlaut_name = "Müller & Zöhne GmbH"
        try:
            p3 = [
                {
                    "supplier_name": umlaut_name,
                    "iban": "NL20INGB0001234567",
                    "amount": 1.0,
                    "description": "U",
                    "invoice_number": "U1",
                    "execution_date": exec_dt,
                }
            ]
            path3 = generate_xml(p3, debtor, output_dir=tmp_dir)
            t3 = etree.parse(path3)
            _record(results, True, "Test 3 — geldige XML na speciale tekens")
            nm_elems = t3.findall(f".//{qn('Cdtr')}/{qn('Nm')}")
            got = nm_elems[0].text if nm_elems else None
            _record(results, got == umlaut_name, "Test 3 — leveranciersnaam correct (Unicode)")
        except Exception as e:
            _record(results, False, f"Test 3 crash ({e.__class__.__name__})")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> int:
    results: list[bool] = []

    _run_parser_checks(results)
    _run_supplier_checks(results)
    _run_payment_checks(results)
    m4_start = len(results)
    _run_module4_payment_checks(results)

    print()
    m4_ok = all(results[m4_start:])
    if m4_ok:
        print("All Module 4 checks passed ✅")
    else:
        print("Some Module 4 checks failed ❌")

    m4b_start = len(results)
    _run_module4b_payment_simulations(results)
    m4b_ok = all(results[m4b_start:])
    print("All Module 4b checks passed ✅" if m4b_ok else "Some Module 4b checks failed ❌")

    m5_start = len(results)
    _run_module5_xml_generator_checks(results)
    m5_ok = all(results[m5_start:])
    print("All Module 5 checks passed ✅" if m5_ok else "Some Module 5 checks failed ❌")

    print()
    all_ok = all(results)
    if all_ok:
        print("All checks passed ✅")
        return 0
    print("Some checks failed ❌")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
