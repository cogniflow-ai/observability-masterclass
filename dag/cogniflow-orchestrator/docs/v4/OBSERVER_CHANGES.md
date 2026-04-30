# Observer — v4 change list

This file enumerates every change the Observer needs to support the
three v4 capabilities: validation, human-in-the-loop with approval
routing, and secrets management. Organised by capability, with a
consolidated event-schema delta and data-source list at the end.

Scope: read-model additions (new files and DB tables to read), UI
panels to add or extend, write operations the Observer performs, and
new event types to handle.

The Observer continues to be the *monitoring* UI. Vault creation and
pipeline authoring happen in the Configurator — the Observer has
read-only vault views and write-only approval decisions.

---

## 1. Validation

### 1.1 Data sources to read

- Existing: per-agent `06_status.json`, `.state/events.jsonl`.
- New events in `events.jsonl`:
  - `pipeline_validation_error` (pre-run, blocks run start)
  - `agent_input_schema_violation { agent_id, violations[], phase: "input" }`
  - `agent_output_schema_violation { agent_id, violations[], phase: "output" }`

### 1.2 UI additions

- **Pre-run validation banner** above the pipeline graph. Rendered
  when the last attempted run ended with `pipeline_validation_error`
  or when the Observer was asked to start a run and the Orchestrator
  returned validation errors. Shows the full list of errors, one per
  line. Disables the Run button until cleared by a fresh save in the
  Configurator.
- **Per-agent row markers**: three discrete status chips, each with a
  tooltip listing the violations.
  - `✓ input` / `✗ input` — from the most recent input-schema check.
  - `✓ output` / `✗ output` — from the most recent output-schema check.
  - `✓ struct` / `✗ struct` — derived from the pre-run structural
    check; red if the agent is referenced in any
    `pipeline_validation_error` message.
- **Violation drill-down**: clicking a red chip opens a side panel
  listing the full `violations[]` payload.

### 1.3 Write operations

None. Validation is driven by the Configurator (authoring-time) and
the Orchestrator (runtime). The Observer displays only.

---

## 2. Human-in-the-Loop with approval routing

### 2.1 Data sources to read

- Existing: `07_approval_request.json`, `07_approval.json`,
  `06_status.json`.
- New events to consume:
  - `agent_approval_required` (exists in v3.5; keep).
  - `agent_approved { agent_id, approved_by, note }` (exists; keep).
  - `agent_rejected { agent_id, approved_by, note }` (exists; keep).
  - `agent_rejected_redirected { gate_agent_id, target_agent_id, note, include[] }` (NEW).
  - `agent_approved_redirected  { gate_agent_id, target_agent_id, include[] }` (NEW).
  - `agent_awaiting_feedback { agent_id }` (NEW — gate enters this
    state after routing-on-reject instead of `failed`).

### 2.2 UI additions

- **Approval Queue panel** — list every agent currently in
  `awaiting_approval`. Row contents:
  - Agent ID, pipeline, run ID.
  - First ~80 chars of `05_output.md`.
  - Request timestamp + countdown to `approval.timeout_s`.
  - Action buttons: **Approve**, **Reject**.
  - **Routing preview chip**: if the agent's `00_config.json` has
    `approval_routes.on_reject` (or `on_approve`), show
    `On reject → <target>` / `On approve → <target>` as a compact
    chip next to the action buttons. If not configured, omit the chip.
- **Reject dialog** — free-text note (required, non-empty), with
  routing-preview summary: *"Rejection will post `output + note` to
  agent `writer` as a feedback message."* Commit writes
  `07_approval.json`.
- **Approve dialog** — optional note, with routing-preview summary if
  `on_approve` is configured.
- **Feedback thread view** — on each agent's "messages" tab, clearly
  distinguish a message of type `rejection_feedback` / `approval_feedback`:
  - Badge: `Feedback from <gate>`.
  - Expanded view shows `{status, approved_by, decided_at, note,
    prior_output}`.
- **Awaiting-feedback state badge**: on the gate agent's row, render
  a distinct badge when status is `awaiting_feedback` — visually
  distinct from `awaiting_approval`. Tooltip explains
  *"Rejected; waiting for `<target>` to respond."*

### 2.3 Write operations

- The Observer writes `07_approval.json` atomically (tmp+rename, same
  format as `python cli.py approve|reject`). Schema:

  ```json
  {
    "agent_id":    "<gate_agent>",
    "status":      "approved" | "rejected",
    "approved_by": "<operator-identity>",
    "note":        "<free text>",
    "decided_at":  "<ISO-8601 UTC>"
  }
  ```

  Approver identity should match `config.approval.approver` by
  default; the Observer may override it per session (e.g. authenticated
  user).

### 2.4 Pause / resume handshake (existing, unchanged)

Already in v3.5: the Observer writes `.state/pause` and `.state/resume`
sentinel files between DAG layers. No change in v4.

