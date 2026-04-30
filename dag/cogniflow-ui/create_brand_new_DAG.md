## 3. Build `model-release-dag` in the Configurator

Same workflow as § 2 but for the 18-agent DAG.

### 3.1 — Create the pipeline shell

1. Configurator home → **New pipeline**.
2. Set:
   - `id` = `model-release-dag`
   - `name` = `Model Release DAG`
   - `description` (paste exactly):
     `Complex 18-agent DAG modelling a production ML model release: ingest → scrub → parallel quality scans → split → feature engineer → train → 5 parallel evaluators → aggregate → risk → 2 HITL gates → deploy → monitoring. Exercises every v3.5 capability appropriate to DAG mode (schemas at every fan-in, requires_approval gates, vault secrets across ingest/train/deploy/monitor).`
   - `graph_mode` = `dag`
   - `graph_orientation` = `vertical`
   - `category` = `demo`
   - `labels` = `["demo", "dag", "v3.5", "ml", "hitl", "vault"]`
   - `validate_taglines` = `false`
3. Save.

### 3.2 — Add the 18 agents

| # | id | name | type | depends_on | description (one line) |
|---|---|---|---|---|---|
| 1 | `001_dataset_pull` | `001 Dataset Pull` | `worker` | `[]` | Pulls the raw training dataset from S3 and stages it for downstream agents. |
| 2 | `002_pii_scrubber` | `002 Pii Scrubber` | `worker` | `["001_dataset_pull"]` | Removes PII columns and hashes any free-text fields that may contain personal identifiers. |
| 3 | `003_quality_scanner` | `003 Quality Scanner` | `validator` | `["002_pii_scrubber"]` | Computes dataset quality stats: null fraction, duplicate rate, class balance, range checks. |
| 4 | `004_schema_validator` | `004 Schema Validator` | `validator` | `["002_pii_scrubber"]` | Validates dataset schema against the expected feature contract — types, ranges, allowed enums. |
| 5 | `005_train_split` | `005 Train Split` | `worker` | `["003_quality_scanner", "004_schema_validator"]` | Splits the cleaned dataset into train / val / test partitions using a deterministic seed. |
| 6 | `006_feature_engineer` | `006 Feature Engineer` | `worker` | `["005_train_split"]` | Materialises engineered features and writes them to the feature store. |
| 7 | `007_trainer` | `007 Trainer` | `worker` | `["006_feature_engineer"]` | Trains the model using the train partition. Logs training metrics to Weights & Biases and pushes the checkpoint to the model registry. |
| 8 | `008_eval_accuracy` | `008 Eval Accuracy` | `validator` | `["007_trainer"]` | Computes accuracy, precision, recall, F1, and AUROC on the held-out test set. |
| 9 | `009_eval_fairness` | `009 Eval Fairness` | `validator` | `["007_trainer"]` | Computes per-group fairness metrics (demographic parity, equalised odds) across protected attributes. |
| 10 | `010_eval_robustness` | `010 Eval Robustness` | `validator` | `["007_trainer"]` | Runs adversarial-perturbation and out-of-distribution robustness probes. |
| 11 | `011_eval_latency` | `011 Eval Latency` | `validator` | `["007_trainer"]` | Measures inference-time latency p50/p95/p99 and memory footprint at production batch sizes. |
| 12 | `012_eval_explainability` | `012 Eval Explainability` | `validator` | `["007_trainer"]` | Generates a SHAP-based feature-importance report and checks for spurious-correlation tells. |
| 13 | `013_metric_aggregator` | `013 Metric Aggregator` | `synthesizer` | `["008_eval_accuracy", "009_eval_fairness", "010_eval_robustness", "011_eval_latency", "012_eval_explainability"]` | Aggregates the five evaluation reports into a single release-readiness summary. |
| 14 | `014_risk_assessor` | `014 Risk Assessor` | `classifier` | `["013_metric_aggregator"]` | Translates the metric summary into a numeric risk score and a release recommendation. |
| 15 | `015_compliance_review` | `015 Compliance Review` | `reviewer` | `["014_risk_assessor"]` | Compliance-officer HITL gate. Reviews the risk assessment for regulatory blockers (GDPR, AI Act, sector-specific rules). Rejection halts the run. |
| 16 | `016_release_manager` | `016 Release Manager` | `reviewer` | `["015_compliance_review"]` | Release-manager HITL gate. Decides whether the model can be deployed today. Looks at compliance signoff, risk score, and current incident state. |
| 17 | `017_deployer` | `017 Deployer` | `worker` | `["016_release_manager"]` | Deploys the approved model to the production Kubernetes cluster behind a canary rollout. |
| 18 | `018_monitoring_setup` | `018 Monitoring Setup` | `worker` | `["017_deployer"]` | Provisions Datadog dashboards, latency / drift / error-rate alerts, and PagerDuty routing for the new model version. |

