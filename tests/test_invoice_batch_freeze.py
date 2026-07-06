"""Freeze guards for invoice batch versioning."""

from __future__ import annotations

import copy

import pytest

from logic.batch_load_types import (
    MatchedInvoiceBatch,
    RawInvoiceBatch,
    assert_frozen_batch_unchanged,
    assert_no_shared_refs,
    freeze_invoice_tuple,
    snapshot_batch_invoices,
)


def test_freeze_invoice_tuple_deepcopy() -> None:
    inv = {"a": 1, "items": [1, 2]}
    frozen = freeze_invoice_tuple([inv])
    inv["a"] = 99
    inv["items"].append(3)
    assert frozen[0]["a"] == 1
    assert frozen[0]["items"] == [1, 2]


def test_assert_no_shared_refs_nested_list() -> None:
    inner = [1, 2]
    a = {"items": inner}
    b = {"items": inner}
    try:
        assert_no_shared_refs(a, b)
        raised = False
    except AssertionError:
        raised = True
    assert raised


def test_mutation_in_v1_detected_by_snapshot_guard() -> None:
    inv = {"items": [1]}
    v1 = RawInvoiceBatch(batch_id="v0", invoices=freeze_invoice_tuple([inv]))
    snap = snapshot_batch_invoices(v1)
    list(v1.invoices)[0]["items"].append(2)
    with pytest.raises(AssertionError):
        assert_frozen_batch_unchanged(v1, snap)


def test_v1_v2_no_shared_refs_after_resolve_shape() -> None:
    v1 = MatchedInvoiceBatch(
        batch_id="v1",
        parent_batch_id="v0",
        invoices=freeze_invoice_tuple([{"x": [1]}]),
    )
    v2_inv = copy.deepcopy(list(v1.invoices))
    v2_inv[0]["x"].append(99)
    assert_no_shared_refs(v1.invoices, freeze_invoice_tuple(v2_inv))
