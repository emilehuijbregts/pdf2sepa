"""Tests for AmountOverrideStore and apply_amount_overrides."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from logic.amount_override_apply import apply_amount_overrides
from logic.amount_override_store import (
    AmountOverride,
    AmountOverrideSession,
    AmountOverrideStore,
    amount_override_session_fingerprint,
)


# ── Store tests ────────────────────────────────────────────────────────────────

def _make_override(doc_id: str, old: str, new: str) -> AmountOverride:
    return AmountOverride(
        document_id=doc_id,
        old_amount=Decimal(old),
        new_amount=Decimal(new),
        reason="test",
        created_at="2026-01-01T00:00:00Z",
    )


def test_load_session_missing_file(tmp_path):
    store = AmountOverrideStore(tmp_path / "overrides.json")
    assert store.load_session("batch1") is None


def test_upsert_and_load(tmp_path):
    store = AmountOverrideStore(tmp_path / "overrides.json")
    override = _make_override("doc-1", "100.00", "90.00")
    store.upsert_override("batch1", override)
    session = store.load_session("batch1")
    assert session is not None
    assert len(session.overrides) == 1
    assert session.overrides[0].document_id == "doc-1"
    assert session.overrides[0].new_amount == Decimal("90.00")


def test_upsert_replaces_existing(tmp_path):
    store = AmountOverrideStore(tmp_path / "overrides.json")
    store.upsert_override("batch1", _make_override("doc-1", "100.00", "90.00"))
    store.upsert_override("batch1", _make_override("doc-1", "100.00", "80.00"))
    session = store.load_session("batch1")
    assert session is not None
    assert len(session.overrides) == 1
    assert session.overrides[0].new_amount == Decimal("80.00")


def test_multiple_docs(tmp_path):
    store = AmountOverrideStore(tmp_path / "overrides.json")
    store.upsert_override("batch1", _make_override("doc-1", "100.00", "90.00"))
    store.upsert_override("batch1", _make_override("doc-2", "200.00", "150.00"))
    session = store.load_session("batch1")
    assert session is not None
    assert len(session.overrides) == 2


def test_remove_override(tmp_path):
    store = AmountOverrideStore(tmp_path / "overrides.json")
    store.upsert_override("batch1", _make_override("doc-1", "100.00", "90.00"))
    store.remove_override("batch1", "doc-1")
    session = store.load_session("batch1")
    assert session is not None
    assert len(session.overrides) == 0


def test_batch_isolation(tmp_path):
    store = AmountOverrideStore(tmp_path / "overrides.json")
    store.upsert_override("batch-a", _make_override("doc-1", "100.00", "90.00"))
    assert store.load_session("batch-b") is None


def test_history_event_recorded(tmp_path):
    store = AmountOverrideStore(tmp_path / "overrides.json")
    store.upsert_override(
        "batch1",
        _make_override("doc-1", "100.00", "90.00"),
        history_event={"event": "user_adjusted_amount", "document_id": "doc-1"},
    )
    session = store.load_session("batch1")
    assert session is not None
    assert any(h.get("event") == "user_adjusted_amount" for h in session.history)


def test_fingerprint_changes_with_overrides():
    empty = amount_override_session_fingerprint(None)
    session = AmountOverrideSession(
        batch_key="b",
        overrides=(_make_override("doc-1", "100", "90"),),
        history=(),
    )
    nonempty = amount_override_session_fingerprint(session)
    assert empty != nonempty


# ── Apply tests ────────────────────────────────────────────────────────────────

def _inv(source_file: str, amount_dec: str) -> dict:
    return {
        "source_file": source_file,
        "invoice_number": "INV-" + source_file,
        "amount_dec": Decimal(amount_dec),
        "amount": amount_dec,
        "type": "invoice",
    }


def test_apply_no_session_returns_original():
    matched = [_inv("a.pdf", "100.00")]
    result = apply_amount_overrides(matched, None)
    assert result is matched  # same object, no copy


def test_apply_empty_session_returns_original():
    matched = [_inv("a.pdf", "100.00")]
    session = AmountOverrideSession(batch_key="b", overrides=(), history=())
    result = apply_amount_overrides(matched, session)
    assert result is matched


def test_apply_patches_matching_doc():
    matched = [_inv("a.pdf", "100.00")]
    override = _make_override("a.pdf", "100.00", "90.00")  # doc_id = source_file
    session = AmountOverrideSession(batch_key="b", overrides=(override,), history=())
    result = apply_amount_overrides(matched, session)
    assert result is not matched
    assert result[0]["amount_dec"] == Decimal("90.00")
    assert result[0]["amount"] == "90.00"


def test_apply_does_not_mutate_original():
    inv = _inv("a.pdf", "100.00")
    matched = [inv]
    override = _make_override("a.pdf", "100.00", "90.00")
    session = AmountOverrideSession(batch_key="b", overrides=(override,), history=())
    apply_amount_overrides(matched, session)
    # original untouched
    assert matched[0]["amount_dec"] == Decimal("100.00")


def test_apply_leaves_unmatched_docs_unchanged():
    matched = [_inv("a.pdf", "100.00"), _inv("b.pdf", "200.00")]
    override = _make_override("a.pdf", "100.00", "50.00")
    session = AmountOverrideSession(batch_key="b", overrides=(override,), history=())
    result = apply_amount_overrides(matched, session)
    assert result[0]["amount_dec"] == Decimal("50.00")
    assert result[1] is matched[1]  # same dict object — not copied
