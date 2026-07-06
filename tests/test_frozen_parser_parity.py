"""Verify parser config (strategy bundle) is identical in dev and simulated frozen mode."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from logic.runtime_paths import bundled_engine_data_path
from parser.profile_strategy_engine import (
    get_semantic_scoring,
    get_strategy_pipeline,
    load_strategy_order_overrides,
    reload_strategy_engine_state,
)

_REPO = Path(__file__).resolve().parents[1]
_DEV_BUNDLE = _REPO / "data" / "strategy_engine_bundle.json"


def _capture_parser_config() -> dict:
    reload_strategy_engine_state(skip_bundle_validation=True)
    return {
        "strategy_order": {
            field: list(pipeline)
            for field, pipeline in load_strategy_order_overrides().items()
        },
        "amount_pipeline": list(get_strategy_pipeline("amount")),
        "invoice_pipeline": list(get_strategy_pipeline("invoice_number")),
        "amount_scoring": get_semantic_scoring("amount"),
    }


@pytest.fixture()
def dev_bundle_snapshot() -> dict:
    assert _DEV_BUNDLE.is_file(), "missing data/strategy_engine_bundle.json"
    return _capture_parser_config()


def test_dev_bundle_loads_non_empty(dev_bundle_snapshot: dict) -> None:
    assert dev_bundle_snapshot["strategy_order"]
    assert dev_bundle_snapshot["amount_pipeline"]
    assert dev_bundle_snapshot["amount_scoring"] is not None


def test_frozen_bundle_path_matches_dev_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dev_bundle_snapshot: dict
) -> None:
    """Simulate PyInstaller onedir: bundle lives under _MEIPASS/data/."""
    internal = tmp_path / "_internal"
    data_dir = internal / "data"
    data_dir.mkdir(parents=True)
    shutil.copy2(_DEV_BUNDLE, data_dir / "strategy_engine_bundle.json")

    fake_exe = tmp_path / "PDF2SEPA.exe"
    fake_exe.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)

    frozen_path = bundled_engine_data_path("strategy_engine_bundle.json")
    assert frozen_path.is_file()
    assert json.loads(frozen_path.read_text(encoding="utf-8")) == json.loads(
        _DEV_BUNDLE.read_text(encoding="utf-8")
    )

    frozen_snapshot = _capture_parser_config()
    assert frozen_snapshot == dev_bundle_snapshot


def test_settings_path_uses_user_data_in_frozen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """internal_vat_blacklist must read settings from user data dir, not _internal."""
    from logic.paths import write_user_data_root
    from parser.pdf_parser import _settings_json_path

    user_data = tmp_path / "userdata"
    user_data.mkdir()
    settings = user_data / "settings.json"
    settings.write_text(
        json.dumps({"internal_vat_numbers": ["NL148005664B01"]}),
        encoding="utf-8",
    )

    fake_exe = tmp_path / "app" / "PDF2SEPA.exe"
    fake_exe.parent.mkdir(parents=True)
    fake_exe.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "app" / "_internal"), raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    write_user_data_root(user_data, fake_exe.parent)

    assert _settings_json_path() == settings

    from parser.pdf_parser import load_internal_vat_blacklist

    bl = load_internal_vat_blacklist()
    assert "NL148005664B01" in bl
