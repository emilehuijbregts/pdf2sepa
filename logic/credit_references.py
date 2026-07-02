"""Extract parent invoice references from credit-note document text."""

from __future__ import annotations

import re

# Reuse parent-ref context marker from field_candidates semantics.
_PARENT_INVOICE_REF_CTX_RE = re.compile(
    r"(?i)\b(?:fact\.?\s*nr\.?|vereffening\s+met\s+factuurnr|"
    r"betr\.?:?\s*(?:onze\s+)?factuur|onze\s+factuur|"
    r"referentie\s+factuur|credit\s+(?:naar|voor|van)\s+factuur)\b"
)

_REF_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)\b(?:fact\.?\s*nr\.?|factuurnr\.?|factuurnummer|invoice\s*(?:no\.?|number)?)"
        r"\s*[:\s]+([A-Z0-9][A-Z0-9./+\-_]{2,})"
    ),
    re.compile(
        r"(?i)\b(?:betr\.?:?\s*)?(?:onze\s+)?factuur\s+([A-Z0-9][A-Z0-9./+\-_]{2,})"
    ),
    re.compile(
        r"(?i)\bvereffening\s+met\s+factuurnr\.?\s*([A-Z0-9][A-Z0-9./+\-_]{2,})"
    ),
    re.compile(
        r"(?i)\bcredit\s+(?:naar|voor|van)\s+factuur\s+([A-Z0-9][A-Z0-9./+\-_]{2,})"
    ),
    re.compile(
        r"(?i)\breferentie\s+factuur\s*[:\s]+([A-Z0-9][A-Z0-9./+\-_]{2,})"
    ),
)

# Credit document's own number prefixes — never treat as parent reference.
_OWN_CREDIT_PREFIX_RE = re.compile(r"(?i)^(?:VCR|CN|CREN|CR|C\d)")


def _normalize_ref(value: str) -> str:
    return re.sub(r"\++$", "", str(value or "").strip())


def _is_plausible_parent_ref(value: str, *, line: str) -> bool:
    val = _normalize_ref(value)
    if not val or len(val) < 3:
        return False
    if not re.search(r"\d", val):
        return False
    if _OWN_CREDIT_PREFIX_RE.match(val):
        return False
    # Must appear in a parent-invoice context line.
    if not _PARENT_INVOICE_REF_CTX_RE.search(line):
        return False
    return True


def extract_referenced_invoice_numbers(text: str) -> list[str]:
    """Return deduplicated parent invoice numbers referenced on a credit document."""
    body = text or ""
    seen: set[str] = set()
    out: list[str] = []

    for line in body.splitlines():
        for pat in _REF_PATTERNS:
            for m in pat.finditer(line):
                raw = _normalize_ref(m.group(1))
                if not _is_plausible_parent_ref(raw, line=line):
                    continue
                key = raw.upper()
                if key in seen:
                    continue
                seen.add(key)
                out.append(raw)
    return out
