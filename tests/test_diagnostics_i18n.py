"""Tests for diagnostics i18n (no _NL dicts in logic)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from logic import diagnostics as diagnostics_mod
from logic import field_diagnostics as field_diag_mod
from ui.i18n import UiStrings, tr


@pytest.fixture(autouse=True)
def reset_language() -> None:
    yield
    UiStrings.set_language("nl")


def test_no_nl_dicts_in_diagnostics_modules() -> None:
    for path in (Path("logic/diagnostics.py"), Path("logic/field_diagnostics.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.endswith("_NL"):
                        raise AssertionError(f"{path}: found banned dict {target.id}")


def test_diagnostics_amount_status_via_keys() -> None:
    from logic.field_diagnostics import build_amount_diag_block

    snap = {"amount_result": {"status": "confirmed", "value": "10.00", "confidence": 90}}
    block = build_amount_diag_block(snap, reason_code="", warning_keys=[])
    assert block["status_nl"] == "diag.amount.status.confirmed"
    assert tr(block["status_nl"]) == "Bedrag gevonden met hoge zekerheid"


def test_match_status_key_in_build_diagnostics() -> None:
    diag = diagnostics_mod.build_diagnostics(
        {
            "supplier_name": "X",
            "match_status": "confirmed",
            "amount_result": {"status": "confirmed", "value": "1"},
        }
    )
    assert diag["supplier"]["status_nl"] == "diag.match.status.confirmed"
    assert tr(diag["supplier"]["status_nl"]).startswith("Leverancier")
