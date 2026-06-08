from __future__ import annotations

import sys
from pathlib import Path


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "golden: golden dataset concern-split tests (Phase C)")
    config.addinivalue_line("markers", "golden_slow: golden tests that load PDF folder pipeline")

    """
    Ensure pytest uses the same vendored dependencies as headless scripts.

    Some scripts (e.g. saving golden datasets) add `.deps/` to `sys.path` so that
    PDF parsing dependencies are consistent and deterministic. Pytest, by default,
    does not. This can cause subtle mismatches in parsing/matching outcomes.
    """

    app_base = Path(__file__).resolve().parents[1]
    deps = app_base / ".deps"
    if deps.exists() and deps.is_dir():
        deps_str = str(deps)
        if deps_str not in sys.path:
            # Prepend so vendored deps take precedence over site-packages.
            sys.path.insert(0, deps_str)

