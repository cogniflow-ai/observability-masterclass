"""Cogniflow Configurator — server-side SVG graph renderer.

Supports both DAG (layered layout) and cyclic (SCC-grouped layout) pipelines.
"""
from __future__ import annotations
import json
import math
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

# Layout constants
NODE_W, NODE_H = 170, 54
H_GAP, V_GAP   = 80, 70
MARGIN          = 40

TYPE_COLORS = {
    "orchestrator": "#1E3A5F",
    "worker":       "#2E75B6",
    "reviewer":     "#1B6CA8",
    "synthesizer":  "#1E8449",
    "router":       "#8E44AD",
    "classifier":   "#D4A017",
    "validator":    "#C0392B",
    "summarizer":   "#5D6D7E",
}
DEFAULT_COLOR = "#555555"


# ── public entry point ────────────────────────────────────────────────────────

def build_svg(pipeline_dir: Path, data: dict | None = None,
              error_agent_ids: set[str] | None = None,
              orientation: str | None = None) -> str:
    """Render the pipeline graph.

    `error_agent_ids` — optional set of agent ids that currently have
    validation errors. Those nodes render in red with a flashing amber
    outline so the user can spot the culprit directly on the graph.

    `orientation` — "horizontal" (left→right, default) or "vertical"
    (top→bottom). If None, read `graph_orientation` from `data`.
    """
    if data is None:
        pj = pipeline_dir / "pipeline.json"
        if not pj.exists():
            return _empty_svg("pipeline.json not found")
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
        except Exception as e:
            return _empty_svg(f"JSON parse error: {e}")

    agents   = data.get("agents", [])
    edges    = data.get("edges", [])
    gmode    = data.get("graph_mode", "dag")
    err_ids  = error_agent_ids or set()
    orient   = (orientation
                or data.get("graph_orientation", "vertical")).lower()
    if orient not in ("horizontal", "vertical"):
        orient = "vertical"

    if not agents:
        return _empty_svg("No agents defined yet")

    if gmode == "dag":
        return _build_dag_svg(agents, gmode, err_ids, orient)
    else:
        return _build_cyclic_svg(agents, edges, gmode, err_ids, orient)


# ── DAG renderer ─────────────────────────────────────────────────────────────

