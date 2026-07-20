"""Tests for credit override store and apply layer."""

from __future__ import annotations

from pathlib import Path
from decimal import Decimal

from logic.credit_override_apply import (
    apply_credit_overrides,
    make_detach_override,
    make_reassign_override,
)
from logic.credit_override_store import (
    CreditOverrideAllocation,
    CreditOverrideStore,
    OverrideSession,
    override_session_fingerprint,
)
from logic.credit_matching import match_credits_in_batch
from logic.payment_decisions import DECISION_INCLUDED, REASON_EXPORT_ALLOWED
from logic.payment_engine import calculate_payments, calculate_payments_with_overrides
from logic.settlement_export import exportable_groups


def _base_invoice(**overrides):
    inv = {
        "supplier_name": "Test BV",
        "match_status": "confirmed",
        "amount": 200.0,
        "amount_excl_vat": 165.29,
        "discount": 0,
        "iban": "NL20INGB0001234567",
        "type": "invoice",
        "invoice_number": "INV-1",
        "invoice_date": "2025-06-01",
        "invoice_date_source": "parsed",
        "supplier_term_trusted": True,
        "supplier_payment_term_days_raw": 0,
        "source_file": "inv1.pdf",
    }
    inv.update(overrides)
    return inv


def _max_group_amount(result) -> Decimal:
    return max((g["final_amount_due"] for g in result.settlement_groups), default=Decimal("0.00"))


def test_override_detach(tmp_path):
    inv = _base_invoice()
    credit = _base_invoice(
        amount=50.0,
        type="credit_note",
        invoice_number="CR-1",
        source_file="cr1.pdf",
    )
    match_results = match_credits_in_batch([inv, credit])
    assert match_results[0].linked_invoices

    cid = "cr1.pdf"
    session = OverrideSession(
        batch_key="test",
        overrides=(make_detach_override(cid),),
        history=(),
    )
    modified, events = apply_credit_overrides(match_results, session, batch_invoices=[inv, credit])
    assert not modified[0].linked_invoices
    assert modified[0].match_method == "user_override"
    assert any(e.get("event") == "user_detached" for e in events)


def test_override_reassign(tmp_path):
    inv_a = _base_invoice(invoice_number="INV-A", amount=300.0, source_file="a.pdf")
    inv_b = _base_invoice(invoice_number="INV-B", amount=200.0, source_file="b.pdf")
    credit = _base_invoice(
        amount=50.0,
        type="credit_note",
        invoice_number="CR-1",
        source_file="cr1.pdf",
    )
    match_results = match_credits_in_batch([inv_a, inv_b, credit])
    # credit matched to one invoice by default - reassign to inv_b
    session = OverrideSession(
        batch_key="test",
        overrides=(
            make_reassign_override(
                "cr1.pdf",
                (
                    CreditOverrideAllocation(
                        invoice_document_id="b.pdf",
                        invoice_number="INV-B",
                        amount_applied=Decimal("50.00"),
                    ),
                ),
            ),
        ),
        history=(),
    )
    modified, events = apply_credit_overrides(
        match_results, session, batch_invoices=[inv_a, inv_b, credit]
    )
    assert modified[0].linked_invoices
    assert str(modified[0].linked_invoices[0].get("invoice_number")) == "INV-B"
    assert any(e.get("event") == "user_reassigned" for e in events)


def test_override_reset_via_empty_session():
    inv = _base_invoice()
    credit = _base_invoice(
        amount=50.0,
        type="credit_note",
        invoice_number="CR-1",
        source_file="cr1.pdf",
    )
    auto = calculate_payments([inv, credit])
    with_override = calculate_payments_with_overrides(
        [inv, credit],
        override_session=OverrideSession(
            batch_key="x",
            overrides=(make_detach_override("cr1.pdf"),),
            history=(),
        ),
    )
    assert _max_group_amount(with_override) > _max_group_amount(auto)
    reset = calculate_payments_with_overrides([inv, credit], override_session=None)
    assert _max_group_amount(reset) == _max_group_amount(auto)


