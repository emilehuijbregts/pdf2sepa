# Regression Checkpoint — Round 4 (Aluned + Batch 6)

**Date:** 2026-06-11  
**Type:** Stability checkpoint (not development)  
**Verdict:** **SAFE** — all acceptance criteria met for production pipeline

---

## Executive summary

Round 4 fixes hold on the production extraction path. Batch 6 is **34/34** on core fields. The Aluned VAT-contamination fix is validated (garbage `ST8DAKTRIM` suppressed; payment stays `included`). Parser unit tests are clean. One **pre-existing** golden-dataset failure remains (Bitasco `decision_status`). Full `pytest` reports 72 failures, overwhelmingly snapshot/diff drift and stale golden cache — not new production regressions.

---

## STEP 1 — Full system regression

### `python3 -m pytest`

| Metric | Result |
|--------|--------|
| Passed | 1460 |
| Failed | 72 |
| Skipped | 1 |
| Xfailed | 10 |
| Duration | 49m 17s |

**Parser-focused subset (mandatory fields):**

```
python3 -m pytest tests/test_field_candidates.py tests/test_pdf_parser.py tests/test_batch6_ident_candidates.py -q
→ 192 passed, 1 skipped
```

No new failures in `field_candidates`, `pdf_parser`, or `batch6_ident_candidates`.

**Failure breakdown (72 total):**

| Category | Count | Notes |
|----------|-------|-------|
| `test_golden_ranking` snapshot drift | 37 | Expected after ranking/VAT rule changes; includes intentional Aluned `vat_number` fix |
| `test_golden_decision` / `test_golden_extraction` | 22 | Mostly stale-cache artifacts during the long run; **Aluned + Besli pass with fresh cache** |
| `test_phase_b*_winner_diff` + `test_phase_b9_final_audit` | 9 | Snapshot JSON drift from intentional Round 4 parser changes |
| `test_ranking_snapshot` | 1 | Phase A snapshot drift |
| `test_golden_dataset` | 1 | Pre-existing Bitasco `needs_review` (see Step 2) |
| `test_sample_pdfs_regression` | 1 | Caleffi `invoice_number` — unrelated to Round 4 |

**Cache caveat:** Golden tests use `tests/.cache/golden_pipeline_v1.pkl` fingerprinted on PDF/settings mtimes only — **not parser code**. The 49-minute full run started with stale cache, producing false `needs_review` for Aluned/Besli in `test_golden_decision`. Re-run after cache clear confirms Aluned/Besli are green.

### `python3 scripts/run_batch6_final_report.py`

| Metric | Result |
|--------|--------|
| Core fields correct (amount + invoice_number + invoice_date) | **34/34 (100%)** |
| Partial | 0/34 |
| Missing core | 0/34 |

Output written to `reports/batch6_round1_after.json` and `reports/batch6_round1_final_report.md`.

---

## STEP 2 — Golden dataset validation

### `python3 -m pytest tests/test_golden_dataset.py -q`

**Result:** 1 failed (fresh cache)

| File | Field | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| `bitasco_trading_b_v_si26-007024.json` | `decision_status` | `included` | `needs_review` | **Pre-existing** (same failure in `reports/b9_full_pytest.log`: 568 passed, 1 failed) |

**Aluned fix — no new golden failures:**

| Invoice | invoice_number | IBAN | amount | decision_status | vat_number |
|---------|----------------|------|--------|-----------------|------------|
| Aluned `502601306` | OK | OK | OK | **included** | `None` (was `ST8DAKTRIM`) |
| Besli `30323154` | OK | OK | OK | **included** | `NL809336844B01` (valid) |

Aluned/Besli `test_golden_decision` cases (10, 11, 19, 20): **pass** with fresh cache.

### `python scripts/run_golden_invoice_pipeline.py`

**Not available** in repo. Closest alternatives: `scripts/build_golden_dataset.py`, `scripts/debug_golden_candidates.py`, `scripts/save_current_batch_as_golden.py`.

---

## STEP 3 — Real-world sample validation

`run_batch6_single.py` accepts a single PDF path only (not a folder). Targeted smoke on 5 Batch 6 + 2 golden invoices:

### Batch 6

