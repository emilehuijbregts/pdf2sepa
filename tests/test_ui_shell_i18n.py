"""i18n contract tests for virtual UI shell helpers."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from ui.i18n import UiStrings, tr
from ui.settlement_badges import settlement_badge_label
from ui.settlement_expand import _settlement_warning_message


@pytest.fixture(autouse=True)
def reset_language() -> None:
    yield
    UiStrings.set_language("nl")


def test_settlement_badges_no_nl_dict() -> None:
    path = Path("ui/settlement_badges.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.endswith("_NL"):
                    raise AssertionError(f"banned dict {target.id}")


def test_settlement_badge_labels_via_tr() -> None:
    assert settlement_badge_label("zero_amount") == "Volledig verrekend"
    assert settlement_badge_label("detached") == "Losgekoppeld"


def test_settlement_warning_generic() -> None:
    from ui.settlement_view import SettlementGroupVM, SettlementLineVM

    vm = SettlementGroupVM(
        group_id="g1",
        supplier_name="X",
        customer_number="",
        description="",
        final_amount_due="0",
        settlement_status="manual_review",
        exportable=False,
        invoices=(),
        credits=(
            SettlementLineVM(
                doc_type="credit_note",
                invoice_number="CN1",
                gross_amount="10.00",
                amount_applied="0",
                remaining_balance="10.00",
            ),
        ),
        allocations=(),
        invoices_total="0",
        credits_total="10.00",
    )
    msg = _settlement_warning_message(vm)
    assert msg is not None
    assert "CN1" in msg
    assert "verrekend" in msg


def test_main_window_virtual_shell_functions_no_nl_literals() -> None:
    source = Path("main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    banned = re.compile(r'"(Instellingen|Map selecteren|Bevestig factuur|Export geblokkeerd)"')
    for fn_name in ("_setup_ui", "_on_table_context_menu", "_on_make_xml"):
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == fn_name)
        segment = ast.get_source_segment(source, fn) or ""
        assert banned.search(segment) is None, fn_name
        assert "tr(" in segment
