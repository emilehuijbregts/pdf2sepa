# Fase 2 Analyse: UI Internationalisatie (i18n)

**Status:** Analyse afgerond — geen code gewijzigd, geen i18n-systeem gebouwd.  
**Doel:** In kaart brengen waar UI-tekst nu zit en hoe die gestructureerd moet worden voor internationalisatie zonder regressies.

---

## 1. Architectuur: waar wordt UI-tekst gegenereerd?

### 1.1 Dataflow batch-load

```
batch_load_pipeline.py          invoice_batch_load_worker.py       main_window.py              loading_overlay.py
─────────────────────          ────────────────────────────       ──────────────              ──────────────────
_emit(stage_key, filename)  →   progress / current_file /       →  show_overlay(title)     →   STAGE_LABELS lookup
                                current_stage signals               update_progress()           counter / ETA format
build_iban_dialog_specs()   →   (via PreprocessCheckpoint)      →  QMessageBox.question()      (render only)
```

### 1.2 Tekst per laag

| Laag | Wat | Voorbeeld | i18n-conform? |
|------|-----|-----------|---------------|
| **Domain** | Stage keys (EN identifiers) | `"parsing_pdf"` | Ja — keys, geen UI-tekst |
| **Domain** | Dialog title + message (NL) | `iban_resolution_engine.build_iban_dialog_specs` | **Nee** — formatted NL strings |
| **Domain** | Error codes → NL maps | `logic/diagnostics.py`, `payment_decisions.py` | Gedeeltelijk — codes goed, vertaling in domain |
| **Worker** | Engelse exception strings | `"batch load params missing"` | **Nee** — UI-tekst in signal |
| **UI (overlay)** | Stage label mapping + ETA/counter | `STAGE_LABELS`, `"bijna klaar"` | Ja — UI-only |
| **UI (main_window)** | ~200+ strings inline | toolbar, QMessageBox, status bar | Ja qua laag, nee qua structuur |
| **UI (dialogs)** | Dedicated modules | `field_review.py`, `suppliers_dialog.py` | Ja qua laag |

### 1.3 Formatting-locatie

| Element | Waar geformatteerd | Bestand |
|---------|-------------------|---------|
| Counter `14 / 82 bestanden` | UI | `ui/loading_overlay.py:312` |
| ETA `~ N sec resterend` | UI | `ui/loading_overlay.py:331–351` |
| Filenames (PDF basename) | Domain emit → UI elide | `batch_load_pipeline.py:110` → overlay |
| IBAN dialog body | Domain (anti-pattern) | `logic/iban_resolution_engine.py:68–74` |
| Status bar na load | UI | `main_window.py:~3461` |
| Bedragen / datums | UI formatters | `_format_amount_nl`, `QLocale` (deels) |

### 1.4 pdf_ocr_session.py

Geen directe UI-teksten. Alleen interne status-enums (`confirmed`, `tentative`, `failed`). Indirect zichtbaar via diagnostics; vertaling in `logic/field_diagnostics.py`.

### 1.5 Bestaande UI-layer contract

`tests/test_ui_layer_contract.py` bewaakt:
- Geen imports van `iban_ui_mapping` / `resolve_iban_context` in main_window
- Geen domain choice literals (`keep_db`, `use_pdf`) in main_window
- Geen business conditionals in `_collect_iban_raw_answers`

Dit contract dekt **import-scheiding**, niet dialog-copy in domain.

---

## 2. UI Text Inventory

Elke string is geclassificeerd in:

- **STATIC LABELS** — vaste teksten (buttons, titles, headers)
- **DYNAMIC STATUS TEXTS** — progress stage updates, overlay status
- **DIALOG TEXTS** — QMessageBox content
- **SYSTEM GENERATED TEXTS** — filenames, counters, ETA, formatted data

---

### 2A. Batch-load flow (focus)

#### STATIC LABELS

| String | Bestand | Regel | Categorie |
|--------|---------|-------|-----------|
| `PDF's worden ingelezen…` | `main_window.py` | 3394 | STATIC |
| `Betalingen herberekenen…` | `main_window.py` | 3394 | STATIC |
| `PDF's uitlezen` | `main_window.py` | 2144 | STATIC |
| `Laden` (QProgressDialog titel) | `main_window.py` | 3131 | STATIC |
| `Leveranciers opnieuw koppelen…` | `main_window.py` | 3130 | STATIC |

#### DYNAMIC STATUS TEXTS — stage protocol

