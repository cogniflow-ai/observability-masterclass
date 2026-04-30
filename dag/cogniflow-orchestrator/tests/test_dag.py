"""Tests for DAG loader and mode detection."""
import pytest
from orchestrator.dag import build_dag, is_cyclic_pipeline
from orchestrator.exceptions import CycleDetectedError


def _spec(agents, edges=None):
    out = {"agents": [{"id": a, "depends_on": []} for a in agents]}
    if edges:
        out["edges"] = edges
    return out


def test_single_agent_no_deps():
    spec = {"agents": [{"id": "a", "depends_on": []}]}
    layers = build_dag(spec)
    assert layers == [["a"]]


def test_linear_chain():
    spec = {"agents": [
        {"id": "a", "depends_on": []},
        {"id": "b", "depends_on": ["a"]},
        {"id": "c", "depends_on": ["b"]},
    ]}
    layers = build_dag(spec)
    assert ["a"] in layers
    assert ["b"] in layers
    assert ["c"] in layers
    a_idx = next(i for i, l in enumerate(layers) if "a" in l)
    b_idx = next(i for i, l in enumerate(layers) if "b" in l)
    c_idx = next(i for i, l in enumerate(layers) if "c" in l)
    assert a_idx < b_idx < c_idx


def test_parallel_layer():
    spec = {"agents": [
        {"id": "root", "depends_on": []},
        {"id": "left", "depends_on": ["root"]},
        {"id": "right", "depends_on": ["root"]},
        {"id": "sink", "depends_on": ["left", "right"]},
    ]}
    layers = build_dag(spec)
    mid_layer = next(l for l in layers if "left" in l)
    assert "right" in mid_layer


def test_cycle_raises():
    spec = {"agents": [
        {"id": "a", "depends_on": ["b"]},
        {"id": "b", "depends_on": ["a"]},
    ]}
    with pytest.raises(CycleDetectedError):
        build_dag(spec)


def test_is_cyclic_false_for_dag():
    spec = _spec(["a","b"], edges=[{"from":"a","to":"b","type":"task","directed":True}])
    assert not is_cyclic_pipeline(spec)


def test_is_cyclic_true_for_feedback():
    spec = _spec(["a","b"], edges=[
        {"from":"a","to":"b","type":"feedback","directed":False}
    ])
    assert is_cyclic_pipeline(spec)


def test_is_cyclic_true_for_peer():
    spec = _spec(["a","b"], edges=[
        {"from":"a","to":"b","type":"peer","directed":False}
    ])
    assert is_cyclic_pipeline(spec)
