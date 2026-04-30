# Configurator — v4 change list

This file enumerates every change the Configurator needs to support
the three v4 capabilities. Organised by capability, followed by a
consolidated list of file writes, validation-library integration
points, and UI/UX rules.

Scope: editing panels to add or extend, library calls to make (most
notably `validate_pipeline()` on save), file writes the Configurator
owns, and UX rules for the vault and the approval-routes editor.

The Configurator remains the single writer of authored artifacts
(`pipeline.json`, `config.json`, per-agent `00_config.json`, and source
prompt files) plus the Vault. It is *not* the runtime — running
pipelines still happens via the Orchestrator.

---

## 1. Validation

### 1.1 Editing panels — two per agent

For every agent, the Configurator gains an **Input schema** panel and
an **Output schema** panel side by side. Both panels have identical
controls, differing only in where the result is written in
`00_config.json` (`input_schema` vs `output_schema`).

Controls per panel:

- **Mode picker** — multi-select, bound to the Orchestrator's
  `VALID_SCHEMA_MODES` whitelist. Current whitelist includes
  `has_sections`, `contains`, `json_schema`. The Configurator fetches
  the whitelist from the Orchestrator library rather than hard-coding
  it — so adding a new mode in `schema.py` automatically surfaces in
  the Configurator.
- **Sections editor** — shown when `has_sections` is selected.
  Editable list of markdown section titles (case-sensitive match,
  any heading level).
- **Contains editor** — shown when `contains` is selected. Editable
  list of substring tokens.
- **JSON-schema editor** — shown when `json_schema` is selected.
  Monaco / CodeMirror pane with JSON Schema syntax highlighting.
- **Require-upstream selector** — input-schema panel only. Multi-select
  constrained to the agent's `depends_on` list. Empty selection means
  "all upstream outputs must satisfy the schema".
- **Static-inputs-required toggle** — input-schema panel only.

### 1.2 Live validation on save

On every save operation, the Configurator invokes
`orchestrator.validate.validate_pipeline(pipeline_dir)` as a library
call. The raised `PipelineValidationError` is caught and its
`errors[]` list is rendered inline next to the offending field:

- Errors referencing a specific agent appear next to that agent's
  card.
- Cyclic-graph errors (V-CYC-xxx) appear on the pipeline-level
  settings panel.
- Errors with no obvious anchor appear in a top-level "Pipeline
  issues" banner.

Semantic-schema fields (mode, sections, json_schema) are also
shape-checked by the Configurator itself (e.g. `sections` must be a
list of strings) before delegating to `validate_pipeline`. Shape
errors use the same inline rendering.

### 1.3 File writes for validation

`<agent>/00_config.json` gains two new top-level blocks:

```json
{
  "input_schema":  { ... },
  "output_schema": { ... }
}
```

Both blocks are optional. When the author clears all fields in a
panel, the corresponding block is omitted from the written file (not
written as `{}` or `null`).

---

## 2. Human-in-the-Loop with approval routing

### 2.1 Approval panel per agent

Every agent card gains an **Approval** panel with:

- **`Requires approval` toggle** — writes `requires_approval: true|false`.
- **Approver label** — overrides pipeline-wide `approval.approver`
  for this agent only. Optional. If unset, the pipeline-wide default
  is inherited. (Note: if this override is desirable, add it to the
  Orchestrator side; otherwise keep read-only showing the pipeline
  default.)
- **On reject** sub-panel (hidden unless the toggle is on):
  - Target dropdown — populated with every agent in the pipeline
    except the current agent. Option `— continue normally —` clears
    the field.
  - Include checklist — `output`, `note`, `full_context`. Default:
    `output + note`.
  - Mode selector — `feedback` (default) or `task`. Tooltip explains
    the difference.
- **On approve** sub-panel — same controls, target dropdown includes
  `— continue normally —` (the default, meaning no message is posted
  on approve).

### 2.2 Pipeline-wide approval settings

In the pipeline-settings panel, a new **Approval** section edits:

- `approval.approver` (string, default `"operator"`).
- `approval.poll_interval_s` (int seconds, default `10`).
- `approval.timeout_s` (int seconds, default `3600`).

Writes to `config.json.approval.*`.

### 2.3 Downstream badges

For each agent referenced as a target in any other agent's
`approval_routes`, the Configurator renders a compact read-only badge
on that agent's card:

