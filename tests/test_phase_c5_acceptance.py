"""Golden Suite v2 — verify contract-layer structure and acceptance metadata."""

from __future__ import annotations

import ast
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ACCEPTANCE_PATH = REPO / "reports" / "phase_c_acceptance.json"

V2_SPLIT_FILES = (
    "tests/golden/extraction/test_golden_extraction_hard.py",
    "tests/golden/decision/test_golden_decision_soft.py",
    "tests/golden/ranking/test_golden_ranking_debug.py",
)


def _test_02_has_skip_marker() -> bool:
    src = (REPO / "tests" / "test_golden_dataset.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "test_02_golden_dataset_business_output":
            continue
        for dec in node.decorator_list:
            dec_src = ast.get_source_segment(src, dec) or ""
            if "skip" in dec_src:
                return True
    return False


def test_golden_v2_test_02_migrated_to_skip() -> None:
    assert _test_02_has_skip_marker(), "test_02 must be skipped; checks live in tests/golden/"


def test_golden_v2_split_files_exist() -> None:
    missing = [rel for rel in V2_SPLIT_FILES if not (REPO / rel).is_file()]
    assert missing == [], f"Missing Golden Suite v2 files: {missing}"


def test_golden_v2_acceptance_report() -> None:
    assert ACCEPTANCE_PATH.is_file(), "Missing reports/phase_c_acceptance.json"
    report = json.loads(ACCEPTANCE_PATH.read_text(encoding="utf-8") or "{}")
    assert report.get("suite") == "golden_v2"
    assert report.get("blocking_gate") == "tests/golden/extraction/"
    split = report.get("split_files") or []
    for path in V2_SPLIT_FILES:
        assert path in split