For DAG mode, additionally tick **`require_approval` = true** on
agents 015 and 016 (the pipeline.json field on the agent record;
distinct from `requires_approval` in `00_config.json` — both are
populated in the shipped pipeline).

### 3.3 — Edges

DAG mode uses `depends_on` only. Leave `edges` empty (`[]`).

### 3.4 — Pipeline-level config

```json
{
  "approval": {
    "approver": "release-board",
    "poll_interval_s": 15,
    "timeout_s": 14400
  },
  "secrets": {
    "rehydrate_outputs": true
  },
  "substitutions": {
    "MODEL_NAME": "claims-fraud-detector",
    "MODEL_VERSION": "v2.7.0",
    "TARGET_CLUSTER": "prod-us-east-1",
    "DEFAULT_LANGUAGE": "en"
  }
}
```

DAG mode does not use `termination` — leave it absent.

### 3.5 — Per-agent `00_config.json`

| Agent | Configuration |
|---|---|
| `001_dataset_pull` | `{"output_schema": {"mode": ["json"], "json_schema": {"type": "object", "required": ["staged_uri", "row_count_raw"], "properties": {"staged_uri": {"type": "string"}, "row_count_raw": {"type": "integer"}}}}}` |
| `002_pii_scrubber` | `{"input_schema": {"mode": ["json"], "json_schema": {"type": "object", "required": ["staged_uri"]}}}` |
| `003_quality_scanner` | `{"output_schema": {"mode": ["json"], "json_schema": {"type": "object", "required": ["row_count", "null_pct", "dup_pct", "class_balance"], "properties": {"row_count": {"type": "integer"}, "null_pct": {"type": "number"}, "dup_pct": {"type": "number"}, "class_balance": {"type": "object"}}}}}` |
| `004_schema_validator` | `{"output_schema": {"mode": ["json"], "json_schema": {"type": "object", "required": ["valid", "errors"], "properties": {"valid": {"type": "boolean"}, "errors": {"type": "array", "items": {"type": "string"}}}}}}` |
| `005_train_split` | `{"input_schema": {"mode": ["json"], "require_upstream": ["004_schema_validator"], "json_schema": {"type": "object", "required": ["valid"], "properties": {"valid": {"const": true}}}}, "output_schema": {"mode": ["json"], "json_schema": {"type": "object", "required": ["train_uri", "val_uri", "test_uri", "seed"]}}}` |
| `006_feature_engineer` | `{"output_schema": {"mode": ["has_sections"], "sections": ["Features", "Transformations", "Notes"]}}` |
| `007_trainer` | `{"output_schema": {"mode": ["json"], "json_schema": {"type": "object", "required": ["model_uri", "train_loss", "val_loss", "epochs"], "properties": {"model_uri": {"type": "string"}, "train_loss": {"type": "number"}, "val_loss": {"type": "number"}, "epochs": {"type": "integer"}}}}}` |
| `008_eval_accuracy` | `{"output_schema": {"mode": ["json"], "json_schema": {"type": "object", "required": ["accuracy", "precision", "recall", "f1", "auroc"], "properties": {"accuracy": {"type": "number"}, "precision": {"type": "number"}, "recall": {"type": "number"}, "f1": {"type": "number"}, "auroc": {"type": "number"}}}}}` |
| `009_eval_fairness` | `{"output_schema": {"mode": ["json"], "json_schema": {"type": "object", "required": ["demographic_parity", "equalised_odds_diff", "groups_evaluated"]}}}` |
| `010_eval_robustness` | `{"output_schema": {"mode": ["json"], "json_schema": {"type": "object", "required": ["adversarial_acc_drop", "ood_acc_drop"]}}}` |
| `011_eval_latency` | `{"output_schema": {"mode": ["json"], "json_schema": {"type": "object", "required": ["p50_ms", "p95_ms", "p99_ms", "peak_memory_mb"]}}}` |
| `012_eval_explainability` | `{"output_schema": {"mode": ["has_sections"], "sections": ["TopFeatures", "SuspectFeatures", "Notes"]}}` |
| `013_metric_aggregator` | `{"input_schema": {"mode": ["json"], "require_upstream": ["008_eval_accuracy", "009_eval_fairness", "010_eval_robustness", "011_eval_latency"]}, "output_schema": {"mode": ["has_sections"], "sections": ["Accuracy", "Fairness", "Robustness", "Latency", "Explainability", "Summary"]}}` |
| `014_risk_assessor` | `{"input_schema": {"mode": ["has_sections"], "sections": ["Accuracy", "Fairness", "Robustness", "Latency"]}, "output_schema": {"mode": ["json"], "json_schema": {"type": "object", "required": ["risk_score", "recommendation", "rationale"], "properties": {"risk_score": {"type": "number", "minimum": 0, "maximum": 1}, "recommendation": {"type": "string", "enum": ["release", "hold", "block"]}, "rationale": {"type": "string"}}}}}` |
| `015_compliance_review` | `{"input_schema": {"mode": ["contains"], "contains": ["risk_score"]}, "requires_approval": true}` |
| `016_release_manager` | `{"input_schema": {"mode": ["has_sections"], "sections": ["Accuracy", "Fairness", "Robustness", "Summary"]}, "requires_approval": true}` |
| `017_deployer` | `{"input_schema": {"mode": ["json"], "json_schema": {"type": "object", "required": ["approved", "model_uri"], "properties": {"approved": {"type": "boolean"}, "model_uri": {"type": "string"}}}}, "output_schema": {"mode": ["json"], "json_schema": {"type": "object", "required": ["deployment_uri", "canary_pct", "rollback_plan"]}}}` |
| `018_monitoring_setup` | `{"output_schema": {"mode": ["has_sections"], "sections": ["Dashboards", "Alerts", "Runbook"]}}` |