- `May receive rejection feedback from: <gate_agent>` (for
  `on_reject.target`).
- `May receive approval task from: <gate_agent>` (for
  `on_approve.target`).

Clicking the badge scrolls to / highlights the source agent's
Approval panel.

### 2.4 Cyclic-safety nudges

When the author:

- Adds an `on_reject.target` that is an *upstream* agent of the gate
  (i.e. a cycle is being introduced), *and*
- The pipeline's `termination.max_cycles` is below a heuristic
  threshold (e.g. 5),

the Configurator shows a non-blocking tip: *"This rejection routes to
an upstream agent, creating a loop. Consider raising `max_cycles` —
current value is N."*

### 2.5 DAG-mode restriction

In DAG pipelines (no `feedback` / `peer` edges), the `approval_routes`
panel is hidden. The toggle for `requires_approval` remains, and a
tooltip explains: *"Approval routing is available in cyclic pipelines.
In DAG mode, rejection stops the run."*

Rationale: keeping this in UI + validation avoids authoring a
configuration that would silently be ignored at runtime.

### 2.6 Validation rule to add on the Orchestrator side

V-APPROVE-001: `approval_routes.on_*.target` must reference an
existing agent and must not equal the gate's own `agent_id`. Enforced
both in the Configurator (live) and by `validate_pipeline` (save-time
and run-time).

### 2.7 File writes for HITL

Per-agent `00_config.json`:

```json
{
  "requires_approval": true,
  "approval_routes": {
    "on_reject":  { "target": "...", "include": ["output","note"], "mode": "feedback" },
    "on_approve": { "target": "...", "include": ["output"],        "mode": "task" }
  }
}
```

Either sub-block may be absent. Both may be absent when
`requires_approval` is true (pipeline fails on reject — v3.5
behaviour).

`config.json.approval.*` as described above.

---

## 3. Secrets management

### 3.1 Vault panel — repo-wide

A new **Vault** panel, shown at the repository / pipelines-directory
level (not per-pipeline). Lists every row in `secrets`:

- Columns: `name`, `description`, `tags`, `origin_pipeline`,
  `updated_at`, `used_in` count.
- **Values are never displayed**. Not on row hover, not in tooltip,
  not when viewing usage.
- Row actions: **Edit metadata** (description, tags; not value),
  **Replace value**, **Delete**, **Show usage**.
- Create button opens a New Secret dialog with fields `name`
  (validated against `[A-Za-z_][A-Za-z0-9_]*`), `description`,
  `tags` (chips), `value` (password-masked, required).

Operations call `python cli.py vault set|delete` (or the library
equivalent) — the Configurator never writes the SQLite file directly.

### 3.2 Prompt editor integration

In every prompt-editing surface (`01_system.md`, `02_prompt.md`):

- **Autocomplete**: typing `<<s` offers the vault name list. Selecting
  an entry inserts `<<secret:NAME>>`.
- **Chip rendering**: each `<<secret:NAME>>` token renders as a chip.
  - Green chip = name exists in vault.
  - Red chip = name does not exist in vault. Tooltip: *"Not in vault
    — click to create."* Clicking opens the New Secret dialog with
    the name pre-filled.
  - Gold chip = name exists but its value is empty.
- **Chip click**: opens an edit dialog scoped to that secret
  (description, value, tags).

The `{{VAR}}` templating marker retains its existing chip rendering
and autocomplete (from `config.json.substitutions`); the two systems
are visually distinct.

### 3.3 Migration banner

On opening a pipeline whose `config.json.substitutions` contains keys
whose values match any of the existing credential regex patterns
(`scan_for_secrets` — AWS keys, GitHub tokens, bearer, connection
strings, etc.), the Configurator surfaces a banner:

> *"This pipeline's `substitutions` block contains values that look
> like secrets. Move them to the vault?"*

Clicking "Migrate" does the following for each flagged key:

1. Calls `vault set --name <key>` with the current value.
2. Removes the key from `config.json.substitutions`.
3. Walks every source file in the pipeline and rewrites `{{KEY}}` →
   `<<secret:KEY>>`.
4. Shows a diff confirmation before committing.

Non-flagged keys in `substitutions` are left alone — they are
legitimate template variables.

### 3.4 Pipeline-wide settings

