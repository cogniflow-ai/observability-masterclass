"""
Cogniflow Observer — server-side DAG SVG generation.

Generates a deterministic top-down grid SVG from pipeline.json.
No JavaScript layout engine. No external libraries.
Nodes are coloured by current agent status.
"""
from __future__ import annotations
import html
from pathlib import Path
from typing import Optional

# ── Colour map (fill, stroke, text) ───────────────────────────────────────

STATUS_COLOURS: dict[str, tuple[str, str, str]] = {
    "pending":           ("#E8E6DF", "#888780", "#444441"),
    "running":           ("#DCEEFB", "#1566A9", "#0C447C"),
    "done":              ("#D6ECC4", "#38690F", "#27500A"),
    "failed":            ("#FAD9D9", "#B02929", "#791F1F"),
    "timeout":           ("#FAE8D0", "#9A5800", "#633806"),
    "schema_invalid":    ("#FAD9D9", "#B02929", "#791F1F"),
    "awaiting_approval": ("#E8E4FB", "#5044B7", "#3C3489"),
    "bypassed":          ("#E8E6DF", "#888780", "#444441"),
    "rejected":          ("#FAE8D0", "#9A5800", "#633806"),
    "cancelled":         ("#FAE8D0", "#9A5800", "#633806"),
}
DEFAULT_COLOURS = ("#E6F1FB", "#185FA5", "#0C447C")


def _colours(status: str) -> tuple[str, str, str]:
    return STATUS_COLOURS.get(status, DEFAULT_COLOURS)


# ── Layer computation (self-contained, no orchestrator import) ─────────────

def compute_layers(agents: list[dict]) -> list[list[dict]]:
    """Kahn's algorithm — returns agents grouped into execution layers."""
    id_to_deps  = {a["id"]: set(a.get("depends_on", [])) for a in agents}
    id_to_agent = {a["id"]: a for a in agents}
    placed: set[str]      = set()
    remaining: set[str]   = set(id_to_deps.keys())
    layers: list[list[dict]] = []

    while remaining:
        layer_ids = sorted(
            aid for aid in remaining
            if id_to_deps[aid].issubset(placed)
        )
        if not layer_ids:
            # Cycle — just dump the rest into one layer
            layer_ids = sorted(remaining)
        for aid in layer_ids:
            remaining.discard(aid)
            placed.add(aid)
        layers.append([id_to_agent[aid] for aid in layer_ids])

    return layers


# ── SVG builder ────────────────────────────────────────────────────────────

def _xe(s: str) -> str:
    return html.escape(str(s))


