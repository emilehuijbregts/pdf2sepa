"""Worker slot thinness guards."""

from __future__ import annotations

import ast
from pathlib import Path


def test_worker_only_calls_pipeline() -> None:
    source = Path("ui/workers/invoice_batch_load_worker.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    banned = {"iban_ui_mapping", "iban_resolution_engine", "resolve_iban_context"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for b in banned:
                assert b not in node.module


def test_worker_slot_bodies_are_thin() -> None:
    source = Path("ui/workers/invoice_batch_load_worker.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("run_"):
            body_lines = len(node.body)
            assert body_lines <= 15, f"{node.name} too large ({body_lines} statements)"
