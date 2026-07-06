"""Frozen invoice batch types and freeze guards for batch load pipeline."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class RawInvoiceBatch:
    version: Literal["v0_raw"] = "v0_raw"
    batch_id: str = ""
    invoices: tuple[dict, ...] = ()

    def __post_init__(self) -> None:
        if not self.batch_id:
            object.__setattr__(self, "batch_id", _new_batch_id())
        object.__setattr__(self, "invoices", freeze_invoice_tuple(self.invoices))


@dataclass(frozen=True)
class MatchedInvoiceBatch:
    version: Literal["v1_matched"] = "v1_matched"
    batch_id: str = ""
    parent_batch_id: str = ""
    invoices: tuple[dict, ...] = ()
    match_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.batch_id:
            object.__setattr__(self, "batch_id", _new_batch_id())
        object.__setattr__(self, "invoices", freeze_invoice_tuple(self.invoices))
        object.__setattr__(self, "match_metadata", _freeze_mapping(self.match_metadata))


@dataclass(frozen=True)
class SupplierDbMutation:
    supplier_name: str
    iban: str


@dataclass(frozen=True)
class ResolvedInvoiceBatch:
    version: Literal["v2_resolved"] = "v2_resolved"
    batch_id: str = ""
    parent_batch_id: str = ""
    invoices: tuple[dict, ...] = ()
    iban_resolution_map: Mapping[str, str] = field(default_factory=dict)
    pending_db_mutations: tuple[SupplierDbMutation, ...] = ()

    def __post_init__(self) -> None:
        if not self.batch_id:
            object.__setattr__(self, "batch_id", _new_batch_id())
        object.__setattr__(self, "invoices", freeze_invoice_tuple(self.invoices))
        object.__setattr__(self, "iban_resolution_map", _freeze_mapping(self.iban_resolution_map))


@dataclass(frozen=True)
class IbanAmbiguity:
    ambiguity_index: int
    supplier_name: str
    db_iban: str
    pdf_iban: str
    invoice_count: int
    source_files: tuple[str, ...]


@dataclass(frozen=True)
class IbanAmbiguityDialogSpec:
    ambiguity_index: int
    supplier_name: str
    db_iban: str
    pdf_iban: str
    count: int
    key: str = "dialog.iban.mismatch"
    default_is_yes: bool = False


@dataclass(frozen=True)
class IbanRawUiAnswer:
    ambiguity_index: int
    clicked_yes: bool


@dataclass(frozen=True)
class IbanRawUiAnswers:
    answers: tuple[IbanRawUiAnswer, ...] = ()


@dataclass(frozen=True)
class IbanUserDecision:
    supplier_name: str
    pdf_iban: str
    choice: Literal["keep_db", "use_pdf"]


@dataclass(frozen=True)
class IbanUserDecisions:
    decisions: tuple[IbanUserDecision, ...] = ()


@dataclass(frozen=True)
class PreprocessCheckpoint:
    v0: RawInvoiceBatch
    v1: MatchedInvoiceBatch
    iban_ambiguities: tuple[IbanAmbiguity, ...]
    iban_dialog_specs: tuple[IbanAmbiguityDialogSpec, ...]
    n_dupes: int
    per_source_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class BatchLoadResult:
    v2: ResolvedInvoiceBatch
    engine_result: Any
    n_raw: int
    n_dupes: int


def _new_batch_id() -> str:
    return uuid.uuid4().hex


def _freeze_mapping(value: Mapping[str, Any] | dict[str, Any]) -> Mapping[str, Any]:
    return json.loads(json.dumps(dict(value), default=str))


def freeze_invoice_tuple(invoices: Iterable[dict]) -> tuple[dict, ...]:
    return tuple(deepcopy(inv) for inv in invoices)


def _collect_object_ids(obj: Any, seen: set[int] | None = None) -> set[int]:
    if seen is None:
        seen = set()
    oid = id(obj)
    if oid in seen:
        return seen
    if isinstance(obj, (dict, list, set, tuple)):
        seen.add(oid)
    if isinstance(obj, dict):
        for k, v in obj.items():
            _collect_object_ids(k, seen)
            _collect_object_ids(v, seen)
    elif isinstance(obj, list):
        for item in obj:
            _collect_object_ids(item, seen)
    elif isinstance(obj, tuple):
        for item in obj:
            _collect_object_ids(item, seen)
    elif isinstance(obj, set):
        for item in obj:
            _collect_object_ids(item, seen)
    return seen


def assert_no_shared_refs(a: Any, b: Any) -> None:
    """Raise AssertionError if nested mutable structures are shared between a and b."""
    ids_a = _collect_object_ids(a)
    ids_b = _collect_object_ids(b)
    shared = ids_a & ids_b
    if shared:
        raise AssertionError(f"shared object references detected: {len(shared)} ids")


def snapshot_batch_invoices(batch: RawInvoiceBatch | MatchedInvoiceBatch | ResolvedInvoiceBatch) -> tuple[dict, ...]:
    return freeze_invoice_tuple(batch.invoices)


def assert_frozen_batch_unchanged(
    batch: RawInvoiceBatch | MatchedInvoiceBatch | ResolvedInvoiceBatch,
    snapshot: tuple[dict, ...],
) -> None:
    current = freeze_invoice_tuple(batch.invoices)
    if json.dumps(current, sort_keys=True, default=str) != json.dumps(snapshot, sort_keys=True, default=str):
        raise AssertionError("frozen batch changed after snapshot")
