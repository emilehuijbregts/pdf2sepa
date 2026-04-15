"""Tests for output/sepa_xml.py — SEPA XML generation."""

from __future__ import annotations

import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from lxml import etree

from output.sepa_xml import NS, generate_xml, validate_export_batch


def qn(local: str) -> str:
    return f"{{{NS}}}{local}"


@pytest.fixture
def tmp_export_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def debtor():
    return {"name": "Test Bedrijf BV", "iban": "NL02ABNA0123456789", "bic": "ABNANL2A"}


@pytest.fixture
def exec_dt():
    return (date.today() + timedelta(days=1)).isoformat()


def _payment(execution_date: str | None = None, **overrides):
    ex = execution_date if execution_date is not None else (date.today() + timedelta(days=1)).isoformat()
    p = {
        "supplier_name": "Leverancier A",
        "iban": "NL20INGB0001234567",
        "amount": 100.00,
        "description": "REF001",
        "invoice_number": "INV001",
        "execution_date": ex,
        "decision": {
            "status": "included",
            "reason_code": "included_validated",
            "reason_detail": None,
            "editable": False,
            "requires_rerun": False,
            "reason_code_version": 1,
            "input_field_fingerprint": "test",
            "causal_inputs": ["amount", "iban"],
        },
    }
    p.update(overrides)
    return p


def test_includes_user_approved_decision(tmp_export_dir, debtor, exec_dt):
    approved = _payment(
        execution_date=exec_dt,
        invoice_number="U1",
        decision={
            "status": "included",
            "reason_code": "user_approved",
            "reason_detail": "context_menu_approve",
            "editable": False,
            "requires_rerun": False,
            "reason_code_version": 1,
            "input_field_fingerprint": "x",
            "causal_inputs": ["user_approve"],
        },
    )
    path = generate_xml([approved], debtor, output_dir=tmp_export_dir)
    root = etree.parse(path).getroot()
    txs = root.findall(f".//{qn('CdtTrfTxInf')}")
    assert len(txs) == 1


class TestBasicGeneration:
    def test_generates_file(self, tmp_export_dir, debtor, exec_dt):
        path = generate_xml([_payment(execution_date=exec_dt)], debtor, output_dir=tmp_export_dir)
        assert Path(path).is_file()

    def test_valid_xml(self, tmp_export_dir, debtor, exec_dt):
        path = generate_xml([_payment(execution_date=exec_dt)], debtor, output_dir=tmp_export_dir)
        tree = etree.parse(path)
        root = tree.getroot()
        assert root.tag == qn("Document")
        assert root.nsmap.get(None) == NS

    def test_correct_namespace(self, tmp_export_dir, debtor, exec_dt):
        path = generate_xml([_payment(execution_date=exec_dt)], debtor, output_dir=tmp_export_dir)
        root = etree.parse(path).getroot()
        assert "pain.001.001.09" in root.nsmap.get(None, "")

    def test_ignores_optional_decision_trace_field(self, tmp_export_dir, debtor, exec_dt):
        p = _payment(
            execution_date=exec_dt,
            decision_trace={
                "amount_decision_reason": "amount_selected_single_candidate",
                "engine_status_flags": ["amount_low_confidence"],
            },
        )
        path = generate_xml([p], debtor, output_dir=tmp_export_dir)
        root = etree.parse(path).getroot()
        txs = root.findall(f".//{qn('CdtTrfTxInf')}")
        assert len(txs) == 1

    def test_excludes_non_included_decisions(self, tmp_export_dir, debtor, exec_dt):
        included = _payment(execution_date=exec_dt, invoice_number="I1")
        excluded = _payment(
            execution_date=exec_dt,
            invoice_number="I2",
            decision={
                "status": "excluded",
                "reason_code": "missing_iban",
                "reason_detail": None,
                "editable": True,
                "requires_rerun": False,
                "reason_code_version": 1,
                "input_field_fingerprint": "x",
                "causal_inputs": ["iban"],
            },
        )
        path = generate_xml([included, excluded], debtor, output_dir=tmp_export_dir)
        root = etree.parse(path).getroot()
        txs = root.findall(f".//{qn('CdtTrfTxInf')}")
        assert len(txs) == 1


