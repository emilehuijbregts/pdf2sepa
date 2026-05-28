"""Universeel veld-kandidaatmodel (adapterlaag boven AmountResult / IdentFieldResult)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

FieldId = Literal["amount", "invoice_number", "customer_number", "iban"]
FieldStatus = Literal["confirmed", "tentative", "ambiguous", "failed"]

DecisionTraceEntry = dict[str, Any]

_VALID_STATUSES = frozenset({"confirmed", "tentative", "ambiguous", "failed"})

_RESULT_KEY_BY_FIELD: dict[FieldId, str] = {
    "amount": "amount_result",
    "invoice_number": "invoice_number_result",
    "customer_number": "customer_number_result",
    "iban": "iban_result",
}

_LEGACY_VALUE_KEY_BY_FIELD: dict[FieldId, str] = {
    "amount": "amount",
    "invoice_number": "invoice_number",
    "customer_number": "customer_number",
    "iban": "iban",
}

ALL_FIELD_IDS: tuple[FieldId, ...] = (
    "amount",
    "invoice_number",
    "customer_number",
    "iban",
)

CORE_PROFILE_FIELD_KEYS: tuple[str, ...] = (
    "amount",
    "invoice_number",
    "customer_number",
)


def normalize_field_status(raw: str | None) -> FieldStatus:
    s = str(raw or "").strip().lower()
    if s in _VALID_STATUSES:
        return s  # type: ignore[return-value]
    return "failed"


@dataclass
class FieldCandidate:
    value: Any
    source: str
    confidence: int
    context: str = ""
    label: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "value": self.value,
            "source": self.source,
            "confidence": self.confidence,
            "context": self.context,
        }
        if self.label:
            d["label"] = self.label
        if self.meta:
            for k, v in self.meta.items():
                if k not in d:
                    d[k] = v
        return d


@dataclass
class FieldResult:
    field_id: FieldId
    candidates: list[FieldCandidate] = field(default_factory=list)
    selected_value: Any | None = None
    confidence: int = 0
    source: str = "UNKNOWN"
    status: FieldStatus = "failed"
    user_selected: bool = False
    context: str | None = None
    value_display: str | None = None
    user_overridden: bool = False
    previous_value: Any | None = None
    decision_trace: list[DecisionTraceEntry] = field(default_factory=list)
    override_reason: str = ""
    resolver_finalized: bool = False

    def __post_init__(self) -> None:
        self.status = normalize_field_status(self.status)

    @property
    def is_pickable(self) -> bool:
        if not self.candidates:
            return False
        st = self.status
        if st == "ambiguous":
            return True
        if st in ("tentative", "failed") and len(self.candidates) >= 2:
            return True
        return len(self.candidates) >= 2

    def resolved_context(self, *, target_value: Any | None = None) -> str | None:
        if self.context:
            return self.context
        target = target_value if target_value is not None else self.selected_value
        if target is None:
            return None
        if self.field_id == "amount":
            return _resolved_context_amount(self, target)
        return _resolved_context_string(self, target)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "field_id": self.field_id,
            "candidates": [c.to_dict() for c in self.candidates],
            "selected_value": self.selected_value,
            "confidence": self.confidence,
            "source": self.source,
            "status": self.status,
        }
        if self.user_selected:
            d["user_selected"] = True
        if self.user_overridden:
            d["user_overridden"] = True
        if self.previous_value is not None:
            d["previous_value"] = self.previous_value
        if self.decision_trace:
            d["decision_trace"] = list(self.decision_trace)
        if self.override_reason:
            d["override_reason"] = self.override_reason
        if self.context:
            d["context"] = self.context
        if self.value_display:
            d["value_display"] = self.value_display
        if self.resolver_finalized:
            d["resolver_finalized"] = True
        return d


def is_resolver_final_field_result(fr: FieldResult) -> bool:
    """True iff suitable for profile learning (post-resolve or user-locked)."""
    if fr.selected_value is None:
        return False
    if fr.user_overridden:
        return True
    if fr.decision_trace:
        return True
    if fr.resolver_finalized:
        return True
    return False


def normalize_field_value(field_id: FieldId, value: Any) -> Any | None:
    """Normalize a resolved value for profile specs (single place for field-specific rules)."""
    if value is None:
        return None
    if field_id == "amount":
        try:
            from logic.payment_amounts import amount_to_decimal

            dec = amount_to_decimal(value)
            if dec > Decimal("0.00"):
                return dec
        except (TypeError, ValueError, InvalidOperation):
            return None
        return None
    if field_id == "iban":
        from logic.validation import clean_iban

        v = clean_iban(str(value))
        return v or None
    s = str(value).strip()
    return s or None


def field_result_from_result_dict(
    data: dict[str, Any] | None,
    *,
    field_id: FieldId,
) -> FieldResult:
    from parser.field_adapters import field_result_from_legacy_dict

    fr = field_result_from_legacy_dict(data, field_id=field_id)
    if isinstance(data, dict) and data.get("resolver_finalized") is True:
        fr.resolver_finalized = True
    return fr


def _resolved_context_string(fr: FieldResult, target: Any) -> str | None:
    target_s = str(target or "").strip()
    if not target_s:
        return None
    best_ctx: str | None = None
    best_key: tuple[int, int] = (-1, -1)
    user_picked = fr.user_selected
    for c in fr.candidates:
        if str(c.value or "").strip() != target_s:
            continue
        ctx = str(c.context or "").strip()
        if not ctx:
            continue
        conf = int(c.confidence or 0)
        prio = 2 if user_picked else 1
        key = (prio, conf)
        if key > best_key:
            best_key = key
            best_ctx = ctx
    return best_ctx


def _resolved_context_amount(fr: FieldResult, target: Any) -> str | None:
    try:
        from logic.payment_amounts import amount_to_decimal

        target_dec = amount_to_decimal(str(target))
    except (TypeError, ValueError, InvalidOperation):
        return None
    best_ctx: str | None = None
    best_key: tuple[int, int] = (-1, -1)
    user_picked = fr.user_selected
    for c in fr.candidates:
        raw_v = c.value
        if raw_v is None:
            continue
        try:
            from logic.payment_amounts import amount_to_decimal

            if amount_to_decimal(str(raw_v)) != target_dec:
                continue
        except (TypeError, ValueError, InvalidOperation):
            continue
        ctx = str(c.context or "").strip()
        if not ctx:
            continue
        conf = int(c.confidence or 0)
        prio = 0
        src = str(c.source or "").lower()
        if user_picked and src in ("manual", "user", "picked"):
            prio = 2
        elif user_picked:
            prio = 1
        key = (prio, conf)
        if key > best_key:
            best_key = key
            best_ctx = ctx
    return best_ctx


@dataclass
class CandidateCollection:
    fields: dict[FieldId, FieldResult] = field(default_factory=dict)

    def get(self, field_id: FieldId) -> FieldResult | None:
        return self.fields.get(field_id)

    @classmethod
    def from_invoice_dict(cls, inv: dict[str, Any]) -> CandidateCollection:
        from parser.field_adapters import (
            field_result_from_amount,
            field_result_from_iban,
            field_result_from_ident,
        )

        fields: dict[FieldId, FieldResult] = {}
        ar = inv.get("amount_result")
        if ar is not None:
            fields["amount"] = field_result_from_amount(ar)
        ir = inv.get("invoice_number_result")
        if ir is not None:
            fields["invoice_number"] = field_result_from_ident(
                ir, field_id="invoice_number"
            )
        cr = inv.get("customer_number_result")
        if cr is not None:
            fields["customer_number"] = field_result_from_ident(
                cr, field_id="customer_number"
            )
        br = inv.get("iban_result")
        if br is not None:
            fields["iban"] = field_result_from_iban(br)
        return cls(fields=fields)

    def patch_invoice_dict(self, inv: dict[str, Any]) -> dict[str, Any]:
        from logic.validation import clean_iban
        from parser.field_adapters import field_result_to_legacy_dict

        out = dict(inv)
        for field_id, fr in self.fields.items():
            key = _RESULT_KEY_BY_FIELD.get(field_id)
            if key:
                out[key] = field_result_to_legacy_dict(fr)
            legacy_key = _LEGACY_VALUE_KEY_BY_FIELD.get(field_id)
            if legacy_key and fr.selected_value is not None:
                if field_id == "iban":
                    out[legacy_key] = clean_iban(str(fr.selected_value))
                elif field_id != "amount":
                    out[legacy_key] = str(fr.selected_value).strip()
        return out
