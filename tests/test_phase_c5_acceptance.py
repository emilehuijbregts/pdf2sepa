"""Phase C5 — verify regression lock and Phase C acceptance."""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ACCEPTANCE_PATH = REPO / "reports" / "phase_c_acceptance.json"
RUNTIME_PATH = REPO / "reports" / "phase_c_runtime.json"
PRE_PHASE_C_BASE = "b23714c"


def _test_02_source() -> str | None:
    src = (REPO / "tests" / "test_golden_dataset.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "test_02_golden_dataset_business_output":
            return ast.get_source_segment(src, node)
    return None


def _pre_phase_c_test_02_source() -> str | None:
    pre = subprocess.check_output(
        ["git", "show", f"{PRE_PHASE_C_BASE}:tests/test_golden_dataset.py"],
        text=True,
    )
    tree = ast.parse(pre)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "test_02_golden_dataset_business_output":
            return ast.get_source_segment(pre, node)
    return None


def test_phase_c5_test_02_regression_lock_preserved() -> None:
    pre = _pre_phase_c_test_02_source()
    cur = _test_02_source()
    assert pre is not None and cur is not None
    assert pre == cur, "test_02_golden_dataset_business_output body changed during Phase C"


def test_phase_c5_no_production_code_changed() -> None:
    out = subprocess.check_output(
        ["git", "diff", f"{PRE_PHASE_C_BASE}..HEAD", "--", "parser/", "logic/", "main_window.py"],
        text=True,
        cwd=REPO,
    )
    assert out.strip() == "", "Production code changed during Phase C"


def test_phase_c5_acceptance_report() -> None:
    assert ACCEPTANCE_PATH.is_file(), "Missing reports/phase_c_acceptance.json"
    report = json.loads(ACCEPTANCE_PATH.read_text(encoding="utf-8") or "{}")
    assert report.get("production_code_changed") is False
    assert report.get("test_02_preserved") is True
    assert report.get("recommendation") == "Phase C complete"
    split = report.get("split_files") or []
    assert "tests/test_golden_extraction.py" in split
    assert "tests/test_golden_ranking.py" in split
    assert "tests/test_golden_decision.py" in split


def test_phase_c5_runtime_report() -> None:
    assert RUNTIME_PATH.is_file(), "Missing reports/phase_c_runtime.json"
    report = json.loads(RUNTIME_PATH.read_text(encoding="utf-8") or "{}")
    targets = report.get("targets") or {}
    assert targets.get("extraction_warm_cache_under_60_sec") is True
    assert targets.get("ranking_under_600_sec") is True
    assert targets.get("decision_under_900_sec") is True
    assert targets.get("per_concern_feedback_under_300_sec") is True
    runs = report.get("golden_concern_runs_sec") or {}
    assert runs.get("golden_regression_lock_sec") is not None
    assert report.get("exit_codes", {}).get("golden_regression_lock_sec") == 1
