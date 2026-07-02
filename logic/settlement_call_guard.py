"""Track settlement-pipeline calls during engine runs for isolation guards."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

logger = logging.getLogger(__name__)

_GUARD_ACTIVE: ContextVar[bool] = ContextVar("settlement_guard_active", default=False)
_CALL_COUNTS: ContextVar[dict[str, int]] = ContextVar("settlement_call_counts", default={})
_LAST_COUNTS: dict[str, int] = {}

_TRACKED_CALLS = frozenset(
    {
        "enrich_credit_documents",
        "compute_settlement_groups",
        "settlement_pipeline",
    }
)


def _strict_isolation_enabled() -> bool:
    return os.environ.get("PDF2SEPA_STRICT_ISOLATION", "").strip() in ("1", "true", "yes")


@contextmanager
def settlement_call_guard() -> Iterator[None]:
    """Activate call counting for one calculate_payments invocation."""
    token_active = _GUARD_ACTIVE.set(True)
    token_counts = _CALL_COUNTS.set({name: 0 for name in _TRACKED_CALLS})
    try:
        yield
    finally:
        global _LAST_COUNTS
        _LAST_COUNTS = dict(_CALL_COUNTS.get())
        _GUARD_ACTIVE.reset(token_active)
        _CALL_COUNTS.reset(token_counts)


def record_settlement_call(name: str) -> None:
    if not _GUARD_ACTIVE.get():
        return
    counts = dict(_CALL_COUNTS.get())
    counts[name] = counts.get(name, 0) + 1
    _CALL_COUNTS.set(counts)


def settlement_call_counts() -> dict[str, int]:
    return dict(_CALL_COUNTS.get())


def last_settlement_call_counts() -> dict[str, int]:
    return dict(_LAST_COUNTS)


def assert_zero_settlement_calls(*, context: str = "") -> None:
    """Assert no settlement code ran during a legacy no-credit production path."""
    counts = settlement_call_counts()
    violations = {name: n for name, n in counts.items() if n > 0}
    if not violations:
        return
    msg = f"settlement calls in legacy path{f' ({context})' if context else ''}: {violations}"
    if _strict_isolation_enabled():
        raise AssertionError(msg)
    logger.error("ISOLATION_VIOLATION %s", msg)


def allocation_edges_from_result(settlement_groups: list[dict]) -> int:
    total = 0
    for group in settlement_groups:
        alloc = group.get("credit_allocation") or []
        if isinstance(alloc, list):
            total += len(alloc)
    return total