def _build_dag_svg(agents: list[dict], gmode: str,
                   err_ids: set[str], orientation: str) -> str:
    ids   = [a["id"] for a in agents if isinstance(a, dict) and "id" in a]
    deps  = {a["id"]: a.get("depends_on", []) for a in agents if isinstance(a, dict) and "id" in a}

    layers = _compute_layers(ids, deps)
    layer_map: dict[str, int] = {}
    pos_in_layer: dict[str, int] = {}
    for li, layer in enumerate(layers):
        for pi, aid in enumerate(layer):
            layer_map[aid] = li
            pos_in_layer[aid] = pi

    n_layers = max(len(layers), 1)
    max_layer = max((len(l) for l in layers), default=1)

    # Cross-axis centering: narrower layers sit in the middle of the track
    # defined by the widest layer, not pinned to the left/top.
    vertical = orientation == "vertical"
    if vertical:
        W = MARGIN * 2 + max_layer * NODE_W + (max_layer - 1) * H_GAP
        H = MARGIN * 2 + n_layers * NODE_H + (n_layers - 1) * V_GAP
        row_max_px = max_layer * NODE_W + (max_layer - 1) * H_GAP
        def top_left(aid: str) -> tuple[float, float]:
            li = layer_map.get(aid, 0)
            pi = pos_in_layer.get(aid, 0)
            layer_len = len(layers[li]) if 0 <= li < len(layers) else 1
            row_px = layer_len * NODE_W + (layer_len - 1) * H_GAP
            offset = (row_max_px - row_px) / 2.0
            return (MARGIN + offset + pi * (NODE_W + H_GAP),
                    MARGIN + li * (NODE_H + V_GAP))
    else:
        W = MARGIN * 2 + n_layers * NODE_W + (n_layers - 1) * H_GAP
        H = MARGIN * 2 + max_layer * NODE_H + (max_layer - 1) * V_GAP
        col_max_px = max_layer * NODE_H + (max_layer - 1) * V_GAP
        def top_left(aid: str) -> tuple[float, float]:
            li = layer_map.get(aid, 0)
            pi = pos_in_layer.get(aid, 0)
            layer_len = len(layers[li]) if 0 <= li < len(layers) else 1
            col_px = layer_len * NODE_H + (layer_len - 1) * V_GAP
            offset = (col_max_px - col_px) / 2.0
            return (MARGIN + li * (NODE_W + H_GAP),
                    MARGIN + offset + pi * (NODE_H + V_GAP))

    node_centers: dict[str, tuple[float, float]] = {}
    for aid in ids:
        x, y = top_left(aid)
        node_centers[aid] = (x + NODE_W / 2, y + NODE_H / 2)

    parts = [_svg_header(W, H), _svg_defs()]

    # Edges (depends_on = solid arrows)
    for aid in ids:
        tx, ty = node_centers.get(aid, (0, 0))
        for dep in deps.get(aid, []):
            if dep in node_centers:
                sx, sy = node_centers[dep]
                parts.append(_arrow(sx, sy, tx, ty, "solid", "#888888"))

    # Nodes
    for a in agents:
        if not isinstance(a, dict) or "id" not in a:
            continue
        aid   = a["id"]
        atype = a.get("type", "worker")
        x, y = top_left(aid)
        layer_num = layer_map.get(aid, 0) + 1
        parts.append(_node_box(x, y, aid, atype, f"L{layer_num}",
                               has_error=aid in err_ids))

    parts.append("</svg>")
    return "".join(parts)


# ── Cyclic renderer ───────────────────────────────────────────────────────────

