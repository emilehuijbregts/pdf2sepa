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
import time
from difflib import SequenceMatcher
from pathlib import Path

from logic.settings import atomic_write
from logic.validation import mask_iban_for_log

logger = logging.getLogger(__name__)
_AGENT_DEBUG_LOG_PATH = Path("/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-791bb6.log")
_AGENT_DEBUG_SESSION_ID = "791bb6"


def _agent_debug_log(
    location: str,
    message: str,
    data: dict,
    *,
    hypothesis_id: str,
    run_id: str = "initial",
) -> None:
    # region agent log
    try:
        payload = {
            "sessionId": _AGENT_DEBUG_SESSION_ID,
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with _AGENT_DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # endregion


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
            vat_numbers = supplier.get("vat_numbers")
            if not isinstance(vat_numbers, list):
                vat_numbers = [vat_numbers] if vat_numbers else []
            supplier["vat_numbers"] = [
                self._normalize_vat_number(v) for v in vat_numbers if self._normalize_vat_number(v)
            ]
            supplier["_clean_vat_numbers"] = list(supplier["vat_numbers"])

            kvk_numbers = supplier.get("kvk_numbers")
            if not isinstance(kvk_numbers, list):
                kvk_numbers = [kvk_numbers] if kvk_numbers else []
            supplier["kvk_numbers"] = [
                self._normalize_kvk_number(k) for k in kvk_numbers if self._normalize_kvk_number(k)
            ]
            supplier["_clean_kvk_numbers"] = list(supplier["kvk_numbers"])

            email_domains = supplier.get("email_domains")
            if not isinstance(email_domains, list):
                email_domains = [email_domains] if email_domains else []
            supplier["email_domains"] = [
                self._normalize_email_domain(e)
                for e in email_domains
                if self._normalize_email_domain(e)
            ]
            supplier["_clean_email_domains"] = list(supplier["email_domains"])
        except Exception:
            # Optimization only; ignore
            supplier["_clean_name"] = ""
            supplier["_clean_aliases"] = []
            supplier["_clean_customer_codes"] = []
            supplier["_clean_vat_numbers"] = []
            supplier["_clean_kvk_numbers"] = []
            supplier["_clean_email_domains"] = []

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

    def _normalize_vat_number(self, vat: str) -> str:
        try:
            s = str(vat or "").upper()
            s = re.sub(r"\s+", "", s)
            return s
        except Exception:
            return ""

    def _normalize_kvk_number(self, kvk: str) -> str:
        try:
            digits = re.sub(r"\D", "", str(kvk or ""))
            if len(digits) in (7, 8):
                return digits
            return ""
        except Exception:
            return ""

    def _normalize_email_domain(self, dom: str) -> str:
        try:
            s = str(dom or "").strip().lower()
            s = re.sub(r"^www\.", "", s)
            if "@" in s:
                s = s.split("@", 1)[1]
            return s
        except Exception:
            return ""

    def find_supplier_scored(
        self,
        supplier_hint: str | None,
        iban: str | None,
        customer_number: str | None = None,
        *,
        vat_number: str | None = None,
        kvk_number: str | None = None,
        email_domain: str | None = None,
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
            "vat_match": False,
            "kvk_match": False,
            "email_domain_match": False,
        }

        try:
            iban_clean = self._clean_iban(iban) if iban else ""
            hint_clean = self._clean_name(supplier_hint) if supplier_hint else ""
            inv_cc = self._normalize_customer_code(str(customer_number)) if customer_number else ""
            inv_vat = self._normalize_vat_number(vat_number or "")
            inv_kvk = self._normalize_kvk_number(kvk_number or "")
            inv_dom = self._normalize_email_domain(email_domain or "")

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
                if inv_vat:
                    for v in (s.get("_clean_vat_numbers") or []):
                        if v and v == inv_vat:
                            info["vat_match"] = True
                            break
                if inv_kvk:
                    for k in (s.get("_clean_kvk_numbers") or []):
                        if k and k == inv_kvk:
                            info["kvk_match"] = True
                            break
                if inv_dom:
                    for d in (s.get("_clean_email_domains") or []):
                        if d and d == inv_dom:
                            info["email_domain_match"] = True
                            break

                n = sum([
                    info["iban_match"],
                    info["alias_match"],
                    info["customer_code_match"],
                    info["fuzzy_match"],
                    info["vat_match"],
                    info["kvk_match"],
                    info["email_domain_match"],
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
                supplier_hint,
                iban,
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
        default_payment_term_days: int = 0,
        vat_numbers: list | None = None,
        kvk_numbers: list | None = None,
        email_domains: list | None = None,
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
            if vat_numbers is None or not isinstance(vat_numbers, list):
                vat_numbers = []
            if kvk_numbers is None or not isinstance(kvk_numbers, list):
                kvk_numbers = []
            if email_domains is None or not isinstance(email_domains, list):
                email_domains = []

            # Optional sanity check (lightweight)
            if iban and not self._is_plausible_nl_iban(iban):
                logger.warning("IBAN lijkt geen NL-IBAN: %s", mask_iban_for_log(iban))

            # Duplicate prevention (strict): only exact IBAN or exact cleaned name.
            existing = None
            clean_name = self._clean_name(name)
            clean_iban = self._clean_iban(iban)
            for s in self.suppliers:
                s_name = self._clean_name(str(s.get("name") or ""))
                s_iban = self._clean_iban(str(s.get("iban") or ""))
                if clean_iban and s_iban and clean_iban == s_iban:
                    existing = s
                    break
                if clean_name and s_name and clean_name == s_name:
                    existing = s
                    break
            if existing:
                logger.info("Supplier '%s' al aanwezig, skip.", name)
                # region agent log
                _agent_debug_log(
                    "supplier_db.py:add_supplier:duplicate_skip",
                    "add_supplier geskipt door bestaande supplier",
                    {
                        "input_name": name,
                        "input_iban_present": bool(iban),
                        "matched_existing_name": str(existing.get("name") or ""),
                        "matched_existing_iban": str(existing.get("iban") or ""),
                        "duplicate_mode": "strict_exact_name_or_iban",
                    },
                    hypothesis_id="H7",
                    run_id="post-fix",
                )
                # endregion
                return

            # Ensure name in aliases
            aliases = [*aliases, name]
            aliases = [str(a).strip() for a in aliases if str(a or "").strip()]
            aliases = self._dedup_preserve_order(aliases)

            codes = [str(c).strip() for c in customer_codes if str(c or "").strip()]
            codes = self._dedup_preserve_order(codes)
            vats = [self._normalize_vat_number(v) for v in vat_numbers if self._normalize_vat_number(v)]
            vats = self._dedup_preserve_order(vats)
            kvks = [self._normalize_kvk_number(k) for k in kvk_numbers if self._normalize_kvk_number(k)]
            kvks = self._dedup_preserve_order(kvks)
            doms = [self._normalize_email_domain(e) for e in email_domains if self._normalize_email_domain(e)]
            doms = self._dedup_preserve_order(doms)

            try:
                discount_f = float(discount)
            except Exception:
                discount_f = 0.0
            try:
                term_d = int(default_payment_term_days)
                if term_d < 0:
                    term_d = 0
            except Exception:
                term_d = 0

            supplier = {
                "name": name,
                "iban": self._clean_iban(iban) if iban else "",
                "discount": discount_f,
                "aliases": aliases,
                "customer_codes": codes,
                "default_payment_term_days": term_d,
                "vat_numbers": vats,
                "kvk_numbers": kvks,
                "email_domains": doms,
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
        *,
        default_payment_term_days: int | None = None,
        vat_number: str | None = None,
        kvk_number: str | None = None,
        email_domain: str | None = None,
    ) -> bool:
        """
        Voeg leverancier toe, of merge klantcode in bestaande (match op IBAN, anders op naam).

        Retourneert ``True`` als opslaan is gelukt.
        """

        try:
            name = str(name or "").strip()
            iban = str(iban or "").strip()
            # region agent log
            _agent_debug_log(
                "supplier_db.py:merge_or_add_supplier:start",
                "merge_or_add gestart",
                {
                    "db_path": str(self.path),
                    "name": name,
                    "iban_present": bool(iban),
                    "customer_code": str(customer_code or "").strip(),
                },
                hypothesis_id="H5",
            )
            # endregion
            code_raw = str(customer_code or "").strip()
            vat_raw = self._normalize_vat_number(vat_number or "")
            kvk_raw = self._normalize_kvk_number(kvk_number or "")
            dom_raw = self._normalize_email_domain(email_domain or "")
            if not name or not iban:
                return False

            existing: dict | None = None
            existing_reason = ""
            ic = self._clean_iban(iban)
            if ic:
                for s in self.suppliers:
                    if self._clean_iban(s.get("iban") or "") == ic:
                        existing = s
                        existing_reason = "iban_exact"
                        break
            if existing is None:
                nc = self._clean_name(name)
                for s in self.suppliers:
                    if self._clean_name(s.get("name") or "") == nc:
                        existing = s
                        existing_reason = "name_exact"
                        break
            if existing is None:
                cand, match_info = self.find_supplier_scored(
                    name,
                    iban,
                    code_raw or None,
                    vat_number=vat_raw or None,
                    kvk_number=kvk_raw or None,
                    email_domain=dom_raw or None,
                )
                strong_identity = bool(
                    match_info.get("iban_match")
                    or (
                        match_info.get("customer_code_match")
                        and (
                            match_info.get("vat_match")
                            or match_info.get("kvk_match")
                            or match_info.get("email_domain_match")
                        )
                    )
                    or (match_info.get("vat_match") and match_info.get("kvk_match"))
                )
                # region agent log
                _agent_debug_log(
                    "supplier_db.py:merge_or_add_supplier:scored_candidate",
                    "Scored kandidaat voor merge_or_add beoordeeld",
                    {
                        "name": name,
                        "candidate_name": str(cand.get("name") or "") if cand else "",
                        "strong_identity": bool(strong_identity),
                        "match_info": match_info,
                    },
                    hypothesis_id="H10",
                    run_id="post-fix",
                )
                # endregion
                if cand is not None and strong_identity:
                    existing = cand
                    existing_reason = "scored_strong_identity"
            if existing is not None:
                # region agent log
                _agent_debug_log(
                    "supplier_db.py:merge_or_add_supplier:existing_selected",
                    "Bestaande supplier geselecteerd voor merge",
                    {
                        "input_name": name,
                        "existing_name": str(existing.get("name") or ""),
                        "reason": existing_reason or "unknown",
                        "has_customer_code": bool(code_raw),
                        "has_vat": bool(vat_raw),
                        "has_kvk": bool(kvk_raw),
                        "has_domain": bool(dom_raw),
                    },
                    hypothesis_id="H14",
                    run_id="post-fix",
                )
                # endregion
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
                try:
                    existing["discount"] = float(discount)
                except Exception:
                    pass
                if default_payment_term_days is not None:
                    try:
                        td = int(default_payment_term_days)
                        existing["default_payment_term_days"] = max(0, td)
                    except Exception:
                        pass
                if vat_raw:
                    vats = list(existing.get("vat_numbers") or [])
                    if vat_raw not in vats:
                        vats.append(vat_raw)
                    existing["vat_numbers"] = self._dedup_preserve_order(vats)
                if kvk_raw:
                    kvks = list(existing.get("kvk_numbers") or [])
                    if kvk_raw not in kvks:
                        kvks.append(kvk_raw)
                    existing["kvk_numbers"] = self._dedup_preserve_order(kvks)
                if dom_raw:
                    doms = list(existing.get("email_domains") or [])
                    if dom_raw not in doms:
                        doms.append(dom_raw)
                    existing["email_domains"] = self._dedup_preserve_order(doms)
                self._refresh_supplier_cache(existing)
                self.save()
                return True

            n_before = len(self.suppliers)
            # region agent log
            _agent_debug_log(
                "supplier_db.py:merge_or_add_supplier:add_attempt",
                "Geen bestaande supplier gekozen; poging nieuwe supplier toevoegen",
                {
                    "name": name,
                    "has_customer_code": bool(code_raw),
                    "has_vat": bool(vat_raw),
                    "has_kvk": bool(kvk_raw),
                    "has_domain": bool(dom_raw),
                },
                hypothesis_id="H15",
                run_id="post-fix",
            )
            # endregion
            self.add_supplier(
                name,
                iban,
                discount,
                aliases=[name],
                customer_codes=[code_raw] if code_raw else [],
                vat_numbers=[vat_raw] if vat_raw else [],
                kvk_numbers=[kvk_raw] if kvk_raw else [],
                email_domains=[dom_raw] if dom_raw else [],
            )
            added = len(self.suppliers) > n_before
            # region agent log
            _agent_debug_log(
                "supplier_db.py:merge_or_add_supplier:add_result",
                "Resultaat nieuwe supplier toevoegen",
                {"name": name, "added": bool(added)},
                hypothesis_id="H15",
                run_id="post-fix",
            )
            # endregion
            return added
        except Exception as e:
            # region agent log
            _agent_debug_log(
                "supplier_db.py:merge_or_add_supplier:exception",
                "merge_or_add exceptie",
                {
                    "db_path": str(self.path),
                    "name": str(name if "name" in locals() else ""),
                    "error": repr(e),
                },
                hypothesis_id="H5",
            )
            # endregion
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
                # region agent log
                _agent_debug_log(
                    "supplier_db.py:update_supplier:not_found",
                    "update_supplier leverancier niet gevonden",
                    {
                        "db_path": str(self.path),
                        "name": str(name),
                        "target_clean": target_clean,
                    },
                    hypothesis_id="H6",
                )
                # endregion
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

            if "default_payment_term_days" in kwargs:
                try:
                    td = int(kwargs.get("default_payment_term_days"))
                    supplier["default_payment_term_days"] = max(0, td)
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

            if "vat_numbers" in kwargs:
                overwrite_vat = bool(kwargs.get("overwrite_vat_numbers", False))
                new_vat = kwargs.get("vat_numbers")
                if not isinstance(new_vat, list):
                    new_vat = []
                clean_vat = [self._normalize_vat_number(v) for v in new_vat if self._normalize_vat_number(v)]
                if overwrite_vat:
                    merged_vat = clean_vat
                else:
                    existing_vat = supplier.get("vat_numbers") or []
                    if not isinstance(existing_vat, list):
                        existing_vat = []
                    merged_vat = [*existing_vat, *clean_vat]
                supplier["vat_numbers"] = self._dedup_preserve_order(merged_vat)

            if "kvk_numbers" in kwargs:
                overwrite_kvk = bool(kwargs.get("overwrite_kvk_numbers", False))
                new_kvk = kwargs.get("kvk_numbers")
                if not isinstance(new_kvk, list):
                    new_kvk = []
                clean_kvk = [self._normalize_kvk_number(v) for v in new_kvk if self._normalize_kvk_number(v)]
                if overwrite_kvk:
                    merged_kvk = clean_kvk
                else:
                    existing_kvk = supplier.get("kvk_numbers") or []
                    if not isinstance(existing_kvk, list):
                        existing_kvk = []
                    merged_kvk = [*existing_kvk, *clean_kvk]
                supplier["kvk_numbers"] = self._dedup_preserve_order(merged_kvk)

            if "email_domains" in kwargs:
                overwrite_dom = bool(kwargs.get("overwrite_email_domains", False))
                new_dom = kwargs.get("email_domains")
                if not isinstance(new_dom, list):
                    new_dom = []
                clean_dom = [self._normalize_email_domain(v) for v in new_dom if self._normalize_email_domain(v)]
                if overwrite_dom:
                    merged_dom = clean_dom
                else:
                    existing_dom = supplier.get("email_domains") or []
                    if not isinstance(existing_dom, list):
                        existing_dom = []
                    merged_dom = [*existing_dom, *clean_dom]
                supplier["email_domains"] = self._dedup_preserve_order(merged_dom)

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