| Stage key (protocol, EN) | Display string (NL) | Key owner | Label owner |
|--------------------------|---------------------|-----------|-------------|
| `listing_pdfs` | `PDF-bestanden zoeken…` | `logic/batch_load_pipeline.py:105` | `ui/loading_overlay.py:20` |
| `parsing_pdf` | `OCR uitvoeren…` | pipeline:110,120 | overlay:21 |
| `deduplicating` | `Dubbelen verwijderen…` | pipeline:126 | overlay:22 |
| `matching_suppliers` | `Leveranciers koppelen…` | pipeline:144 | overlay:23 |
| `enriching_credits` | `Creditnota's verwerken…` | pipeline:148 | overlay:24 |
| `computing_payments` | `Betalingen berekenen…` | pipeline:197 | overlay:25 |
| *(fallback)* | `{stage_key.replace("_"," ")}` | — | overlay:294 (Engels) |

#### DIALOG TEXTS — IBAN ambiguity (batch load, domain-built)

| Onderdeel | String | Bestand | Regel |
|-----------|--------|---------|-------|
| Title | `IBAN-afwijking gedetecteerd` | `logic/iban_resolution_engine.py` | 79 |
| Body | `Leverancier: {name}` | idem | 69 |
| Body | `Aantal facturen: {count}` | idem | 70 |
| Body | `Database IBAN:\t{db}` | idem | 71 |
| Body | `Factuur IBAN:\t{pdf}` | idem | 72 |
| Body | `De database-IBAN wordt standaard gebruikt (aanbevolen).` | idem | 73 |
| Body | `Wil je de database bijwerken naar het factuur-IBAN?` | idem | 74 |

**Legacy duplicate (zelfde copy, UI-inline):** `main_window.py:2647–2655` — oude supplier-sync flow, niet via `IbanAmbiguityDialogSpec`.

#### DIALOG TEXTS — batch-load fouten

| Title | Message | Bestand | Regel |
|-------|---------|---------|-------|
| `Laden` | `Laden mislukt bij verwerken resultaat:\n{exc}` | `main_window.py` | 3367 |
| `Laden` | `Laden mislukt:\n{message}` | `main_window.py` | 3373 |
| `Laden` | `Geen geparste facturen in cache. Klik eerst op 'PDF's uitlezen'.` | `main_window.py` | 3383–3386 |

#### SYSTEM GENERATED TEXTS — batch-load

| Type | Pattern | Owner | Regel |
|------|---------|-------|-------|
| File counter | `{done} / {total} bestanden` | UI overlay | `loading_overlay.py:312` |
| ETA bijna klaar | `bijna klaar` | UI overlay | 331, 340 |
| ETA seconden | `~ {n} sec resterend` | UI overlay | 342 |
| ETA min+sec | `~ {m} min {s} sec resterend` | UI overlay | 349 |
| ETA minuten | `~ {m} min resterend` | UI overlay | 351 |
| Filename | PDF basename (elided) | pipeline → worker → overlay | pipeline:110 |
| Worker error | `batch load params missing` | worker | `invoice_batch_load_worker.py:39` |
| Worker error | `run_preprocess must complete before resolve phase` | worker | :70 |
| Pipeline exception | `warm_invoices required when parse_pdfs=False` | pipeline | `batch_load_pipeline.py:131` |
| Pipeline label | `Map: {folder.name}` | pipeline | :160 |
| Status na load | `PDF's: {n}, betalingsregels: {n}, foutregels: {n}. Map: {folder}. Exportmap: {path}` | main_window | ~3461 |

#### Tests die NL literals asserten

| Test | String | Bestand |
|------|--------|---------|
| Overlay title parse | `PDF's worden ingelezen…` | `tests/test_loading_overlay.py:37` |
| Counter | `14 / 82 bestanden` | :39 |
| Overlay title rematch | `Betalingen herberekenen…` | :49 |
| Stage label substring | `Leveranciers` | :51 |

---

### 2B. App-brede string-clusters (~400+ strings)

#### main_window.py (~200+ strings)

