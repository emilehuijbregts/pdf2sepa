"""Resolve invoice vs credit_note using user override, profile fit, and classifier."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from logic.credit_classifier import CreditDetectionResult, classify_credit_document
from parser.profile_extractor import extract_with_profile, values_match
from parser.profile_strategy_engine import is_valid_field_spec

DocumentType = Literal["invoice", "credit_note"]
ResolutionSource = Literal["user_override", "profile_fit", "classifier", "ambiguous"]

_INVOICE_TYPE: DocumentType = "invoice"
_CREDIT_TYPE: DocumentType = "credit_note"

_INVOICE_PROFILE_FIELDS: tuple[str, ...] = ("amount", "invoice_number", "customer_number")
_CREDIT_PROFILE_FIELDS: tuple[str, ...] = ("amount", "credit_number")

_PROFILE_FIT_WIN_SCORE = 1.0
_PROFILE_FIT_LOSE_SCORE = 0.5


@dataclass(frozen=True)
class DocumentTypeResolution:
    document_type: DocumentType
    source: ResolutionSource
    invoice_profile_score: float
    credit_profile_score: float
    needs_review: bool
    reason: str
    classifier_is_credit: bool = False
    classifier_confidence: int = 0


def resolution_to_dict(resolution: DocumentTypeResolution) -> dict[str, Any]:
    return {
        "document_type": resolution.document_type,
        "source": resolution.source,
        "invoice_profile_score": resolution.invoice_profile_score,
        "credit_profile_score": resolution.credit_profile_score,
        "needs_review": resolution.needs_review,
        "reason": resolution.reason,
        "classifier_is_credit": resolution.classifier_is_credit,
        "classifier_confidence": resolution.classifier_confidence,
    }


def _field_spec(profile: dict[str, Any], field: str) -> dict[str, Any] | None:
    spec = profile.get(field)
    if not isinstance(spec, dict):
        return None
    validate_as = "invoice_number" if field == "credit_number" else field
    if not is_valid_field_spec(spec, validate_as):  # type: ignore[arg-type]
        return None
    return spec


def _field_matches_profile(
    raw_text: str,
    profile: dict[str, Any],
    field: str,
) -> bool | None:
    """Return True/False when field spec exists; None when field absent from profile."""
    spec = _field_spec(profile, field)
    if spec is None:
        return None
    extract_key = "invoice_number" if field == "credit_number" else field
    extracted = extract_with_profile(raw_text, {extract_key: spec})
    confirmed = spec.get("confirmed_value")
    if confirmed is None:
        return None
    val = extracted.get(extract_key)
    return values_match(extract_key, val, confirmed)  # type: ignore[arg-type]


def score_profile_fit(raw_text: str, profile: dict[str, Any] | None, field_keys: tuple[str, ...]) -> float:
    """Return matched_fields / total_profile_fields in [0.0, 1.0]; 0 when no profile fields."""
    if not isinstance(profile, dict) or not profile:
        return 0.0
    total = 0
    matched = 0
    for field in field_keys:
        result = _field_matches_profile(raw_text, profile, field)
        if result is None:
            continue
        total += 1
        if result:
            matched += 1
    if total == 0:
        return 0.0
    return matched / total


def resolve_document_type(
    inv: dict[str, Any],
    *,
    user_override: DocumentType | None = None,
    detection: CreditDetectionResult | None = None,
) -> DocumentTypeResolution:
    """Pick document type for one matched invoice dict."""
    text = str(inv.get("raw_text") or "")
    if detection is None:
        detection = classify_credit_document(
            text,
            metadata={"type": inv.get("type"), "amount": inv.get("amount")},
        )

    invoice_profile = inv.get("extraction_profile")
    credit_profile = inv.get("credit_profile")
    invoice_score = score_profile_fit(
        text,
        invoice_profile if isinstance(invoice_profile, dict) else None,
        _INVOICE_PROFILE_FIELDS,
    )
    credit_score = score_profile_fit(
        text,
        credit_profile if isinstance(credit_profile, dict) else None,
        _CREDIT_PROFILE_FIELDS,
    )

    parser_type = str(inv.get("type") or "").strip()
    classifier_type: DocumentType = _CREDIT_TYPE if detection.is_credit else _INVOICE_TYPE
    parser_is_credit = parser_type == _CREDIT_TYPE
    classifier_conflict = parser_is_credit != detection.is_credit

    if user_override in (_INVOICE_TYPE, _CREDIT_TYPE):
        return DocumentTypeResolution(
            document_type=user_override,
            source="user_override",
            invoice_profile_score=invoice_score,
            credit_profile_score=credit_score,
            needs_review=False,
            reason="user_document_type_override",
            classifier_is_credit=detection.is_credit,
            classifier_confidence=detection.confidence,
        )

    if invoice_score >= _PROFILE_FIT_WIN_SCORE and credit_score < _PROFILE_FIT_LOSE_SCORE:
        return DocumentTypeResolution(
            document_type=_INVOICE_TYPE,
            source="profile_fit",
            invoice_profile_score=invoice_score,
            credit_profile_score=credit_score,
            needs_review=False,
            reason="invoice_profile_fit",
            classifier_is_credit=detection.is_credit,
            classifier_confidence=detection.confidence,
        )

    if credit_score >= _PROFILE_FIT_WIN_SCORE and invoice_score < _PROFILE_FIT_LOSE_SCORE:
        return DocumentTypeResolution(
            document_type=_CREDIT_TYPE,
            source="profile_fit",
            invoice_profile_score=invoice_score,
            credit_profile_score=credit_score,
            needs_review=False,
            reason="credit_profile_fit",
            classifier_is_credit=detection.is_credit,
            classifier_confidence=detection.confidence,
        )

    if invoice_score >= _PROFILE_FIT_WIN_SCORE and credit_score >= _PROFILE_FIT_WIN_SCORE:
        return DocumentTypeResolution(
            document_type=classifier_type,
            source="ambiguous",
            invoice_profile_score=invoice_score,
            credit_profile_score=credit_score,
            needs_review=True,
            reason="both_profiles_fit",
            classifier_is_credit=detection.is_credit,
            classifier_confidence=detection.confidence,
        )

    needs_review = classifier_conflict
    return DocumentTypeResolution(
        document_type=classifier_type,
        source="classifier",
        invoice_profile_score=invoice_score,
        credit_profile_score=credit_score,
        needs_review=needs_review,
        reason="classifier_credit_conflict" if classifier_conflict else detection.reason,
        classifier_is_credit=detection.is_credit,
        classifier_confidence=detection.confidence,
    )


def apply_document_type_resolution(inv: dict[str, Any], resolution: DocumentTypeResolution) -> dict[str, Any]:
    """Attach resolved type metadata to an invoice dict (mutates copy)."""
    out = dict(inv)
    out["type"] = resolution.document_type
    out["document_type_resolution"] = resolution_to_dict(resolution)
    return out
