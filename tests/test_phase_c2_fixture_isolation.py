"""Phase C2 / Golden Suite v2 — golden test files use isolated per-file fixtures."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

_FILES = {
    "extraction": _REPO / "tests" / "golden" / "extraction" / "test_golden_extraction_hard.py",
    "ranking": _REPO / "tests" / "golden" / "ranking" / "test_golden_ranking_debug.py",
    "decision": _REPO / "tests" / "golden" / "decision" / "test_golden_decision_soft.py",
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
            needle = f"from tests.golden.{other}"
            assert needle not in text, f"{path.name} imports fixture from {other}"