| Cluster | Regels | Categorie | Aantal entries |
|---------|--------|-----------|----------------|
| `_ERROR_REASON_NL` | 421–442 | DYNAMIC (error display) | 20 |
| `_WARNING_NL` | 444–453 | DYNAMIC | 8 |
| `_SIGNAL_LABELS` | 458–466 | STATIC | 7 |
| `_PROFILE_BLOCK_TOOLTIPS` | 4513–4519 | STATIC (tooltips) | 6 |
| `DEBTOR_FORM_FIELDS` | 893–908 | STATIC | 5 velden × 2 |
| `_UW_GEGEVENS_XML_HINT` | 887–889 | STATIC | 1 |
| `_DEBTOR_KVK_VAT_TOOLTIP` | 911–914 | STATIC | 1 |
| Window title | 1205 | STATIC | `PDF2SEPA Desktop Client` (EN) |
| Settings dialog | 1022–1191 | STATIC + DIALOG | ~15 |
| Toolbar buttons | 2139–2196 | STATIC | ~10 |
| Table column headers | 2216–2232 | STATIC | 16 |
| Context menu actions | 5712–5816 | STATIC | ~15 |
| QMessageBox clusters | verspreid | DIALOG | ~40 dialogs |
| Status bar patterns | verspreid | SYSTEM GENERATED | ~15 templates |
| Export validation | 7511–7584 | DIALOG + STATIC | ~10 |
| Dynamische formatters | 494–536, 969–974 | SYSTEM GENERATED | `_matches_completeness_text`, `_core_matches_text`, `_term_status_label` |

**Table headers (STATIC):** Leverancier, IBAN, Bedrag, Klantcode, Omschrijving, PDF, Korting, Factuurdatum, Betaaldatum, Betaaltermijn, Kernmatches, Matches compleet, Status, Foutmelding, Info, Verrekening.

**Toolbar (STATIC):** Map selecteren, PDF's uitlezen, Maak XML bestand, Batch status: VALID/WARNING/BLOCKED, Mijn leveranciers, Voeg toe / update, Profiel aanmaken/aanvullen, Instellingen.

#### ui/loading_overlay.py (14 user-facing)

Zie sectie 2A. Geen andere UI-teksten.

#### ui/field_review.py (~40 strings)

| Cluster | Categorie |
|---------|-----------|
| `_AMOUNT_SOURCE_NL` (8 entries) | STATIC (picker labels) |
| `FIELD_REVIEW_SPECS` menu titles (8 velden) | DIALOG |
| `menu_no_candidates_nl` per veld | DIALOG |
| `CUSTOMER_ABSENT_MENU_LABEL_NL` | STATIC |
| `make_customer_absent_pick_candidate` label | STATIC |

Voorbeelden: `Bedrag kiezen`, `IBAN kiezen`, `Totaal te betalen`, `Geen klantnummer`.

#### ui/diagnostics_dialog.py (~80 strings)

Window title `Diagnostics — {supplier}`, buttons (`Bevestig selectie`, `Sla profiel op`, `Sluiten`), sectie-labels, waarschuwingsteksten. Geopend vanuit main_window.

#### ui/suppliers_dialog.py (~50 strings)

Leveranciersbeheer: titels, kolomkoppen, knoppen, validatie-dialogen.

#### ui/profile_confirm_dialog.py (~20 strings)

Profiel bevestiging: veldlabels, knoppen, statusmeldingen.

#### ui/credit_override_dialog.py (~15 strings)

`Credit koppelen`, kandidaatlabels, bevestigingsteksten.

#### ui/settlement_*.py (~30 strings)

| Module | Strings |
|--------|---------|
| `settlement_badges.py` | OK, Volledig verrekend, Controle credit, Terugbetaling, Losgekoppeld |
| `settlement_expand.py` | Credit niet toegewezen, Credit niet volledig verrekend (+ formatted amounts) |
| `settlement_inspector.py` | Inspector regels (via main_window selection) |

#### logic/diagnostics.py (~50 NL maps)

| Map | Entries | Categorie |
|-----|---------|-----------|
| `AMOUNT_STATUS_NL` | 5 | DYNAMIC |
| `AMOUNT_SOURCE_NL` | 10 | DYNAMIC |
| `MATCH_STATUS_NL` | 6 | DYNAMIC |
| `LOAD_ERROR_NL` | 3 | DYNAMIC |
| `ERROR_REASON_NL` | 20 | DYNAMIC |
| `WARNING_NL` | 8 | DYNAMIC |

#### logic/payment_decisions.py (~25 strings)

`decision_status_label_nl`: Wordt betaald, Controle nodig, Wordt niet betaald.  
`decision_reason_text_nl`: 15+ reason codes → NL.  
`decision_fix_hint_nl`: fix hints per reason.

#### logic/field_diagnostics.py (~40 strings)

