"""
Phase 5 strategy regression guard — read-only protection layer.

Captures and compares strategy-engine snapshots under evaluation_mode=True.
Validates deployment bundle structural compatibility against the engine contract.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from parser.profile_strategy_engine import StrategyFieldResult

from parser.field_model import FieldId

BASELINE_VERSION = 1
CONFIDENCE_EPSILON = 0.02
FRAGILE_CONFIDENCE_THRESHOLD = 0.75

CORE_FIELDS: tuple[FieldId, ...] = (
    "amount",
    "invoice_number",
    "customer_number",
    "iban",
)

KNOWN_SEMANTIC_ADJUSTMENT_KEYS = frozenset(
    {
        "incl_btw_boost",
        "payable_label_boost",
        "totaal_anchor_boost",
        "vat_line_penalty",
        "excl_without_payable_penalty",
        "multi_amount_penalty",
    }
)

ALLOWED_SEMANTIC_FIELD_KEYS = frozenset({"enabled", "adjustments", "trigger_observed"})

RegressionDriftType = Literal[
    "winner",
    "candidate",
    "confidence",
    "breakdown",
    "missing",
    "extra",
]

RegressionTag = Literal["stable", "fragile", "changed_behavior", "new_strategy"]


class StrategyRegressionError(Exception):
    """Raised when snapshot comparison detects engine behavior drift."""


class BundleCompatibilityError(Exception):
    """Raised when a deployment bundle violates the engine contract."""


@dataclass
class RegressionSnapshot:
    pdf_id: str
    field_id: str
    candidate: str | None
    winner: str | None
    confidence: float
    confidence_breakdown: dict[str, float] = field(default_factory=dict)
    semantic_score: dict[str, Any] | None = None

    def key(self) -> tuple[str, str]:
        return (self.pdf_id, self.field_id)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegressionSnapshot:
        return cls(
            pdf_id=str(data["pdf_id"]),
            field_id=str(data["field_id"]),
            candidate=data.get("candidate"),
            winner=data.get("winner"),
            confidence=float(data.get("confidence") or 0.0),
            confidence_breakdown={
                str(k): float(v) for k, v in (data.get("confidence_breakdown") or {}).items()
            },
            semantic_score=data.get("semantic_score"),
        )


@dataclass
class RegressionDiff:
    pdf_id: str
    field_id: str
    drift_type: RegressionDriftType
    baseline: RegressionSnapshot | None
    current: RegressionSnapshot | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pdf_id": self.pdf_id,
            "field_id": self.field_id,
            "drift_type": self.drift_type,
            "baseline": self.baseline.to_dict() if self.baseline else None,
            "current": self.current.to_dict() if self.current else None,
            "detail": self.detail,
        }


@dataclass
class RegressionBaseline:
    version: int
    generated_at: str
    golden_hash: str
    bundle_fingerprint: dict[str, Any]
    snapshots: list[RegressionSnapshot] = field(default_factory=list)
    capture_engine: str | None = None
    capture_subprocess: bool | None = None
    capture_import_graph_audit_passed: bool | None = None
    capture_execution_state_diffing: bool | None = None
    capture_preconditions: dict[str, Any] | None = None
    engine_fingerprint: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "version": self.version,
            "generated_at": self.generated_at,
            "golden_hash": self.golden_hash,
            "bundle_fingerprint": self.bundle_fingerprint,
            "snapshots": [s.to_dict() for s in self.snapshots],
        }
        if self.capture_engine is not None:
            out["capture_engine"] = self.capture_engine
        if self.capture_subprocess is not None:
            out["capture_subprocess"] = self.capture_subprocess
        if self.capture_import_graph_audit_passed is not None:
            out["capture_import_graph_audit_passed"] = self.capture_import_graph_audit_passed
        if self.capture_execution_state_diffing is not None:
            out["capture_execution_state_diffing"] = self.capture_execution_state_diffing
        if self.capture_preconditions is not None:
            out["capture_preconditions"] = dict(self.capture_preconditions)
        if self.engine_fingerprint is not None:
            out["engine_fingerprint"] = dict(self.engine_fingerprint)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegressionBaseline:
        snapshots = [RegressionSnapshot.from_dict(s) for s in (data.get("snapshots") or [])]
        return cls(
            version=int(data.get("version") or BASELINE_VERSION),
            generated_at=str(data.get("generated_at") or ""),
            golden_hash=str(data.get("golden_hash") or ""),
            bundle_fingerprint=dict(data.get("bundle_fingerprint") or {}),
            snapshots=snapshots,
            capture_engine=data.get("capture_engine"),
            capture_subprocess=data.get("capture_subprocess"),
            capture_import_graph_audit_passed=data.get("capture_import_graph_audit_passed"),
            capture_execution_state_diffing=data.get("capture_execution_state_diffing"),
            capture_preconditions=dict(data.get("capture_preconditions") or {})
            if data.get("capture_preconditions")
            else None,
            engine_fingerprint=dict(data.get("engine_fingerprint") or {})
            if data.get("engine_fingerprint")
            else None,
        )


def normalize_candidate(field_id: FieldId | str, value: Any) -> str | None:
    if value is None:
        return None
    if field_id == "amount":
        try:
            return str(Decimal(str(value)).quantize(Decimal("0.01")))
        except Exception:
            return str(value)
    return str(value).strip()


def _winner_breakdown(result: StrategyFieldResult) -> dict[str, float]:
    winner = result.strategy_used
    for attempt in reversed(result.all_attempted_strategies):
        if attempt.strategy == winner and attempt.status == "valid":
            return dict(attempt.confidence_breakdown)
    return {}


def capture_strategy_snapshot(
    pdf_id: str,
    field_id: FieldId | str,
    result: StrategyFieldResult,
    *,
    semantic_score: dict[str, Any] | None = None,
) -> RegressionSnapshot:
    """Build a regression snapshot from a strategy engine result."""
    return RegressionSnapshot(
        pdf_id=pdf_id,
        field_id=str(field_id),
        candidate=normalize_candidate(field_id, result.value),
        winner=result.strategy_used,
        confidence=float(result.confidence or 0.0),
        confidence_breakdown=_winner_breakdown(result),
        semantic_score=semantic_score,
    )


def capture_snapshot_from_row(row: dict[str, Any]) -> RegressionSnapshot:
    """Build a snapshot from a golden runner result row."""
    field_id = str(row.get("field") or "")
    semantic = row.get("semantic_score")
    return RegressionSnapshot(
        pdf_id=str(row.get("pdf") or ""),
        field_id=field_id,
        candidate=normalize_candidate(field_id, row.get("actual")),
        winner=row.get("strategy_used"),
        confidence=float(row.get("confidence") or 0.0),
        confidence_breakdown={
            str(k): float(v) for k, v in (row.get("confidence_breakdown") or {}).items()
        },
        semantic_score=semantic if isinstance(semantic, dict) else None,
    )


def snapshots_from_golden_results(
    results: list[dict[str, Any]],
    *,
    semantic_scoring: dict[str, Any] | None = None,
) -> list[RegressionSnapshot]:
    """Extract snapshots for all evaluable successes."""
    out: list[RegressionSnapshot] = []
    for row in results:
        if row.get("status") != "success":
            continue
        field_id = str(row.get("field") or "")
        semantic = None
        if semantic_scoring and isinstance(semantic_scoring.get(field_id), dict):
            entry = semantic_scoring[field_id]
            if entry.get("enabled"):
                semantic = dict(entry)
        snap = capture_snapshot_from_row(row)
        if semantic is not None:
            snap.semantic_score = semantic
        out.append(snap)
    return out


def build_bundle_fingerprint(bundle: dict[str, Any]) -> dict[str, Any]:
    """Stable structural fingerprint (schema/keys, not float tuning values)."""
    patch = bundle.get("patch") if isinstance(bundle.get("patch"), dict) else {}
    orders = patch.get("order") if isinstance(patch.get("order"), dict) else {}
    order_fp: dict[str, list[str]] = {}
    for field_id in sorted(orders.keys()):
        order = orders[field_id]
        if isinstance(order, list):
            order_fp[str(field_id)] = sorted(str(s) for s in order)

    semantic = bundle.get("semantic_scoring") if isinstance(bundle.get("semantic_scoring"), dict) else {}
    schema: dict[str, Any] = {}
    for field_id in sorted(semantic.keys()):
        entry = semantic[field_id]
        if not isinstance(entry, dict):
            continue
        enabled = bool(entry.get("enabled"))
        adj = entry.get("adjustments") if isinstance(entry.get("adjustments"), dict) else {}
        schema[str(field_id)] = {
            "enabled": enabled,
            "adjustment_keys": sorted(str(k) for k in adj.keys()) if enabled else [],
        }

    return {
        "version": int(bundle.get("version") or 0),
        "order": order_fp,
        "semantic_scoring_schema": schema,
    }


def _index_snapshots(snapshots: list[RegressionSnapshot]) -> dict[tuple[str, str], RegressionSnapshot]:
    return {s.key(): s for s in snapshots}


def compare_snapshots(
    before: list[RegressionSnapshot],
    after: list[RegressionSnapshot],
) -> list[RegressionDiff]:
    """Diff two snapshot runs keyed by (pdf_id, field_id)."""
    base_map = _index_snapshots(before)
    curr_map = _index_snapshots(after)
    diffs: list[RegressionDiff] = []
    all_keys = sorted(set(base_map.keys()) | set(curr_map.keys()))

    for key in all_keys:
        pdf_id, field_id = key
        baseline = base_map.get(key)
        current = curr_map.get(key)

        if baseline is None and current is not None:
            diffs.append(
                RegressionDiff(
                    pdf_id=pdf_id,
                    field_id=field_id,
                    drift_type="extra",
                    baseline=None,
                    current=current,
                    detail="snapshot present in current run but absent in baseline",
                )
            )
            continue
        if baseline is not None and current is None:
            diffs.append(
                RegressionDiff(
                    pdf_id=pdf_id,
                    field_id=field_id,
                    drift_type="missing",
                    baseline=baseline,
                    current=None,
                    detail="snapshot present in baseline but absent in current run",
                )
            )
            continue
        if baseline is None or current is None:
            continue

        if baseline.winner != current.winner:
            diffs.append(
                RegressionDiff(
                    pdf_id=pdf_id,
                    field_id=field_id,
                    drift_type="winner",
                    baseline=baseline,
                    current=current,
                    detail=f"winner drift: {baseline.winner!r} -> {current.winner!r}",
                )
            )

        if baseline.candidate != current.candidate:
            diffs.append(
                RegressionDiff(
                    pdf_id=pdf_id,
                    field_id=field_id,
                    drift_type="candidate",
                    baseline=baseline,
                    current=current,
                    detail=f"candidate drift: {baseline.candidate!r} -> {current.candidate!r}",
                )
            )

        conf_delta = abs(baseline.confidence - current.confidence)
        if conf_delta > CONFIDENCE_EPSILON:
            diffs.append(
                RegressionDiff(
                    pdf_id=pdf_id,
                    field_id=field_id,
                    drift_type="confidence",
                    baseline=baseline,
                    current=current,
                    detail=f"confidence drift: {baseline.confidence:.4f} -> {current.confidence:.4f} (delta={conf_delta:.4f})",
                )
            )

        base_keys = set(baseline.confidence_breakdown.keys())
        curr_keys = set(current.confidence_breakdown.keys())
        if base_keys != curr_keys:
            diffs.append(
                RegressionDiff(
                    pdf_id=pdf_id,
                    field_id=field_id,
                    drift_type="breakdown",
                    baseline=baseline,
                    current=current,
                    detail=f"breakdown key drift: {sorted(base_keys)} -> {sorted(curr_keys)}",
                )
            )

    return diffs


def assert_no_regression(
    before: list[RegressionSnapshot] | RegressionBaseline,
    after: list[RegressionSnapshot],
) -> None:
    """Raise StrategyRegressionError if any drift is detected."""
    baseline_snaps = before.snapshots if isinstance(before, RegressionBaseline) else before
    diffs = compare_snapshots(baseline_snaps, after)
    if not diffs:
        return
    lines = [f"{d.pdf_id} {d.field_id} [{d.drift_type}]: {d.detail}" for d in diffs[:20]]
    extra = len(diffs) - 20
    msg = f"Strategy regression detected ({len(diffs)} drift(s)):\n" + "\n".join(lines)
    if extra > 0:
        msg += f"\n... and {extra} more"
    raise StrategyRegressionError(msg)


def validate_bundle_compatibility(
    bundle: dict[str, Any],
    *,
    baseline: RegressionBaseline | None = None,
) -> None:
    """Fail fast when bundle is not structure-compatible with the engine contract."""
    from parser.profile_strategy_engine import STRATEGY_REGISTRY, known_strategy_impl_names

    if not isinstance(bundle, dict) or not bundle:
        raise BundleCompatibilityError("bundle is empty or not a dict")

    for key in ("version", "patch", "semantic_scoring"):
        if key not in bundle:
            raise BundleCompatibilityError(f"bundle missing required key: {key}")

    patch = bundle.get("patch")
    if not isinstance(patch, dict):
        raise BundleCompatibilityError("bundle.patch must be a dict")

    orders = patch.get("order")
    if not isinstance(orders, dict):
        raise BundleCompatibilityError("bundle.patch.order must be a dict")

    impl_names = known_strategy_impl_names()

    for field_id in CORE_FIELDS:
        if field_id not in orders:
            raise BundleCompatibilityError(f"bundle.patch.order missing core field: {field_id}")

    for field_id, order in orders.items():
        if field_id not in CORE_FIELDS:
            raise BundleCompatibilityError(f"bundle.patch.order has unknown field_id: {field_id}")

        if not isinstance(order, list) or not order:
            raise BundleCompatibilityError(f"bundle.patch.order[{field_id}] must be a non-empty list")

        registry = STRATEGY_REGISTRY.get(field_id, ())
        order_set = set(str(s) for s in order)
        registry_set = set(registry)

        if order_set != registry_set:
            missing = registry_set - order_set
            extra = order_set - registry_set
            parts: list[str] = []
            if missing:
                parts.append(f"missing registry strategies: {sorted(missing)}")
            if extra:
                parts.append(f"unknown strategies: {sorted(extra)}")
            raise BundleCompatibilityError(
                f"bundle.patch.order[{field_id}] is not a permutation of STRATEGY_REGISTRY: {'; '.join(parts)}"
            )

        if len(order) != len(registry):
            raise BundleCompatibilityError(
                f"bundle.patch.order[{field_id}] length mismatch: {len(order)} vs registry {len(registry)}"
            )

        for strategy in order:
            name = str(strategy)
            if name not in registry_set:
                raise BundleCompatibilityError(
                    f"bundle.patch.order[{field_id}] references unknown strategy: {name}"
                )
            if name not in impl_names:
                raise BundleCompatibilityError(
                    f"bundle.patch.order[{field_id}] references strategy without implementation: {name}"
                )

    semantic = bundle.get("semantic_scoring")
    if not isinstance(semantic, dict):
        raise BundleCompatibilityError("bundle.semantic_scoring must be a dict")

    for field_id in CORE_FIELDS:
        if field_id not in semantic:
            raise BundleCompatibilityError(f"bundle.semantic_scoring missing core field: {field_id}")

    for field_id, entry in semantic.items():
        if field_id not in CORE_FIELDS:
            raise BundleCompatibilityError(f"bundle.semantic_scoring has unknown field_id: {field_id}")
        if not isinstance(entry, dict):
            raise BundleCompatibilityError(f"bundle.semantic_scoring[{field_id}] must be a dict")

        unknown_keys = set(entry.keys()) - ALLOWED_SEMANTIC_FIELD_KEYS
        if unknown_keys:
            raise BundleCompatibilityError(
                f"bundle.semantic_scoring[{field_id}] has unknown keys: {sorted(unknown_keys)}"
            )

        if "enabled" not in entry:
            raise BundleCompatibilityError(f"bundle.semantic_scoring[{field_id}] missing 'enabled' key")

        if entry.get("enabled"):
            adj = entry.get("adjustments")
            if not isinstance(adj, dict):
                raise BundleCompatibilityError(
                    f"bundle.semantic_scoring[{field_id}] enabled but adjustments missing"
                )
            unknown_adj = set(adj.keys()) - KNOWN_SEMANTIC_ADJUSTMENT_KEYS
            if unknown_adj:
                raise BundleCompatibilityError(
                    f"bundle.semantic_scoring[{field_id}] has unknown adjustment keys: {sorted(unknown_adj)}"
                )

    if baseline is not None:
        if baseline.capture_import_graph_audit_passed is not True:
            raise BundleCompatibilityError(
                "baseline missing capture_import_graph_audit_passed=true"
            )
        bundle_hash = str(bundle.get("golden_hash") or "")
        if bundle_hash and baseline.golden_hash and bundle_hash != baseline.golden_hash:
            raise BundleCompatibilityError(
                f"bundle golden_hash mismatch: expected {baseline.golden_hash}, got {bundle_hash}"
            )

        current_fp = build_bundle_fingerprint(bundle)
        if baseline.bundle_fingerprint and current_fp != baseline.bundle_fingerprint:
            raise BundleCompatibilityError(
                "bundle structural fingerprint mismatch with regression baseline"
            )


def load_regression_baseline(path: Path) -> RegressionBaseline:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise StrategyRegressionError(f"invalid baseline JSON: {path}")
    return RegressionBaseline.from_dict(data)


def write_regression_baseline(baseline: RegressionBaseline, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(baseline.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def build_regression_baseline(
    results: list[dict[str, Any]],
    *,
    golden_hash: str,
    bundle: dict[str, Any],
    generated_at: str | None = None,
    capture_engine: str | None = "run_phase5_evaluation_sweep",
    capture_subprocess: bool | None = True,
    capture_import_graph_audit_passed: bool | None = True,
    capture_execution_state_diffing: bool | None = True,
    capture_preconditions: dict[str, Any] | None = None,
    engine_fingerprint: dict[str, Any] | None = None,
) -> RegressionBaseline:
    ts = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    semantic = bundle.get("semantic_scoring") if isinstance(bundle.get("semantic_scoring"), dict) else {}
    preconditions = capture_preconditions or {
        "resolved_attempt_carrier": True,
        "dual_fingerprint_tiebreak": True,
        "equivalence_rejection": True,
        "no_import_time_reload": True,
        "fresh_context_per_case": True,
    }
    return RegressionBaseline(
        version=BASELINE_VERSION,
        generated_at=ts,
        golden_hash=golden_hash,
        bundle_fingerprint=build_bundle_fingerprint(bundle),
        snapshots=snapshots_from_golden_results(results, semantic_scoring=semantic),
        capture_engine=capture_engine,
        capture_subprocess=capture_subprocess,
        capture_import_graph_audit_passed=capture_import_graph_audit_passed,
        capture_execution_state_diffing=capture_execution_state_diffing,
        capture_preconditions=preconditions,
        engine_fingerprint=engine_fingerprint,
    )


def tag_regression_attempts(
    attempts: list[dict[str, Any]],
    *,
    baseline_strategies: set[str],
    baseline_attempts: dict[str, dict[str, Any]] | None = None,
    winning_confidence: float,
) -> list[dict[str, Any]]:
    """Apply regression_tag to attempt dicts for report output."""
    baseline_attempts = baseline_attempts or {}
    tagged: list[dict[str, Any]] = []
    for attempt in attempts:
        row = dict(attempt)
        name = str(row.get("strategy") or "")
        if name not in baseline_strategies:
            row["regression_tag"] = "new_strategy"
        elif name in baseline_attempts:
            base = baseline_attempts[name]
            base_conf = float(base.get("confidence") or 0.0)
            curr_conf = float(row.get("confidence") or 0.0)
            base_cand = base.get("candidate")
            curr_cand = row.get("candidate")
            if base_cand != curr_cand or abs(base_conf - curr_conf) > CONFIDENCE_EPSILON:
                row["regression_tag"] = "changed_behavior"
            elif winning_confidence < FRAGILE_CONFIDENCE_THRESHOLD and row.get("status") == "valid":
                row["regression_tag"] = "fragile"
            else:
                row["regression_tag"] = "stable"
        elif winning_confidence < FRAGILE_CONFIDENCE_THRESHOLD and row.get("status") == "valid":
            row["regression_tag"] = "fragile"
        else:
            row["regression_tag"] = "stable"
        tagged.append(row)
    return tagged


def build_regression_report(
    baseline: RegressionBaseline,
    current_snapshots: list[RegressionSnapshot],
    *,
    golden_success_count: int,
    expected_success_count: int,
) -> dict[str, Any]:
    """Assemble reports/strategy_regression_report.json payload."""
    diffs = compare_snapshots(baseline.snapshots, current_snapshots)
    drift_by_field: dict[str, int] = {f: 0 for f in CORE_FIELDS}
    drift_by_type: dict[str, int] = {
        "winner": 0,
        "candidate": 0,
        "confidence": 0,
        "breakdown": 0,
        "missing": 0,
        "extra": 0,
    }
    broken_strategies: dict[str, int] = {}
    conf_deltas: list[float] = []

    for d in diffs:
        drift_by_field[d.field_id] = drift_by_field.get(d.field_id, 0) + 1
        drift_by_type[d.drift_type] = drift_by_type.get(d.drift_type, 0) + 1
        if d.drift_type == "winner" and d.current and d.current.winner:
            w = str(d.current.winner)
            broken_strategies[w] = broken_strategies.get(w, 0) + 1
        if d.drift_type == "confidence" and d.baseline and d.current:
            conf_deltas.append(abs(d.baseline.confidence - d.current.confidence))

    histogram = {
        "0.00-0.01": sum(1 for d in conf_deltas if d <= 0.01),
        "0.01-0.02": sum(1 for d in conf_deltas if 0.01 < d <= 0.02),
        ">0.02": sum(1 for d in conf_deltas if d > 0.02),
    }

    top_broken = [
        {"strategy": name, "count": count}
        for name, count in sorted(broken_strategies.items(), key=lambda x: (-x[1], x[0]))[:10]
    ]

    passed = (
        len(diffs) == 0
        and golden_success_count == expected_success_count
        and golden_success_count == len(baseline.snapshots)
    )

    return {
        "passed": passed,
        "golden_success_count": golden_success_count,
        "expected_success_count": expected_success_count,
        "summary": {
            "total_snapshots": len(baseline.snapshots),
            "drift_count": len(diffs),
            "drift_by_field": drift_by_field,
            "drift_by_type": drift_by_type,
        },
        "confidence_drift_histogram": histogram,
        "top_broken_strategies": top_broken,
        "semantic_scoring_delta": None,
        "drifts": [d.to_dict() for d in diffs],
    }


def _engine_fingerprint() -> dict[str, Any]:
    import hashlib
    import subprocess

    from parser.profile_strategy_engine import STRATEGY_REGISTRY

    registry_payload = {k: list(v) for k, v in STRATEGY_REGISTRY.items()}
    registry_hash = hashlib.sha256(
        json.dumps(registry_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    git_sha = ""
    try:
        git_sha = (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=str(Path(__file__).resolve().parents[1]),
                stderr=subprocess.DEVNULL,
            )
            .decode("utf-8")
            .strip()
        )
    except (OSError, subprocess.CalledProcessError):
        git_sha = "unknown"
    return {"registry_hash": f"sha256:{registry_hash}", "git_sha": git_sha}


def run_phase5_evaluation_sweep(
    *,
    enable_state_diffing: bool = True,
    enable_runtime_parity: bool = False,
    regression_baseline_path: Path | None | Literal["auto"] = "auto",
) -> dict[str, Any]:
    """
    Canonical Phase 5 evaluation sweep — shared by CI guard and baseline capture.

    Order: import audit → static supplement → reload → import stable → golden eval sweep.
    """
    from parser.golden_dataset_learning_pass import compute_golden_hash
    from parser.profile_strategy_engine import (
        StrategyContext,
        reload_strategy_engine_state,
        run_strategies,
        extracted_values_equal,
    )
    from parser.strategy_statelessness_audit import (
        WATCHED_MODULES,
        assert_import_graph_stable,
        assert_no_mutation,
        audit_import_graph_statelessness,
        audit_strategy_layer_statelessness,
        snapshot_execution_state,
    )
    from parser.pdf_parser import extract_text_strict
    from tests.golden_test_support import GOLDEN_PDFS_DIR, golden_expected, iter_golden_cases

    FIELD_GOLDEN_KEY = {
        "amount": "amount",
        "invoice_number": "invoice_number",
        "customer_number": "customer_code",
        "iban": "iban",
    }

    static_violations = audit_strategy_layer_statelessness()
    import_before = audit_import_graph_statelessness()
    reload_strategy_engine_state(regression_baseline_path=regression_baseline_path)
    import_after = snapshot_execution_state(WATCHED_MODULES)
    assert_import_graph_stable(import_before, import_after)

    results: list[dict[str, Any]] = []
    mutations_detected = 0
    parity_checked = 0
    value_divergences: list[dict[str, Any]] = []
    strategy_divergences: list[dict[str, Any]] = []

    for case in iter_golden_cases():
        for field_id in CORE_FIELDS:
            pdf_path = GOLDEN_PDFS_DIR / case.source_file
            if not pdf_path.is_file():
                continue
            golden_key = FIELD_GOLDEN_KEY[field_id]
            if not case.golden.get(golden_key):
                continue

            raw_text = extract_text_strict(str(pdf_path))
            expected = golden_expected(
                case,
                field_id if field_id != "customer_number" else "customer_code",
            )

            ctx_base = dict(
                field_id=field_id,
                raw_text=raw_text,
                confirmed_value=expected,
                mode="learn",
            )

            if enable_state_diffing:
                before = snapshot_execution_state(WATCHED_MODULES)

            eval_ctx = StrategyContext(**ctx_base, evaluation_mode=True)
            eval_result = run_strategies(field_id, eval_ctx)

            if enable_state_diffing:
                mid = snapshot_execution_state(WATCHED_MODULES)
                try:
                    assert_no_mutation(
                        before,
                        mid,
                        context=f"{case.source_file}/{field_id}/eval",
                    )
                except Exception:
                    mutations_detected += 1
                    raise

            if enable_runtime_parity:
                runtime_ctx = StrategyContext(**ctx_base, evaluation_mode=False)
                runtime_result = run_strategies(field_id, runtime_ctx)
                parity_checked += 1

                if enable_state_diffing:
                    after = snapshot_execution_state(WATCHED_MODULES)
                    try:
                        assert_no_mutation(
                            mid,
                            after,
                            context=f"{case.source_file}/{field_id}/runtime",
                        )
                    except Exception:
                        mutations_detected += 1
                        raise

                if not extracted_values_equal(field_id, eval_result.value, runtime_result.value):
                    value_divergences.append(
                        {
                            "pdf": case.source_file,
                            "field": field_id,
                            "eval_value": eval_result.value,
                            "runtime_value": runtime_result.value,
                        }
                    )
                elif eval_result.strategy_used != runtime_result.strategy_used:
                    strategy_divergences.append(
                        {
                            "pdf": case.source_file,
                            "field": field_id,
                            "eval_strategy": eval_result.strategy_used,
                            "runtime_strategy": runtime_result.strategy_used,
                        }
                    )
            elif enable_state_diffing:
                after = snapshot_execution_state(WATCHED_MODULES)
                try:
                    assert_no_mutation(
                        mid,
                        after,
                        context=f"{case.source_file}/{field_id}/post_eval",
                    )
                except Exception:
                    mutations_detected += 1
                    raise

            expected_norm = normalize_candidate(field_id, expected)
            actual_norm = normalize_candidate(field_id, eval_result.value)
            from parser.profile_strategy_engine import value_in_raw_text

            if not value_in_raw_text(raw_text, expected, field_id):
                results.append(
                    {
                        "pdf": case.source_file,
                        "field": field_id,
                        "status": "expected_failure",
                        "reason": "value_not_in_text",
                        "expected": expected_norm,
                    }
                )
                continue

            success = actual_norm == expected_norm and eval_result.strategy_used is not None
            results.append(
                {
                    "pdf": case.source_file,
                    "field": field_id,
                    "status": "success" if success else "failure",
                    "success": success,
                    "expected": expected_norm,
                    "actual": actual_norm,
                    "strategy_used": eval_result.strategy_used,
                    "all_attempted_strategies": [
                        a.to_dict() for a in eval_result.all_attempted_strategies
                    ],
                    "validation_trace": eval_result.validation_trace,
                    "confidence": eval_result.confidence,
                    "confidence_breakdown": next(
                        (
                            a.confidence_breakdown
                            for a in reversed(eval_result.all_attempted_strategies)
                            if a.strategy == eval_result.strategy_used and a.status == "valid"
                        ),
                        {},
                    ),
                }
            )

    ghash = compute_golden_hash(results)
    return {
        "results": results,
        "golden_hash": ghash,
        "engine_fingerprint": _engine_fingerprint(),
        "static_audit_violations": static_violations,
        "execution_state_diffing": {
            "enabled": enable_state_diffing,
            "mutations_detected": mutations_detected,
            "passed": mutations_detected == 0,
        },
        "runtime_value_parity": {
            "enabled": enable_runtime_parity,
            "checked": parity_checked,
            "value_divergence_count": len(value_divergences),
            "strategy_divergence_count": len(strategy_divergences),
            "no_shortcuts": enable_runtime_parity and parity_checked > 0,
            "fresh_context_per_case": True,
            "value_divergences": value_divergences[:20],
            "strategy_divergences": strategy_divergences[:20],
            "passed": len(value_divergences) == 0,
        },
    }


def run_runtime_value_parity(*, limit: int | None = None) -> dict[str, Any]:
    """Full golden runtime parity — limit=None required for Phase 5 CI."""
    sweep = run_phase5_evaluation_sweep(
        enable_state_diffing=True,
        enable_runtime_parity=True,
    )
    parity = sweep.get("runtime_value_parity") or {}
    if limit is not None:
        raise StrategyRegressionError("runtime parity limit is not allowed in Phase 5 CI")
    if not parity.get("passed"):
        count = int(parity.get("value_divergence_count") or 0)
        raise StrategyRegressionError(
            f"runtime value parity failed: {count} divergence(s)"
        )
    return parity
