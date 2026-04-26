"""Tests for DAG layer resolution and cycle detection."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.dag import compute_layers_fallback
from orchestrator.exceptions import CycleDetectedError


def agents(*pairs):
    """Helper: build agent list from (id, deps) tuples."""
    return [{"id": a, "depends_on": list(d)} for a, d in pairs]


class TestComputeLayers:
    def test_single_agent(self):
        result = compute_layers_fallback(agents(("a", [])))
        assert result == [["a"]]

    def test_linear_chain(self):
        result = compute_layers_fallback(agents(
            ("a", []),
            ("b", ["a"]),
            ("c", ["b"]),
        ))
        assert result == [["a"], ["b"], ["c"]]

    def test_parallel_root_agents(self):
        result = compute_layers_fallback(agents(
            ("a", []),
            ("b", []),
            ("c", []),
        ))
        assert result == [["a", "b", "c"]]

    def test_fan_in(self):
        result = compute_layers_fallback(agents(
            ("r1", []),
            ("r2", []),
            ("r3", []),
            ("syn", ["r1", "r2", "r3"]),
        ))
        assert result[0] == ["r1", "r2", "r3"]
        assert result[1] == ["syn"]

    def test_diamond(self):
        """A → B, A → C, B+C → D."""
        result = compute_layers_fallback(agents(
            ("A", []),
            ("B", ["A"]),
            ("C", ["A"]),
            ("D", ["B", "C"]),
        ))
        assert result[0] == ["A"]
        assert set(result[1]) == {"B", "C"}
        assert result[2] == ["D"]

    def test_full_7_agent_pipeline(self):
        result = compute_layers_fallback(agents(
            ("001", []),
            ("002", []),
            ("003", []),
            ("004", ["001", "002", "003"]),
            ("005", ["004"]),
            ("006", ["004"]),
            ("007", ["005", "006"]),
        ))
        assert set(result[0]) == {"001", "002", "003"}
        assert result[1] == ["004"]
        assert set(result[2]) == {"005", "006"}
        assert result[3] == ["007"]

    def test_cycle_raises(self):
        with pytest.raises(CycleDetectedError):
            compute_layers_fallback(agents(
                ("a", ["b"]),
                ("b", ["a"]),
            ))

    def test_self_loop_raises(self):
        with pytest.raises(CycleDetectedError):
            compute_layers_fallback(agents(("a", ["a"])))

    def test_longer_cycle_raises(self):
        with pytest.raises(CycleDetectedError):
            compute_layers_fallback(agents(
                ("a", ["c"]),
                ("b", ["a"]),
                ("c", ["b"]),
            ))


class TestComputeLayersNetworkx:
    """Same tests using the networkx path when available."""
    def setup_method(self):
        try:
            import networkx  # noqa: F401
            self.has_nx = True
        except ImportError:
            self.has_nx = False

    def _run(self, agent_list):
        if not self.has_nx:
            pytest.skip("networkx not installed")
        from orchestrator.dag import build_graph, compute_layers
        g = build_graph(agent_list)
        return compute_layers(g)

    def test_fan_in_nx(self):
        result = self._run(agents(
            ("r1", []), ("r2", []), ("r3", []),
            ("syn", ["r1", "r2", "r3"]),
        ))
        assert result[0] == ["r1", "r2", "r3"]
        assert result[1] == ["syn"]

    def test_cycle_nx(self):
        if not self.has_nx:
            pytest.skip("networkx not installed")
        from orchestrator.dag import build_graph, assert_no_cycle
        from orchestrator.exceptions import CycleDetectedError
        g = build_graph(agents(("a", ["b"]), ("b", ["a"])))
        with pytest.raises(CycleDetectedError):
            assert_no_cycle(g)