`_OVERRIDE_REASON_NL`, `_FINAL_DECISION_REASON_NL`, `_REJECTION_REASON_NL`, `_WINNER_REASON_NL`, `_EXTRACTION_METHOD_NL`, `_CONTEXT_HINT_NL`, `_SOURCE_LABEL_NL`, `_SCORE_LABEL_NL`, `_IBAN_SOURCE_NL`.

#### logic/settings.py (5 strings)

`REQUIRED_DEBTOR_MESSAGES`: naam, iban, bic validatie.  
`DEBTOR_MISSING_KEY_FALLBACK`.

#### output/sepa_xml.py (3 strings)

Export geblokkeerd-meldingen bij ambiguïteit.

#### Taalmix (normalisatie nodig vóór vertaling)

| String | Taal | Locatie |
|--------|------|---------|
| `PDF2SEPA Desktop Client` | EN | main_window:1205 |
| `VALID` / `WARNING` / `BLOCKED` | EN | main_window:2155, 7409 |
| `Reset override`, `Diagnostics` | EN | context menu |
| Stage fallback | EN | loading_overlay:294 |
| Worker/pipeline errors | EN | worker, pipeline |
| Debug inspector labels | EN | diagnostics_dialog |

---

## 3. Ownership Map (current vs desired)

| Type | Owner nu | Moet eigenaar worden | Migratie-notitie |
|------|----------|----------------------|------------------|
| Overlay stage display text | UI — `STAGE_LABELS` in overlay | **UI only** | Al goed; keys blijven in domain |
| Overlay titels | UI — main_window | **UI only** | Verplaats naar string registry |
| Stage protocol keys | Domain — batch_load_pipeline | **Domain** (keys, geen tekst) | Geen wijziging |
| IBAN dialog title + message | Domain — iban_resolution_engine | **UI mapping layer** (key + params) | Hoogste prioriteit |
| IBAN dialog (legacy duplicate) | UI — main_window:2647 | **Verwijderen / unificeren** | Zelfde mapping als batch flow |
| Worker error messages | Worker — hardcoded EN | **Domain error codes** → UI vertaling | Signal payload = code |
| Progress counter template | UI — overlay | **UI only** | Template met placeholders |
| ETA text | UI — overlay | **UI only** | Idem |
| Filenames | Domain/worker (data) | **Data** (niet vertalen) | Geen actie |
| Field picker menu titles | UI — field_review.py | **UI only** | Registry per field_id |
| Error/warning display (table) | Logic NL maps + main_window `_ERROR_REASON_NL` | **UI resolver** op error codes | Domain houdt codes |
| Decision status/reason | Logic — payment_decisions.py | **UI resolver** op reason_code | Domain houdt codes |
| Diagnostics labels | Logic — field_diagnostics.py + diagnostics.py | **UI resolver** (codes uit domain) | Grootste volume |
| Debtor validation | Logic — settings.py | **UI resolver** | Codes: `missing_name`, etc. |
| Settlement badges | UI — settlement_badges.py | **UI only** | Al op key-basis |
| Export blocked messages | output/sepa_xml.py | **UI resolver** of codes | Laag-prioriteit |

---

## 4. Top 5 risico's voor internationalisatie

### R1 — Domain layer bevat formatted UI-tekst

`build_iban_dialog_specs()` in `logic/iban_resolution_engine.py` bouwt complete NL QMessageBox-strings. Bij EN/NL moet dit gedupliceerd worden of verplaatst — schendt UI-strict model. Contract in `tests/test_ui_layer_contract.py` dekt dit niet.

### R2 — Verspreide `_NL` dictionaries over drie lagen

Identieke patronen in `main_window._ERROR_REASON_NL`, `logic/diagnostics.ERROR_REASON_NL`, en `logic/field_diagnostics._*_NL`. Risico op divergerende vertalingen per taal.

### R3 — Signal payloads met UI-strings

`worker.error.emit(message: str)` toont ruwe Engelse exceptions. Pipeline `ValueError("warm_invoices required...")` komt direct in QMessageBox. Geen stabiele error codes.

### R4 — Duplicatie IBAN-dialog (legacy + batch)

Zelfde copy op twee plekken (`main_window.py:2647` vs `iban_resolution_engine.py:68`). EN/NL chaos gegarandeerd als één pad wordt gemist.

### R5 — Geen centraal string registry + inconsistente naming

Stage keys (EN snake_case) vs display labels (NL) vs error codes (mixed) vs `_NL` suffix conventie. Geen enum, geen key-namespace. Tests asserten op literal NL strings — breken bij elke taalwissel.

---

## 5. Ideale doelarchitectuur (beschrijving)

