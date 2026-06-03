from __future__ import annotations

from ui.field_rendering import confidence_color, is_missing_selected_value


def _rgb(c) -> tuple[int, int, int]:
    return (int(c.red()), int(c.green()), int(c.blue()))


def test_is_missing_selected_value() -> None:
    assert is_missing_selected_value(None) is True
    assert is_missing_selected_value("") is True
    assert is_missing_selected_value("   ") is True
    assert is_missing_selected_value("x") is False


def test_confidence_color_thresholds() -> None:
    # missing always red
    assert _rgb(confidence_color(99, missing=True)) == (180, 0, 0)

    # > 80 => green
    assert _rgb(confidence_color(81)) == (0, 128, 0)
    assert _rgb(confidence_color(100)) == (0, 128, 0)

    # 50-80 inclusive => orange
    assert _rgb(confidence_color(50)) == (200, 120, 0)
    assert _rgb(confidence_color(80)) == (200, 120, 0)

    # < 50 => red
    assert _rgb(confidence_color(49)) == (180, 0, 0)
    assert _rgb(confidence_color(0)) == (180, 0, 0)

