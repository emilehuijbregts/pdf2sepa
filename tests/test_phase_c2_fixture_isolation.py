"""Phase C2 — golden test files use isolated per-file fixtures (no cross-file coupling)."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

_FILES = {
    "extraction": _REPO / "tests" / "test_golden_extraction.py",
    "ranking": _REPO / "tests" / "test_golden_ranking.py",
    "decision": _REPO / "tests" / "test_golden_decision.py",
    "regression_lock": _REPO / "tests" / "test_golden_dataset.py",
}


def _fixture_names(path: Path) -> set[str]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            dec_src = ast.get_source_segment(src, dec) or ""
            if "fixture" in dec_src:
                names.add(node.name)
    return names


def test_phase_c2_per_file_fixture_names() -> None:
    extraction = _fixture_names(_FILES["extraction"])
    ranking = _fixture_names(_FILES["ranking"])
    decision = _fixture_names(_FILES["decision"])
    lock = _fixture_names(_FILES["regression_lock"])
    assert extraction == {"pipeline_output"}
    assert ranking == {"matched_by_pdf", "ranking_snapshot"}
    assert decision == {"pipeline_output"}
    assert lock == {"pipeline_output"}


def test_phase_c2_no_cross_file_fixture_imports() -> None:
    for label, path in _FILES.items():
        text = path.read_text(encoding="utf-8")
        for other in _FILES:
            if other == label:
                continue
            needle = f"from tests.test_golden_{other}"
            assert needle not in text, f"{path.name} imports fixture from {other}"