```
Domain                          UI Layer
──────                          ────────
emit stage_key                  UiStrings.resolve("overlay.stage.parsing_pdf")
emit error_code                 UiStrings.resolve("error.batch.params_missing")
emit DialogSpec(                UiStrings.resolve("dialog.iban.mismatch", params={...})
  key="iban.mismatch",
  params={supplier, db_iban, ...}
)
```

**Regels:**

1. **UI = ONLY rendering** — alle zichtbare tekst via resolver of Qt `tr()`.
2. **Domain = emits keys + structured data** — geen formatted NL/EN strings in DTOs.
3. **Worker = passes keys only** — `error.emit("batch.load.params_missing")`, niet vrije tekst.
4. **Geen string literals in signals** — alleen identifiers + numerieke/data payloads.
5. **Filenames, bedragen, datums** — locale-aware formatting in UI (`QLocale`), niet in domain.

**IBAN-dialog doel:**

- Domain: `IbanAmbiguityDialogSpec(dialog_key="iban.mismatch", params={...})` — geen `title`/`message`.
- UI: `_collect_iban_raw_answers` roept resolver aan voor title + body template.
- Legacy pad in main_window verwijderen; één code path.

---

## 6. Aanbeveling Phase 2 implementatie

### Aanpak: Hybrid (enum keys + translation dict + Qt tr() waar zinvol)

| Mechanisme | Wanneer | Reden |
|------------|---------|-------|
| **Stable string keys** (dot-notation) | Stage labels, dialogs, errors, status | Namespace voorkomt collisions; testbaar zonder locale |
| **Enum/constant class** | Stage keys, error codes, dialog types | Type safety; bestaande stage keys al EN identifiers |
| **Translation dict per taal** | NL (default), EN (eerste target) | Past bij huidige `_NL` dict-patroon; lichter dan Linguist voor start |
| **Qt `tr()` / `.ts` files** | Optioneel later voor main_window bulk | 200+ strings; Qt-native maar zwaarder setup |

**Niet aanbevolen:** pure Qt Linguist vanaf dag 1 (te groot oppervlak), of alles inline in main_window houden.

### Voorgestelde key-structuur

```
overlay.title.parse_pdfs
overlay.title.rematch_payments
overlay.stage.listing_pdfs
overlay.stage.parsing_pdf
overlay.stage.deduplicating
overlay.stage.matching_suppliers
overlay.stage.enriching_credits
overlay.stage.computing_payments
overlay.counter.files              # "{done} / {total} bestanden"
overlay.eta.almost_done
overlay.eta.seconds_remaining      # "~ {seconds} sec resterend"
overlay.eta.minutes_seconds
overlay.eta.minutes_only
dialog.iban.mismatch.title
dialog.iban.mismatch.body
dialog.batch_load.failed
dialog.batch_load.no_cache
error.batch.params_missing
error.batch.warm_invoices_required
error.batch.resolve_phase_incomplete
```

### Migratievolgorde (implementatiefase, na deze analyse)

1. **Pilot: batch-load overlay path** — `STAGE_LABELS`, overlay titles, counter/ETA → centrale `ui/strings/` registry.
2. **IBAN dialog unificatie** — domain → `dialog_key + params`; verwijder legacy duplicate in main_window.
3. **Worker/pipeline errors** — error codes in signals; UI vertaling.
4. **Consolideer `_NL` maps** — verplaats van logic/ naar UI resolver; domain behoudt codes.
5. **main_window bulk** — module dictionaries → registry; Qt tr() optioneel.
6. **Tests** — assert op keys, niet op NL literals; aparte translation coverage test.

### Bestaande goede patronen om te behouden

- Stage keys als protocol tussen pipeline en overlay (al decoupled).
- `iban_ui_mapping.py` — pure domain choice mapping, geen UI-tekst.
- `settlement_badges.py` — key → label lookup in UI layer.
- `tests/test_ui_layer_contract.py` — uitbreiden met "geen NL literals in domain dialog builders".

---

## 7. Deliverables checklist

| Deliverable | Locatie in dit document |
|-------------|-------------------------|
| **A. UI Text Inventory** | Secties 2A (batch-load compleet) + 2B (app-breed per module) |
| **B. Ownership map** | Sectie 3 |
| **C. Risk list (top 5)** | Sectie 4 |
| **D. Implementation recommendation** | Sectie 6 |

**Geen code gewijzigd. Geen refactor uitgevoerd. Geen i18n-systeem gebouwd.**
