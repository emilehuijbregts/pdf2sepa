"""
Verify script voor PDF2SEPA - Module 1 checks.
Voer uit met: python verify.py
"""

from __future__ import annotations

import json
import sys
from importlib import import_module
from pathlib import Path


def _print_result(ok: bool, message: str) -> None:
    icon = "✅" if ok else "❌"
    print(f"{icon} {message}")


def _check_python_version() -> bool:
    ok = sys.version_info >= (3, 9)
    _print_result(ok, f"Python versie is {sys.version.split()[0]} (vereist: 3.9+)")
    return ok


def _check_import(module_name: str) -> bool:
    try:
        import_module(module_name)
    except Exception as e:
        _print_result(False, f"Package '{module_name}' is NIET geïnstalleerd of niet importeerbaar ({e.__class__.__name__})")
        return False

    _print_result(True, f"Package '{module_name}' is geïnstalleerd en importeerbaar")
    return True


def _check_dir(base: Path, name: str) -> bool:
    p = base / name
    ok = p.is_dir()
    _print_result(ok, f"Map bestaat: {name}/")
    return ok


def _check_file(base: Path, rel_path: str) -> bool:
    p = base / rel_path
    ok = p.is_file()
    _print_result(ok, f"Bestand bestaat: {rel_path}")
    return ok


def _check_suppliers_json(base: Path) -> bool:
    p = base / "data" / "suppliers.json"
    if not p.is_file():
        _print_result(False, "data/suppliers.json ontbreekt")
        return False

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        _print_result(False, f"data/suppliers.json is geen geldige JSON ({e.__class__.__name__})")
        return False

    if not isinstance(data, dict):
        _print_result(False, "data/suppliers.json: top-level is geen object (dict)")
        return False

    suppliers = data.get("suppliers")
    if not isinstance(suppliers, list):
        _print_result(False, 'data/suppliers.json: sleutel "suppliers" ontbreekt of is geen lijst')
        return False

    # Basale structuurvalidatie (geen corrupte structuur)
    for idx, s in enumerate(suppliers):
        if not isinstance(s, dict):
            _print_result(False, f"data/suppliers.json: suppliers[{idx}] is geen object (dict)")
            return False
        if not isinstance(s.get("name"), str) or not s.get("name", "").strip():
            _print_result(False, f"data/suppliers.json: suppliers[{idx}].name ontbreekt/leeg of is geen string")
            return False
        if "iban" not in s or not isinstance(s.get("iban"), str):
            _print_result(False, f"data/suppliers.json: suppliers[{idx}].iban ontbreekt of is geen string")
            return False
        aliases = s.get("aliases")
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            _print_result(False, f"data/suppliers.json: suppliers[{idx}].aliases ontbreekt of is geen lijst van strings")
            return False

    _print_result(True, 'data/suppliers.json is valide (JSON + "suppliers" structuur)')
    return True


def _remove_test_supplier_entries(db, test_name: str, test_iban: str) -> int:
    """
    Verwijder alle entries die overeenkomen met test supplier.

    We matchen op:
    - cleaned name (via db._clean_name)
    - cleaned iban (via db._clean_iban)
    """

    try:
        name_clean = db._clean_name(test_name)
        iban_clean = db._clean_iban(test_iban)

        before = len(db.suppliers)
        kept = []
        for s in db.suppliers:
            try:
                s_name_clean = db._clean_name(s.get("name") or "")
                s_iban_clean = db._clean_iban(s.get("iban") or "")
                if (name_clean and s_name_clean == name_clean) or (iban_clean and s_iban_clean == iban_clean):
                    continue
                kept.append(s)
            except Exception:
                kept.append(s)

        db.suppliers = kept
        removed = before - len(db.suppliers)
        # SupplierDB gebruikt db.suppliers als onderliggende lijst; zorg dat caches geen issues geven
        try:
            db._rebuild_runtime_cache()
        except Exception:
            pass
        return removed
    except Exception:
        return 0


