1. CORE PRINCIPLE (HARD RULE)
De engine (DecisionStore + engine output) is de enige bron van waarheid.
Alles in de UI is pure weergave (render-only) van engine data.
De UI mag nooit beslissingen maken, aanvullen of interpreteren.
2. ALLOWED ENGINE OUTPUT (STRICT CONTRACT)

Elke row decision uit de engine mag alleen bevatten:

status ∈ {included, needs_review, excluded}
reason_code (string, verplicht)
reason_detail (string, optioneel)
row_id

❌ Geen extra states toegestaan zoals:

missing
omitted
not_evaluated
pending
grey
unknown
3. MISSING DATA RULE (IMPORTANT)

Als een row geen decision heeft in DecisionStore:

UI behandelt dit als:
status = needs_review
reason_code = missing_decision_in_store
reason_detail = ""

👉 Dit is geen extra engine state, maar een vaste mappingregel.

4. UI RENDER RULE (NO LOGIC IN UI)

UI mag alleen:

kleuren tonen op basis van status
tekst tonen op basis van reason_code + reason_detail
NL vertaling tonen via static lookup

❌ UI mag NIET:

statuses afleiden
presence interpreteren
fallback states genereren
decisions “reconstrueren” uit table data
UI-tekst gebruiken als input voor logic
5. COLOR RULE (ABSOLUTE)
included → groen
needs_review → geel
excluded → rood

❌ Geen grijs, geen fallback kleuren, geen unknown state

6. REASON COLUMN RULE (2 REGELS EXACT)

Elke rij toont exact:

Line 1 (RAW)
exact: reason_code + reason_detail (engine output onveranderd)
Line 2 (NL)
statische mapping:
reason_code → human readable string
geen AI
geen heuristiek
geen extra interpretatie
7. DEBUG RULE (READ-ONLY)

Debug UI (inspector, overlays, traces):

mag engine data tonen
mag provenance tonen
mag decision trace tonen

❌ mag nooit:

UI beïnvloeden
rendering beïnvloeden
decisions wijzigen
kleuren beïnvloeden
8. UPDATE / USER ACTION RULE

Alle user acties:

veranderen nooit direct de UI state
sturen alleen requests naar engine/store
engine commit is enige moment van waarheid
UI rerender gebeurt uitsluitend vanuit DecisionStore
9. UI STATE VERBOD (CRITICAL)

Volgende concepten zijn verboden in rendering logic:

DecisionPresence
UIStatus
grey/omitted/not_evaluated logic
fallback UI decisions
UI-derived status
legacy status parsing uit table cells
10. SINGLE SOURCE OF TRUTH ENFORCEMENT

De enige toegestane dataflow:

Engine → DecisionStore → Resolver → UI Render


❌ Niet toegestaan:

UI → logic → UI
UI → decision inference
UI cell data → engine logic
debug state → rendering
11. TABLE RENDER RULE

Elke row render gebruikt uitsluitend:

DecisionStore.committed_decision_map(row_id)
fallback mapping (alleen needs_review + missing_decision_in_store)

Geen andere bron toegestaan.

12. GOLDEN DATASET IS REGRESSION GATE (HARD)

De golden dataset is de enige bron van waarheid voor engine-output correctheid.

Elke wijziging buiten de UI-laag MOET gevalideerd worden tegen de golden dataset.

**Golden Suite v2 — drie contractlagen**

| Laag | Pad | Contract | CI-blokkering |
|------|-----|----------|-----------------|
| Extraction (hard) | `tests/golden/extraction/` | `invoice_number`, `customer_number`, `iban`, `amount` — exact match | **JA** |
| Decision (soft) | `tests/golden/decision/` | `decision_status`, `amount_status`, legacy velden — mismatch = warning | Nee |
| Ranking (debug) | `tests/golden/ranking/` | snapshot drift — log only | Nee |

Blocking gate (minimaal verplicht na parser/engine-wijzigingen):

```bash
python3 -m pytest tests/golden/extraction/
```

Volledige golden v2 (alleen extraction faalt hard):

```bash
python3 -m pytest tests/golden/
```

13. VERPLICHTE TEST TRIGGER (HARD)

Na ELKE wijziging in één van deze gebieden:

logic/ (engine, matching, decision building)
output/ (SEPA XML, export logic)
scripts/ (batch/golden save flows)
dependency loading / OCR / parsing pipeline
data extraction / invoice parsing

MOET direct worden uitgevoerd:

python3 -m pytest


OF minimaal:

```bash
python3 -m pytest tests/golden/extraction/
```

14. NO TEST = INVALID CHANGE (HARD BLOCK)

Als de golden dataset tests niet zijn gedraaid:

❌ wijziging is ongeldig
❌ code mag niet als “werkend” beschouwd worden
❌ geen verdere development toegestaan

15. GOLDEN DATASET MISMATCH = STOP (HARD)

**Hard contract (extraction):** mismatch op `invoice_number`, `customer_number`, `iban` of `amount` = STOP.

**Soft contract (decision/legacy):** mismatch op status of secundaire velden = onderzoeken, geen automatische CI-block.

**Debug (ranking):** snapshot drift = signaal only, geen fail.

Als golden dataset output verandert:

aantal payments ≠ expected
status verandert (included ↔ needs_review ↔ excluded)
reason_code verandert
export set verandert

Dan:

❌ ALTIJD STOPPEN
❌ NOOIT “even doorwerken”
❌ NOOIT negeren

16. MISMATCH MOET EXPLICIET VERKLAARD WORDEN

Bij elke golden mismatch moet exact één van deze waar zijn:

A. Intentional change
business logic is bewust aangepast
golden dataset wordt geüpdatet
reden wordt gedocumenteerd
B. Bug
gedrag is onbedoeld veranderd
code moet worden teruggedraaid of gefixt

👉 “We zien wel” is NIET toegestaan

17. UI IS GEEN VALIDATIE (CRUCIAAL)

UI mag NOOIT gebruikt worden om correctheid te bepalen.

Dus:

❌ “het ziet er goed uit in de UI” = irrelevant
✅ alleen golden dataset bepaalt correctheid

18. HEADLESS = UI PARITY (HARD)

Golden dataset tests moeten draaien in dezelfde condities als productie:

zelfde dependencies (./.deps)
zelfde OCR availability
zelfde config

Als UI en headless verschillen:

❌ test is ongeldig
❌ eerst environment fixen

19. PRE-COMMIT CHECK (STERK AANRADER)

Voor elke commit:

python3 -m pytest


Als tests falen:

❌ commit verboden

20. POST-FIX VERIFICATIE (VERPLICHT)

Na elke bugfix:

Run golden tests
Check:
aantal invoices
aantal payments
aantal errors
Moet identiek zijn aan expected
21. NO SILENT REGRESSIONS

Het is verboden dat:

aantal payments stil verandert (10 → 8)
status stil verandert (included → needs_review)
OCR stil uitvalt
dependencies ontbreken zonder foutmelding

Elke afwijking moet:

zichtbaar zijn
of tests moeten falen

Het systeem is correct als:

Elke row heeft exact 1 van 3 kleuren
Geen grijs in UI bestaat
UI kan engine decisions niet beïnvloeden
Reason altijd 2 regels bevat (RAW + NL)
Missing data is altijd zichtbaar als needs_review
Golden dataset tests slagen volledig