from __future__ import annotations

from ui.diagnostics_dialog import DiagnosticsDialog


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
