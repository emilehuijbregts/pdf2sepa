"""Phase B8 — verify G5 dead code is removed and unused."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_REMOVED_SYMBOLS = (
    "_generic_is_strong",
    "_generic_is_weak",
    "_db_master_conflict_winner",
    "_pick_best_override",
    "_ambiguous_tie_trace",
    "prefer_label_over_resolved",
)


def _defined_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            names.add(node.name)
        if isinstance(node, ast.arg):
            names.add(node.arg)
    return names


def test_g5_dead_code_symbols_are_absent() -> None:
    resolver = _REPO_ROOT / "parser" / "field_resolver.py"
    candidates = _REPO_ROOT / "parser" / "field_candidates.py"
    resolver_names = _defined_names(resolver)
    candidate_names = _defined_names(candidates)
    assert "_pick_best_override" not in resolver_names
    assert "_generic_is_strong" not in resolver_names
    assert "_generic_is_weak" not in resolver_names
    assert "_db_master_conflict_winner" not in resolver_names
    assert "_ambiguous_tie_trace" not in candidate_names
    assert "prefer_label_over_resolved" not in candidate_names

    for rel in ("parser/field_resolver.py", "parser/field_candidates.py", "parser/hybrid_field_apply.py"):
        text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        for symbol in _REMOVED_SYMBOLS:
            assert symbol not in text, f"{symbol} still referenced in {rel}"
