from __future__ import annotations

from ui.diagnostics_dialog import DiagnosticsDialog
from ui.field_review import is_customer_absent_pick, make_customer_absent_pick_candidate


def test_override_trace_lines_uses_human_labels() -> None:
    lines = DiagnosticsDialog._override_trace_lines(
        {
            "override_reason_nl": "Herkend via leveranciersprofiel",
            "decision_trace_human": [
                {
                    "source": "generic",
                    "source_nl": "Generieke parser",
                    "confidence": 92,
                    "win": True,
                    "value": "€ 1250,55",
                },
                {
                    "source": "profile",
                    "source_nl": "Leveranciersprofiel",
                    "confidence": 88,
                    "win": False,
                    "value": "€ 1240,55",
                    "rejection_reason_nl": "Een sterkere generieke match won",
                },
                {
                    "kind": "final",
                    "final_decision_reason_nl": "Had de hoogste betrouwbaarheid",
                    "winner": {
                        "source_nl": "Generieke parser",
                        "confidence": 92,
                        "value": "€ 1250,55",
                    },
                },
            ],
        }
    )
    text = "\n".join(lines)
    assert "Had de hoogste betrouwbaarheid" in text
    assert "Een sterkere generieke match won" in text
    assert "highest_confidence" not in text
    assert "generic_preferred" not in text


def test_customer_number_section_lines_local_absent_preview() -> None:
    lines = DiagnosticsDialog._customer_number_section_lines(
        {"status_nl": "Ontbreekt"},
        local_selected=make_customer_absent_pick_candidate(),
    )
    text = "\n".join(lines)
    assert "Geen klantnummer" in text


def test_absent_click_preview_only_does_not_invoke_apply_callback() -> None:
    applied: list[tuple[str, dict]] = []
    dlg = DiagnosticsDialog.__new__(DiagnosticsDialog)
    dlg._selected_values = {}
    dlg._diag = {}
    dlg._on_candidate_click = lambda fid, c: applied.append((fid, c)) or {}
    dlg._schedule_set_diag = lambda *_a, **_k: None
    dlg._on_customer_absent_clicked("customer_number")
    assert not applied
    assert is_customer_absent_pick(dlg._selected_values.get("customer_number"))


def test_selected_by_field_includes_absent_dict() -> None:
    dlg = DiagnosticsDialog.__new__(DiagnosticsDialog)
    dlg._diag = {
        "customer_number": {"value": "740777", "selected_value": "740777"},
    }
    dlg._selected_values = {"customer_number": make_customer_absent_pick_candidate()}
    selected = dlg.selected_by_field()
    assert is_customer_absent_pick(selected.get("customer_number"))
    assert selected["customer_number"] is dlg._selected_values["customer_number"]


def test_qt_clicked_lambda_preserves_field_id() -> None:
    """QPushButton.clicked(bool) must not overwrite the field_id default arg."""
    field_id = "customer_number"
    received: list[str | bool] = []

    def handler(fid: str | bool) -> None:
        received.append(fid)

    buggy = lambda fid=field_id: handler(fid)
    buggy(False)
    assert received[-1] is False

    fixed = lambda _checked=False, fid=field_id: handler(fid)
    fixed(False)
    assert received[-1] == "customer_number"


def test_override_trace_lines_do_not_expose_raw_final_reason_code() -> None:
    lines = DiagnosticsDialog._override_trace_lines(
        {
            "decision_trace": [
                {
                    "kind": "final",
                    "final_decision_reason": "highest_confidence",
                    "winner": {"source": "generic", "confidence": 90, "value": "x"},
                }
            ]
        }
    )
    text = "\n".join(lines)
    assert "highest_confidence" not in text
    assert "Gekozen op basis van beschikbare signalen" in text
