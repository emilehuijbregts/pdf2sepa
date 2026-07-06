"""Generieke UI voor het kiezen van parser-veldkandidaten."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import QMenu, QWidget

from logic.validation import clean_iban, is_plausible_iban
from parser.field_adapters import field_result_from_amount, field_result_from_iban, field_result_from_ident
from parser.field_model import FieldId, FieldResult
from ui.field_review import CUSTOMER_ABSENT_MENU_LABEL_KEY, make_customer_absent_pick_candidate
from ui.i18n import tr


def field_result_from_snapshot(
    snap: dict[str, Any] | None,
    field_id: FieldId,
) -> FieldResult | None:
    if not isinstance(snap, dict):
        return None
    if field_id == "amount":
        return field_result_from_amount(snap)
    if field_id in ("invoice_number", "customer_number"):
        return field_result_from_ident(snap, field_id=field_id)
    if field_id == "iban":
        return field_result_from_iban(snap)
    return None


def filter_iban_menu_candidates(
    cands: list[Any],
) -> list[dict[str, Any]]:
    """Alleen mod-97 valide IBANs in het keuzemenu."""
    out: list[dict[str, Any]] = []
    for c in cands:
        if not isinstance(c, dict):
            continue
        val = clean_iban(str(c.get("value") or ""))
        if val and is_plausible_iban(val):
            row = dict(c)
            row["value"] = val
            out.append(row)
    return out


def picker_eligible(snap: dict[str, Any] | None, *, field_id: FieldId) -> bool:
    fr = field_result_from_snapshot(snap, field_id)
    if fr is None:
        return False
    if field_id == "amount":
        if fr.status not in ("ambiguous", "tentative", "failed"):
            return False
        return bool(fr.candidates)
    if field_id == "iban":
        if not fr.is_pickable:
            return False
        plausible = filter_iban_menu_candidates(
            [c.to_dict() if hasattr(c, "to_dict") else c for c in fr.candidates]
        )
        return len(plausible) >= 2
    if field_id == "customer_number":
        return bool(fr.candidates)
    return fr.is_pickable


def filter_amount_menu_candidates(
    cands: list[Any],
) -> list[dict[str, Any]]:
    """Amount menu: bij ≥2 incl.-kandidaten alleen incl.; anders alle kandidaten."""
    all_opts: list[dict[str, Any]] = [c for c in cands if isinstance(c, dict)]
    incl_opts = [c for c in all_opts if str(c.get("type") or "").lower() == "incl"]
    if len(incl_opts) >= 2:
        return incl_opts
    return all_opts


def build_field_candidate_menu(
    parent: QWidget,
    *,
    candidates: list[dict[str, Any]],
    format_label: Callable[[dict[str, Any]], str],
    on_pick: Callable[[dict[str, Any]], None],
    tooltip_from_candidate: Callable[[dict[str, Any]], str] | None = None,
) -> QMenu | None:
    menu = QMenu(parent)
    for cand in candidates:
        label = format_label(cand)
        act = menu.addAction(label)
        if tooltip_from_candidate:
            tip = tooltip_from_candidate(cand)
            if tip:
                act.setToolTip(tip)
        act.triggered.connect(lambda checked=False, c=cand: on_pick(c))
    if menu.isEmpty():
        return None
    return menu


def append_customer_absent_menu_action(
    menu: QMenu,
    *,
    on_pick: Callable[[], None],
) -> None:
    """Extra keuze onder klantnummer-kandidaten."""
    menu.addSeparator()
    act = menu.addAction(tr(CUSTOMER_ABSENT_MENU_LABEL_KEY))
    act.triggered.connect(lambda checked=False: on_pick())
