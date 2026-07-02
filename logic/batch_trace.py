"""Structured batch trace logging for payment/settlement pipeline debugging."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from logic.engine_result import EngineResult
    from logic.shadow_mode import ShadowReport

logger = logging.getLogger(__name__)


def log_batch_stage(stage: str, **counts: Any) -> None:
    parts = " ".join(f"{key}={value}" for key, value in sorted(counts.items()))
    line = f"BATCH_TRACE stage={stage} {parts}".strip()
    logger.info(line)


def log_batch_summary(
    *,
    input_invoices: int,
    settlement_groups: int,
    review_documents: int,
    ui_rows: int | None = None,
    pipeline: str = "",
    extra: str = "",
) -> str:
    line = (
        f"INPUT: {input_invoices} invoices | "
        f"SETTLEMENT_GROUPS: {settlement_groups} | "
        f"REVIEW_DOCUMENTS: {review_documents}"
    )
    if ui_rows is not None:
        line += f" | UI_ROWS: {ui_rows}"
    if pipeline:
        line += f" | PIPELINE: {pipeline}"
    if extra:
        line += f" | {extra}"
    logger.info("BATCH_SUMMARY %s", line)
    return line


def validate_no_credit_batch_invariants(
    *,
    settlement_groups: int,
    allocation_edges: int,
    n_invoices: int,
    n_groups: int,
) -> None:
    """Hard check: no-credit batches must not group or allocate."""
    assert allocation_edges == 0, f"expected no allocation edges, got {allocation_edges}"
    assert settlement_groups == n_groups, (
        f"expected {n_groups} settlement groups, got {settlement_groups}"
    )
    if n_groups > 0:
        assert n_groups == n_invoices, (
            f"expected one group per invoice ({n_invoices}), got {n_groups}"
        )


_LEGACY_PAYMENT_FORBIDDEN_KEYS = (
    "settlement_group_id",
    "settlement_status",
    "settlement",
)


def assert_legacy_output_isolation(result: "EngineResult") -> None:
    """Hard check: legacy EngineResult must not carry settlement artifacts."""
    assert result.pipeline == "legacy", f"expected legacy pipeline, got {result.pipeline!r}"
    assert result.legacy_payments is not None, "legacy_payments required on legacy path"
    assert len(result.settlement_groups) == 0, (
        f"legacy path must not emit settlement groups, got {len(result.settlement_groups)}"
    )
    for payment in result.legacy_payments:
        for key in _LEGACY_PAYMENT_FORBIDDEN_KEYS:
            val = payment.get(key)
            if val not in (None, "", [], {}):
                raise AssertionError(f"legacy payment contains forbidden key {key!r}: {val!r}")
        credits_applied = payment.get("credit_notes_applied")
        if credits_applied:
            raise AssertionError(
                f"legacy payment contains credit_notes_applied: {credits_applied!r}"
            )


def log_shadow_result(report: ShadowReport) -> str:
    """Emit SHADOW_MODE_TEST log block for a shadow validation report."""
    lines = [
        f"SHADOW_MODE_TEST BATCH_ID={report.batch_id} BATCH_TYPE={report.batch_type}",
    ]
    if report.batch_type == "no-credit":
        lines.append(
            f"LEGACY_ROWS={report.legacy_rows} SETTLEMENT_ROWS={report.settlement_rows} "
            f"REVIEW_DOCS={report.review_docs}"
        )
        if report.diffs:
            lines.append("DIFF:")
            lines.extend(f"- {diff}" for diff in report.diffs)
        else:
            lines.append("DIFF=NONE")
        lines.append(
            f"PIPELINE_MATCH={'TRUE' if report.pipeline_match else 'FALSE'} "
            f"STATUS={report.status}"
        )
    else:
        lines.append(f"SETTLEMENT_ROWS={report.settlement_rows}")
        extra = report.extra or {}
        determinism = extra.get("determinism", "UNKNOWN")
        coverage = extra.get("coverage", "UNKNOWN")
        lines.append(f"DETERMINISM={determinism} COVERAGE={coverage} STATUS={report.status}")
        if report.diffs:
            lines.append("DIFF:")
            lines.extend(f"- {diff}" for diff in report.diffs)
    text = "\n".join(lines)
    logger.info("%s", text)
    return text
