from __future__ import annotations

from parser.supplier_db import SupplierDB


def match_suppliers(invoices: list[dict], db: SupplierDB) -> list[dict]:
    out: list[dict] = []

    for invoice in invoices:
        invoice_copy = invoice.copy()

        supplier = db.find_supplier(
            invoice.get("supplier_hint"),
            invoice.get("iban"),
        )

        if supplier:
            invoice_copy["supplier_name"] = supplier["name"]
            invoice_copy["discount"] = supplier.get("discount", 0.0)
            invoice_copy["match_status"] = "matched"

            if invoice.get("iban") and supplier.get("iban"):
                if db._clean_iban(invoice["iban"]) != db._clean_iban(supplier["iban"]):
                    invoice_copy["iban_mismatch"] = True
        else:
            invoice_copy["supplier_name"] = None
            invoice_copy["discount"] = 0.0

            if invoice.get("supplier_hint"):
                invoice_copy["match_status"] = "unmatched"
            else:
                invoice_copy["match_status"] = "no_hint"

        out.append(invoice_copy)

    return out

