# Tesseract binaries (placeholder)

Deze map is voorbereid voor het bundelen van Tesseract OCR met de Windows-build.

## Verwachte structuur (fase 2)

```
packaging/tesseract/
├── tesseract.exe
├── *.dll                    # libtesseract, libleptonica, afbeelding-deps
└── tessdata/
    ├── nld.traineddata
    └── eng.traineddata
```

## Waarom nu leeg?

- Platform-specifieke Windows-binaries horen niet in de Mac-ontwikkelomgeving.
- Bestanden zijn groot en vereisen een bewuste distributiekeuze (licentie, versie-pinning).
- In deze fase wordt alleen de mapstructuur en documentatie voorbereid.

## Activeren in de build

1. Plaats `tesseract.exe`, bijbehorende DLL's en `tessdata/*.traineddata` in deze map, **of** laat CI ze stagen (zie `.github/workflows/build-windows.yml`).
2. De PyInstaller-spec bundelt automatisch wanneer `tesseract.exe` aanwezig is.
3. Bij startup roept `main.py` `logic.runtime_paths.configure_tesseract_runtime()` aan (`TESSDATA_PREFIX`, `pytesseract.tesseract_cmd`, `PATH`).

## Benodigde talen

| Backend | Taalparameter | traineddata |
|---------|---------------|-------------|
| PyMuPDF | `nld` | `nld.traineddata` |
| pytesseract | `nld+eng` | `nld.traineddata` + `eng.traineddata` |

Zie [`parser/pdf_parser.py`](../../parser/pdf_parser.py) voor de OCR-aanroepen.