def main() -> int:
    base = Path(__file__).resolve().parent

    results: list[bool] = []

    results.append(_check_python_version())

    results.append(_check_import("pdfplumber"))
    results.append(_check_import("lxml"))
    results.append(_check_import("customtkinter"))

    for d in ["ui", "parser", "logic", "output", "data", "logs"]:
        results.append(_check_dir(base, d))

    for f in [
        "ui/__init__.py",
        "parser/__init__.py",
        "logic/__init__.py",
        "output/__init__.py",
    ]:
        results.append(_check_file(base, f))

    results.append(_check_suppliers_json(base))

    print()
    print("[ Module 3 — Supplier Database ]")

    # 1) Bestanden bestaan
    results.append(_check_file(base, "parser/supplier_db.py"))
    results.append(_check_file(base, "parser/supplier_matcher.py"))

    # 2) Import test
    SupplierDB = None
    try:
        from parser.supplier_db import SupplierDB as _SupplierDB  # type: ignore

        SupplierDB = _SupplierDB
        _print_result(True, "Import OK: from parser.supplier_db import SupplierDB")
        results.append(True)
    except Exception as e:
        _print_result(False, f"Import FAIL: SupplierDB niet importeerbaar ({e.__class__.__name__})")
        results.append(False)

    test_name = "Test BV"
    test_iban = "NL00TEST0000000000"
    db = None
    added_in_this_run = False

    # 3–8) DB load, add, match, cleanup (cleanup altijd)
    try:
        if SupplierDB is None:
            raise RuntimeError("SupplierDB import failed")

        # 3) Database load (instantie maken, geen crash)
        try:
            db = SupplierDB(path=str(base / "data" / "suppliers.json"))
            _print_result(True, "Database load OK: SupplierDB instantie gemaakt")
            results.append(True)
        except Exception as e:
            _print_result(False, f"Database load FAIL: crash bij instantiëren ({e.__class__.__name__})")
            results.append(False)
            db = None
            return 1 if not all(results) else 0

        # Vooraf opschonen: verwijder eventuele bestaande test entries (idempotent)
        try:
            removed_pre = _remove_test_supplier_entries(db, test_name, test_iban)
            if removed_pre:
                db.save()
        except Exception:
            removed_pre = 0

        # 4) Test supplier toevoegen
        try:
            db.add_supplier(test_name, test_iban)
            added_in_this_run = True
            _print_result(True, f'Test supplier toegevoegd: "{test_name}" ({test_iban})')
            results.append(True)
        except Exception as e:
            _print_result(False, f"Test supplier toevoegen FAIL ({e.__class__.__name__})")
            results.append(False)

        # 5) Test IBAN match
        try:
            m = db.find_supplier(None, test_iban)
            ok = isinstance(m, dict) and (m.get("name") == test_name)
            _print_result(ok, f'IBAN match {"OK" if ok else "FAIL"}: find_supplier(None, "{test_iban}")')
            results.append(ok)
        except Exception as e:
            _print_result(False, f"IBAN match FAIL ({e.__class__.__name__})")
            results.append(False)

        # 6) Test naam match
        try:
            m = db.find_supplier(test_name, None)
            ok = isinstance(m, dict) and (m.get("name") == test_name)
            _print_result(ok, f'Naam match {"OK" if ok else "FAIL"}: find_supplier("{test_name}", None)')
            results.append(ok)
        except Exception as e:
            _print_result(False, f"Naam match FAIL ({e.__class__.__name__})")
            results.append(False)

        # 7) Test fuzzy match
        try:
            m = db.find_supplier("Test", None)
            ok = isinstance(m, dict) and (m.get("name") == test_name)
            _print_result(ok, 'Fuzzy match ' + ("OK" if ok else "FAIL") + ': find_supplier("Test", None)')
            results.append(ok)
        except Exception as e:
            _print_result(False, f"Fuzzy match FAIL ({e.__class__.__name__})")
            results.append(False)

    finally:
        # 8) Cleanup + save (altijd)
        if db is not None:
            try:
                removed = _remove_test_supplier_entries(db, test_name, test_iban)
                db.save()
                ok = True
                msg = f'Cleanup OK: "{test_name}" verwijderd (removed={removed}) en save() uitgevoerd'
            except Exception as e:
                ok = False
                msg = f'Cleanup FAIL: kon "{test_name}" niet verwijderen/saven ({e.__class__.__name__})'
            _print_result(ok, msg)
            results.append(ok)
        else:
            _print_result(False, "Cleanup FAIL: database was niet geladen, geen cleanup mogelijk")
            results.append(False)

    # 9) JSON check na afloop
    results.append(_check_suppliers_json(base))

    all_ok = all(results)
    print()
    if all_ok:
        print("✅ Verify OK: Module 1 + Module 3 checks zijn geslaagd.")
        return 0
    print("❌ Verify FAIL: Los de ❌ checks hierboven op.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

