"""Pure batch load pipeline: preprocess and resolve+engine stages."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

from logic.amount_override_apply import apply_amount_overrides
from logic.amount_override_store import AmountOverrideSession, amount_override_session_fingerprint
from logic.document_type_apply import resolve_document_types
from logic.document_type_override_store import (
    DocumentTypeOverrideSession,
    document_type_override_session_fingerprint,
)
from logic.batch_load_types import (
    BatchLoadResult,
    MatchedInvoiceBatch,
    PreprocessCheckpoint,
    RawInvoiceBatch,
    freeze_invoice_tuple,
    IbanRawUiAnswers,
)
from logic.credit_enrichment import enrich_credit_documents
from logic.credit_override_store import CreditOverrideStore, OverrideSession
from logic.credit_settlement import document_id
from logic.engine_cache import SettlementEngineCache
from logic.engine_result import EngineResult
from logic.iban_resolution_engine import postprocess_match_stage, resolve_iban_context
from logic.iban_ui_mapping import map_raw_ui_answers_to_decisions
from logic.invoice_folder_loader import load_invoice_from_pdf_path, strip_raw_text_from_invoices
from logic.invoice_parse_cache import list_invoice_pdf_paths
from logic.payment_decisions import stable_hash
from logic.payment_engine import batch_requires_settlement, calculate_payments_with_overrides
from parser.supplier_db import SupplierDBSnapshot
from parser.supplier_matcher import match_suppliers

ProgressCallback = Callable[[int, int, str, str], None]


@dataclass(frozen=True)
class BatchLoadParams:
    folder: Path
    parse_pdfs: bool
    debtor_iban: str | None
    debtor_kvk: str | None
    debtor_vat: str | None
    debtor_name: str | None
    supplier_db_snapshot: SupplierDBSnapshot
    session_date: date
    batch_key: str
    amount_override_session: AmountOverrideSession | None
    document_type_override_session: DocumentTypeOverrideSession | None
    override_store: CreditOverrideStore
    warm_invoices: tuple[dict, ...] | None = None
    cancel_check: Callable[[], bool] | None = None


def _load_override_session(params: BatchLoadParams, invoices: tuple[dict, ...]) -> OverrideSession | None:
    credit_ids: set[str] = set()
    for inv in invoices:
        if str(inv.get("type") or "") == "credit_note":
            credit_ids.add(document_id({"raw": inv}))
    if not credit_ids:
        return None
    store = params.override_store
    return store.load_applicable_session(params.batch_key, credit_ids)


def deduplicate_invoices(invoices: list[dict]) -> tuple[list[dict], int]:
    """Remove duplicate invoices based on filename or invoice_number+supplier_hint."""
    seen_files: set[str] = set()
    seen_keys: set[str] = set()
    unique: list[dict] = []
    skipped = 0

    for inv in invoices:
        sf = str(inv.get("source_file") or "").strip()
        if sf:
            basename = Path(sf).name
            if basename in seen_files:
                skipped += 1
                continue
            seen_files.add(basename)

        inv_no = str(inv.get("invoice_number") or "").strip()
        hint = str(inv.get("supplier_hint") or "").strip().lower()
        if inv_no and hint:
            key = f"{hint}|{inv_no}"
            if key in seen_keys:
                skipped += 1
                continue
            seen_keys.add(key)

        unique.append(inv)

    return unique, skipped


def _emit(progress_cb: ProgressCallback | None, done: int, total: int, filename: str, stage: str) -> None:
    if progress_cb is not None:
        progress_cb(done, total, filename, stage)


def _load_cold_invoices(params: BatchLoadParams, progress_cb: ProgressCallback | None) -> list[dict]:
    folder = params.folder.resolve()
    paths = list_invoice_pdf_paths(folder)
    total = len(paths)
    _emit(progress_cb, 0, total, "", "listing_pdfs")
    invoices: list[dict] = []
    for index, path in enumerate(paths):
        if params.cancel_check and params.cancel_check():
            break
        _emit(progress_cb, index, total, path.name, "parsing_pdf")
        invoices.append(
            load_invoice_from_pdf_path(
                path,
                debtor_iban=params.debtor_iban,
                debtor_kvk=params.debtor_kvk,
                debtor_vat=params.debtor_vat,
                debtor_name=params.debtor_name,
            )
        )
    _emit(progress_cb, total, total, "", "parsing_pdf")
    return invoices


def apply_match_postprocess(
    matched_list: list[dict],
    *,
    document_type_override_session: DocumentTypeOverrideSession | None = None,
    strip_raw_text: bool = True,
) -> list[dict]:
    """Resolve document types and optionally enrich credits after supplier matching."""
    resolved = resolve_document_types(matched_list, document_type_override_session)
    if batch_requires_settlement(resolved):
        resolved = enrich_credit_documents(resolved)
    if strip_raw_text:
        strip_raw_text_from_invoices(resolved)
    return resolved


def run_preprocess(params: BatchLoadParams, progress_cb: ProgressCallback | None = None) -> PreprocessCheckpoint:
    """Stage parse → match → postprocess; produces v0/v1 checkpoint."""
    _emit(progress_cb, 0, 1, "", "deduplicating")
    if params.parse_pdfs:
        raw_list = _load_cold_invoices(params, progress_cb)
    else:
        if not params.warm_invoices:
            raise ValueError("warm_invoices required when parse_pdfs=False")
        raw_list = list(params.warm_invoices)

    unique, n_dupes = deduplicate_invoices(raw_list)
    batch_id = stable_hash(
        {
            "folder": str(params.folder.resolve()),
            "batch_key": params.batch_key,
            "n_invoices": len(unique),
        }
    )
    v0 = RawInvoiceBatch(batch_id=batch_id, invoices=freeze_invoice_tuple(unique))

    _emit(progress_cb, 0, 1, "", "matching_suppliers")
    db = params.supplier_db_snapshot.matcher_db()
    matched_list = match_suppliers(list(v0.invoices), db)
    _emit(progress_cb, 0, 1, "", "resolving_document_types")
    matched_list = apply_match_postprocess(
        matched_list,
        document_type_override_session=params.document_type_override_session,
        strip_raw_text=True,
    )

    v1 = MatchedInvoiceBatch(
        batch_id=stable_hash({"parent": v0.batch_id, "stage": "v1"}),
        parent_batch_id=v0.batch_id,
        invoices=freeze_invoice_tuple(matched_list),
        match_metadata={"n_dupes": n_dupes},
    )
    _, ambiguities, dialog_specs = postprocess_match_stage(v1)

    per_source_counts = ((f"Map: {params.folder.name}", len(v0.invoices)),)
    return PreprocessCheckpoint(
        v0=v0,
        v1=v1,
        iban_ambiguities=ambiguities,
        iban_dialog_specs=dialog_specs,
        n_dupes=n_dupes,
        per_source_counts=per_source_counts,
    )


def run_engine(params: BatchLoadParams, v2_invoices: tuple[dict, ...]) -> EngineResult:
    """Payment engine on v2 only (fresh cache, no UI coupling)."""
    matched = list(v2_invoices)
    amount_session = params.amount_override_session
    override_session = _load_override_session(params, v2_invoices)
    effective_matched = apply_amount_overrides(matched, amount_session)
    engine_cache = SettlementEngineCache()
    return engine_cache.get_or_compute(
        effective_matched,
        override_session,
        lambda: calculate_payments_with_overrides(
            effective_matched,
            override_session=override_session,
            session_date=params.session_date,
        ),
        amount_override_fingerprint=amount_override_session_fingerprint(amount_session),
        document_type_override_fingerprint=document_type_override_session_fingerprint(
            params.document_type_override_session
        ),
    )


def run_resolve_iban_and_engine(
    params: BatchLoadParams,
    checkpoint: PreprocessCheckpoint,
    raw_answers: IbanRawUiAnswers,
    progress_cb: ProgressCallback | None = None,
) -> BatchLoadResult:
    """Domain orchestration: map → resolve → engine."""
    _emit(progress_cb, 0, 1, "", "computing_payments")
    decisions = map_raw_ui_answers_to_decisions(checkpoint.iban_ambiguities, raw_answers)
    v2 = resolve_iban_context(checkpoint.v1, decisions, params.supplier_db_snapshot)
    engine_result = run_engine(params, v2.invoices)
    return BatchLoadResult(
        v2=v2,
        engine_result=engine_result,
        n_raw=len(checkpoint.v0.invoices),
        n_dupes=checkpoint.n_dupes,
    )