def test_rerun_settlement_after_override():
    inv_a = _base_invoice(invoice_number="INV-A", amount=100.0, source_file="a.pdf")
    inv_b = _base_invoice(invoice_number="INV-B", amount=100.0, source_file="b.pdf")
    credit = _base_invoice(
        amount=50.0,
        type="credit_note",
        invoice_number="CR-1",
        source_file="cr1.pdf",
    )
    auto = calculate_payments([inv_a, inv_b, credit])
    detached = calculate_payments_with_overrides(
        [inv_a, inv_b, credit],
        override_session=OverrideSession(
            batch_key="b",
            overrides=(make_detach_override("cr1.pdf"),),
            history=(),
        ),
    )
    auto_total = sum(g["final_amount_due"] for g in auto.settlement_groups)
    detached_total = sum(g["final_amount_due"] for g in detached.settlement_groups)
    assert detached_total > auto_total
    assert auto_total == Decimal("150.00")
    assert detached_total == Decimal("200.00")


def test_multiple_overrides():
    inv = _base_invoice(invoice_number="INV-1", amount=200.0, source_file="i.pdf")
    cr1 = _base_invoice(
        amount=30.0,
        type="credit_note",
        invoice_number="CR-1",
        source_file="c1.pdf",
    )
    cr2 = _base_invoice(
        amount=20.0,
        type="credit_note",
        invoice_number="CR-2",
        source_file="c2.pdf",
    )
    session = OverrideSession(
        batch_key="m",
        overrides=(
            make_detach_override("c1.pdf"),
            make_reassign_override(
                "c2.pdf",
                (
                    CreditOverrideAllocation(
                        invoice_document_id="i.pdf",
                        invoice_number="INV-1",
                        amount_applied=Decimal("20.00"),
                    ),
                ),
            ),
        ),
        history=(),
    )
    auto = calculate_payments([inv, cr1, cr2])
    result = calculate_payments_with_overrides([inv, cr1, cr2], override_session=session)
    assert _max_group_amount(auto) == Decimal("150.00")
    assert _max_group_amount(result) == Decimal("180.00")


def test_user_override_reassign_group_is_exportable():
    inv_a = _base_invoice(invoice_number="INV-A", amount=300.0, source_file="a.pdf")
    inv_b = _base_invoice(invoice_number="INV-B", amount=200.0, source_file="b.pdf")
    credit = _base_invoice(
        amount=50.0,
        type="credit_note",
        invoice_number="CR-1",
        source_file="cr1.pdf",
    )
    session = OverrideSession(
        batch_key="test",
        overrides=(
            make_reassign_override(
                "cr1.pdf",
                (
                    CreditOverrideAllocation(
                        invoice_document_id="b.pdf",
                        invoice_number="INV-B",
                        amount_applied=Decimal("50.00"),
                    ),
                ),
            ),
        ),
        history=(),
    )
    result = calculate_payments_with_overrides([inv_a, inv_b, credit], override_session=session)
    linked = next(g for g in result.settlement_groups if "INV-B" in str(g.get("description") or ""))
    assert linked.get("exportable") is True
    assert (linked.get("decision") or {}).get("status") == DECISION_INCLUDED
    assert (linked.get("decision") or {}).get("reason_code") == REASON_EXPORT_ALLOWED
    assert exportable_groups(result).groups


def test_orphan_override_ignored():
    inv = _base_invoice()
    credit = _base_invoice(
        amount=50.0,
        type="credit_note",
        invoice_number="CR-1",
        source_file="cr1.pdf",
    )
    session = OverrideSession(
        batch_key="o",
        overrides=(make_detach_override("nonexistent.pdf"),),
        history=(),
    )
    auto = calculate_payments([inv, credit])
    with_orphan = calculate_payments_with_overrides([inv, credit], override_session=session)
    assert with_orphan.settlement_groups[0]["final_amount_due"] == auto.settlement_groups[0]["final_amount_due"]


def test_override_store_session_memory(tmp_path: Path):
    store = CreditOverrideStore(tmp_path / "credit_overrides.json")
    ov = make_detach_override("cr1.pdf")
    session = store.upsert_override("batch1", ov, history_event={"event": "user_detached"})
    assert len(session.overrides) == 1
    loaded = store.load_session("batch1")
    assert loaded is not None
    assert loaded.overrides[0].credit_document_id == "cr1.pdf"
    store.remove_override("batch1", "cr1.pdf")
    assert store.load_session("batch1") is not None
    assert not store.load_session("batch1").overrides
    fresh = CreditOverrideStore(tmp_path / "credit_overrides_fresh.json")
    assert fresh.load_session("batch1") is None


def test_override_session_fingerprint():
    s1 = OverrideSession(batch_key="a", overrides=(make_detach_override("x"),), history=())
    s2 = OverrideSession(batch_key="a", overrides=(), history=())
    assert override_session_fingerprint(s1) != override_session_fingerprint(s2)