---

## 3. Secrets management

### 3.1 Data sources to read

- New SQLite file: `pipelines/secrets.db`.
  - `secrets` table (read only: name, description, tags,
    origin_pipeline, created_at, updated_at — never value).
  - `secret_pipeline_link` (read-only).
  - `secret_audit` (read-only).
- New events in `events.jsonl` for run-scoped visibility:
  - `secret_substituted { agent_id, direction, secret_name, file, occurrences }`
  - `secret_missing     { agent_id, secret_name, file }`
  - `secret_leaked      { agent_id, secret_name, file: "response_raw" }`

The events duplicate information that is also in `secret_audit` — the
events drive live run UI; the audit table is the canonical record.

### 3.2 UI additions

- **Vault viewer** (read-only list):
  - Columns: name, description, tags, origin_pipeline, updated_at,
    `used_in` count. No values.
  - Clicking a row opens a metadata detail + usage list (which
    pipelines reference this secret). Still no value.
  - Pointer/button "Edit in Configurator" that deep-links to the
    Configurator's Vault panel.
- **Run audit tab** (per run):
  - Table: `ts, agent_id, direction, secret_name, file, occurrences`.
  - Filters by `direction` and by `agent_id`.
  - Summary row per run: totals of each direction.
- **Leak-warning banner** — if any `direction=leaked` row exists for
  the current run, a persistent banner surfaces at the top of the run
  view. Click expands into the leak list (agents, secret names, no
  values).
- **Per-agent substitution summary** — on each agent row during/after a
  run: two compact counters, `outbound: N` and `leaked: M` (M in red
  when > 0).

### 3.3 Write operations

None. Vault CRUD is owned by the Configurator and the `python cli.py
vault` subcommand. The Observer is read-only for the vault.

### 3.4 Security-sensitive display rules

- Never render a secret *value* in any panel. If the underlying table
  accidentally includes one, truncate and replace with `<redacted>`.
- The audit tab displays names only. There is no mode that shows
  values.
- Clipboard copy from the audit tab copies names only.

---

## 4. Consolidated event-schema delta

These are the new / changed events the Observer must parse from
`.state/events.jsonl`.

| Event | Fields | Purpose |
|---|---|---|
| `pipeline_validation_error` | `run_id, errors[]` | Pre-run structural failure; block Run button. |
| `agent_input_schema_violation` | `agent_id, violations[], phase:"input"` | Input-schema red chip on agent row. |
| `agent_output_schema_violation` | `agent_id, violations[], phase:"output"` | Output-schema red chip on agent row. |
| `agent_rejected_redirected` | `gate_agent_id, target_agent_id, note, include[]` | Routing preview + feedback-thread badge. |
| `agent_approved_redirected` | `gate_agent_id, target_agent_id, include[]` | Same, approve side. |
| `agent_awaiting_feedback` | `agent_id` | New status distinct from `awaiting_approval`. |
| `secret_substituted` | `agent_id, direction, secret_name, file, occurrences` | Run-audit table + per-agent counters. |
| `secret_missing` | `agent_id, secret_name, file` | Surfaced as audit rows and a minor warning. |
| `secret_leaked` | `agent_id, secret_name, file:"response_raw"` | Triggers leak banner. |

The existing v3.5 events (`agent_approval_required`, `agent_approved`,
`agent_rejected`, `secret_warning`, `secret_substitution_warning`) are
preserved and continue to be consumed.

---

## 5. Status-file delta (per-agent `06_status.json`)

New values the Observer must recognise:

| `status` value | New in v4? | Meaning |
|---|---|---|
| `awaiting_feedback` | yes | Gate rejected; feedback routed; waiting for downstream to deliver a response message. |
| `input_schema_failed` | yes | Input schema failed; agent did not invoke Claude. |
| `output_schema_failed` | yes | Already present conceptually; made explicit. |

Each corresponding row should use a distinct colour or icon so the
operator can distinguish "waiting on a person" from "waiting on another
agent."

---

## 6. Configuration surfaces the Observer reads

The Observer consults (read-only) the following files to render
tooltips and routing previews:

- `config.json` — `approval.approver`, `approval.timeout_s`,
  `secrets.rehydrate_outputs`.
- Each agent's `00_config.json`:
  - `requires_approval`
  - `approval_routes.on_reject` / `on_approve`
  - `input_schema` / `output_schema` (for tooltip "this agent
    declares ..." text).
- `pipelines/secrets.db` — metadata only, never values.

---

## 7. Out of scope for Observer v4

- Vault CRUD (Configurator).
- Pipeline authoring / config editing (Configurator).
- Running pipelines is already delegated to `python cli.py run` (or
  its library equivalent) — v4 does not change this.
- Secret value display in any form.
- Mutating `secret_audit` rows.