def _build_cyclic_svg(agents: list[dict], edges: list[dict], gmode: str,
                      err_ids: set[str], orientation: str) -> str:
    ids  = [a["id"] for a in agents if isinstance(a, dict) and "id" in a]
    deps = {a["id"]: a.get("depends_on", []) for a in agents if isinstance(a, dict) and "id" in a}

    # Build full adjacency for SCC (include all edge types)
    adj: dict[str, list[str]] = {aid: list(deps.get(aid, [])) for aid in ids}
    for e in edges:
        if isinstance(e, dict):
            ef, et = e.get("from"), e.get("to")
            if ef in adj and et and et not in adj[ef]:
                adj[ef].append(et)

    sccs = _tarjan_sccs(ids, adj)
    scc_map: dict[str, int] = {}
    for si, scc in enumerate(sccs):
        for aid in scc:
            scc_map[aid] = si

    # (scc_idx, position_within_scc) — axis assignment happens per-orientation.
    scc_idx: dict[str, int] = {}
    pos_within: dict[str, int] = {}
    for si, scc in enumerate(sccs):
        for ni, aid in enumerate(scc):
            scc_idx[aid] = si
            pos_within[aid] = ni

    n_sccs = len(sccs)
    max_scc = max((len(s) for s in sccs), default=1)

    vertical = orientation == "vertical"
    if vertical:
        # SCCs become columns (left→right); nodes stack within each column.
        # Centering: a small SCC sits vertically centered in the track of the
        # tallest SCC rather than pinned to the top.
        W = MARGIN * 2 + 30 + n_sccs * NODE_W + (n_sccs - 1) * (H_GAP + 20) + 30
        H = MARGIN * 2 + 20 + max_scc * NODE_H + (max_scc - 1) * V_GAP + 20
        col_max_px = max_scc * NODE_H + (max_scc - 1) * V_GAP
        def top_left(aid: str) -> tuple[float, float]:
            si = scc_idx.get(aid, 0)
            ni = pos_within.get(aid, 0)
            scc_len = len(sccs[si]) if 0 <= si < n_sccs else 1
            col_px = scc_len * NODE_H + (scc_len - 1) * V_GAP
            offset = (col_max_px - col_px) / 2.0
            return (MARGIN + 30 + si * (NODE_W + H_GAP + 20),
                    MARGIN + 20 + offset + ni * (NODE_H + V_GAP))
    else:
        # SCCs become rows (top→bottom); nodes spread within each row.
        # Centering: a small SCC sits horizontally centered in the track of
        # the widest SCC rather than pinned to the left.
        W = MARGIN * 2 + max_scc * NODE_W + (max_scc - 1) * H_GAP + 60
        H = MARGIN * 2 + n_sccs * NODE_H + (n_sccs - 1) * (V_GAP + 20) + 40
        row_max_px = max_scc * NODE_W + (max_scc - 1) * H_GAP
        def top_left(aid: str) -> tuple[float, float]:
            si = scc_idx.get(aid, 0)
            ni = pos_within.get(aid, 0)
            scc_len = len(sccs[si]) if 0 <= si < n_sccs else 1
            row_px = scc_len * NODE_W + (scc_len - 1) * H_GAP
            offset = (row_max_px - row_px) / 2.0
            return (MARGIN + 30 + offset + ni * (NODE_W + H_GAP),
                    MARGIN + 20 + si * (NODE_H + V_GAP + 20))

    node_centers: dict[str, tuple[float, float]] = {}
    for aid in ids:
        x, y = top_left(aid)
        node_centers[aid] = (x + NODE_W / 2, y + NODE_H / 2)

    parts = [_svg_header(W, H), _svg_defs()]

    # SCC bounding boxes — dimension flips depending on orientation.
    for si, scc in enumerate(sccs):
        if len(scc) > 1:
            # Compute bounds from the member nodes' top-left corners.
            tls = [top_left(aid) for aid in scc]
            xs = [p[0] for p in tls]
            ys = [p[1] for p in tls]
            x0 = min(xs) - 10
            y0 = min(ys) - 10
            x1 = max(xs) + NODE_W + 10
            y1 = max(ys) + NODE_H + 10
            parts.append(
                f'<rect x="{x0}" y="{y0}" width="{x1-x0}" height="{y1-y0}" '
                f'rx="8" fill="none" stroke="#1B6CA8" stroke-width="1.5" '
                f'stroke-dasharray="6,3" opacity="0.6"/>'
                f'<text x="{x0+6}" y="{y0-3}" font-size="10" fill="#1B6CA8" '
                f'font-family="Arial">SCC {si+1}</text>'
            )

    # depends_on edges
    for aid in ids:
        tx, ty = node_centers.get(aid, (0, 0))
        for dep in deps.get(aid, []):
            if dep in node_centers:
                sx, sy = node_centers[dep]
                parts.append(_arrow(sx, sy, tx, ty, "solid", "#888888"))

    # extra cyclic edges
    for e in edges:
        if not isinstance(e, dict):
            continue
        ef, et, etype = e.get("from"), e.get("to"), e.get("type", "feedback")
        if ef in node_centers and et in node_centers:
            sx, sy = node_centers[ef]
            tx, ty = node_centers[et]
            style = "dashed" if etype == "feedback" else "peer"
            color = "#C0392B" if etype == "feedback" else "#8E44AD"
            parts.append(_arrow(sx, sy, tx, ty, style, color))

    # Nodes
    for a in agents:
        if not isinstance(a, dict) or "id" not in a:
            continue
        aid   = a["id"]
        atype = a.get("type", "worker")
        x, y = top_left(aid)
        si   = scc_map.get(aid, 0)
        parts.append(_node_box(x, y, aid, atype, f"S{si+1}",
                               has_error=aid in err_ids))

    # Legend
    parts.append(_cyclic_legend(W))
    parts.append("</svg>")
    return "".join(parts)


# ── SVG primitives ────────────────────────────────────────────────────────────

