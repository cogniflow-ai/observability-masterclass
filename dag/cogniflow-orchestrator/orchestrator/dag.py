"""
Cogniflow Orchestrator v3.0 — DAG loader.

Unchanged from v2.1.0.  Builds a directed acyclic graph from
pipeline.json (depends_on format) and performs topological layering
for parallel execution.

v3.0 adds is_cyclic_pipeline() for mode detection.
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any

try:
    import networkx as nx
    _HAS_NX = True
except ImportError:
    _HAS_NX = False

from .exceptions import CycleDetectedError


def load_pipeline(pipeline_dir: Path) -> dict[str, Any]:
    """Load and return the parsed pipeline.json."""
    path = pipeline_dir / "pipeline.json"
    if not path.exists():
        raise FileNotFoundError(f"No pipeline.json found in {pipeline_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


def is_cyclic_pipeline(spec: dict[str, Any]) -> bool:
    """
    Return True if the pipeline spec contains cyclic edges (feedback or peer).
    Used by core.py for mode detection (REQ-EXEC-001).
    """
    for edge in spec.get("edges", []):
        if edge.get("type") in ("feedback", "peer"):
            return True
    # Also check if any depends_on forms a cycle (shouldn't, but be safe)
    return False


def build_dag(spec: dict[str, Any]) -> list[list[str]]:
    """
    Build a layered execution plan from depends_on structure.

    Returns a list of layers; each layer is a list of agent IDs that
    can run in parallel.  Raises CycleDetectedError if a cycle exists.
    """
    agents = {a["id"]: a for a in spec["agents"]}
    deps   = {a["id"]: list(a.get("depends_on", [])) for a in spec["agents"]}

    if _HAS_NX:
        return _nx_layers(agents, deps)
    return _kahn_layers(agents, deps)


def _nx_layers(agents: dict, deps: dict) -> list[list[str]]:
    G = nx.DiGraph()
    G.add_nodes_from(agents)
    for aid, dep_list in deps.items():
        for dep in dep_list:
            G.add_edge(dep, aid)
    if not nx.is_directed_acyclic_graph(G):
        raise CycleDetectedError(
            "pipeline.json contains a cycle.  Use feedback/peer edges for "
            "intentional cycles, or remove the circular depends_on dependency."
        )
    return list(nx.topological_generations(G))


def _kahn_layers(agents: dict, deps: dict) -> list[list[str]]:
    """Pure-Python Kahn's algorithm — used when networkx is not installed."""
    in_degree: dict[str, int] = {aid: 0 for aid in agents}
    successors: dict[str, list[str]] = {aid: [] for aid in agents}

    for aid, dep_list in deps.items():
        for dep in dep_list:
            successors[dep].append(aid)
            in_degree[aid] += 1

    queue = deque(aid for aid, d in in_degree.items() if d == 0)
    layers: list[list[str]] = []
    visited = 0

    while queue:
        # Collect all zero-in-degree nodes into one layer
        layer = list(queue)
        queue.clear()
        layers.append(layer)
        visited += len(layer)
        for aid in layer:
            for succ in successors[aid]:
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    queue.append(succ)

    if visited != len(agents):
        raise CycleDetectedError(
            "pipeline.json contains a cycle (Kahn detection).  Check depends_on."
        )
    return layers
