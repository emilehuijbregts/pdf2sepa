"""Phase B7 — field status is written only in field_resolver._build_result."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from logic.payment_engine import _effective_amount_status
from parser.field_adapters import field_result_to_legacy_dict
from parser.field_model import FieldCandidate, FieldResult
from parser.field_resolver import _build_result, resolve_field
from parser.hybrid_field_apply import apply_hybrid_field_extraction
_REPO_ROOT = Path(__file__).resolve().parents[1]
_STATUS_MUTATION_FORBIDDEN = (
    "_cap_amount_tentative",
)


def test_amount_profile_review_cap_is_applied_in_build_result() -> None:
    generic = FieldResult(
        field_id="amount",
        candidates=[],
        selected_value=None,
        confidence=0,
        source="TEST",
        status="ambiguous",
    )
    winner = FieldCandidate(value="100.00", source="profile", confidence=90, context="ctx")
    fr = _build_result(
        "amount",
        generic,
        winner,
        [winner],
        override_reason="profile_higher_confidence",
        decision_trace=[],
        amount_profile_review_cap=True,
    )
    assert fr.status == "tentative"
    assert fr.confidence == 75
    assert any(
        isinstance(e, dict) and e.get("kind") == "amount_profile_review_cap"
        for e in fr.decision_trace
    )
    legacy = field_result_to_legacy_dict(fr)
    assert legacy["status"] == "tentative"
    assert legacy.get("review_suggested") is True


def test_amount_profile_review_cap_is_applied_in_build_result() -> None:
    generic = FieldResult(
        field_id="amount",
        candidates=[],
        selected_value=None,
        confidence=0,
        source="TEST",
        status="ambiguous",
    )
    winner = FieldCandidate(value="100.00", source="profile", confidence=90, context="ctx")
    fr = _build_result(
        "amount",
        generic,
        winner,
        [winner],
        override_reason="profile_higher_confidence",
        decision_trace=[],
        amount_profile_review_cap=True,
    )
    assert fr.status == "tentative"
    assert fr.confidence == 75
    assert any(
        isinstance(e, dict) and e.get("kind") == "amount_profile_review_cap"
        for e in fr.decision_trace
    )
    legacy = field_result_to_legacy_dict(fr)
    assert legacy["status"] == "tentative"
    assert legacy.get("review_suggested") is True


def test_validated_profile_amount_skips_review_cap() -> None:
    generic = FieldResult(
        field_id="amount",
        candidates=[],
        selected_value=None,
        confidence=0,
        source="INCL_CONFLICT",
        status="ambiguous",
    )
    winner = FieldCandidate(
        value="614.93",
        source="profile",
        confidence=90,
        context="Te betalen",
        meta={"profile_validated": True, "payable_score": 100, "type": "incl"},
    )
    fr = _build_result(
        "amount",
        generic,
        winner,
        [winner],
        override_reason="profile_higher_confidence",
        decision_trace=[],
        amount_profile_review_cap=True,
    )
    assert fr.status == "confirmed"
    assert fr.confidence == 90
    assert not any(
        isinstance(e, dict) and e.get("kind") == "amount_profile_review_cap"
        for e in fr.decision_trace
    )


def test_resolve_field_validated_profile_amount_keeps_confirmed_on_iban_match_path() -> None:
    generic = FieldResult(
        field_id="amount",
        candidates=[
            FieldCandidate(value="1.00", source="TEST", confidence=10, context=""),
        ],
        selected_value="1.00",
        confidence=10,
        source="TEST",
        status="ambiguous",
    )
    profile = FieldCandidate(
        value="614.93",
        source="profile",
        confidence=90,
        context="Te betalen",
        meta={"profile_validated": True, "payable_score": 100, "type": "incl"},
    )
    fr = resolve_field(
        "amount",
        generic,
        [profile],
        amount_profile_review_cap=True,
    )
    assert str(fr.source).lower() == "profile"
    assert fr.status == "confirmed"
    assert fr.confidence == 90


def test_resolve_field_amount_cap_matches_supplier_iban_match_path() -> None:
    generic = FieldResult(
        field_id="amount",
        candidates=[
            FieldCandidate(value="1.00", source="TEST", confidence=10, context=""),
        ],
        selected_value="1.00",
        confidence=10,
        source="TEST",
        status="ambiguous",
    )
    profile = FieldCandidate(value="1551.22", source="profile", confidence=90, context="prof")
    fr = resolve_field(
        "amount",
        generic,
        [profile],
        amount_profile_review_cap=True,
    )
    assert fr.selected_value is not None
    assert str(fr.source).lower() == "profile"
    assert fr.status == "tentative"
    assert fr.confidence == 75


def test_hybrid_validated_amount_profile_sf_ict_style(tmp_path) -> None:
    import json
    from pathlib import Path as PathLib

    repo = PathLib(__file__).resolve().parents[1]
    pdf_path = repo / "tests/Batch 6/test/SF inv26800314.pdf"
    if not pdf_path.is_file():
        pdf_path = repo / "tests/Batch 6/SF inv26800314.pdf"
    if not pdf_path.is_file():
        import pytest

        pytest.skip("SF ICT fixture PDF not available")
    data = {
        "suppliers": [
            {
                "name": "SF-ICT B.V.",
                "iban": "NL14SNSB0924226188",
                "aliases": ["SF-ICT B.V."],
                "customer_codes": [],
                "extraction_profile": {
                    "amount": {
                        "label": "Totaal (incl.BTW)",
                        "strategy": "same_line_last_amount",
                        "confirmed_value": "614.93",
                    },
                    "invoice_number": {
                        "label": "Factuurnummer: ",
                        "strategy": "same_line_after_colon",
                        "confirmed_value": "26800314",
                    },
                },
            }
        ]
    }
    p = tmp_path / "suppliers.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    from parser.supplier_db import SupplierDB

    db = SupplierDB(path=str(p))
    supplier = data["suppliers"][0]
    invoice = {
        "raw_text": "short header only",
        "source_file": str(pdf_path),
        "iban": "NL14SNSB0924226188",
        "amount_result": {
            "status": "ambiguous",
            "value": None,
            "confidence": 0,
            "source": "INCL_CONFLICT",
            "candidates": [],
        },
    }
    invoice_copy: dict[str, Any] = {}
    apply_hybrid_field_extraction(
        invoice,
        invoice_copy,
        supplier,
        db,
        amount_status="tentative",
        use_profile=True,
    )
    ar = invoice_copy.get("amount_result") or {}
    assert ar.get("source") == "profile"
    assert ar.get("status") == "confirmed"
    assert int(ar.get("confidence") or 0) == 90
    assert "amount" in (invoice_copy.get("profile_fields") or [])


def test_hybrid_apply_does_not_mutate_status_after_resolve(monkeypatch) -> None:
    captured: list[tuple[str, str, int]] = []

    def _spy_apply(invoice_copy: dict, field_id: str, resolved_dict: dict[str, Any], **kwargs: Any) -> None:
        captured.append(
            (
                field_id,
                str(resolved_dict.get("status") or ""),
                int(resolved_dict.get("confidence") or 0),
            )
        )

    monkeypatch.setattr(
        "parser.hybrid_field_apply.apply_resolved_field_result",
        _spy_apply,
    )

    invoice: dict[str, Any] = {
        "raw_text": "Totaal 100,00",
        "amount": 1.0,
        "amount_result": {"status": "ambiguous", "source": "TEST", "value": "1.00", "candidates": []},
    }
    invoice_copy = dict(invoice)
    supplier = {"name": "Test", "iban": "NL20INGB0001234567"}
    db = type("DB", (), {"get_extraction_profile": lambda _s, _n: None})()

    apply_hybrid_field_extraction(
        invoice,
        invoice_copy,
        supplier,
        db,  # type: ignore[arg-type]
        amount_status="tentative",
        use_profile=False,
    )
    assert captured


def test_effective_amount_status_is_read_only() -> None:
    inv = {
        "match_status": "confirmed",
        "amount_result": {
            "source": "profile",
            "confidence": 90,
            "status": "tentative",
            "amount_status": "tentative",
        },
    }
    st, result = _effective_amount_status(inv)
    assert st == "tentative"
    assert result["status"] == "tentative"
    assert result["confidence"] == 90


def test_no_post_resolve_status_cap_helper_in_production_modules() -> None:
    for rel in (
        "parser/hybrid_field_apply.py",
        "logic/payment_engine.py",
        "parser/resolved_field_apply.py",
    ):
        tree = ast.parse((_REPO_ROOT / rel).read_text(encoding="utf-8"))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        for forbidden in _STATUS_MUTATION_FORBIDDEN:
            assert forbidden not in names, f"{rel} still references {forbidden}"


def test_field_resolver_assigns_status_only_in_build_result() -> None:
    source = (_REPO_ROOT / "parser/field_resolver.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    build_fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_build_result"
    )
    build_src = ast.get_source_segment(source, build_fn) or ""
    assert 'st = "failed"' in build_src or "st = 'failed'" in build_src
    assert "amount_profile_review_cap" in build_src
    resolve_fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "resolve_field"
    )
    resolve_src = ast.get_source_segment(source, resolve_fn) or ""
    assert 'st = "' not in resolve_src and "st = '" not in resolve_src