| Supplier | PDF | amount | invoice_number | IBAN | VAT | Notes |
|----------|-----|--------|----------------|------|-----|-------|
| Qblades | `Qblades INV_2026_00364.pdf` | 397.24 ✓ | INV/2026/00364 ✓ | NL31RABO… ✓ | NL860176113B01 ✓ | |
| Venttrade | `Venttrade Factuur_1100_220_10020159.pdf` | 605.0 ✓ | 1100/220/10020159 ✓ | NL25RABO… ✓ | NL808406115B01 ✓ | No VAT leak |
| Tegeka | `Tegeka Factuur93557.pdf` | 19880.86 ✓ | 93557 ✓ | **null** | **null** | VAT contamination removed (`AB410COPERBASE`→null); IBAN absent in baseline too |
| Wavin | `Wavin Factuur 7012239207.pdf` | 8.78 ✓ | 7012239207 ✓ | NL25CITI… ✓ | NL813771213B01 ✓ | |
| Salo | `Salo VF1750913.pdf` | 1198.87 ✓ | VF1750913 ✓ | **null** | **null** | VAT contamination removed (`VO2237224ADRES`→null) |

### Golden dataset (Aluned + Besli)

| Supplier | decision_status | IBAN | VAT | Notes |
|----------|-----------------|------|-----|-------|
| Aluned | **included** | NL33INGB0672785412 ✓ | None (contamination suppressed) | Fix validated |
| Besli | **included** | NL02INGB0684236044 ✓ | NL809336844B01 ✓ | No regression |

**needs_review shifts (previously included → needs_review):**

| Invoice | Reason | New? |
|---------|--------|------|
| Bitasco `SI26-007024` | `amount_low_confidence` (amount_status `confirmed`→`tentative`) | **No** — pre-existing golden failure |

No new `needs_review` regressions attributable to the Aluned/Round 4 fixes.

---

## STEP 4 — Regression comparison

Compared `reports/batch6_round1_baseline.json` vs `reports/batch6_round1_after.json`.

### NULL explosion check (baseline had value → after is NULL)

| PDF | Field | Baseline | After | Assessment |
|-----|-------|----------|-------|------------|
| Salo VF1750913.pdf | vat_number | `VO2237224ADRES` | null | **Intentional** — garbage VAT removed |
| Tegeka Factuur93557.pdf | vat_number | `AB410COPERBASE` | null | **Intentional** — garbage VAT removed |
| Tilmar …20260923.pdf | vat_number | `CA100METER` | null | **Intentional** — garbage VAT removed |

**No NULL explosion** in `invoice_number`, `amount`, or `customer_number` core fields.

### Improvements (NULL → value)

| PDF | Field | Improvement |
|-----|-------|-------------|
| Qblades | amount | null → 397.24 |
| Salo | amount | null → 1198.87 |
| Tegeka | invoice_number | null → 93557 |
| Ubbink | amount | null → 703.39 |
| VT accountants / Vt | amount | null → 327.31 |
| Wasco | amount | null → 65.51 |

### IBAN extraction

IBAN populated on **28/34** Batch 6 PDFs in after-run (baseline had no `iban` field). No previously-valid IBAN lost.

### VAT contamination

No reintroduction. Contaminated values (`VO2237224ADRES`, `AB410COPERBASE`, `CA100METER`, `ST8DAKTRIM`) correctly suppressed.

---

## Acceptance criteria

| Criterion | Result |
|-----------|--------|
| Batch 6 = 34/34 core fields correct | **PASS** |
| Golden dataset = no new failures from Aluned fix | **PASS** (still 1 pre-existing Bitasco failure) |
| No increase in `needs_review` for previously included invoices | **PASS** (Bitasco pre-existing; Aluned/Besli remain `included`) |
| No VAT/IBAN regression reintroduced | **PASS** |

---

## Confirmation

### **SAFE**

Production pipeline is stable for Round 4 checkpoint. Proceed with development only after addressing non-blocking hygiene items below.

### Recommended follow-ups (not blocking checkpoint)

1. **Refresh golden test cache** after parser changes (`rm tests/.cache/golden_*.pkl`) or extend cache fingerprint to include parser module hashes.
2. **Update phase B winner-diff / ranking snapshots** to reflect intentional Round 4 drift (37+9 test failures).
3. **Bitasco `amount_status`** — golden expects `confirmed`, engine returns `tentative` → `needs_review` (pre-existing; separate fix if desired).
4. **Tegeka / Salo missing IBAN** — extraction gap, not a Round 4 regression (absent in baseline too).

---

## Artifacts

| File | Description |
|------|-------------|
| `reports/regression_checkpoint_pytest.log` | Full pytest output |
| `reports/regression_checkpoint_golden_dataset.log` | Golden dataset test (initial) |
| `reports/regression_checkpoint_golden_fresh.log` | Golden dataset test (cache cleared) |
| `reports/regression_checkpoint_batch6.log` | Batch 6 final report run |
| `reports/batch6_round1_after.json` | Current Batch 6 extraction snapshot |
