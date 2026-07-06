# PDF2SEPA — Windows packaging (PyInstaller)

Voorbereiding voor een Windows `.exe`-build met PyInstaller. Deze map bevat geen binaries en wordt op de Mac alleen als structuur en documentatie gebruikt.

## Doel

- **onedir** GUI-build (`console=False`)
- Programmabestanden in `%LOCALAPPDATA%\PDF2SEPA\app\`
- Klantdata buiten de bundle, in `%LOCALAPPDATA%\PDF2SEPA\data\`

## Vereisten (toekomstige build op Windows)

- Windows-machine met Python 3.11+ (zelfde major als ontwikkeling)
- Virtuele omgeving met projectdependencies + PyInstaller:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt pyinstaller
```

## Build-commando (nog niet uitvoeren in deze fase)

Vanuit de repository-root:

```bash
pyinstaller packaging/pdf2sepa.spec
```

Output:

```
packaging/dist/PDF2SEPA/
  PDF2SEPA.exe
  _internal/
    ...
```

De installer kopieert deze map naar `%LOCALAPPDATA%\PDF2SEPA\app\`.

## Doel-layout op de klant-PC

De applicatie wordt **niet** in Program Files geïnstalleerd.

```
%LOCALAPPDATA%\PDF2SEPA\
  app\              ← PyInstaller onedir; bij update alleen deze map vervangen
  data\             ← klantdata (nooit overschrijven bij update)
  logs\
  backups\
  data_root.json    ← bootstrap: wijst naar data\
```

Bootstrap-logica: [`logic/paths.py`](../logic/paths.py)  
Runtime-paden (logs, frozen detection): [`logic/runtime_paths.py`](../logic/runtime_paths.py)

## Fresh install vs. update

### Fresh install (nieuwe klant)

De installer:

1. Maakt `data\`, `logs\` en `backups\` aan onder `%LOCALAPPDATA%\PDF2SEPA\`
2. Kopieert de PyInstaller-output naar `app\`
3. Schrijft `data_root.json`:

```json
{
  "user_data_directory": "C:\\Users\\<user>\\AppData\\Local\\PDF2SEPA\\data"
}
```

Er is nog geen `settings.json` of `suppliers.json` — de applicatie maakt die bij eerste gebruik aan.

### Update install (bestaande klant)

De installer:

1. Vervangt **alleen** de inhoud van `app\`
2. Laat `data\`, `logs\`, `backups\` en `data_root.json` ongemoeid

Bestanden die **nooit** door een nieuwe build mogen worden overschreven:

| Bestand | Locatie |
|---------|---------|
| `settings.json` | `data\` |
| `suppliers.json` | `data\` |
| `amount_overrides.json` | `data\` |
| `credit_overrides.json` | `data\` |

De PyInstaller-spec bundelt geen van deze bestanden.

## Wat niet in de bundle hoort

**Klantdata (nooit bundelen):**

- `data/settings.json`
- `data/suppliers.json`
- `data/amount_overrides.json`
- `data/credit_overrides.json`
- `data/user_approvals.json`
- exports, logs, backups

**Dev-only (niet bundelen):**

- `tests/`, `scripts/`, `reports/`
- `.deps/`, `.venv/`

**App-engine data (geen klantdata, wel shipped config):**

- `data/strategy_engine_bundle.json` — strategy-engine configuratie; in de spec staat een gecommentarieerde placeholder. Padresolutie in frozen mode is vervolgwerk.

## Tesseract

Zie [`tesseract/README.md`](tesseract/README.md). Binaries en traineddata worden later toegevoegd; de spec heeft voorbereide (uitgecommentarieerde) `binaries`/`datas`-regels.

Benodigde talen: `nld` (PyMuPDF) en `nld+eng` (pytesseract fallback).

## Bekende vervolgstappen (niet in deze fase)

De volgende punten zijn gedocumenteerd voor een latere build-fase; er is nu geen applicatiecode gewijzigd:

1. **`main_window.APP_BASE`** gebruikt `Path(__file__).parent` i.p.v. `runtime_paths.app_root()` — moet worden aangesloten vóór productie-build.
2. **`strategy_engine_bundle.json`** — pad in frozen mode (`parser/profile_strategy_engine.py`).
3. **`tesseract_path()`** — implementeren wanneer `packaging/tesseract/` binaries bevat; env-vars (`TESSDATA_PREFIX`, `tesseract_cmd`, PATH) instellen bij startup.

## Bestanden in deze map

| Bestand | Doel |
|---------|------|
| `pdf2sepa.spec` | PyInstaller-specificatie |
| `tesseract/README.md` | Placeholder voor OCR-binaries |
| `tesseract/tessdata/README.md` | Placeholder voor traineddata |

Build-artefacten (`packaging/build/`, `packaging/dist/`) worden door `.gitignore` genegeerd.
