from parser.pdf_parser import extract_text, extract_invoice_data
from parser.supplier_rules import detect_supplier, extract_supplier_name_hint
from pprint import pprint


def run_quick_string_cases() -> None:
    samples = [
        "Klant nr: 1012146",
        "Klantnr 1012146",
        "Klant nr. 1012146",
        "Debiteurnummer: 1012146",
        "Lidnummer: 1012146",
        "Customer number: 1012146",
        "7012254003 / 1012146",
        "Factuurnummer: INV-123\n7012254003 / 1012146",
        "Acme B.V.\nFactuurnummer: 7012254003\nKlant nr: 1012146",
    ]

    for s in samples:
        print("\n--- SAMPLE ---")
        print(s)
        data = extract_invoice_data(s)
        print(
            {
                "invoice_number": data.get("invoice_number"),
                "customer_number": data.get("customer_number"),
                "description": data.get("description"),
                "supplier_hint": data.get("supplier_hint"),
            }
        )


def main() -> None:
    pdf_path = input("Pad naar PDF: ").strip().strip('"').strip("'")

    if not pdf_path:
        run_quick_string_cases()
        return

    text = extract_text(pdf_path)
    print(text[:500])

    data = extract_invoice_data(text)
    pprint(data)

    supplier = detect_supplier(text, [])
    print(supplier)

    hint = extract_supplier_name_hint(text)
    print(hint)


if __name__ == "__main__":
    main()
