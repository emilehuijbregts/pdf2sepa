"""AST guards for UI layer contract."""

from __future__ import annotations

import ast
from pathlib import Path


def test_main_window_no_domain_imports() -> None:
    source = Path("main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    banned = {"iban_ui_mapping", "map_raw_ui_answers_to_decisions", "resolve_iban_context"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and any(b in node.module for b in banned):
                raise AssertionError(f"banned import: {node.module}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(b in alias.name for b in banned):
                    raise AssertionError(f"banned import: {alias.name}")


def test_main_window_no_domain_choice_literals() -> None:
    source = Path("main_window.py").read_text(encoding="utf-8")
    assert "keep_db" not in source
    assert "use_pdf" not in source


def test_collect_iban_raw_answers_no_business_conditionals() -> None:
    source = Path("main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_collect_iban_raw_answers"
    )
    banned_names = {"iban_mismatch", "pdf_iban", "db_iban", "supplier_name"}
    for node in ast.walk(fn):
        if isinstance(node, ast.If):
            for name in banned_names:
                seg = ast.get_source_segment(source, node.test) or ""
                assert name not in seg, f"business conditional on {name}"


def test_main_window_ui_calls_avoid_raw_dutch_titles() -> None:
    source = Path("main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    banned_titles = {
        "Instellingen",
        "Leveranciers",
        "Profiel opslaan",
        "Export geblokkeerd",
        "Map selecteren",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"warning", "information", "critical", "question", "about"}:
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "QMessageBox":
            continue
        if len(node.args) < 2:
            continue
        title = node.args[0]
        if isinstance(title, ast.Constant) and isinstance(title.value, str):
            assert title.value not in banned_titles, f"raw QMessageBox title: {title.value!r}"
