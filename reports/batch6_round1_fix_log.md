# Batch 6 Round 1 — Fix Log

Per-fix validation log (single PDF only).

---

## Fix 1 — Venttrade invoice_number

Supplier: Venttrade  
Issue: invoice_number — BTW-nummer leak + missing `1100/220/10020159`  
Fix applied: Skip `btw-nummer` label match; multi-slash + header-table last column + next-line scan (`pdf_parser.py`, `field_candidates.py`)  
Before: `NL001740777B35`  
After: `1100/220/10020159`  
Result: **FIXED**

---

## Fix 2 — VTE credit invoice_number

Supplier: VTE credit  
Issue: invoice_number — parent factuur `VF2600115` i.p.v. creditnota `VCR2600003`  
Fix applied: VCR title kandidaat + filter parent `Fact.nr.` refs (`field_candidates.py`, `pdf_parser.py`)  
Before: `VF2600115`  
After: `VCR2600003`  
Result: **FIXED**

---

## Fix 3 — Ubbink amount

Supplier: Ubbink  
Issue: amount missing (expected 703,39)  
Fix applied: Table header `Totaal` column + glued EUR value row (`pdf_parser.py`)  
Before: `None`  
After: `703.39`  
Result: **FIXED**

---

## Fix 4 — Qblades amount

Supplier: Qblades  
Issue: amount missing (expected 397,24 incl)  
Fix applied: `derived_excl_plus_vat` from Excl. btw + BTW lines (`pdf_parser.py`)  
Before: `None`  
After: `397.24`  
Result: **FIXED**

---

## Fix 5 — Salo invoice_date

Supplier: Salo  
Issue: factuurdatum `8-1-2026` niet gevonden (soft hyphen)  
Fix applied: `_normalize_text_for_invoice_dates` — U+00AD → `-` (`pdf_parser.py`)  
Before: `None`  
After: `2026-01-08`  
Result: **FIXED**

---

## Fix 6 — False positives VAT/KVK

Supplier: Salo — vat `VO2237224ADRES` → `None` (**FIXED**)  
Supplier: Samedia — vat `NL001740777B35` → `DE141994165` (**FIXED**)  
Supplier: Wavin — vat footer → header `NL813771213B01` (**FIXED**)  
Fix applied: `_supplier_vat_shape_ok`, customer VAT block filter, footer dedupe (`field_candidates.py`, `pdf_parser.py`)

---

## Fix 7 — Van den Borne invoice_number

Before: `4126` → After: `4126VF01369` (**FIXED**)

---

## Fix 8 — Van Walraven invoice_number

Before: `RABONL2U` → After: `VP601987` (**FIXED**)

---

## Fix 9 — Qblades customer_number guard

Before: `None` → After: `None` (**FIXED** — geen hallucinatie)

---

## Bonus — Partial PDFs (batch >90%)

Supplier: VT accountants / Vt  
Issue: amount missing (`Te voldoen € 327,31`)  
Fix: `te voldoen` in `total_label_payable` (`pdf_parser.py`)  
Result: **FIXED** → `327.31`

Supplier: Wasco  
Issue: amount ambiguous (`Factuurbedrag EUR(Incl. BTW)`)  
Fix: classify `incl. btw` on factuurbedrag as `incl` (`pdf_parser.py`)  
Result: **FIXED** → `65.51`

Supplier: Salo  
Issue: amount ambiguous (corrupt orderbedrag OCR)  
Fix: skip corrupt order-total lines; `Totaal: … EUR` + netto+BTW derivatie (`pdf_parser.py`)  
Result: **FIXED** → `1198.87`

Supplier: Tegeka  
Issue: invoice_number missing (`93557` in Factuur/Debiteur table)  
Fix: header-table Factuur+Debiteur + last-column invoice pick (`field_candidates.py`)  
Result: **FIXED** → `93557`

## Batch final validation

- **34/34 (100%)** fully correct (amount + invoice_number + invoice_date)
- Regressions vs baseline: verwachte false-positive verwijdering (VAT/KVK), geen kernveld-regressies

---
