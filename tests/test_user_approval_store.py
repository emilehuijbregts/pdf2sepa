from __future__ import annotations

from logic.decision_store import UserApprovalStore


def test_remove_from_batch_drops_row(tmp_path) -> None:
    store = UserApprovalStore(tmp_path / "user_approvals.json")
    batch_key = "batch-1"
    store.upsert_batch(
        batch_key,
        {
            "a|1|x.pdf": {"status": "included", "reason_code": "user_approved"},
            "b|2|y.pdf": {"status": "included", "reason_code": "user_approved"},
        },
    )
    store.remove_from_batch(batch_key, {"a|1|x.pdf"})
    loaded = store.load_batch(batch_key)
    assert "a|1|x.pdf" not in loaded
    assert "b|2|y.pdf" in loaded
