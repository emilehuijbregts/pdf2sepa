"""Phase C3 — verify golden concern tests use a single assert per test function."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

_GOLDEN_TEST_FILES = (
    _REPO / "tests" / "test_golden_extraction.py",
    _REPO / "tests" / "test_golden_ranking.py",
    _REPO / "tests" / "test_golden_decision.py",
)

_EXEMPT = frozenset({"test_02_golden_dataset_business_output"})


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
            if node.name in _EXEMPT:
                continue
            n = _assert_count_in_function(node)
            if n != 1:
                violations.append(f"{path.name}::{node.name} has {n} assert statements (expected 1)")
    assert violations == [], "Non-atomic golden tests:\n" + "\n".join(violations)
