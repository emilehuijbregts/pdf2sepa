"""
Execution-time statelessness audit for profile strategy engine.

Static audit is a supplement; per-case execution state diffing is the primary enforcement.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

APP_BASE = Path(__file__).resolve().parents[1]

WATCHED_MODULES: tuple[str, ...] = (
    "parser.profile_strategy_engine",
    "parser.profile_extractor",
    "parser.profile_learner",
    "parser.pdf_parser",
    "parser.field_model",
)

IMPORT_WATCHED: tuple[str, ...] = WATCHED_MODULES

# Module attrs allowed to change during reload_strategy_engine_state only.
_BUNDLE_CACHE_ATTRS = frozenset(
    {
        "_engine_bundle_cache",
        "_engine_bundle_version",
        "_strategy_order_cache",
        "_semantic_scoring_cache",
        "_bundle_load_attempted",
    }
)

_STATIC_SCAN_PATHS: tuple[Path, ...] = (
    APP_BASE / "parser" / "profile_strategy_engine.py",
    APP_BASE / "parser" / "profile_extractor.py",
)


class StrategyStatelessnessError(Exception):
    """Raised when execution mutates forbidden module state."""


@dataclass(frozen=True)
class ExecutionStateSnapshot:
    module_fingerprints: dict[str, str]
    registry_ids: dict[str, int]
    impl_ids: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_fingerprints": dict(self.module_fingerprints),
            "registry_ids": dict(self.registry_ids),
            "impl_ids": dict(self.impl_ids),
        }


@dataclass
class StateMutation:
    context: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"context": self.context, "detail": self.detail}


def _import_module(name: str) -> Any:
    if name not in sys.modules:
        importlib.import_module(name)
    return sys.modules[name]


def _fingerprint_mutable(obj: Any) -> str:
    if isinstance(obj, dict):
        keys = sorted(str(k) for k in obj.keys())
        return hashlib.sha256(repr((id(obj), len(obj), keys)).encode()).hexdigest()[:16]
    if isinstance(obj, (list, set, tuple)):
        return hashlib.sha256(repr((id(obj), len(obj), type(obj).__name__)).encode()).hexdigest()[:16]
    return hashlib.sha256(repr((type(obj).__name__, id(obj))).encode()).hexdigest()[:16]


def _module_fingerprint(mod: Any) -> str:
    parts: list[str] = []
    for key in sorted(k for k in mod.__dict__ if not k.startswith("__")):
        val = mod.__dict__[key]
        if callable(val) or isinstance(val, (type, str, int, float, bool, tuple, frozenset)):
            parts.append(f"{key}:{type(val).__name__}:{id(val)}")
        elif isinstance(val, (dict, list, set)):
            parts.append(f"{key}:{_fingerprint_mutable(val)}")
        else:
            parts.append(f"{key}:{type(val).__name__}:{id(val)}")
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest


def snapshot_execution_state(modules: tuple[str, ...] = WATCHED_MODULES) -> ExecutionStateSnapshot:
    fps: dict[str, str] = {}
    registry_ids: dict[str, int] = {}
    impl_ids: dict[str, int] = {}
    for name in modules:
        mod = _import_module(name)
        fps[name] = _module_fingerprint(mod)
        if name == "parser.profile_strategy_engine":
            reg = getattr(mod, "STRATEGY_REGISTRY", None)
            impls = getattr(mod, "_STRATEGY_IMPLS", None)
            if reg is not None:
                registry_ids["STRATEGY_REGISTRY"] = id(reg)
            if impls is not None:
                impl_ids["_STRATEGY_IMPLS"] = id(impls)
    return ExecutionStateSnapshot(
        module_fingerprints=fps,
        registry_ids=registry_ids,
        impl_ids=impl_ids,
    )


def diff_execution_state(
    before: ExecutionStateSnapshot,
    after: ExecutionStateSnapshot,
    *,
    allow_bundle_cache_only: bool = False,
) -> list[StateMutation]:
    mutations: list[StateMutation] = []
    for name, fp_before in before.module_fingerprints.items():
        fp_after = after.module_fingerprints.get(name)
        if fp_after is None:
            mutations.append(StateMutation(context=name, detail="module missing after snapshot"))
        elif fp_before != fp_after:
            if allow_bundle_cache_only and name == "parser.profile_strategy_engine":
                continue
            mutations.append(
                StateMutation(
                    context=name,
                    detail=f"module fingerprint changed {fp_before[:8]} -> {fp_after[:8]}",
                )
            )
    for key, id_before in before.registry_ids.items():
        id_after = after.registry_ids.get(key)
        if id_after != id_before:
            mutations.append(
                StateMutation(context=key, detail=f"registry object id changed {id_before} -> {id_after}")
            )
    for key, id_before in before.impl_ids.items():
        id_after = after.impl_ids.get(key)
        if id_after != id_before:
            mutations.append(
                StateMutation(context=key, detail=f"impl map object id changed {id_before} -> {id_after}")
            )
    return mutations


def assert_no_mutation(
    before: ExecutionStateSnapshot,
    after: ExecutionStateSnapshot,
    *,
    context: str,
    allow_bundle_cache_only: bool = False,
) -> None:
    mutations = diff_execution_state(
        before,
        after,
        allow_bundle_cache_only=allow_bundle_cache_only,
    )
    if mutations:
        details = "; ".join(f"{m.context}: {m.detail}" for m in mutations[:5])
        raise StrategyStatelessnessError(f"execution state mutation at {context}: {details}")


def assert_import_graph_stable(
    before: ExecutionStateSnapshot,
    after: ExecutionStateSnapshot,
) -> None:
    """Reload must not mutate registry/impl identity or import-time module shape."""
    assert_no_mutation(before, after, context="import_graph_reload", allow_bundle_cache_only=True)
    if before.registry_ids != after.registry_ids:
        raise StrategyStatelessnessError("STRATEGY_REGISTRY identity changed after reload")
    if before.impl_ids != after.impl_ids:
        raise StrategyStatelessnessError("_STRATEGY_IMPLS identity changed after reload")


def audit_strategy_layer_statelessness() -> list[str]:
    """Static supplement — flags obvious patterns; does NOT alone gate CI."""
    violations: list[str] = []
    for path in _STATIC_SCAN_PATHS:
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8")
        if "@lru_cache" in source or "@cache" in source:
            violations.append(f"{path.name}: contains cache decorator")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for default in node.args.defaults + node.args.kw_defaults:
                    if isinstance(default, (ast.Dict, ast.List, ast.Set)):
                        violations.append(
                            f"{path.name}:{node.lineno}: mutable default argument in {node.name}"
                        )
    return violations


def audit_import_graph_statelessness() -> ExecutionStateSnapshot:
    """Return baseline import snapshot for import-graph barrier."""
    return snapshot_execution_state(IMPORT_WATCHED)
