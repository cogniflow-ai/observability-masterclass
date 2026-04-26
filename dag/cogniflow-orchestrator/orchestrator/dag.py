"""
Cogniflow Orchestrator — DAG resolution.

Uses networkx for graph operations so the topological sort,
cycle detection, and layer extraction are correct, tested,
and O(V+E) — not the O(n²) grep loops of the Bash version.
"""

from __future__ import annotations
from typing import Any

try:
    import networkx as nx
    _HAS_NX = True
except ImportError:
    _HAS_NX = False

from .exceptions import CycleDetectedError


AgentDef = dict[str, Any]   # One entry from pipeline.json["agents"]


# ── Public API ────────────────────────────────────────────────────────────────

def build_graph(agents: list[AgentDef]) -> "nx.DiGraph":
    """
    Build a directed graph from the pipeline agent list.
    Edge A → B means B depends on A (A must run before B).
    """
    if not _HAS_NX:
        raise ImportError(
            "networkx is required: pip install networkx"
        )
    g = nx.DiGraph()
    for agent in agents:
        g.add_node(agent["id"], **{k: v for k, v in agent.items() if k != "id"})
    for agent in agents:
        for dep in agent.get("depends_on", []):
            g.add_edge(dep, agent["id"])
    return g


def assert_no_cycle(g: "nx.DiGraph") -> None:
    """
    Raise CycleDetectedError if the graph contains a cycle.
    networkx.find_cycle raises NetworkXNoCycle if the graph is a DAG.
    """
    try:
        cycle_edges = nx.find_cycle(g, orientation="original")
        cycle_nodes = [e[0] for e in cycle_edges] + [cycle_edges[-1][1]]
        raise CycleDetectedError(cycle_nodes)
    except nx.NetworkXNoCycle:
        pass  # Graph is acyclic — good


def compute_layers(g: "nx.DiGraph") -> list[list[str]]:
    """
    Return agents grouped into execution layers using Kahn's algorithm
    (implemented here via networkx's topological_generations).

    Agents in the same layer have no ordering dependency and can
    run in parallel.  Layers are ordered: layer[0] runs first.

    Example:
        001, 002, 003 → all in layer 0 (parallel)
        004           → layer 1 (fan-in, waits for layer 0)
        005, 006      → layer 2 (parallel)
        007           → layer 3 (fan-in, final)
    """
    assert_no_cycle(g)
    layers = []
    for generation in nx.topological_generations(g):
        layers.append(sorted(generation))   # sorted for deterministic order
    return layers


def get_dependencies(g: "nx.DiGraph", agent_id: str) -> list[str]:
    """Return the direct predecessors of agent_id (its depends_on list)."""
    return list(g.predecessors(agent_id))


def get_dependents(g: "nx.DiGraph", agent_id: str) -> list[str]:
    """Return agents that directly depend on agent_id."""
    return list(g.successors(agent_id))


def all_ancestors(g: "nx.DiGraph", agent_id: str) -> set[str]:
    """Return all transitive upstream agents of agent_id."""
    return nx.ancestors(g, agent_id)


def all_descendants(g: "nx.DiGraph", agent_id: str) -> set[str]:
    """Return all transitive downstream agents of agent_id."""
    return nx.descendants(g, agent_id)


# ── Fallback (no networkx) ────────────────────────────────────────────────────

def compute_layers_fallback(agents: list[AgentDef]) -> list[list[str]]:
    """
    Pure-Python Kahn's algorithm used when networkx is not installed.
    O(n²) but correct for small pipelines (< 50 agents).
    """
    id_to_deps: dict[str, set[str]] = {
        a["id"]: set(a.get("depends_on", [])) for a in agents
    }
    placed: set[str] = set()
    layers: list[list[str]] = []
    remaining = set(id_to_deps.keys())

    while remaining:
        layer = sorted(
            aid for aid in remaining
            if id_to_deps[aid].issubset(placed)
        )
        if not layer:
            cycle = sorted(remaining)
            raise CycleDetectedError(cycle)
        layers.append(layer)
        placed.update(layer)
        remaining -= set(layer)

    return layers
