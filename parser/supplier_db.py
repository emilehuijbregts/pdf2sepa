"""
Supplier database beheer + robuuste matching logica.

- Beheert `data/suppliers.json`
- Faalt veilig bij ontbrekend/corrupte JSON
- Matching volgorde: IBAN (hard) → alias → fuzzy

Let op: gebruikt alleen standaard libraries (json, difflib, re) zoals vereist.
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher


class SupplierDB:
    """
    Kleine JSON-backed leveranciersdatabase.

    Interne runtime velden (zoals `_clean_aliases`) worden niet opgeslagen.
    """

    def __init__(self, path: str = "data/suppliers.json"):
        """
        Initialiseer DB en laad JSON.

        - Als bestand niet bestaat: maak aan met `{ "suppliers": [] }`
        - Als JSON corrupt is: reset naar lege structuur (fail-safe)
        - Nooit crashen: bij IO/JSON issues blijft DB bruikbaar in-memory.
        """

        self.path = path or "data/suppliers.json"
        self._data: dict = {"suppliers": []}
        self.suppliers: list[dict] = []

        self._load_or_init()

    def _load_or_init(self) -> None:
        """Laad leveranciersbestand fail-safe, of initialiseert lege structuur."""

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = f.read()
        except FileNotFoundError:
            self._data = {"suppliers": []}
            self.suppliers = []
            self._rebuild_runtime_cache()
            self.save()
            return
        except Exception:
            # IO error (permissions, etc.) → fail-safe in-memory
            self._data = {"suppliers": []}
            self.suppliers = []
            self._rebuild_runtime_cache()
            return

        try:
            parsed = json.loads(raw or "")
            if not isinstance(parsed, dict):
                raise ValueError("Top-level JSON is not a dict")
            suppliers = parsed.get("suppliers")
            if not isinstance(suppliers, list):
                raise ValueError('"suppliers" is not a list')
            self._data = {"suppliers": suppliers}
            self.suppliers = suppliers
        except Exception:
            # Corrupt/invalid JSON → reset and persist
            self._data = {"suppliers": []}
            self.suppliers = []
            self._rebuild_runtime_cache()
            self.save()
            return

        self._rebuild_runtime_cache()

    def _rebuild_runtime_cache(self) -> None:
        """(Re)bouw runtime caches voor alle suppliers."""

        try:
            for s in self.suppliers:
                self._refresh_supplier_cache(s)
        except Exception:
            # No crash: caches zijn een optimization
            pass

    def _refresh_supplier_cache(self, supplier: dict) -> None:
        """
        Cache cleaned name/aliases op supplier dict (runtime-only).

        Dit helpt `find_supplier(...)` sneller en leesbaarder te maken.
        """

        try:
            name = supplier.get("name") or ""
            aliases = supplier.get("aliases") or []
            if not isinstance(aliases, list):
                aliases = []

            supplier["_clean_name"] = self._clean_name(str(name))
            supplier["_clean_aliases"] = [self._clean_name(str(a)) for a in aliases if a]
        except Exception:
            # Optimization only; ignore
            supplier["_clean_name"] = ""
            supplier["_clean_aliases"] = []

    def _clean_iban(self, iban: str) -> str:
        """
        Normaliseer IBAN voor vergelijking.

        - uppercase
        - verwijder whitespace (spaties, tabs, newlines)
        """

        try:
            s = str(iban or "")
            s = re.sub(r"\s+", "", s)
            return s.upper().strip()
        except Exception:
            return ""

    def _is_plausible_nl_iban(self, iban: str) -> bool:
        """
        Lightweight sanity check (optioneel).

        Doel: rommel-IBANs verminderen. Dit is geen volledige IBAN validatie.
        """

        c = self._clean_iban(iban)
        return bool(c) and c.startswith("NL")

    def _clean_name(self, text: str) -> str:
        """
        Normaliseer leveranciernaam/alias voor matching.

        Moet:
        - lowercase maken
        - leading rommel verwijderen (zoals "2/2", "1/3", etc.)
        - leestekens vervangen door spatie (niet verwijderen)
        - extra spaties normaliseren
        - alleen letters + spaties overhouden

        Voorbeeld:
        - "2/2 Wavin Nederland B.V." -> "wavin nederland bv"
        - "Wavin-Nederland" -> "wavin nederland"
        """

        try:
            s = str(text or "").strip().lower()
            if not s:
                return ""

            # Remove leading "2/2", "1/3", etc.
            s = re.sub(r"^\s*\d+\s*/\s*\d+\s+", "", s)

            # Replace punctuation/other non-letters with spaces (prevents word-sticking)
            s = re.sub(r"[^a-z\s]", " ", s)

            # Collapse whitespace and strip
            s = re.sub(r"\s+", " ", s).strip()

            # Merge common Dutch legal form tokens split by punctuation removal
            # e.g. "b.v." -> "b v" -> "bv"
            s = re.sub(r"\bb\s+v\b", "bv", s)
            s = re.sub(r"\bn\s+v\b", "nv", s)
            s = re.sub(r"\s+", " ", s).strip()
            return s
        except Exception:
            return ""

    def _dedup_preserve_order(self, items: list) -> list:
        """Dedup een lijst, orde behouden, fail-safe."""

        try:
            seen: set[str] = set()
            out: list = []
            for x in items or []:
                key = str(x)
                if key not in seen:
                    seen.add(key)
                    out.append(x)
            return out
        except Exception:
            return []

    def find_supplier(self, supplier_hint: str | None, iban: str | None) -> dict | None:
        """
        Zoek supplier met EXACT deze volgorde:

        1) IBAN match (HARD RULE)
           - als iban niet None: loop suppliers, als IBAN matcht → direct return
           - IBAN vergelijking gebeurt altijd via `_clean_iban(...)`

        2) Alias match
           - als supplier_hint bestaat: clean hint en check aliases (substring of exact)

        3) Fuzzy match
           - SequenceMatcher ratio op cleaned hint vs cleaned name + aliases
           - beste score bepaalt match
           - match alleen als best_supplier != None én best_score >= 0.80

        Geen match → None.
        """

        try:
            # Step 1 — IBAN match (hard rule)
            if iban is not None:
                iban_clean = self._clean_iban(iban)
                if iban_clean:
                    for s in self.suppliers:
                        sup_iban = self._clean_iban(s.get("iban") or "")
                        if iban_clean and sup_iban and iban_clean == sup_iban:
                            return s

            # Step 2 — Alias match
            if supplier_hint:
                hint_clean = self._clean_name(supplier_hint)
                if hint_clean:
                    for s in self.suppliers:
                        aliases_clean = s.get("_clean_aliases")
                        if not isinstance(aliases_clean, list):
                            self._refresh_supplier_cache(s)
                            aliases_clean = s.get("_clean_aliases") or []

                        for a_clean in aliases_clean:
                            if not a_clean:
                                continue
                            if hint_clean == a_clean or (hint_clean in a_clean) or (a_clean in hint_clean):
                                return s

            # Step 3 — Fuzzy match
            if supplier_hint:
                hint_clean = self._clean_name(supplier_hint)
                if not hint_clean:
                    return None

                best_supplier: dict | None = None
                best_score: float = -1.0

                for s in self.suppliers:
                    name_clean = s.get("_clean_name")
                    aliases_clean = s.get("_clean_aliases")
                    if not isinstance(name_clean, str) or not isinstance(aliases_clean, list):
                        self._refresh_supplier_cache(s)
                        name_clean = s.get("_clean_name") or ""
                        aliases_clean = s.get("_clean_aliases") or []

                    candidates = [name_clean, *aliases_clean]
                    for cand in candidates:
                        if not cand:
                            continue
                        score = SequenceMatcher(None, hint_clean, cand).ratio()
                        if score > best_score:
                            best_score = score
                            best_supplier = s

                # Safety: alleen matchen als er echt een kandidaat is en score >= 0.80
                if best_supplier is not None and best_score >= 0.80:
                    # TODO: later confidence level opslaan voor UI
                    return best_supplier

                return None

            return None
        except Exception:
            return None

    def add_supplier(self, name: str, iban: str, discount: float = 0.0, aliases: list | None = None):
        """
        Voeg een supplier toe en sla direct op.

        Regels:
        - name zit altijd in aliases
        - aliases=None → lege lijst
        - voorkom duplicates: als `find_supplier(name, iban)` iets oplevert → skip (optioneel log)
        - nooit crashen op slechte input
        """

        try:
            name = str(name or "").strip()
            iban = str(iban or "").strip()
            if aliases is None or not isinstance(aliases, list):
                aliases = []

            # Optional sanity check (lightweight)
            if iban and not self._is_plausible_nl_iban(iban):
                print(f"[SupplierDB] Waarschuwing: IBAN lijkt geen NL-IBAN: {iban!r}")

            # Duplicate prevention
            if self.find_supplier(supplier_hint=name, iban=iban):
                print(f"[SupplierDB] Supplier '{name}' al aanwezig, skip.")
                return

            # Ensure name in aliases
            aliases = [*aliases, name]
            aliases = [str(a).strip() for a in aliases if str(a or "").strip()]
            aliases = self._dedup_preserve_order(aliases)

            try:
                discount_f = float(discount)
            except Exception:
                discount_f = 0.0

            supplier = {
                "name": name,
                "iban": self._clean_iban(iban) if iban else "",
                "discount": discount_f,
                "aliases": aliases,
            }

            self.suppliers.append(supplier)
            self._refresh_supplier_cache(supplier)
            self.save()
        except Exception:
            return

    def update_supplier(self, name: str, **kwargs):
        """
        Update een bestaande supplier en sla direct op.

        - Zoek supplier op naam via `_clean_name` (geen bugs op hoofdletters/BV vs B.V./spaties).
        - Update velden:
          - iban (overwrite)
          - discount (overwrite)
          - aliases:
            - append (dedup) standaard
            - overwrite alleen als `overwrite_aliases=True`
        """

        try:
            target_clean = self._clean_name(name)
            if not target_clean:
                return

            supplier: dict | None = None
            for s in self.suppliers:
                if self._clean_name(s.get("name") or "") == target_clean:
                    supplier = s
                    break

            if supplier is None:
                return

            # IBAN overwrite
            if "iban" in kwargs:
                try:
                    new_iban = kwargs.get("iban")
                    if new_iban is not None:
                        new_iban_s = str(new_iban).strip()
                        if new_iban_s and not self._is_plausible_nl_iban(new_iban_s):
                            print(f"[SupplierDB] Waarschuwing: IBAN lijkt geen NL-IBAN: {new_iban_s!r}")
                        supplier["iban"] = self._clean_iban(new_iban_s) if new_iban_s else ""
                except Exception:
                    pass

            # Discount overwrite
            if "discount" in kwargs:
                try:
                    supplier["discount"] = float(kwargs.get("discount"))
                except Exception:
                    pass

            # Aliases update
            if "aliases" in kwargs:
                overwrite = bool(kwargs.get("overwrite_aliases", False))
                new_aliases = kwargs.get("aliases")
                if not isinstance(new_aliases, list):
                    new_aliases = []

                cleaned_new_aliases = [str(a).strip() for a in new_aliases if str(a or "").strip()]
                if overwrite:
                    merged = cleaned_new_aliases
                else:
                    existing = supplier.get("aliases") or []
                    if not isinstance(existing, list):
                        existing = []
                    merged = [*existing, *cleaned_new_aliases]

                # Ensure name always present
                supplier_name = str(supplier.get("name") or "").strip()
                if supplier_name:
                    merged.append(supplier_name)

                supplier["aliases"] = self._dedup_preserve_order([a for a in merged if a])

            self._refresh_supplier_cache(supplier)
            self.save()
        except Exception:
            return

    def get_all(self) -> list:
        """
        Geef alle suppliers terug als kopieën (voorkomt side-effects).

        `aliases` wordt ook gekopieerd zodat callers intern state niet muteren.
        Runtime cache keys worden niet meegegeven.
        """

        try:
            out: list[dict] = []
            for s in self.suppliers:
                d = {}
                for k, v in (s or {}).items():
                    if isinstance(k, str) and k.startswith("_clean"):
                        continue
                    if k == "aliases" and isinstance(v, list):
                        d[k] = list(v)
                    else:
                        d[k] = v
                out.append(d)
            return out
        except Exception:
            return []

    def save(self) -> None:
        """
        Schrijf JSON netjes terug naar disk.

        - indent=2, ensure_ascii=False
        - runtime cache velden worden niet opgeslagen
        - alle errors worden afgevangen (no-crash)
        """

        try:
            payload = {"suppliers": self.get_all()}
            text = json.dumps(payload, indent=2, ensure_ascii=False)
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(text)
                f.write("\n")
        except Exception:
            return

