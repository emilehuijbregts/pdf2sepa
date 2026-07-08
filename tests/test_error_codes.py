"""Tests for worker/UI error code translation."""

from __future__ import annotations

import pytest

from main_window import (
    _batch_error_message,
    _looks_like_internal_code,
    _user_facing_error_text,
)
from ui.i18n import UiStrings, tr, tr_or_code
from ui.workers.invoice_batch_load_worker import _exception_to_error_code


@pytest.fixture(autouse=True)
def reset_language() -> None:
    yield
    UiStrings.set_language("nl")


def test_worker_maps_params_missing() -> None:
    from ui.workers.invoice_batch_load_worker import InvoiceBatchLoadWorker

    worker = InvoiceBatchLoadWorker()
    emitted: list[str] = []
    worker.error.connect(emitted.append)
    worker.start_preprocess()
    assert emitted == ["error.batch.params_missing"]


def test_worker_maps_warm_invoices_required() -> None:
    assert _exception_to_error_code(ValueError("warm_invoices required when parse_pdfs=False")) == (
        "error.batch.warm_invoices_required"
    )


def test_batch_error_message_translates_code() -> None:
    msg = _batch_error_message("error.batch.params_missing")
    assert "Laden mislukt" in msg
    assert "Batch-load parameters ontbreken" in msg


def test_tr_or_code_unknown_falls_back() -> None:
    assert tr_or_code("error.reason.totally_unknown", "fallback") == "fallback"


def test_user_facing_error_hides_internal_codes() -> None:
    msg = _user_facing_error_text(reason_code="amount_low_confidence")
    assert "amount_low_confidence" not in msg
    assert "lage betrouwbaarheid" in msg


def test_user_facing_error_translates_warnings() -> None:
    msg = _user_facing_error_text(warnings="amount_tentative|credit_match_needs_review")
    assert "amount_tentative" not in msg
    assert "credit_match_needs_review" not in msg
    assert " · " not in msg
    assert "\n" in msg


def test_looks_like_internal_code_snake_case() -> None:
    assert _looks_like_internal_code("amount_low_confidence")
    assert not _looks_like_internal_code("Bedrag ontbreekt of is ongeldig.")