In the pipeline-settings panel, a new **Secrets** section edits:

- `secrets.rehydrate_outputs` — toggle, default `true`. Tooltip
  explains: *"When on (recommended for course / development use),
  final `04_context.md` and `05_output.md` files contain rehydrated
  secret values for readability. When off, placeholders are preserved
  on disk."*

Writes to `config.json.secrets.rehydrate_outputs`.

### 3.5 Configurator-side vault validations

Live checks during authoring:

- A `<<secret:NAME>>` present in any source file with no matching
  vault row → chip rendered red; saving is allowed but a banner
  counts how many references are unresolved across the pipeline.
- A vault row exists but no source file references it anywhere in the
  repo → row shows a small "unused" tag. Not an error.
- Name grammar violation on create/rename → dialog-level error,
  submit disabled.

### 3.6 File writes for secrets

- `pipelines/secrets.db` — via CLI/library calls; the Configurator
  does not open the SQLite file directly.
- Each agent's source prompts (`01_system.md`, `02_prompt.md`) — as
  edited by the author; secret markers are saved as typed.
- `config.json.secrets.rehydrate_outputs`.
- `.gitignore` — verified to contain `pipelines/secrets.db` (added
  automatically by the Orchestrator on every run; the Configurator
  checks on save and appends if missing).

---

## 4. Consolidated file-write matrix

| File | Writer in v4 |
|---|---|
| `pipeline.json` | Configurator |
| `config.json` | Configurator (+ `approval.*`, + `secrets.*`) |
| `<agent>/00_config.json` | Configurator (+ `input_schema`, + `output_schema`, + `approval_routes`) |
| `<agent>/01_system.md` | Configurator |
| `<agent>/02_prompt.md` | Configurator |
| `<agent>/03_inputs/*` (authored) | Configurator |
| `.gitignore` | Orchestrator (auto); Configurator verifies entry |
| `pipelines/secrets.db` | CLI / library via Configurator actions |
| `<agent>/04_context.md` | Orchestrator (runtime) |
| `<agent>/05_output.md` | Orchestrator (runtime) |
| `<agent>/06_status.json` | Orchestrator |
| `<agent>/07_approval_request.json` | Orchestrator |
| `<agent>/07_approval.json` | **Observer** (and CLI); NOT the Configurator |
| `.state/events.jsonl` | Orchestrator |
| `.state/pause` / `.state/resume` | Observer (pause/resume sentinels) |
| `history/v{N}_...` | Orchestrator (snapshot) |

---

## 5. Orchestrator-library integration points

Functions the Configurator calls as a library (not via shell):

- `orchestrator.validate.validate_pipeline(pipeline_dir)` — on every
  save, to populate inline error markers.
- `orchestrator.schema.VALID_SCHEMA_MODES` — to populate the mode
  picker.
- `orchestrator.secrets.scan_for_secrets(agent_id, agent_dir, log)`
  — applied to live edits of `01_system.md` / `02_prompt.md` to
  produce the migration banner. (The existing function already exists
  in v3.5; reuse it.)
- A new `orchestrator.vault.Vault` API (to be added in v4
  implementation) — `list()`, `get_metadata(name)`, `put`, `delete`,
  `usage(name)`.

---

## 6. UX rules that apply throughout

- **Never render a secret value in the Configurator UI after create.**
  On create/replace the password-masked field shows the value only
  while the dialog is open; once committed, the value is written via
  the Vault CLI and is never read back into the UI. Metadata edits do
  not re-read the value.
- **Clipboard safety.** Copy actions on chip or Vault row copy the
  *name* (e.g. `<<secret:DB_PASSWORD>>`), never the value.
- **Diff confirmation** is mandatory for bulk operations (migration
  banner, rename across sources).
- **Undo** is supported for in-session text edits but NOT for vault
  operations (create/replace/delete). Those require explicit
  confirmation.
- **Accessibility**: every chip must have an accessible label including
  the marker type and name, e.g. "secret chip, DB_PASSWORD, in vault".

---

## 7. Out of scope for Configurator v4

- Displaying secret values anywhere (including after create).
- Running pipelines (delegated to the Orchestrator / Observer).
- Writing `07_approval.json` — that is the Observer's (and CLI's)
  responsibility.
- Modifying `secret_audit` rows.
- Building a cross-repo vault. The vault is per-repository by design.