def build_dag_svg(
    pipeline_json: dict,
    agent_statuses: dict[str, str],   # agent_id → status string
    width: int = 860,
) -> str:
    """
    Build a complete SVG string for the pipeline DAG.

    agent_statuses maps agent_id to its current status string.
    Returns an SVG element as a string (no DOCTYPE, no wrapper).
    """
    agents = pipeline_json.get("agents", [])
    if not agents:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="860" height="60"><text x="430" y="35" text-anchor="middle" font-family="Arial" font-size="14" fill="#888">No agents defined</text></svg>'

    layers = compute_layers(agents)

    # ── Layout constants ───────────────────────────────────────────────
    BOX_W      = 160
    BOX_H      = 52
    H_GAP      = 20    # gap between boxes in same row
    V_GAP      = 64    # vertical gap between rows
    TOP_PAD    = 72    # space above first row (title)
    BOT_PAD    = 56    # space below last row (legend)
    SIDE_PAD   = 40    # left/right margin

    n_layers   = len(layers)
    max_agents = max(len(l) for l in layers)

    # Determine required width
    min_needed = 2 * SIDE_PAD + max_agents * BOX_W + (max_agents - 1) * H_GAP
    svg_w      = max(width, min_needed)
    svg_h      = TOP_PAD + n_layers * BOX_H + (n_layers - 1) * V_GAP + BOT_PAD

    # ── Compute box centres ────────────────────────────────────────────
    centres: dict[str, tuple[float, float]] = {}
    for row_idx, layer in enumerate(layers):
        n  = len(layer)
        total_w = n * BOX_W + (n - 1) * H_GAP
        x_start = (svg_w - total_w) / 2
        cy = TOP_PAD + row_idx * (BOX_H + V_GAP) + BOX_H / 2
        for col_idx, agent in enumerate(layer):
            cx = x_start + col_idx * (BOX_W + H_GAP) + BOX_W / 2
            centres[agent["id"]] = (cx, cy)

    parts: list[str] = []

    # ── Arrow marker ───────────────────────────────────────────────────
    parts.append(
        '<defs>'
        '<marker id="dagar" viewBox="0 0 10 10" refX="8" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M2 1L8 5L2 9" fill="none" stroke="#AAAAAA" '
        'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
        '</marker>'
        '</defs>'
    )

    # ── Connectors ─────────────────────────────────────────────────────
    for row_idx, layer in enumerate(layers):
        if row_idx == 0:
            continue
        for agent in layer:
            deps = agent.get("depends_on", [])
            if not deps:
                deps = [a["id"] for a in layers[row_idx - 1]]
            x2, y2 = centres[agent["id"]]
            ty = y2 - BOX_H / 2
            for dep_id in deps:
                if dep_id not in centres:
                    continue
                x1, y1 = centres[dep_id]
                by = y1 + BOX_H / 2
                my = (by + ty) / 2
                parts.append(
                    f'<path d="M{x1:.1f},{by:.1f} '
                    f'C{x1:.1f},{my:.1f} {x2:.1f},{my:.1f} {x2:.1f},{ty:.1f}" '
                    f'fill="none" stroke="#BBBBBB" stroke-width="1.5" '
                    f'marker-end="url(#dagar)"/>'
                )

    # ── Layer labels (highlight the active layer — any running/awaiting agent) ──
    AMARANTO = "#9F2D35"
    LBL_W, LBL_H, LBL_X = 92, 20, 4
    for row_idx, layer in enumerate(layers):
        cy = TOP_PAD + row_idx * (BOX_H + V_GAP) + BOX_H / 2
        n  = len(layer)
        tag = f"L{row_idx} · {'parallel' if n > 1 else 'sequential'}"
        is_active = any(
            agent_statuses.get(a["id"]) in {"running", "awaiting_approval"}
            for a in layer
        )
        if is_active:
            parts.append(
                f'<g class="dag-layer-active">'
                f'<rect x="{LBL_X}" y="{cy - LBL_H/2:.1f}" width="{LBL_W}" '
                f'height="{LBL_H}" rx="4" fill="{AMARANTO}"/>'
                f'<text x="{LBL_X + LBL_W/2}" y="{cy:.1f}" font-family="Arial,sans-serif" '
                f'font-size="10" font-weight="bold" fill="#FFFFFF" text-anchor="middle" '
                f'dominant-baseline="central">{_xe(tag)}</text>'
                f'</g>'
            )
        else:
            parts.append(
                f'<text x="10" y="{cy:.1f}" font-family="Arial,sans-serif" '
                f'font-size="10" fill="#9A9890" dominant-baseline="central">{_xe(tag)}</text>'
            )

    # ── Boxes ──────────────────────────────────────────────────────────
    for agent in agents:
        aid    = agent["id"]
        status = agent_statuses.get(aid, "pending")
        fill, stroke, text_col = _colours(status)
        cx, cy = centres[aid]
        x = cx - BOX_W / 2
        y = cy - BOX_H / 2

        label = aid
        if len(label) > 20:
            label = label[:18] + "…"

        status_disp = {
            "pending":           "Waiting",
            "running":           "Running",
            "done":              "Complete",
            "failed":            "Failed",
            "timeout":           "Timed out",
            "awaiting_approval": "Needs approval",
            "bypassed":          "Skipped",
            "schema_invalid":    "Output rejected",
            "rejected":          "Rejected",
            "cancelled":         "Cancelled",
        }.get(status, status)

        g_classes = "dag-agent" + (" dag-running" if status == "running" else "")

        parts.append(
            f'<g class="{g_classes}" data-agent-id="{_xe(aid)}" data-status="{_xe(status)}">'
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{BOX_W}" height="{BOX_H}" '
            f'rx="8" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
            f'<text class="dag-agent-name" '
            f'x="{cx:.1f}" y="{cy - 9:.1f}" font-family="Arial,sans-serif" '
            f'font-size="11" font-weight="bold" fill="{text_col}" '
            f'text-anchor="middle" dominant-baseline="central">{_xe(label)}</text>'
            f'<text x="{cx:.1f}" y="{cy + 9:.1f}" font-family="Arial,sans-serif" '
            f'font-size="10" fill="{stroke}" '
            f'text-anchor="middle" dominant-baseline="central">{_xe(status_disp)}</text>'
            f'</g>'
        )

    # ── Legend ─────────────────────────────────────────────────────────
    legend_y = svg_h - 38
    legend_items = [
        ("Waiting",      "#E8E6DF", "#888780"),
        ("Running",      "#DCEEFB", "#1566A9"),
        ("Complete",     "#D6ECC4", "#38690F"),
        ("Needs approval","#E8E4FB","#5044B7"),
        ("Cancelled",    "#FAE8D0", "#9A5800"),
        ("Failed",       "#FAD9D9", "#B02929"),
    ]
    spacing = svg_w / len(legend_items)
    for i, (lbl, fill, stroke) in enumerate(legend_items):
        lx = spacing * i + spacing / 2
        parts.append(
            f'<rect x="{lx - 40:.0f}" y="{legend_y}" width="14" height="14" '
            f'rx="3" fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
            f'<text x="{lx - 22:.0f}" y="{legend_y + 7}" font-family="Arial,sans-serif" '
            f'font-size="10" fill="#5A5A56" dominant-baseline="central">{_xe(lbl)}</text>'
        )

    # ── Title ──────────────────────────────────────────────────────────
    name = _xe(pipeline_json.get("name", "Pipeline"))
    n_agents = len(agents)
    n_layers_n = len(layers)
    parts.append(
        f'<text x="{svg_w / 2:.0f}" y="24" font-family="Arial,sans-serif" '
        f'font-size="14" font-weight="bold" fill="#1A1A18" text-anchor="middle">{name}</text>'
        f'<text x="{svg_w / 2:.0f}" y="44" font-family="Arial,sans-serif" '
        f'font-size="11" fill="#9A9890" text-anchor="middle">'
        f'{n_agents} agents · {n_layers_n} layers</text>'
    )

    body = "\n".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="100%" viewBox="0 0 {svg_w} {svg_h:.0f}" '
        f'style="max-width:{svg_w}px">\n'
        f'<rect width="{svg_w}" height="{svg_h:.0f}" fill="transparent"/>\n'
        f'{body}\n'
        f'</svg>'
    )
