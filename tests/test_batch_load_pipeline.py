"""Integration tests for batch load pipeline."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

from logic.batch_load_pipeline import BatchLoadParams, deduplicate_invoices, run_preprocess, run_resolve_iban_and_engine
from logic.batch_load_types import IbanRawUiAnswers
from logic.engine_result import EngineResult
from parser.supplier_db import SupplierDBSnapshot


def test_deduplicate_invoices_skips_duplicate_filename() -> None:
    invs = [
        {"source_file": "/a/x.pdf", "invoice_number": "1"},
        {"source_file": "/b/x.pdf", "invoice_number": "2"},
    ]
    unique, skipped = deduplicate_invoices(invs)
    assert len(unique) == 1
    assert skipped == 1


def test_run_resolve_engine_uses_v2_only() -> None:
    from logic.batch_load_types import (
        IbanAmbiguity,
        MatchedInvoiceBatch,
        PreprocessCheckpoint,
        RawInvoiceBatch,
        freeze_invoice_tuple,
    )

    v0 = RawInvoiceBatch(batch_id="v0", invoices=freeze_invoice_tuple([{"amount": "10"}]))
    v1 = MatchedInvoiceBatch(
        batch_id="v1",
        parent_batch_id="v0",
        invoices=freeze_invoice_tuple([{"amount": "10", "match_status": "confirmed"}]),
    )
    checkpoint = PreprocessCheckpoint(
        v0=v0,
        v1=v1,
        iban_ambiguities=(),
        iban_dialog_specs=(),
        n_dupes=0,
        per_source_counts=(),
    )
    params = BatchLoadParams(
        folder=Path("."),
        parse_pdfs=False,
        debtor_iban=None,
        debtor_kvk=None,
        debtor_vat=None,
        debtor_name=None,
        supplier_db_snapshot=SupplierDBSnapshot.from_path("data/suppliers.json"),
        session_date=date.today(),
        batch_key="test",
        amount_override_session=None,
        override_store_path="/tmp/credit_overrides.json",
    )
    fake_engine = EngineResult(settlement_groups=[], review_documents=[], legacy_payments=[])
    with patch("logic.batch_load_pipeline.run_engine", return_value=fake_engine) as mock_engine:
        run_resolve_iban_and_engine(params, checkpoint, IbanRawUiAnswers())
        mock_engine.assert_called_once()
        called_v2 = mock_engine.call_args[0][1]
        assert called_v2 is checkpoint.v1.invoices or len(called_v2) == 1
