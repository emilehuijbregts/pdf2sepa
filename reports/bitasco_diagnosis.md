# Bitasco Diagnosis

## Current Output (after fix)

| Field | Value |
|-------|-------|
| supplier | Bitasco Trading B.V |
| invoice_number | SI26-007024 |
| amount | 4287.89 |
| amount_status | **confirmed** |
| match_status | confirmed |
| decision_status | **included** |
| decision_reason | included_validated |

## Expected Output

| Field | Expected |
|-------|----------|
| amount | 4287.89 |
| amount_status | confirmed |
| decision_status | included |

## Root Cause

The VAT line `744.18` (`21 % BTW over € 3.543,71`) was misclassified as `incl` instead of `vat`, creating a false second incl group that forced amount selection into `ambiguous` → `tentative`, which the payment engine correctly downgraded to `needs_review`.

## Trigger Location

- **Upstream:** `parser/pdf_parser.py` → `_classify_candidate_amount_type()` → `table_total_column` branch (defaulted to `incl` when VAT wording was `N % BTW over …`)
- **Downstream effect:** `logic/payment_engine.py` → `_process_supplier_group()` lines 916–918 (`tentative` → `needs_review`)

## Fix Applied

Extended VAT detection in `_classify_candidate_amount_type()` for `table_total_column`:

- `BTW/VAT calculated over`
- `BTW/VAT over` (e.g. `BTW over € …`)
- `N % BTW/VAT over` (e.g. `21 % BTW over € …`)

## Minimal Safe Fix

Extraction-only regex additions in `parser/pdf_parser.py`; no payment-engine or matching changes.

## Risk Assessment

**LOW** — narrow patterns on `val_part` after `>>` in table VAT lines; Aluned/Besli patterns unchanged.
