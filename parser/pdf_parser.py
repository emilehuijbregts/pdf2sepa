from __future__ import annotations

import re
from typing import Any

import pdfplumber


def extract_text(file_path: str) -> str:
    """Extracteer alle tekst uit een PDF (alle pagina's), samengevoegd met newlines."""
    try:
        pages_text: list[str] = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                pages_text.append(page.extract_text() or "")
        return "\n".join(pages_text)
    except Exception:
        return ""


def normalize_amount(amount_str: str | None) -> float | None:
    """Normaliseer een bedragstring (EU-notatie) naar `float` of `None`."""
    try:
        if not amount_str:
            return None

        s = str(amount_str).strip()
        if not s:
            return None

        # Remove currency markers and whitespace
        s = re.sub(r"(?i)\bEUR\b", "", s)
        s = s.replace("€", "")
        s = re.sub(r"\s+", "", s)

        # Keep only digits and separators and minus sign
        s = re.sub(r"[^0-9,.\-]", "", s)
        if not s or s in {"-", ".", ",", "-.", "-,"}:
            return None

        # Determine decimal separator by last occurrence of '.' or ','
        last_dot = s.rfind(".")
        last_comma = s.rfind(",")
        sep_idx = max(last_dot, last_comma)
        if sep_idx == -1:
            return None

        int_part = s[:sep_idx]
        dec_part = s[sep_idx + 1 :]
        if not dec_part:
            return None

        # Remove thousands separators from integer part
        sign = ""
        if int_part.startswith("-"):
            sign = "-"
            int_part = int_part[1:]

        int_part = int_part.replace(".", "").replace(",", "")
        if not int_part.isdigit():
            return None

        dec_part = re.sub(r"[^0-9]", "", dec_part)
        if not dec_part.isdigit():
            return None

        normalized = f"{sign}{int_part}.{dec_part}"
        return float(normalized)
    except Exception:
        return None


def build_description(customer_number: str | None, invoice_number: str | None) -> str | None:
    """Bouw description als `{customer_number} / {invoice_number}` wanneer beide bestaan."""
    try:
        if customer_number and invoice_number:
            return f"{customer_number} / {invoice_number}"
        return None
    except Exception:
        return None


def extract_invoice_data(text: str | None) -> dict[str, Any]:
    """
    Parseer ruwe PDF-tekst naar een Module 3-ready JSON dict.

    Let op: bestaande extractie-logica voor IBAN/amount/type/customer_number wordt niet overschreven;
    extra fallbacks/hints vullen alleen ontbrekende waarden aan.
    """
    text = text or ""

    iban: str | None = None
    amount: float | None = None
    invoice_number: str | None = None
    customer_number: str | None = None
    supplier_hint: str | None = None

    # IBAN
    try:
        # Matcht: `iban`
        m_iban = re.search(r"\bNL\d{2}[A-Z]{4}\d{10}\b", text)
        if m_iban:
            iban = m_iban.group(0)
            print(f"IBAN gevonden: {iban}")
        else:
            print("IBAN niet gevonden")
    except Exception:
        print("IBAN niet gevonden")
        iban = None

    # Amount (pick highest)
    try:
        # Matcht: `amount` (bruto candidates; hoogste wordt gekozen)
        amount_matches = re.findall(r"\d{1,3}(?:[.,]\d{3})*[.,]\d{2}", text)
        normalized_amounts: list[float] = []
        for a in amount_matches:
            v = normalize_amount(a)
            if isinstance(v, float):
                normalized_amounts.append(v)
        if normalized_amounts:
            amount = max(normalized_amounts)
            print(f"Bedrag gevonden: {amount}")
        else:
            print("Bedrag niet gevonden")
    except Exception:
        print("Bedrag niet gevonden")
        amount = None

    # Invoice number
    try:
        # Matcht: `invoice_number`
        m_inv = re.search(
            r"(Factuurnummer|Factuurnr\.?|Invoice|Ref)[\s:]*([A-Za-z0-9\-\/]+)",
            text,
            flags=re.IGNORECASE,
        )
        if m_inv:
            invoice_number = m_inv.group(2)
            print(f"Factuurnummer gevonden: {invoice_number}")
        else:
            print("Factuurnummer niet gevonden")
    except Exception:
        print("Factuurnummer niet gevonden")
        invoice_number = None

    # Customer number
    try:
        # Matcht: `customer_number`
        m_cust = re.search(r"(?i)(?:Klant\s*nr\.?|Klantnr|Debiteurnummer|Lidnummer|Customer\s*number)[\s\.:]*([0-9]+)", text)
        if m_cust:
            customer_number = m_cust.group(1)
            print(f"Klantnummer gevonden: {customer_number}")
        else:
            print("Klantnummer niet gevonden")
    except Exception:
        print("Klantnummer niet gevonden")
        customer_number = None

    # Fallback: betaalreferentie "invoice / customer" (vult alleen ontbrekende velden aan)
    try:
        # Matcht: `invoice_number`, `customer_number` (als `123 / 456`)
        if customer_number is None or invoice_number is None:
            m_ref = re.search(r"(\d+)\s*/\s*(\d+)", text)
            if m_ref:
                prev_invoice = invoice_number
                prev_customer = customer_number

                invoice_number = invoice_number or (m_ref.group(1) or "").strip() or None
                customer_number = customer_number or (m_ref.group(2) or "").strip() or None

                if prev_customer is None and customer_number:
                    print(f"Klantnummer gevonden via betaalreferentie: {customer_number}")
                if prev_invoice is None and invoice_number:
                    print(f"Factuurnummer gevonden via betaalreferentie: {invoice_number}")
    except Exception:
        pass

    # Supplier hint (heuristiek; vult alleen aan)
    try:
        # Matcht: `supplier_hint`
        if supplier_hint is None:
            from parser.supplier_rules import extract_supplier_name_hint

            supplier_hint = extract_supplier_name_hint(text)
            if supplier_hint:
                print(f"Supplier hint gevonden: {supplier_hint}")
            else:
                supplier_hint = None
                print("Supplier hint niet gevonden")
    except Exception:
        supplier_hint = None
        print("Supplier hint niet gevonden")

    # Type
    try:
        # Matcht: `type`
        if re.search(r"\b(creditnota|credit note|credit|CREN)\b", text, flags=re.IGNORECASE):
            doc_type = "credit_note"
        else:
            doc_type = "invoice"
        print(f"Type: {doc_type}")
    except Exception:
        doc_type = "invoice"
        print(f"Type: {doc_type}")

    # Description
    try:
        description = build_description(customer_number, invoice_number)
        if description:
            print(f"Description gemaakt: {description}")
        else:
            print("Description niet gemaakt")
    except Exception:
        description = None
        print("Description niet gemaakt")

    # Debug: gemiste velden voor troubleshooting
    try:
        missing: list[str] = []
        if iban is None:
            missing.append("iban")
        if amount is None:
            missing.append("amount")
        if invoice_number is None:
            missing.append("invoice_number")
        if customer_number is None:
            missing.append("customer_number")
        if description is None:
            missing.append("description")
        if supplier_hint is None:
            missing.append("supplier_hint")
        if missing:
            print(f"Gemiste velden: {', '.join(missing)}")
    except Exception:
        pass

    return {
        "iban": iban,
        "amount": amount,
        "invoice_number": invoice_number,
        "customer_number": customer_number,
        "description": description,
        "type": doc_type,
        "supplier_hint": supplier_hint,
        "raw_text": text,
    }