Two intentional quirks worth noting:

- `005_train_split.input_schema.json_schema.properties.valid` uses
  **`const: true`** — only schema-validator outputs that report
  `valid: true` may unblock the train split. This is the load-bearing
  guardrail that makes the DAG fail-closed when the dataset is broken.
- `013_metric_aggregator.input_schema.require_upstream` lists **only
  four of the five evaluators** (omits `012_eval_explainability`).
  This is deliberate: `012`'s `has_sections` output cannot satisfy a
  json `require_upstream`, so the schema requires the four numeric
  evaluators while the aggregator's prompt still references `012`.
  Do not "fix" this by adding `012` to the list.

### 3.6 — Per-agent prompts

Use the same `01_system.md` template as § 2.6 but with two
adjustments:

- `pm` does not exist in this pipeline; the equivalent role is filled
  by 001_dataset_pull as the first agent, but no special handling.
- For agents 005, 006, 007, 014 (deterministic / numeric agents), add
  one extra guardrails line:

  ```
  - Be deterministic where the spec asks for determinism (use the   pipeline seed when relevant).
  ```

For `02_prompt.md`, use the same template as § 2.6 with the **release
cycle** wording in `<goals>`:

```
<goals>
Fulfil the role above for this release cycle.
</goals>
```

`<output>` reads:

```
<output>
A single artifact downstream agents can consume.
</output>
```

(Note: subtle difference vs editorial — `that downstream` becomes
`downstream` without the relative pronoun. Match this if you want
byte-equivalent output.)

Per-agent customizations:

| Agent | `{format_block}` | `{secret_block}` |
|---|---|---|
| `001_dataset_pull` | `<format>\nReturn a single JSON object that satisfies your declared output_schema.json_schema.\n</format>` | `When calling external services, authenticate with:\n  - <<secret:AWS_S3_ACCESS_KEY>>\n  - <<secret:AWS_S3_SECRET_KEY>>` |
| `002_pii_scrubber` | (none) | (none) |
| `003_quality_scanner` | `<format>\nReturn a single JSON object that satisfies your declared output_schema.json_schema.\n</format>` | (none) |
| `004_schema_validator` | `<format>\nReturn a single JSON object that satisfies your declared output_schema.json_schema.\n</format>` | (none) |
| `005_train_split` | `<format>\nReturn a single JSON object that satisfies your declared output_schema.json_schema.\n</format>` | (none) |
| `006_feature_engineer` | `<format>\nStructure your output with these markdown headings: Features, Transformations, Notes.\n</format>` | (none) |
| `007_trainer` | `<format>\nReturn a single JSON object that satisfies your declared output_schema.json_schema.\n</format>` | `When calling external services, authenticate with:\n  - <<secret:WANDB_API_KEY>>\n  - <<secret:HUGGINGFACE_TOKEN>>` |
| `008_eval_accuracy` | `<format>\nReturn a single JSON object that satisfies your declared output_schema.json_schema.\n</format>` | (none) |
| `009_eval_fairness` | `<format>\nReturn a single JSON object that satisfies your declared output_schema.json_schema.\n</format>` | (none) |
| `010_eval_robustness` | `<format>\nReturn a single JSON object that satisfies your declared output_schema.json_schema.\n</format>` | (none) |
| `011_eval_latency` | `<format>\nReturn a single JSON object that satisfies your declared output_schema.json_schema.\n</format>` | (none) |
| `012_eval_explainability` | `<format>\nStructure your output with these markdown headings: TopFeatures, SuspectFeatures, Notes.\n</format>` | (none) |
| `013_metric_aggregator` | `<format>\nStructure your output with these markdown headings: Accuracy, Fairness, Robustness, Latency, Explainability, Summary.\n</format>` | (none) |
| `014_risk_assessor` | `<format>\nReturn a single JSON object that satisfies your declared output_schema.json_schema.\n</format>` | (none) |
| `015_compliance_review` | (none) | (none) |
| `016_release_manager` | (none) | (none) |
| `017_deployer` | `<format>\nReturn a single JSON object that satisfies your declared output_schema.json_schema.\n</format>` | `When calling external services, authenticate with:\n  - <<secret:AWS_DEPLOY_ACCESS_KEY>>\n  - <<secret:KUBE_CONFIG_TOKEN>>` |
| `018_monitoring_setup` | `<format>\nStructure your output with these markdown headings: Dashboards, Alerts, Runbook.\n</format>` | `When calling external services, authenticate with:\n  - <<secret:DATADOG_API_KEY>>\n  - <<secret:PAGERDUTY_TOKEN>>` |

### 3.7 — Validate

1. Configurator → **Validate** → expect ✓.
2. CLI:
   ```bash
   python cli.py validate model-release-dag
   ```
3. Open in the Observer → DAG renders 18 agents with the parallel
   branches (003 ‖ 004 → 005; 008–012 → 013) visible.

---

## 4. Configurator tests (TC-*)

These cases verify the authoring UI and live-validation surfaces
against the (now built) pipelines. They do **not** require a real
`claude.exe` run. Run them in order; each assumes the previous one
passed.

### TC-01 — Pipeline list & open

**Goal:** the configurator can enumerate and open both sample pipelines.

1. Open <http://localhost:8001>.
2. Confirm both pipelines appear in the picker with the correct
   `graph_mode` badge (Cyclic vs DAG).
3. Open `editorial-cyclic-board` → the DAG renderer draws 13 agents
   plus the three non-tree edges (peer 005↔004, feedback 008→004,
   feedback 011→004). HITL chips appear on 008 and 011.
4. Open `model-release-dag` → 18 agents render with two parallel
   branches (003 ‖ 004 converging on 005; 008–012 converging on 013).
   HITL chips appear on 015 and 016.

**Pass:** both DAGs render without console errors. Layout grid is
deterministic — second open should match first.