class TestTransactionCounts:
    def test_single_payment(self, tmp_export_dir, debtor, exec_dt):
        path = generate_xml([_payment(execution_date=exec_dt)], debtor, output_dir=tmp_export_dir)
        root = etree.parse(path).getroot()
        txs = root.findall(f".//{qn('CdtTrfTxInf')}")
        assert len(txs) == 1

    def test_multiple_payments(self, tmp_export_dir, debtor, exec_dt):
        payments = [_payment(execution_date=exec_dt, invoice_number=f"INV{i}") for i in range(5)]
        path = generate_xml(payments, debtor, output_dir=tmp_export_dir)
        root = etree.parse(path).getroot()
        txs = root.findall(f".//{qn('CdtTrfTxInf')}")
        assert len(txs) == 5

    def test_nb_of_txs_correct(self, tmp_export_dir, debtor, exec_dt):
        payments = [
            _payment(execution_date=exec_dt, invoice_number="A"),
            _payment(execution_date=exec_dt, invoice_number="B"),
        ]
        path = generate_xml(payments, debtor, output_dir=tmp_export_dir)
        root = etree.parse(path).getroot()
        grp_nb = root.find(f".//{qn('GrpHdr')}/{qn('NbOfTxs')}")
        assert grp_nb is not None and grp_nb.text == "2"


class TestCtrlSum:
    def test_ctrl_sum_matches(self, tmp_export_dir, debtor, exec_dt):
        payments = [
            _payment(execution_date=exec_dt, amount=10.10, invoice_number="P1"),
            _payment(execution_date=exec_dt, amount=20.20, invoice_number="P2"),
            _payment(execution_date=exec_dt, amount=30.30, invoice_number="P3"),
        ]
        path = generate_xml(payments, debtor, output_dir=tmp_export_dir)
        root = etree.parse(path).getroot()
        cs_elems = root.findall(f".//{qn('CtrlSum')}")
        assert all(el.text == "60.60" for el in cs_elems)


class TestMultipleBatches:
    def test_two_execution_dates_two_pmtinf(self, tmp_export_dir, debtor):
        d1 = "2030-01-15"
        d2 = "2030-02-20"
        payments = [
            _payment(execution_date=d1, amount=10.0, invoice_number="A"),
            _payment(execution_date=d2, amount=20.0, invoice_number="B"),
        ]
        path = generate_xml(payments, debtor, output_dir=tmp_export_dir)
        root = etree.parse(path).getroot()
        pmt_infs = root.findall(f".//{qn('PmtInf')}")
        assert len(pmt_infs) == 2
        grp_nb = root.find(f".//{qn('GrpHdr')}/{qn('NbOfTxs')}")
        assert grp_nb is not None and grp_nb.text == "2"
        grp_cs = root.find(f".//{qn('GrpHdr')}/{qn('CtrlSum')}")
        assert grp_cs is not None and grp_cs.text == "30.00"
        dates = [p.find(f"{qn('ReqdExctnDt')}/{qn('Dt')}").text for p in pmt_infs]
        assert dates == [d1, d2]
        nbs = [p.find(qn("NbOfTxs")).text for p in pmt_infs]
        assert nbs == ["1", "1"]

    def test_missing_execution_date_raises(self, tmp_export_dir, debtor):
        bad = _payment()
        del bad["execution_date"]
        with pytest.raises(ValueError, match="execution_date"):
            generate_xml([bad], debtor, output_dir=tmp_export_dir)


