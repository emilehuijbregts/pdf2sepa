"""
Supplier database beheer + robuuste matching logica.

- Beheert `data/suppliers.json`
- Faalt veilig bij ontbrekend/corrupte JSON
- Matching volgorde: IBAN (hard) → alias → fuzzy → klantcode

Let op: gebruikt alleen standaard libraries (json, difflib, re) zoals vereist.
"""

from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path

from logic.settings import atomic_write
from logic.validation import mask_iban_for_log

logger = logging.getLogger(__name__)


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
            codes = supplier.get("customer_codes")
            if not isinstance(codes, list):
                codes = []
            supplier["customer_codes"] = [
                str(c).strip() for c in codes if str(c or "").strip()
            ]
            supplier["_clean_customer_codes"] = [
                self._normalize_customer_code(c) for c in supplier["customer_codes"]
            ]
        except Exception:
            # Optimization only; ignore
            supplier["_clean_name"] = ""
            supplier["_clean_aliases"] = []
            supplier["_clean_customer_codes"] = []

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

    def _normalize_customer_code(self, code: str) -> str:
        """
        Normaliseer klant-/lidnummer voor vergelijking.

        Lange numerieke codes vergelijken we op cijfers alleen (1012146 vs 1.012.146).
        Korte of alfanumerieke codes: strip + lowercase.
        """

        try:
            s = str(code or "").strip()
            if not s:
                return ""
            digits = re.sub(r"\D", "", s)
            if len(digits) >= 4:
                return digits
            return s.casefold()
        except Exception:
            return ""

    def find_supplier_scored(
        self,
        supplier_hint: str | None,
        iban: str | None,
        customer_number: str | None = None,
    ) -> tuple[dict | None, dict]:
        """Find the best-matching supplier and report which characteristics matched.

        Returns:
            ``(supplier, match_info)`` where ``match_info`` contains booleans
            ``iban_match``, ``alias_match``, ``fuzzy_match``, ``customer_code_match``
            and a ``fuzzy_score`` float.
        """
        empty_info: dict = {
            "iban_match": False,
            "alias_match": False,
            "fuzzy_match": False,
            "fuzzy_score": 0.0,
            "customer_code_match": False,
        }

        try:
            iban_clean = self._clean_iban(iban) if iban else ""
            hint_clean = self._clean_name(supplier_hint) if supplier_hint else ""
            inv_cc = self._normalize_customer_code(str(customer_number)) if customer_number else ""

            scored: list[tuple[dict, dict, int]] = []

            for s in self.suppliers:
                info = dict(empty_info)

                if not isinstance(s.get("_clean_aliases"), list):
                    self._refresh_supplier_cache(s)

                if iban_clean:
                    sup_iban = self._clean_iban(s.get("iban") or "")
                    if sup_iban and iban_clean == sup_iban:
                        info["iban_match"] = True

                if hint_clean:
                    for a in (s.get("_clean_aliases") or []):
                        if a and (hint_clean == a or hint_clean in a or a in hint_clean):
                            info["alias_match"] = True
                            break

                if hint_clean and not info["alias_match"]:
                    name_clean = s.get("_clean_name") or ""
                    cands = [name_clean, *(s.get("_clean_aliases") or [])]
                    best = 0.0
                    for c in cands:
                        if not c:
                            continue
                        sc = SequenceMatcher(None, hint_clean, c).ratio()
                        if sc > best:
                            best = sc
                    info["fuzzy_score"] = best
                    if best >= 0.85:
                        info["fuzzy_match"] = True

                if inv_cc:
                    for cc in (s.get("_clean_customer_codes") or []):
                        if cc and cc == inv_cc:
                            info["customer_code_match"] = True
                            break

                n = sum([
                    info["iban_match"],
                    info["alias_match"],
                    info["customer_code_match"],
                    info["fuzzy_match"],
                ])
                if n > 0:
                    scored.append((s, info, n))

            if not scored:
                return None, dict(empty_info)

            scored.sort(key=lambda x: (x[2], x[1].get("fuzzy_score", 0)), reverse=True)
            best_supplier, best_info, _ = scored[0]
            return best_supplier, best_info

        except Exception:
            return None, dict(empty_info)

    def find_supplier(
        self,
        supplier_hint: str | None,
        iban: str | None,
        customer_number: str | None = None,
        *,
        match_customer_code: bool = True,
    ) -> dict | None:
        """Backward-compatible wrapper around ``find_supplier_scored``."""
        try:
            sup, _ = self.find_supplier_scored(
                supplier_hint, iban,
                customer_number if match_customer_code else None,
            )
            return sup
        except Exception:
            return None

    def add_supplier(
        self,
        name: str,
        iban: str,
        discount: float = 0.0,
        aliases: list | None = None,
        customer_codes: list | None = None,
    ):
        """
        Voeg een supplier toe en sla direct op.

        Regels:
        - name zit altijd in aliases
        - aliases=None → lege lijst
        - voorkom duplicates: ``find_supplier`` zonder klantcode-match
        - nooit crashen op slechte input
        """

        try:
            name = str(name or "").strip()
            iban = str(iban or "").strip()
            if aliases is None or not isinstance(aliases, list):
                aliases = []
            if customer_codes is None or not isinstance(customer_codes, list):
                customer_codes = []

            # Optional sanity check (lightweight)
            if iban and not self._is_plausible_nl_iban(iban):
                logger.warning("IBAN lijkt geen NL-IBAN: %s", mask_iban_for_log(iban))

            # Duplicate prevention (geen klantcode-match: voorkomt skip bij nieuwe code)
            if self.find_supplier(name, iban, None, match_customer_code=False):
                logger.info("Supplier '%s' al aanwezig, skip.", name)
                return

            # Ensure name in aliases
            aliases = [*aliases, name]
            aliases = [str(a).strip() for a in aliases if str(a or "").strip()]
            aliases = self._dedup_preserve_order(aliases)

            codes = [str(c).strip() for c in customer_codes if str(c or "").strip()]
            codes = self._dedup_preserve_order(codes)

            try:
                discount_f = float(discount)
            except Exception:
                discount_f = 0.0

            supplier = {
                "name": name,
                "iban": self._clean_iban(iban) if iban else "",
                "discount": discount_f,
                "aliases": aliases,
                "customer_codes": codes,
            }

            self.suppliers.append(supplier)
            self._refresh_supplier_cache(supplier)
            self.save()
        except Exception:
            return

    def merge_or_add_supplier(
        self,
        name: str,
        iban: str,
        customer_code: str | None = None,
        discount: float = 0.0,
    ) -> bool:
        """
        Voeg leverancier toe, of merge klantcode in bestaande (match op IBAN, anders op naam).

        Retourneert ``True`` als opslaan is gelukt.
        """

        try:
            name = str(name or "").strip()
            iban = str(iban or "").strip()
            code_raw = str(customer_code or "").strip()
            if not name or not iban:
                return False

            existing: dict | None = None
            ic = self._clean_iban(iban)
            if ic:
                for s in self.suppliers:
                    if self._clean_iban(s.get("iban") or "") == ic:
                        existing = s
                        break
            if existing is None:
                nc = self._clean_name(name)
                for s in self.suppliers:
                    if self._clean_name(s.get("name") or "") == nc:
                        existing = s
                        break

            if existing is not None:
                if code_raw:
                    merged = list(existing.get("customer_codes") or [])
                    if not isinstance(merged, list):
                        merged = []
                    merged = [str(x).strip() for x in merged if str(x or "").strip()]
                    if code_raw not in merged:
                        merged.append(code_raw)
                    existing["customer_codes"] = self._dedup_preserve_order(merged)
                if ic and not self._clean_iban(existing.get("iban") or ""):
                    existing["iban"] = ic
                self._refresh_supplier_cache(existing)
                self.save()
                return True

            n_before = len(self.suppliers)
            self.add_supplier(
                name,
                iban,
                discount,
                aliases=[name],
                customer_codes=[code_raw] if code_raw else [],
            )
            return len(self.suppliers) > n_before
        except Exception:
            return False

    def update_supplier(self, name: str, **kwargs) -> bool:
        """
        Update een bestaande supplier en sla direct op.

        - Zoek supplier op naam via `_clean_name` (geen bugs op hoofdletters/BV vs B.V./spaties).
        - Update velden:
          - iban (overwrite)
          - discount (overwrite)
          - aliases:
            - append (dedup) standaard
            - overwrite alleen als `overwrite_aliases=True`

        Retourneert ``True`` als een leverancier is gevonden en opgeslagen.
        """

        try:
            target_clean = self._clean_name(name)
            if not target_clean:
                return False

            supplier: dict | None = None
            for s in self.suppliers:
                if self._clean_name(s.get("name") or "") == target_clean:
                    supplier = s
                    break

            if supplier is None:
                return False

            # IBAN overwrite
            if "iban" in kwargs:
                try:
                    new_iban = kwargs.get("iban")
                    if new_iban is not None:
                        new_iban_s = str(new_iban).strip()
                        if new_iban_s and not self._is_plausible_nl_iban(new_iban_s):
                            logger.warning(
                                "IBAN lijkt geen NL-IBAN: %s",
                                mask_iban_for_log(new_iban_s),
                            )
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

            if "customer_codes" in kwargs:
                overwrite_cc = bool(kwargs.get("overwrite_customer_codes", False))
                new_cc = kwargs.get("customer_codes")
                if not isinstance(new_cc, list):
                    new_cc = []
                cleaned_cc = [str(c).strip() for c in new_cc if str(c or "").strip()]
                if overwrite_cc:
                    merged_cc = cleaned_cc
                else:
                    existing_cc = supplier.get("customer_codes") or []
                    if not isinstance(existing_cc, list):
                        existing_cc = []
                    merged_cc = [
                        *([str(x).strip() for x in existing_cc if str(x or "").strip()]),
                        *cleaned_cc,
                    ]
                supplier["customer_codes"] = self._dedup_preserve_order(merged_cc)

            self._refresh_supplier_cache(supplier)
            self.save()
            return True
        except Exception:
            return False

    def delete_supplier(self, name: str) -> bool:
        """Verwijder leverancier op canonieke naam. ``True`` als er iets verwijderd is."""
        try:
            target_clean = self._clean_name(name)
            if not target_clean:
                return False
            before = len(self.suppliers)
            self.suppliers = [
                s
                for s in self.suppliers
                if self._clean_name(s.get("name") or "") != target_clean
            ]
            if len(self.suppliers) == before:
                return False
            self._data["suppliers"] = self.suppliers
            self._rebuild_runtime_cache()
            self.save()
            return True
        except Exception:
            return False

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
                    if k in ("aliases", "customer_codes") and isinstance(v, list):
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
            text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
            atomic_write(Path(self.path), text)
        except Exception:
            logger.debug("Leveranciersbestand opslaan mislukt", exc_info=True)