def _svg_header(W: float, H: float) -> str:
    W, H = max(int(W), 300), max(int(H), 120)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'style="font-family:Arial,sans-serif;background:transparent;">'
    )


def _svg_defs() -> str:
    return """
<defs>
  <marker id="arr-solid" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
    <path d="M0,0 L8,3 L0,6 Z" fill="#888888"/>
  </marker>
  <marker id="arr-dashed" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
    <path d="M0,0 L8,3 L0,6 Z" fill="#C0392B"/>
  </marker>
  <marker id="arr-peer-end" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
    <path d="M0,0 L8,3 L0,6 Z" fill="#8E44AD"/>
  </marker>
  <marker id="arr-peer-start" markerWidth="8" markerHeight="6" refX="1" refY="3" orient="auto-start-reverse">
    <path d="M0,3 L8,0 L8,6 Z" fill="#8E44AD"/>
  </marker>
</defs>"""


def _node_box(x: float, y: float, aid: str, atype: str, badge: str,
              *, has_error: bool = False) -> str:
    color = TYPE_COLORS.get(atype, DEFAULT_COLOR)
    label = aid if len(aid) <= 20 else aid[:18] + "…"
    type_label = atype[:12] if atype else ""

    node_class = "agent-node-error" if has_error else "agent-node"
    fill_color = "#C0392B" if has_error else color

    # The base node rect. When errored it carries an extra class so inline-SVG
    # CSS can re-style it (kept simple: just the red fill via `fill=` too, so
    # the SVG still looks right when downloaded as a file without the page CSS).
    parts = [
        f'<rect x="{x}" y="{y}" width="{NODE_W}" height="{NODE_H}" '
        f'rx="6" fill="{fill_color}" opacity="0.9" class="{node_class}"/>',
    ]

    # Flashing amber outline overlay for errored nodes.
    if has_error:
        ox, oy = x - 3, y - 3
        ow, oh = NODE_W + 6, NODE_H + 6
        parts.append(
            f'<rect class="agent-node-flash" '
            f'x="{ox}" y="{oy}" width="{ow}" height="{oh}" rx="9" '
            f'fill="none" stroke="#D4A017" stroke-width="3" '
            f'stroke-dasharray="7 4" pointer-events="none">'
            # SMIL animation so even the downloaded standalone SVG flashes.
            f'<animate attributeName="stroke-opacity" '
            f'values="1;0.25;1" dur="1.2s" repeatCount="indefinite"/>'
            f'<animate attributeName="stroke-width" '
            f'values="3;1.5;3" dur="1.2s" repeatCount="indefinite"/>'
            f'</rect>'
        )

    parts.append(
        f'<text x="{x+NODE_W/2}" y="{y+20}" text-anchor="middle" '
        f'font-size="11" font-weight="bold" fill="white">{label}</text>'
        f'<text x="{x+NODE_W/2}" y="{y+34}" text-anchor="middle" '
        f'font-size="9" fill="rgba(255,255,255,0.8)">{type_label}</text>'
        f'<text x="{x+NODE_W-6}" y="{y+12}" text-anchor="end" '
        f'font-size="9" fill="rgba(255,255,255,0.6)">{badge}</text>'
    )
    return "".join(parts)


def _arrow(sx: float, sy: float, tx: float, ty: float,
           style: str, color: str) -> str:
    # Offset endpoints to node edges
    dx, dy = tx - sx, ty - sy
    dist = math.hypot(dx, dy) or 1
    ox, oy = (NODE_W / 2) * dx / dist, (NODE_H / 2) * dy / dist
    x1, y1 = sx + ox, sy + oy
    x2, y2 = tx - ox - 8 * dx / dist, ty - oy - 8 * dy / dist

    if style == "dashed":
        return (
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{color}" stroke-width="1.5" stroke-dasharray="5,3" '
            f'marker-end="url(#arr-dashed)"/>'
        )
    elif style == "peer":
        return (
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{color}" stroke-width="1.5" '
            f'marker-start="url(#arr-peer-start)" marker-end="url(#arr-peer-end)"/>'
        )
    else:
        return (
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{color}" stroke-width="1.5" '
            f'marker-end="url(#arr-solid)"/>'
        )