class TestSepaFields:
    def test_svc_lvl_sepa(self, tmp_export_dir, debtor, exec_dt):
        path = generate_xml([_payment(execution_date=exec_dt)], debtor, output_dir=tmp_export_dir)
        root = etree.parse(path).getroot()
        cd = root.find(f".//{qn('SvcLvl')}/{qn('Cd')}")
        assert cd is not None and cd.text == "SEPA"

    def test_chrg_br_slev(self, tmp_export_dir, debtor, exec_dt):
        path = generate_xml([_payment(execution_date=exec_dt)], debtor, output_dir=tmp_export_dir)
        root = etree.parse(path).getroot()
        ch = root.find(f".//{qn('ChrgBr')}")
        assert ch is not None and ch.text == "SLEV"

    def test_bicfi_used(self, tmp_export_dir, debtor, exec_dt):
        path = generate_xml([_payment(execution_date=exec_dt)], debtor, output_dir=tmp_export_dir)
        root = etree.parse(path).getroot()
        bicfi = root.find(f".//{qn('DbtrAgt')}/{qn('FinInstnId')}/{qn('BICFI')}")
        assert bicfi is not None and bicfi.text == "ABNANL2A"

    def test_reqd_exctn_dt_nested(self, tmp_export_dir, debtor, exec_dt):
        path = generate_xml([_payment(execution_date=exec_dt)], debtor, output_dir=tmp_export_dir)
        root = etree.parse(path).getroot()
        dt = root.find(f".//{qn('ReqdExctnDt')}/{qn('Dt')}")
        assert dt is not None and dt.text == exec_dt


class TestSpecialCharacters:
    def test_unicode_supplier_name(self, tmp_export_dir, debtor, exec_dt):
        p = _payment(execution_date=exec_dt, supplier_name="Müller & Zöhne GmbH")
        path = generate_xml([p], debtor, output_dir=tmp_export_dir)
        root = etree.parse(path).getroot()
        nm = root.find(f".//{qn('Cdtr')}/{qn('Nm')}")
        assert nm is not None and nm.text == "Müller & Zöhne GmbH"

    def test_long_description_truncated(self, tmp_export_dir, debtor, exec_dt):
        p = _payment(execution_date=exec_dt, description="A" * 200)
        path = generate_xml([p], debtor, output_dir=tmp_export_dir)
        root = etree.parse(path).getroot()
        ustrd = root.find(f".//{qn('Ustrd')}")
        assert ustrd is not None and len(ustrd.text) <= 140

    def test_notprovided_fallback(self, tmp_export_dir, debtor, exec_dt):
        p = _payment(execution_date=exec_dt, invoice_number="")
        path = generate_xml([p], debtor, output_dir=tmp_export_dir)
        root = etree.parse(path).getroot()
        e2e = root.find(f".//{qn('EndToEndId')}")
        assert e2e is not None and e2e.text == "NOTPROVIDED"


class TestErrorCases:
    def test_empty_payments_raises(self, tmp_export_dir, debtor):
        with pytest.raises(ValueError):
            generate_xml([], debtor, output_dir=tmp_export_dir)


