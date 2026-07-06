"""Tests for SupplierDBSnapshot."""

from __future__ import annotations

import json
from pathlib import Path

from parser.supplier_db import SupplierDB, SupplierDBSnapshot


def test_snapshot_isolated_from_mutable_db(tmp_path: Path) -> None:
    path = tmp_path / "suppliers.json"
    path.write_text(
        json.dumps({"suppliers": [{"name": "A", "iban": "NL91ABNA0417164300", "aliases": ["A"]}]}),
        encoding="utf-8",
    )
    mutable = SupplierDB(path=str(path))
    snap = SupplierDBSnapshot.from_path(str(path))
    mutable.update_supplier("A", iban="NL99RABO0123456789")
    matcher = snap.matcher_db()
    assert matcher.suppliers[0]["iban"] == "NL91ABNA0417164300"
