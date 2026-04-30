"""One-shot setup script for the editorial-cyclic-board example pipeline.

Creates the full directory tree under <orchestrator-v3.5>/pipelines/
editorial-cyclic-board/ including: pipeline.json, config.json, per-agent
01_system.md / 02_prompt.md, and 00_config.json blocks for the agents that
declare schemas or approval routes. Idempotent — safe to re-run.

Self-contained — no configurator imports. Resolves the pipelines root by
locating its sibling `pipelines/` directory (this script lives next to
cli.py in the orchestrator project root).

Run from the orchestrator-v3.5 project root:
    python _setup_editorial_pipeline.py
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone

PNAME = "editorial-cyclic-board"
ROOT = Path(__file__).resolve().parent / "pipelines" / PNAME

# ── Agent roster ─────────────────────────────────────────────────────────────
# (id, type, description, depends_on, secret_refs, schema, approval)
#   secret_refs   — list of <<secret:NAME>> tokens to inject into 02_prompt.md
#   schema        — dict with optional "input_schema" / "output_schema" blocks
#   approval      — dict with "requires_approval" + "approval_routes" (gates only)

AGENTS = [
    {
        "id": "pm", "type": "orchestrator",
        "description": "Project Manager — coordinates the editorial workflow, "
                       "receives the topic request, and is the escalation target "
                       "when the cycle limit or a deadlock is hit.",
        "depends_on": [],
        "secrets": [], "schema": {}, "approval": {},
    },
    {
        "id": "001_intake", "type": "orchestrator",
        "description": "Normalises the incoming topic request into a "
                       "structured brief (title, audience, language, deadline).",
        "depends_on": ["pm"],
        "secrets": [], "schema": {}, "approval": {},
    },
    {
        "id": "002_researcher", "type": "worker",
        "description": "Gathers source material from the research API and "
                       "compiles a list of URLs, quotes, and key facts.",
        "depends_on": ["001_intake"],
        "secrets": ["RESEARCH_API_KEY"],
        "schema": {}, "approval": {},
    },
    {
        "id": "003_outliner", "type": "worker",
        "description": "Produces a structured outline from the research dossier, "
                       "with an Outline section, a Sources section, and a Risks "
                       "section flagging contested claims.",
        "depends_on": ["002_researcher"],
        "secrets": [],
        "schema": {
            "output_schema": {
                "mode": ["has_sections"],
                "sections": ["Outline", "Sources", "Risks"],
            }
        },
        "approval": {},
    },
    {
        "id": "004_drafter", "type": "worker",
        "description": "Writes the article draft from the outline. Receives "
                       "feedback / task messages from the fact-checker and the "
                       "two HITL gates when they reject.",
        "depends_on": ["003_outliner"],
        "secrets": [],
        "schema": {
            "input_schema": {
                "mode": ["has_sections"],
                "sections": ["Outline", "Sources"],
                "require_upstream": ["003_outliner"],
            },
            "output_schema": {
                "mode": ["has_sections"],
                "sections": ["Title", "Body", "References"],
            },
        },
        "approval": {},
    },
    {
        "id": "005_fact_checker", "type": "validator",
        "description": "Verifies factual claims in the draft against sources. "
                       "Sends a peer feedback message to the drafter if a claim "
                       "is unsupported.",
        "depends_on": ["004_drafter"],
        "secrets": [],
        "schema": {
            "input_schema": {
                "mode": ["has_sections"],
                "sections": ["Body", "References"],
            }
        },
        "approval": {},
    },
    {
        "id": "006_copy_editor", "type": "worker",
        "description": "Edits grammar, style, and tone. Does not change facts.",
        "depends_on": ["005_fact_checker"],
        "secrets": [],
        "schema": {
            "input_schema": {"mode": ["contains"], "contains": ["Body"]},
        },
        "approval": {},
    },
    {
        "id": "007_translator", "type": "worker",
        "description": "Produces a localised version using the translation API. "
                       "Reads the target locale from {{DEFAULT_LANGUAGE}}.",
        "depends_on": ["006_copy_editor"],
        "secrets": ["TRANSLATION_API_KEY"],
        "schema": {}, "approval": {},
    },
    {
        "id": "008_legal_reviewer", "type": "reviewer",
        "description": "Legal compliance gate (HITL). On reject, sends the "
                       "draft + reviewer note back to the drafter as a feedback "
                       "message; on approve, the pipeline continues normally.",
        "depends_on": ["007_translator"],
        "secrets": [],
        "schema": {
            "input_schema": {"mode": ["contains"], "contains": ["Body"]},
        },
        "approval": {
            "requires_approval": True,
            "approval_routes": {
                "on_reject": {
                    "target": "004_drafter",
                    "include": ["output", "note"],
                    "mode": "feedback",
                }
            },
        },
    },
    {
        "id": "009_seo_optimizer", "type": "worker",
        "description": "Generates SEO metadata (title tag, meta description, "
                       "tags) as a JSON object that downstream agents consume.",
        "depends_on": ["008_legal_reviewer"],
        "secrets": [],
        "schema": {
            "output_schema": {
                "mode": ["json"],
                "json_schema": {
                    "type": "object",
                    "required": ["title", "meta_description", "tags"],
                    "properties": {
                        "title": {"type": "string"},
                        "meta_description": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                },
            }
        },
        "approval": {},
    },
    {
        "id": "010_image_generator", "type": "worker",
        "description": "Generates a hero image via the image API and writes a "
                       "caption.",
        "depends_on": ["009_seo_optimizer"],
        "secrets": ["IMAGE_API_KEY"],
        "schema": {}, "approval": {},
    },
    {
        "id": "011_chief_editor", "type": "reviewer",
        "description": "Final HITL gate — chief editor signs off. On reject, "
                       "sends the draft + note + full context back to the "
                       "drafter as a TASK message (resumes the drafter with a "
                       "new run). On approve, the publisher takes over.",
        "depends_on": ["009_seo_optimizer", "010_image_generator"],
        "secrets": [],
        "schema": {
            "input_schema": {
                "mode": ["has_sections"],
                "sections": ["Title", "Body", "References"],
            },
        },
        "approval": {
            "requires_approval": True,
            "approval_routes": {
                "on_reject": {
                    "target": "004_drafter",
                    "include": ["output", "note", "full_context"],
                    "mode": "task",
                }
            },
        },
    },
    {
        "id": "012_publisher", "type": "worker",
        "description": "Publishes the approved article to the CMS and records "
                       "the canonical URL. Reads CMS credentials from the "
                       "vault.",
        "depends_on": ["011_chief_editor"],
        "secrets": ["CMS_API_TOKEN", "CMS_DB_PASSWORD"],
        "schema": {
            "input_schema": {
                "mode": ["json"],
                "json_schema": {
                    "type": "object",
                    "required": ["approved", "title", "body"],
                    "properties": {
                        "approved": {"type": "boolean"},
                        "title": {"type": "string"},
                        "body":  {"type": "string"},
                    },
                },
            }
        },
        "approval": {},
    },
]


# ── Edge list (cyclic only — feedback / peer, all directed:false) ────────────
EDGES = [
    # Fact-checker can ping the drafter back when claims are unsupported.
    {"from": "005_fact_checker", "to": "004_drafter",
     "type": "peer", "directed": False},
    # Legal reviewer's HITL on_reject route — feedback edge.
    {"from": "008_legal_reviewer", "to": "004_drafter",
     "type": "feedback", "directed": False},
    # Chief editor's HITL on_reject route — feedback edge (mode is 'task'
    # at runtime; the edge type only signals graph topology to the engine).
    {"from": "011_chief_editor", "to": "004_drafter",
     "type": "feedback", "directed": False},
]


# ── Prompts ──────────────────────────────────────────────────────────────────
def system_prompt(agent: dict) -> str:
    return (
        f"<role>{agent['description']}</role>\n\n"
        f"<responsibilities>\n"
        f"- Stay strictly within the {agent['id']} role described above.\n"
        f"- Read every input you are given before producing output.\n"
        f"- Produce output in the structure declared by your "
        f"  output_schema (when present).\n"
        f"</responsibilities>\n\n"
        f"<guardrails>\n"
        f"- Never expose secret values: use the <<secret:NAME>> placeholder "
        f"  syntax in any text you produce.\n"
        f"- Never mutate upstream artifacts — only write your own outputs.\n"
        f"</guardrails>\n"
    )


def task_prompt(agent: dict) -> str:
    secret_block = ""
    if agent["secrets"]:
        lines = ["When calling external services, authenticate with:"]
        for s in agent["secrets"]:
            lines.append(f"  - <<secret:{s}>>")
        secret_block = "\n" + "\n".join(lines) + "\n"

    schema_hint = ""
    out = (agent["schema"].get("output_schema") or {})
    if out.get("mode") == ["has_sections"] or "has_sections" in (out.get("mode") or []):
        sections = out.get("sections", [])
        schema_hint = ("\n<format>\n"
                       f"Structure your output with these markdown headings: "
                       f"{', '.join(sections)}.\n</format>\n")
    elif "json" in (out.get("mode") or []):
        schema_hint = ("\n<format>\n"
                       "Return a single JSON object that satisfies "
                       "your declared output_schema.json_schema.\n</format>\n")

    return (
        f"<description>\n{agent['description']}\n</description>\n\n"
        f"<goals>\nFulfil the role above for one editorial cycle.\n</goals>\n\n"
        f"<input>\nUpstream agent outputs (see depends_on).\n</input>\n\n"
        f"<output>\nA single artifact that downstream agents can consume.\n</output>\n"
        f"{schema_hint}"
        f"{secret_block}\n"
        f"<guardrails>\n"
        f"Never echo a literal secret value. Use <<secret:NAME>> markers.\n"
        f"</guardrails>\n"
    )


# ── Pipeline + config writers ─────────────────────────────────────────────────
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_pipeline_json() -> dict:
    return {
        "id": PNAME,
        "name": "Editorial Cyclic Board",
        "description": ("Cyclic 13-agent editorial pipeline that exercises "
                        "the v3.5 capability set: per-agent input/output "
                        "schemas, HITL approval routing with on-reject "
                        "feedback to the drafter, and vault-backed secret "
                        "markers in prompts."),
        "graph_mode": "cyclic",
        "graph_orientation": "vertical",
        "category": "demo",
        "labels": ["demo", "cyclic", "v3.5", "hitl", "vault"],
        "validate_taglines": False,
        "validated_taglines_system": ["role", "responsibilities", "guardrails"],
        "validated_taglines_task":
            ["description", "goals", "input", "output", "format", "guardrails"],
        "agents": [
            {
                "id":          a["id"],
                "name":        a["id"].replace("_", " ").title(),
                "type":        a["type"],
                "category":    "editorial",
                "description": a["description"],
                "depends_on":  a["depends_on"],
                "labels":      [],
            }
            for a in AGENTS
        ],
        "edges": EDGES,
        "termination": {
            "strategy": "all_done",
            "max_cycles": 8,
            "timeout_s": 7200,
            "on_cycle_limit": "escalate_pm",
            "on_deadlock": "escalate_pm",
            "deadlock_check_interval_s": 60,
        },
        "tags": {"domain": ["publishing", "editorial", "content",
                            "research", "review"]},
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


def build_config_json() -> dict:
    return {
        "approval": {
            "approver":        "editor-in-chief",
            "poll_interval_s": 10,
            "timeout_s":       7200,
        },
        "secrets": {"rehydrate_outputs": True},
        "substitutions": {
            "PUBLICATION_NAME": "Cogniflow Daily",
            "DEFAULT_LANGUAGE": "en",
        },
    }


def agent_config_json(agent: dict) -> dict | None:
    out: dict = {}
    out.update(agent["schema"])
    out.update(agent["approval"])
    return out or None


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "agents").mkdir(exist_ok=True)

    (ROOT / "pipeline.json").write_text(
        json.dumps(build_pipeline_json(), indent=2), encoding="utf-8")
    (ROOT / "config.json").write_text(
        json.dumps(build_config_json(), indent=2), encoding="utf-8")
    (ROOT / ".gitignore").write_text(
        ".state/\n**/.state/\npipelines/secrets.db\n**/pipelines/secrets.db\n",
        encoding="utf-8",
    )

    for a in AGENTS:
        adir = ROOT / "agents" / a["id"]
        adir.mkdir(parents=True, exist_ok=True)
        (adir / "01_system.md").write_text(system_prompt(a), encoding="utf-8")
        (adir / "02_prompt.md").write_text(task_prompt(a), encoding="utf-8")
        cfg = agent_config_json(a)
        cfg_path = adir / "00_config.json"
        if cfg:
            cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        elif cfg_path.exists():
            cfg_path.unlink()

    print(f"Pipeline created at: {ROOT}")
    print(f"Agents: {len(AGENTS)}")
    print(f"Edges:  {len(EDGES)}")
    print(f"Approval gates: "
          f"{sum(1 for a in AGENTS if a['approval'].get('requires_approval'))}")
    secrets = sorted({s for a in AGENTS for s in a['secrets']})
    print(f"Secret refs ({len(secrets)}): {', '.join(secrets)}")


if __name__ == "__main__":
    main()
