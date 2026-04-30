"""One-shot setup script for the model-release-dag example pipeline.

Creates an 18-agent **DAG** that exercises the v3.5 capability surface
appropriate to DAG mode: rich input/output schemas at every fan-in
junction, two HITL approval gates (without approval_routes — those are
cyclic-only), and 8 vault `<<secret:NAME>>` references spread across the
data-ingest, training, deployment, and monitoring stages.

Self-contained — no configurator imports. Resolves the pipelines root by
locating its sibling `pipelines/` directory (this script lives next to
cli.py in the orchestrator project root).

Run from the orchestrator-v3.5 project root:
    python _setup_release_dag_pipeline.py
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone

PNAME = "model-release-dag"
ROOT = Path(__file__).resolve().parent / "pipelines" / PNAME

# ── Agent roster ─────────────────────────────────────────────────────────────
# (id, type, description, depends_on, secret_refs, schema, approval)
#   schema   — dict with optional "input_schema" / "output_schema"
#   approval — only "requires_approval" in DAG mode (no routes)

AGENTS = [
    # ── Stage 1: data acquisition + scrubbing ─────────────────────────────
    {
        "id": "001_dataset_pull", "type": "worker",
        "description": "Pulls the raw training dataset from S3 and stages it "
                       "for downstream agents.",
        "depends_on": [],
        "secrets": ["AWS_S3_ACCESS_KEY", "AWS_S3_SECRET_KEY"],
        "schema": {
            "output_schema": {
                "mode": ["json"],
                "json_schema": {
                    "type": "object",
                    "required": ["staged_uri", "row_count_raw"],
                    "properties": {
                        "staged_uri":     {"type": "string"},
                        "row_count_raw":  {"type": "integer"},
                    },
                },
            }
        },
        "approval": {},
    },
    {
        "id": "002_pii_scrubber", "type": "worker",
        "description": "Removes PII columns and hashes any free-text fields "
                       "that may contain personal identifiers.",
        "depends_on": ["001_dataset_pull"],
        "secrets": [],
        "schema": {
            "input_schema": {
                "mode": ["json"],
                "json_schema": {
                    "type": "object",
                    "required": ["staged_uri"],
                },
            }
        },
        "approval": {},
    },

    # ── Stage 2: parallel quality + schema scanners (FAN-OUT 1) ──────────
    {
        "id": "003_quality_scanner", "type": "validator",
        "description": "Computes dataset quality stats: null fraction, "
                       "duplicate rate, class balance, range checks.",
        "depends_on": ["002_pii_scrubber"],
        "secrets": [],
        "schema": {
            "output_schema": {
                "mode": ["json"],
                "json_schema": {
                    "type": "object",
                    "required": ["row_count", "null_pct", "dup_pct",
                                 "class_balance"],
                    "properties": {
                        "row_count":     {"type": "integer"},
                        "null_pct":      {"type": "number"},
                        "dup_pct":       {"type": "number"},
                        "class_balance": {"type": "object"},
                    },
                },
            }
        },
        "approval": {},
    },
    {
        "id": "004_schema_validator", "type": "validator",
        "description": "Validates dataset schema against the expected feature "
                       "contract — types, ranges, allowed enums.",
        "depends_on": ["002_pii_scrubber"],
        "secrets": [],
        "schema": {
            "output_schema": {
                "mode": ["json"],
                "json_schema": {
                    "type": "object",
                    "required": ["valid", "errors"],
                    "properties": {
                        "valid":  {"type": "boolean"},
                        "errors": {"type": "array",
                                   "items": {"type": "string"}},
                    },
                },
            }
        },
        "approval": {},
    },

    # ── Stage 3: data prep (FAN-IN 1) ─────────────────────────────────────
    {
        "id": "005_train_split", "type": "worker",
        "description": "Splits the cleaned dataset into train / val / test "
                       "partitions using a deterministic seed.",
        "depends_on": ["003_quality_scanner", "004_schema_validator"],
        "secrets": [],
        "schema": {
            "input_schema": {
                "mode": ["json"],
                "require_upstream": ["004_schema_validator"],
                "json_schema": {
                    "type": "object",
                    "required": ["valid"],
                    "properties": {
                        "valid": {"const": True},
                    },
                },
            },
            "output_schema": {
                "mode": ["json"],
                "json_schema": {
                    "type": "object",
                    "required": ["train_uri", "val_uri", "test_uri", "seed"],
                },
            },
        },
        "approval": {},
    },
    {
        "id": "006_feature_engineer", "type": "worker",
        "description": "Materialises engineered features and writes them to "
                       "the feature store.",
        "depends_on": ["005_train_split"],
        "secrets": [],
        "schema": {
            "output_schema": {
                "mode": ["has_sections"],
                "sections": ["Features", "Transformations", "Notes"],
            }
        },
        "approval": {},
    },

    # ── Stage 4: training ─────────────────────────────────────────────────
    {
        "id": "007_trainer", "type": "worker",
        "description": "Trains the model using the train partition. Logs "
                       "training metrics to Weights & Biases and pushes the "
                       "checkpoint to the model registry.",
        "depends_on": ["006_feature_engineer"],
        "secrets": ["WANDB_API_KEY", "HUGGINGFACE_TOKEN"],
        "schema": {
            "output_schema": {
                "mode": ["json"],
                "json_schema": {
                    "type": "object",
                    "required": ["model_uri", "train_loss", "val_loss",
                                 "epochs"],
                    "properties": {
                        "model_uri":  {"type": "string"},
                        "train_loss": {"type": "number"},
                        "val_loss":   {"type": "number"},
                        "epochs":     {"type": "integer"},
                    },
                },
            }
        },
        "approval": {},
    },

    # ── Stage 5: parallel evaluation (FAN-OUT 2 — five evaluators) ──────
    {
        "id": "008_eval_accuracy", "type": "validator",
        "description": "Computes accuracy, precision, recall, F1, and AUROC "
                       "on the held-out test set.",
        "depends_on": ["007_trainer"],
        "secrets": [],
        "schema": {
            "output_schema": {
                "mode": ["json"],
                "json_schema": {
                    "type": "object",
                    "required": ["accuracy", "precision", "recall", "f1",
                                 "auroc"],
                    "properties": {
                        "accuracy":  {"type": "number"},
                        "precision": {"type": "number"},
                        "recall":    {"type": "number"},
                        "f1":        {"type": "number"},
                        "auroc":     {"type": "number"},
                    },
                },
            }
        },
        "approval": {},
    },
    {
        "id": "009_eval_fairness", "type": "validator",
        "description": "Computes per-group fairness metrics (demographic "
                       "parity, equalised odds) across protected attributes.",
        "depends_on": ["007_trainer"],
        "secrets": [],
        "schema": {
            "output_schema": {
                "mode": ["json"],
                "json_schema": {
                    "type": "object",
                    "required": ["demographic_parity",
                                 "equalised_odds_diff",
                                 "groups_evaluated"],
                },
            }
        },
        "approval": {},
    },
    {
        "id": "010_eval_robustness", "type": "validator",
        "description": "Runs adversarial-perturbation and out-of-distribution "
                       "robustness probes.",
        "depends_on": ["007_trainer"],
        "secrets": [],
        "schema": {
            "output_schema": {
                "mode": ["json"],
                "json_schema": {
                    "type": "object",
                    "required": ["adversarial_acc_drop", "ood_acc_drop"],
                },
            }
        },
        "approval": {},
    },
    {
        "id": "011_eval_latency", "type": "validator",
        "description": "Measures inference-time latency p50/p95/p99 and "
                       "memory footprint at production batch sizes.",
        "depends_on": ["007_trainer"],
        "secrets": [],
        "schema": {
            "output_schema": {
                "mode": ["json"],
                "json_schema": {
                    "type": "object",
                    "required": ["p50_ms", "p95_ms", "p99_ms",
                                 "peak_memory_mb"],
                },
            }
        },
        "approval": {},
    },
    {
        "id": "012_eval_explainability", "type": "validator",
        "description": "Generates a SHAP-based feature-importance report and "
                       "checks for spurious-correlation tells.",
        "depends_on": ["007_trainer"],
        "secrets": [],
        "schema": {
            "output_schema": {
                "mode": ["has_sections"],
                "sections": ["TopFeatures", "SuspectFeatures", "Notes"],
            }
        },
        "approval": {},
    },

    # ── Stage 6: aggregation + risk (FAN-IN 2 — joins all 5 evals) ──────
    {
        "id": "013_metric_aggregator", "type": "synthesizer",
        "description": "Aggregates the five evaluation reports into a single "
                       "release-readiness summary.",
        "depends_on": ["008_eval_accuracy", "009_eval_fairness",
                       "010_eval_robustness", "011_eval_latency",
                       "012_eval_explainability"],
        "secrets": [],
        "schema": {
            "input_schema": {
                "mode": ["json"],
                "require_upstream": ["008_eval_accuracy",
                                     "009_eval_fairness",
                                     "010_eval_robustness",
                                     "011_eval_latency"],
            },
            "output_schema": {
                "mode": ["has_sections"],
                "sections": ["Accuracy", "Fairness", "Robustness",
                             "Latency", "Explainability", "Summary"],
            },
        },
        "approval": {},
    },
    {
        "id": "014_risk_assessor", "type": "classifier",
        "description": "Translates the metric summary into a numeric risk "
                       "score and a release recommendation.",
        "depends_on": ["013_metric_aggregator"],
        "secrets": [],
        "schema": {
            "input_schema": {
                "mode": ["has_sections"],
                "sections": ["Accuracy", "Fairness", "Robustness",
                             "Latency"],
            },
            "output_schema": {
                "mode": ["json"],
                "json_schema": {
                    "type": "object",
                    "required": ["risk_score", "recommendation",
                                 "rationale"],
                    "properties": {
                        "risk_score": {"type": "number",
                                       "minimum": 0, "maximum": 1},
                        "recommendation": {"type": "string",
                                           "enum": ["release", "hold",
                                                    "block"]},
                        "rationale": {"type": "string"},
                    },
                },
            },
        },
        "approval": {},
    },

    # ── Stage 7: HITL gates (DAG mode — requires_approval only) ─────────
    {
        "id": "015_compliance_review", "type": "reviewer",
        "description": "Compliance-officer HITL gate. Reviews the risk "
                       "assessment for regulatory blockers (GDPR, AI Act, "
                       "sector-specific rules). Rejection halts the run.",
        "depends_on": ["014_risk_assessor"],
        "secrets": [],
        "schema": {
            "input_schema": {
                "mode": ["contains"],
                "contains": ["risk_score"],
            }
        },
        "approval": {"requires_approval": True},
    },
    {
        "id": "016_release_manager", "type": "reviewer",
        "description": "Release-manager HITL gate. Decides whether the model "
                       "can be deployed today. Looks at compliance signoff, "
                       "risk score, and current incident state.",
        "depends_on": ["015_compliance_review"],
        "secrets": [],
        "schema": {
            "input_schema": {
                "mode": ["has_sections"],
                "sections": ["Accuracy", "Fairness", "Robustness",
                             "Summary"],
            }
        },
        "approval": {"requires_approval": True},
    },

    # ── Stage 8: deployment + monitoring (parallel tail) ────────────────
    {
        "id": "017_deployer", "type": "worker",
        "description": "Deploys the approved model to the production "
                       "Kubernetes cluster behind a canary rollout.",
        "depends_on": ["016_release_manager"],
        "secrets": ["AWS_DEPLOY_ACCESS_KEY", "KUBE_CONFIG_TOKEN"],
        "schema": {
            "input_schema": {
                "mode": ["json"],
                "json_schema": {
                    "type": "object",
                    "required": ["approved", "model_uri"],
                    "properties": {
                        "approved":  {"type": "boolean"},
                        "model_uri": {"type": "string"},
                    },
                },
            },
            "output_schema": {
                "mode": ["json"],
                "json_schema": {
                    "type": "object",
                    "required": ["deployment_uri", "canary_pct",
                                 "rollback_plan"],
                },
            },
        },
        "approval": {},
    },
    {
        "id": "018_monitoring_setup", "type": "worker",
        "description": "Provisions Datadog dashboards, latency / drift / "
                       "error-rate alerts, and PagerDuty routing for the new "
                       "model version.",
        "depends_on": ["017_deployer"],
        "secrets": ["DATADOG_API_KEY", "PAGERDUTY_TOKEN"],
        "schema": {
            "output_schema": {
                "mode": ["has_sections"],
                "sections": ["Dashboards", "Alerts", "Runbook"],
            }
        },
        "approval": {},
    },
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
        f"- Never expose secret values in any output. Use the "
        f"  <<secret:NAME>> placeholder syntax.\n"
        f"- Never mutate upstream artifacts — only write your own outputs.\n"
        f"- Be deterministic where the spec asks for determinism (use the "
        f"  pipeline seed when relevant).\n"
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
    modes = out.get("mode") or []
    if "has_sections" in modes:
        sections = out.get("sections", [])
        schema_hint = ("\n<format>\nStructure your output with these markdown "
                       f"headings: {', '.join(sections)}.\n</format>\n")
    elif "json" in modes:
        schema_hint = ("\n<format>\nReturn a single JSON object that "
                       "satisfies your declared output_schema.json_schema.\n"
                       "</format>\n")

    return (
        f"<description>\n{agent['description']}\n</description>\n\n"
        f"<goals>\nFulfil the role above for this release cycle.\n</goals>\n\n"
        f"<input>\nUpstream agent outputs (see depends_on).\n</input>\n\n"
        f"<output>\nA single artifact downstream agents can consume.\n</output>\n"
        f"{schema_hint}"
        f"{secret_block}\n"
        f"<guardrails>\n"
        f"Never echo a literal secret value. Use <<secret:NAME>> markers in "
        f"any output text.\n"
        f"</guardrails>\n"
    )


# ── Pipeline + config writers ─────────────────────────────────────────────────
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_pipeline_json() -> dict:
    return {
        "id": PNAME,
        "name": "Model Release DAG",
        "description": ("Complex 18-agent DAG modelling a production ML "
                        "model release: ingest → scrub → parallel quality "
                        "scans → split → feature engineer → train → 5 "
                        "parallel evaluators → aggregate → risk → 2 HITL "
                        "gates → deploy → monitoring. Exercises every v3.5 "
                        "capability appropriate to DAG mode (schemas at "
                        "every fan-in, requires_approval gates, vault "
                        "secrets across ingest/train/deploy/monitor)."),
        "graph_mode": "dag",
        "graph_orientation": "vertical",
        "category": "demo",
        "labels": ["demo", "dag", "v3.5", "ml", "hitl", "vault"],
        "validate_taglines": False,
        "validated_taglines_system": ["role", "responsibilities", "guardrails"],
        "validated_taglines_task":
            ["description", "goals", "input", "output", "format", "guardrails"],
        "agents": [
            {
                "id":          a["id"],
                "name":        a["id"].replace("_", " ").title(),
                "type":        a["type"],
                "category":    "ml-release",
                "description": a["description"],
                "depends_on":  a["depends_on"],
                "labels":      [],
                "require_approval": bool(a["approval"].get("requires_approval")),
            }
            for a in AGENTS
        ],
        "edges": [],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


def build_config_json() -> dict:
    return {
        "approval": {
            "approver":        "release-board",
            "poll_interval_s": 15,
            "timeout_s":       14400,
        },
        "secrets": {"rehydrate_outputs": True},
        "substitutions": {
            "MODEL_NAME":       "claims-fraud-detector",
            "MODEL_VERSION":    "v2.7.0",
            "TARGET_CLUSTER":   "prod-us-east-1",
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

    secrets = sorted({s for a in AGENTS for s in a['secrets']})
    n_schema = sum(1 for a in AGENTS if a['schema'])
    n_appr = sum(1 for a in AGENTS if a['approval'].get('requires_approval'))
    print(f"Pipeline created at: {ROOT}")
    print(f"Agents: {len(AGENTS)}")
    print(f"Agents with schemas: {n_schema}")
    print(f"HITL gates (requires_approval): {n_appr}")
    print(f"Secret refs ({len(secrets)}): {', '.join(secrets)}")


if __name__ == "__main__":
    main()