def _cyclic_legend(svg_w: float) -> str:
    x, y = 8, 8
    return (
        f'<rect x="{x}" y="{y}" width="160" height="60" rx="4" '
        f'fill="white" opacity="0.85" stroke="#cccccc" stroke-width="1"/>'
        f'<line x1="{x+8}" y1="{y+14}" x2="{x+36}" y2="{y+14}" '
        f'stroke="#888" stroke-width="1.5" marker-end="url(#arr-solid)"/>'
        f'<text x="{x+42}" y="{y+18}" font-size="9" fill="#333">depends_on</text>'
        f'<line x1="{x+8}" y1="{y+30}" x2="{x+36}" y2="{y+30}" '
        f'stroke="#C0392B" stroke-width="1.5" stroke-dasharray="4,2" '
        f'marker-end="url(#arr-dashed)"/>'
        f'<text x="{x+42}" y="{y+34}" font-size="9" fill="#333">feedback</text>'
        f'<line x1="{x+8}" y1="{y+46}" x2="{x+36}" y2="{y+46}" '
        f'stroke="#8E44AD" stroke-width="1.5" '
        f'marker-start="url(#arr-peer-start)" marker-end="url(#arr-peer-end)"/>'
        f'<text x="{x+42}" y="{y+50}" font-size="9" fill="#333">peer</text>'
    )


def _empty_svg(msg: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 100" '
        f'width="400" height="100" style="font-family:Arial">'
        f'<rect width="400" height="100" rx="6" fill="#f8f8f8" stroke="#ddd"/>'
        f'<text x="200" y="55" text-anchor="middle" font-size="13" fill="#888">{msg}</text>'
        f'</svg>'
    )


# ── Graph algorithms ──────────────────────────────────────────────────────────

def _compute_layers(ids: list[str], deps: dict[str, list[str]]) -> list[list[str]]:
    """Kahn's algorithm — returns list of layers."""
    in_deg: dict[str, int] = {aid: 0 for aid in ids}
    for aid in ids:
        for dep in deps.get(aid, []):
            if dep in in_deg:
                in_deg[aid] += 1

    queue = deque(aid for aid in ids if in_deg[aid] == 0)
    layers: list[list[str]] = []
    visited: set[str] = set()

    while queue:
        layer = list(queue)
        layers.append(layer)
        queue.clear()
        for aid in layer:
            visited.add(aid)
            # find agents that depend on aid
            for other in ids:
                if aid in deps.get(other, []) and other not in visited:
                    in_deg[other] -= 1
                    if in_deg[other] == 0:
                        queue.append(other)

    # append any remaining (cycles / disconnected)
    remaining = [aid for aid in ids if aid not in visited]
    if remaining:
        layers.append(remaining)

    return layers


def _tarjan_sccs(ids: list[str], adj: dict[str, list[str]]) -> list[list[str]]:
    """Tarjan's SCC algorithm. Returns list of SCCs in reverse topological order."""
    index_counter = [0]
    stack: list[str] = []
    lowlink: dict[str, int] = {}
    index: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    sccs: list[list[str]] = []

    def strongconnect(v: str):
        index[v] = index_counter[0]
        lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack[v] = True
        for w in adj.get(v, []):
            if w not in ids:
                continue
            if w not in index:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif on_stack.get(w):
                lowlink[v] = min(lowlink[v], index[w])
        if lowlink[v] == index[v]:
            scc: list[str] = []
            while True:
                w = stack.pop()
                on_stack[w] = False
                scc.append(w)
                if w == v:
                    break
            sccs.append(scc)

    import sys
    sys.setrecursionlimit(max(1000, len(ids) * 10))
    for v in ids:
        if v not in index:
            strongconnect(v)

    return sccs
