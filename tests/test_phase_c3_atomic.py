"""Phase C3 / Golden Suite v2 — verify golden concern tests use a single assert per test function."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

_GOLDEN_TEST_FILES = (
    _REPO / "tests" / "golden" / "extraction" / "test_golden_extraction_hard.py",
    _REPO / "tests" / "golden" / "ranking" / "test_golden_ranking_debug.py",
    _REPO / "tests" / "golden" / "decision" / "test_golden_decision_soft.py",
)


def _assert_count_in_function(node: ast.FunctionDef) -> int:
    count = 0
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            count += 1
    return count


def test_phase_c3_golden_tests_are_atomic() -> None:
    violations: list[str] = []
    for path in _GOLDEN_TEST_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not node.name.startswith("test_"):
                continue
            n = _assert_count_in_function(node)
            if n != 1:
                violations.append(f"{path.name}::{node.name} has {n} assert statements (expected 1)")
    assert violations == [], "Non-atomic golden tests:\n" + "\n".join(violations)
