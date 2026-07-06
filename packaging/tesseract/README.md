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

1. Plaats `tesseract.exe`, bijbehorende DLL's en `tessdata/*.traineddata` in deze map.
2. Uncomment de Tesseract-sectie in [`../pdf2sepa.spec`](../pdf2sepa.spec).
3. Implementeer in een vervolgfase runtime-wiring in de applicatie:
   - `TESSDATA_PREFIX` → parent van `tessdata/`
   - `pytesseract.pytesseract.tesseract_cmd` → pad naar `tesseract.exe`
   - `PATH` zodat PyMuPDF `get_textpage_ocr` Tesseract kan vinden
   - `logic/runtime_paths.tesseract_path()` voor logging en consistentie

## Benodigde talen

| Backend | Taalparameter | traineddata |
|---------|---------------|-------------|
| PyMuPDF | `nld` | `nld.traineddata` |
| pytesseract | `nld+eng` | `nld.traineddata` + `eng.traineddata` |

Zie [`parser/pdf_parser.py`](../../parser/pdf_parser.py) voor de OCR-aanroepen.
