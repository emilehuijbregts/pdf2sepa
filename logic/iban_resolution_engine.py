"""Pure IBAN ambiguity extraction and resolution for batch load."""

from __future__ import annotations

from typing import Any

from logic.batch_load_types import (
    IbanAmbiguity,
    IbanAmbiguityDialogSpec,
    IbanUserDecisions,
    MatchedInvoiceBatch,
    ResolvedInvoiceBatch,
    SupplierDbMutation,
    freeze_invoice_tuple,
)
from logic.payment_decisions import stable_hash
from logic.validation import clean_iban
from parser.field_adapters import field_result_from_legacy_dict, field_result_to_legacy_dict
from parser.field_model import FieldCandidate
from parser.field_resolver import resolve_field
from parser.resolved_field_apply import apply_resolved_field_result
from parser.supplier_db import SupplierDB, SupplierDBSnapshot


def extract_iban_ambiguities(v1: MatchedInvoiceBatch) -> tuple[IbanAmbiguity, ...]:
    """Scan matched invoices for IBAN mismatch flags; return ambiguity DTOs."""
    MismatchKey = tuple[str, str]
    groups: dict[MismatchKey, dict[str, Any]] = {}

    for inv in v1.invoices:
        if not inv.get("iban_mismatch"):
            continue
        sup = str(inv.get("supplier_name") or "")
        pdf_iban = str(inv.get("pdf_iban") or "")
        if not sup or not pdf_iban:
            continue
        key: MismatchKey = (sup, pdf_iban)
        if key not in groups:
            groups[key] = {
                "supplier_name": sup,
                "db_iban": str(inv.get("iban") or ""),
                "pdf_iban": pdf_iban,
                "source_files": [],
            }
        sf = str(inv.get("source_file") or "").strip()
        if sf:
            groups[key]["source_files"].append(sf)

    out: list[IbanAmbiguity] = []
    for index, info in enumerate(groups.values()):
        out.append(
            IbanAmbiguity(
                ambiguity_index=index,
                supplier_name=str(info["supplier_name"]),
                db_iban=str(info["db_iban"]),
                pdf_iban=str(info["pdf_iban"]),
                invoice_count=len(info["source_files"]) or 1,
                source_files=tuple(info["source_files"]),
            )
        )
    return tuple(out)


def build_iban_dialog_specs(ambiguities: tuple[IbanAmbiguity, ...]) -> tuple[IbanAmbiguityDialogSpec, ...]:
    """Build structured dialog specs for UI render-only loop."""
    specs: list[IbanAmbiguityDialogSpec] = []
    for amb in ambiguities:
        specs.append(
            IbanAmbiguityDialogSpec(
                ambiguity_index=amb.ambiguity_index,
                supplier_name=amb.supplier_name,
                db_iban=amb.db_iban,
                pdf_iban=amb.pdf_iban,
                count=amb.invoice_count,
            )
        )
    return tuple(specs)


def postprocess_match_stage(
    v1: MatchedInvoiceBatch,
) -> tuple[MatchedInvoiceBatch, tuple[IbanAmbiguity, ...], tuple[IbanAmbiguityDialogSpec, ...]]:
    """Post-match stage: extract ambiguities and dialog specs from v1."""
    ambiguities = extract_iban_ambiguities(v1)
    dialog_specs = build_iban_dialog_specs(ambiguities)
    return v1, ambiguities, dialog_specs


def resolve_iban_context(
    v1: MatchedInvoiceBatch,
    decisions: IbanUserDecisions,
    snapshot: SupplierDBSnapshot,
) -> ResolvedInvoiceBatch:
    """Apply IBAN user decisions to produce v2 (pure, no Qt, no live DB writes)."""
    _ = snapshot  # snapshot is truth anchor; resolve uses decisions + v1 only
    invoices = list(freeze_invoice_tuple(v1.invoices))
    decision_by_key = {(d.supplier_name, d.pdf_iban): d for d in decisions.decisions}
    resolution_map: dict[str, str] = {}
    pending_mutations: list[SupplierDbMutation] = []

    # Group invoices by mismatch key (same logic as legacy _resolve_iban_mismatches)
    MismatchKey = tuple[str, str]
    groups: dict[MismatchKey, list[dict]] = {}
    for inv in invoices:
        if not inv.get("iban_mismatch"):
            continue
        sup = str(inv.get("supplier_name") or "")
        pdf_iban = str(inv.get("pdf_iban") or "")
        if not sup or not pdf_iban:
            continue
        key: MismatchKey = (sup, pdf_iban)
        groups.setdefault(key, []).append(inv)

    for key, invs in groups.items():
        decision = decision_by_key.get(key)
        choice = decision.choice if decision else "keep_db"
        resolution_map[f"{key[0]}|{key[1]}"] = choice
        if choice == "use_pdf":
            pending_mutations.append(SupplierDbMutation(supplier_name=key[0], iban=key[1]))
            for inv in invs:
                generic = field_result_from_legacy_dict(
                    inv.get("iban_result") if isinstance(inv.get("iban_result"), dict) else {},
                    field_id="iban",
                )
                generic.user_overridden = True
                user_pick = FieldCandidate(
                    value=clean_iban(key[1]),
                    source="manual",
                    confidence=100,
                    context="iban_mismatch_dialog",
                )
                resolved = resolve_field("iban", generic, [], user_pick=user_pick)
                resolved.resolver_finalized = True
                apply_resolved_field_result(inv, "iban", field_result_to_legacy_dict(resolved))

        for inv in invs:
            inv.pop("iban_mismatch", None)

    return ResolvedInvoiceBatch(
        batch_id=stable_hash({"parent": v1.batch_id, "stage": "v2_resolved"}),
        parent_batch_id=v1.batch_id,
        invoices=tuple(invoices),
        iban_resolution_map=resolution_map,
        pending_db_mutations=tuple(pending_mutations),
    )


def apply_supplier_db_mutations(db: SupplierDB, mutations: tuple[SupplierDbMutation, ...]) -> None:
    """Apply deferred DB mutations after UI batch commit (persistence only)."""
    for mut in mutations:
        db.update_supplier(mut.supplier_name, iban=mut.iban)