class TestBatchValidation:
    def test_validate_empty_batch_valid(self):
        r = validate_export_batch([])
        assert r.status == "valid"
        assert r.flags == []
        assert r.summary == {
            "payment_count": 0,
            "ambiguous_count": 0,
            "failed_count": 0,
            "invalid_amount_count": 0,
        }

    def test_validate_ok_payment(self, exec_dt):
        r = validate_export_batch([_payment(execution_date=exec_dt)])
        assert r.status == "valid"
        assert "duplicate_risk_detected" not in r.flags

    def test_tentative_amount_result_not_blocked(self, exec_dt):
        p = _payment(
            execution_date=exec_dt,
            amount_result={
                "status": "tentative",
                "value": "184.56",
                "confidence": 80,
                "source": "TOTAL_LABEL_SUM",
                "candidates": [],
            },
        )
        r = validate_export_batch([p])
        assert r.status == "valid"
        assert r.summary["ambiguous_count"] == 0
        assert r.summary["failed_count"] == 0

    def test_blocked_top_level_status_ambiguous(self, exec_dt):
        r = validate_export_batch([_payment(execution_date=exec_dt, status="ambiguous")])
        assert r.status == "blocked"
        assert "has_ambiguous_payment" in r.flags
        assert r.summary["ambiguous_count"] == 1
        assert r.summary["failed_count"] == 0

    def test_blocked_trace_parsed_amount_failed(self, exec_dt):
        p = _payment(
            execution_date=exec_dt,
            decision_trace={
                "reconciliation_snapshot": {
                    "parsed_amount_result": {"status": "failed"},
                },
            },
        )
        r = validate_export_batch([p])
        assert r.status == "blocked"
        assert "has_failed_payment" in r.flags
        assert r.summary["failed_count"] == 1

    def test_amount_result_confirmed_overrides_trace_ambiguous(self, exec_dt):
        """Tabel-herstel: top-level amount_result wint van oude ambiguous trace."""
        p = _payment(
            execution_date=exec_dt,
            decision_trace={
                "reconciliation_snapshot": {
                    "parsed_amount_result": {"status": "ambiguous"},
                },
            },
            amount_result={
                "status": "confirmed",
                "amount_status": "confirmed",
                "value": "121.00",
                "selected_amount": "121.00",
                "candidates": [],
                "user_selected": True,
            },
        )
        r = validate_export_batch([p])
        assert r.status == "valid"
        assert r.summary["ambiguous_count"] == 0
        assert r.summary["failed_count"] == 0

    def test_warning_duplicate_iban_amount(self, exec_dt):
        p1 = _payment(execution_date=exec_dt, invoice_number="A1")
        p2 = _payment(execution_date=exec_dt, invoice_number="A2")
        r = validate_export_batch([p1, p2])
        assert r.status == "warning"
        assert "duplicate_risk_detected" in r.flags
        assert r.summary["payment_count"] == 2

    def test_generate_xml_blocked_raises(self, tmp_export_dir, debtor, exec_dt):
        bad = _payment(execution_date=exec_dt, status="failed")
        with pytest.raises(ValueError, match="Export geblokkeerd"):
            generate_xml([bad], debtor, output_dir=tmp_export_dir)

    def test_generate_xml_warning_logs(self, tmp_export_dir, debtor, exec_dt, caplog):
        import logging

        p1 = _payment(execution_date=exec_dt, invoice_number="X1")
        p2 = _payment(execution_date=exec_dt, invoice_number="X2")
        with caplog.at_level(logging.WARNING, logger="output.sepa_xml"):
            path = generate_xml([p1, p2], debtor, output_dir=tmp_export_dir)
        assert Path(path).is_file()
        assert any("status=warning" in rec.message for rec in caplog.records)


class TestDeterministicExport:
    def test_sepa_xml_byte_identity_snapshot_hash(self, tmp_export_dir, debtor):
        d = "2030-03-01"
        payments = [
            _payment(execution_date=d, amount=10.00, invoice_number="INV-2"),
            _payment(execution_date=d, amount=11.00, invoice_number="INV-1"),
        ]
        p1 = generate_xml(payments, debtor, output_dir=tmp_export_dir, run_id="run-abc")
        p2 = generate_xml(payments, debtor, output_dir=tmp_export_dir, run_id="run-abc")
        assert Path(p1).read_bytes() == Path(p2).read_bytes()

    def test_canonical_ordering_transactions(self, tmp_export_dir, debtor):
        d = "2030-03-01"
        payments = [
            _payment(execution_date=d, amount=10.00, invoice_number="ZZZ"),
            _payment(execution_date=d, amount=11.00, invoice_number="AAA"),
        ]
        path = generate_xml(payments, debtor, output_dir=tmp_export_dir, run_id="run-order")
        root = etree.parse(path).getroot()
        e2e = [x.text for x in root.findall(f".//{qn('EndToEndId')}")]
        assert e2e == ["AAA", "ZZZ"]
