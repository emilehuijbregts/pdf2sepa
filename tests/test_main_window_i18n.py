"""i18n contract tests for main_window shell strings."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from ui.i18n import UiStrings, tr


@pytest.fixture(autouse=True)
def reset_language() -> None:
    yield
    UiStrings.set_language("nl")


_UI_TITLE_CALLS = {
    "QMessageBox.warning",
    "QMessageBox.information",
    "QMessageBox.critical",
    "QMessageBox.question",
    "QMessageBox.about",
    "QPushButton",
    "QMenu",
    "QProgressDialog",
}

_ALLOWLIST_LITERALS = frozenset({"+", "\u2212", "?", "\u2699", "\U0001f50d", "fout", "ok"})

_BANNED_NL_PATTERNS = re.compile(
    r"(Instellingen|Leverancier|Bevestig|Exporteren|Profiel opslaan|Map selecteren|Mijn leveranciers)"
)


def _is_tr_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr"


def _first_string_arg(call: ast.Call) -> ast.Constant | None:
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first
    return None


def test_main_window_qmessagebox_titles_use_tr() -> None:
    source = Path("main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Attribute):
            name = f"{func.value.id}.{func.attr}" if isinstance(func.value, ast.Name) else func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if name not in _UI_TITLE_CALLS:
            continue
        if not node.args:
            continue
        title_node = node.args[0]
        if _is_tr_call(title_node) or isinstance(title_node, ast.Name):
            continue
        if isinstance(title_node, ast.Constant) and isinstance(title_node.value, str):
            if not title_node.value or title_node.value in _ALLOWLIST_LITERALS:
                continue
            offenders.append(f"{name} title literal: {title_node.value!r}")
    assert not offenders, "\n".join(offenders)


def test_shell_nl_snapshots_exact() -> None:
    assert tr("toolbar.export_xml") == "Maak XML bestand"
    assert tr("table.header.supplier") == "Leverancier"
    assert tr("dialog.settings.title") == "Instellingen"
    assert tr("menu.context.choose_amount") == "Kies bedrag\u2026"
    assert tr("status.export_started") == "XML generatie gestart \u2026"


def test_setup_ui_contains_tr_calls() -> None:
    source = Path("main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_setup_ui")
    body = ast.get_source_segment(source, fn) or ""
    assert "tr(" in body
    assert _BANNED_NL_PATTERNS.search(body) is None
