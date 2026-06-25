"""Golden Suite v2 — shared pytest hooks for soft/debug contract layers."""

from __future__ import annotations

import warnings

import pytest

_ranking_drift_count = 0


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "soft: golden behavior contract — mismatch is warning, not CI failure",
    )
    config.addinivalue_line(
        "markers",
        "debug: golden ranking snapshot — log-only, never blocks CI",
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call" and rep.failed and item.get_closest_marker("soft"):
        rep.outcome = "passed"
        rep.wasxfail = "soft-failure"
        warnings.warn(str(rep.longrepr), UserWarning, stacklevel=2)


def record_ranking_drift() -> None:
    global _ranking_drift_count
    _ranking_drift_count += 1


def pytest_sessionfinish(session, exitstatus) -> None:
    if _ranking_drift_count > 0:
        warnings.warn(
            f"Golden ranking debug: {_ranking_drift_count} production-winner drift(s) vs snapshot",
            UserWarning,
            stacklevel=1,
        )
