# tessdata (placeholder)

Deze map is voorbereid voor gebundelde Tesseract-taalbestanden op Windows.

## Bestanden (later toevoegen)

| Bestand | Gebruik |
|---------|---------|
| `nld.traineddata` | PyMuPDF `get_textpage_ocr(language="nld")` |
| `eng.traineddata` | pytesseract fallback (`lang="nld+eng"`) |

## Nu nog leeg

Traineddata-bestanden worden bewust niet in de repository opgenomen (grootte, platform-specifiek). Ze worden later handmatig of via een build-stap in deze map geplaatst vóór de Windows-build.

Zie ook [`../README.md`](../README.md) en [`../../pdf2sepa.spec`](../../pdf2sepa.spec).
