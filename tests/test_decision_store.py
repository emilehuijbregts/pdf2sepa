from __future__ import annotations

from logic.decision_store import DecisionStore


def test_append_only_run_graph_and_parent_chain() -> None:
    store = DecisionStore()
    r1 = store.begin_run(run_id="run-1", input_snapshot_hash="snap-1", decision_map={"a": {"status": "included"}})
    c1 = store.commit_run(r1.run_id, xml_output_hash="xml-1")
    assert c1.parent_run_id is None
    assert c1.engine_run_state == "committed"

    r2 = store.begin_run(run_id="run-2", input_snapshot_hash="snap-2", decision_map={"b": {"status": "excluded"}})
    c2 = store.commit_run(r2.run_id, xml_output_hash="xml-2")
    assert c2.parent_run_id == "run-1"
    assert store.latest_committed_run_id == "run-2"
    assert [r.run_id for r in store.all_runs()] == ["run-1", "run-2"]


def test_run_global_hash_replay_stable() -> None:
    store = DecisionStore()
    rec = store.begin_run(run_id="stable", input_snapshot_hash="snap", decision_map={"x": {"status": "included"}})
    c1 = store.commit_run(rec.run_id, xml_output_hash="xml-hash")

    store2 = DecisionStore()
    rec2 = store2.begin_run(run_id="stable", input_snapshot_hash="snap", decision_map={"x": {"status": "included"}})
    c2 = store2.commit_run(rec2.run_id, xml_output_hash="xml-hash")
    assert c1.run_global_hash == c2.run_global_hash
