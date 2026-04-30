# Cogniflow Orchestrator v4 — User Guide

Validation · Human-in-the-Loop · Secrets Management

This guide documents the three v4 capabilities end-to-end: the authoring
process, what the Orchestrator does at runtime, what the Observer shows,
and what the Configurator exposes for each feature.

Audience: pipeline authors and operators. No prior knowledge of v3.5
internals is assumed, but readers are expected to know what a pipeline,
an agent, and a DAG/cyclic run are.

---

## 0. Terminology

| Term | Meaning |
|---|---|
| **Pipeline** | A directory with `pipeline.json`, `config.json`, and one sub-directory per agent. |
| **Agent** | One node in the pipeline. Has `00_config.json`, `01_system.md`, `02_prompt.md`. |
| **Source file** | A file you author. Contains placeholders; never contains real values. Stable across environments. |
| **Runtime file** | A file the Orchestrator writes during a run (`04_context.md`, `05_output.md`, history snapshots). Rehydrated by default. |
| **Orchestrator** | The Python runtime that validates and executes pipelines. CLI entry: `python cli.py`. |
| **Observer** | The visual monitoring UI. Reads `.state/events.jsonl`, per-agent status files, and the vault audit log. Drives pause/resume via sentinel files. Writes approval decisions. |
| **Configurator** | The visual authoring UI. Writes `pipeline.json`, `config.json`, per-agent `00_config.json`, and source prompts. Calls `validate_pipeline()` live. |
| **Gate agent** | An agent with `requires_approval: true`. |
| **Vault** | SQLite file at `pipelines/secrets.db` that maps secret names to values and records an audit log. |
| **Placeholder** | A token in a source file. `{{VAR}}` for non-secret templating. `<<secret:NAME>>` for vault references. |
| **Rehydration** | Replacing a placeholder with its real value. |

---

## 1. Validation

### 1.1 The three tiers

Validation in v4 is explicitly split by who defines the rules.

| Tier | What is validated | When | Rules defined by | Enforced by |
|---|---|---|---|---|
| **Structural** | Pipeline shape, references resolve, enums whitelisted, cyclic invariants | Before the run starts | Orchestrator (built-in) | Orchestrator |
| **Input schema** | Upstream outputs and static inputs satisfy what this agent needs | Before each agent calls Claude | Author, per-agent `00_config.json` | Orchestrator |
| **Output schema** | `05_output.md` satisfies what this agent promised | After each agent returns | Author, per-agent `00_config.json` | Orchestrator |

Rule-of-thumb for authors: structural rules are framework invariants
you never need to configure; input/output schemas are your contracts
with upstream and downstream and you choose whether to declare them.

### 1.2 Structural validation

No configuration. The rule set lives in the Orchestrator (`validate.py`).

What is checked:
- `pipeline.json` well-formed and lists at least one agent.
- Every agent's directory resolves.
- Every `depends_on` reference exists.
- For cyclic pipelines: termination block present, `strategy` in the
  allowed set, `max_cycles >= 2`, `on_cycle_limit` / `on_deadlock`
  whitelisted, edges reference known agents, `feedback` / `peer` edges
  have `directed: false`, `tags.domain` set, every agent has an
  outbound edge, a `pm` agent exists.
- Per-agent `00_config.json` fields are well-formed: `token_strategy`,
  `requires_approval`, `max_retries`, `retry_delays_s`, `static_inputs`,
  `router.routes`, and the new `input_schema`, `output_schema`,
  `approval_routes` blocks.

Failure behaviour: all errors are collected and raised as one
`PipelineValidationError`. The run does not start. No state is written.
No `.gitignore` is created. The exit code is 1.

The Configurator calls the same `validate_pipeline()` function as a
library on every save, so structural errors surface at authoring time.

### 1.3 Input schema (new in v4)

Declared in `<agent>/00_config.json`:

```json
{
  "input_schema": {
    "mode":                   ["has_sections"],
    "sections":               ["Problem", "Goals", "Constraints"],
    "require_upstream":       ["pm"],
    "static_inputs_required": true
  }
}
```

Fields:

| Field | Type | Meaning |
|---|---|---|
| `mode` | list[string] | Validation modes to apply. Reuses the same whitelist as `output_schema`: `has_sections`, `contains`, `json_schema`, etc. |
| `sections` | list[string] | For `has_sections` — required markdown headers (any level) in the upstream output(s). |
| `contains` | list[string] | For `contains` — substring tokens that must appear. |
| `json_schema` | object | For `json_schema` — the JSON Schema the upstream output must parse against. |
| `require_upstream` | list[string] | Which specific upstream agent IDs must satisfy the schema. Defaults to all `depends_on` targets. |
| `static_inputs_required` | bool | If true, every path in `static_inputs` must exist AND be non-empty (default: only existence is checked). |

Enforcement timing:

- DAG runner: after `collect_inputs()` and `assemble_context()`,
  before the Claude subprocess is started.
- Cyclic runner: on each invocation, after context assembly.

On violation: `SchemaViolationError(agent_id, violations, phase="input")`.
In DAG the run stops at this agent. In cyclic mode the violation follows
the engine's existing error path.

### 1.4 Output schema (existing)

Unchanged from v3.5. Declared in the same `00_config.json` under
`output_schema`; enforced after Claude returns.

### 1.5 Configurator behaviour

Every agent card has two panels with identical controls:

- **Input schema panel**
- **Output schema panel**

Controls per panel:

- *Mode picker* (multi-select, bound to the mode whitelist).
- *Sections editor* (list, shown when `has_sections` is selected).
- *Contains editor* (list, shown when `contains` is selected).
- *JSON-schema editor* (shown when `json_schema` is selected).
- *Require-upstream selector* (input-schema only) — picks specific
  `depends_on` targets whose outputs must satisfy the schema.

On every save, the Configurator invokes `validate_pipeline()` as a
library. Structural errors are rendered inline next to the offending
field. Semantic-schema fields are checked for their own shape (mode is
in the whitelist, sections is a list of strings) but are not
test-executed — that happens at run time.

### 1.6 Observer behaviour

Per-agent row shows three discrete validation markers:

- `✓ input` / `✗ input` — with tooltip listing missing sections or
  tokens.
- `✓ output` / `✗ output` — same for the output schema.
- Structural errors (pre-run) appear as a blocking banner above the
  pipeline graph; the run button is disabled until they are resolved.

Clicking a failed marker expands the full violation list.

### 1.7 Failure matrix

| Mode | Violation | Run outcome |
|---|---|---|
| DAG | structural | Run never starts. |
| DAG | input schema | Agent fails, layer stops, pipeline fails. |
| DAG | output schema | Agent fails, layer stops, pipeline fails. |
| Cyclic | structural | Run never starts. |
| Cyclic | input schema | Agent fails; engine follows its existing error path. |
| Cyclic | output schema | Agent fails; engine follows its existing error path. |

---

## 2. Human-in-the-Loop with approval routing

### 2.1 The model

Any agent can be marked `requires_approval: true`. When it runs:

1. Agent invokes Claude and writes `05_output.md`.
2. Orchestrator writes `07_approval_request.json` and emits the
   `agent_approval_required` event.
3. Agent state becomes `awaiting_approval`.
4. Orchestrator polls `07_approval.json` until the operator writes a
   decision (via the Observer or the CLI).
5. **On approve** — pipeline continues. If `approval_routes.on_approve`
   is configured (cyclic pipelines only), a message is additionally
   posted to the target agent.
6. **On reject** — behaviour depends on `approval_routes.on_reject`:
   - configured (cyclic only) → message posted to the target agent,
     agent enters `awaiting_feedback`, pipeline does NOT fail.
   - not configured → pipeline fails.

### 2.2 Enabling the gate

Per-agent, in `<agent>/00_config.json`:

```json
{ "requires_approval": true }
```

Pipeline-wide defaults, in `config.json`:

```json
{
  "approval": {
    "approver":        "operator",
    "poll_interval_s": 10,
    "timeout_s":       3600
  }
}
```

Every gate agent in the pipeline inherits these values.

### 2.3 Approval routing — cyclic pipelines only

Configured on the gate agent itself:

```json
{
  "requires_approval": true,
  "approval_routes": {
    "on_reject": {
      "target":  "writer",
      "include": ["output", "note"],
      "mode":    "feedback"
    },
    "on_approve": {
      "target":  "publisher",
      "include": ["output"],
      "mode":    "task"
    }
  }
}
```

Fields per route:

| Field | Type | Meaning |
|---|---|---|
| `target` | string | Agent ID to deliver to. Must exist. Must not equal the gate agent itself. |
| `include` | list[string] | Payload parts: `output`, `note`, `full_context`. |
| `mode` | string | `feedback` (default) opens a reply on a feedback edge; `task` posts a forward task. |

**DAG pipelines do not support `approval_routes`**. Rejection in a DAG
fails the run, same as v3.5. DAG has no back-edge concept and adding
one complicates topology without matching the expressivity a DAG
normally needs. Reset and re-run remains the DAG recovery path.

### 2.4 What the receiving agent sees

When the receiving agent runs next, the Orchestrator injects a standard
block into its assembled context:

```markdown
## Feedback from <gate_agent_id>

Status:       rejected
Decided by:   <approver>
Decided at:   <ISO timestamp>

**Note:**
<operator note>

**Prior output (rejected):**
<rejected output>
```

No special configuration on the receiving agent. Its `01_system.md`
should be written to handle feedback naturally — typically a single
line such as:

> If a `## Feedback from …` section is present, read it carefully and
> revise the prior output according to the note.

### 2.5 Configurator behaviour

Each agent card gains an **Approval** panel:

- `Requires approval` toggle.
- `Approver label` field (defaults to the pipeline's `approval.approver`).
- Hidden unless the toggle is on:
  - **On reject** panel — target dropdown (all agents except self),
    include checklist (`output`, `note`, `full_context`), mode selector
    (`feedback` / `task`).
  - **On approve** panel — same controls, optional (empty target means
    "no post-approval side effect, continue normally").

Downstream agents that are listed as a target in any `approval_routes`
block receive a read-only badge: `May receive feedback from: <agent>`.
The badge links back to the source agent's approval panel. No change is
required on the downstream agent's own config.

Validation rule added (V-APPROVE-001): `approval_routes.on_*.target`
must reference an existing agent and must not equal the gate agent.
Checked live by the Configurator and by `validate_pipeline()` before
any run.

When the author adds an `on_reject` that targets an *upstream* agent
(i.e. an agent that feeds the gate), the Configurator suggests raising
`termination.max_cycles` to keep the resulting loop bounded.

### 2.6 Observer behaviour

- **Approval queue**: list of agents currently `awaiting_approval`.
  Each row shows the agent ID, output preview, request timestamp, and
  deadline. Click-through to the full `05_output.md`.
- **Approve / Reject** buttons per row. Reject requires a non-empty
  note. If the gate has `approval_routes.on_reject`, a preview chip
  shows `Will send output + note to: <target>` before the operator
  commits the decision.
- **Rejection-feedback thread**: when routing has fired, the target
  agent's message history shows a clearly marked `Feedback from
  <gate>` thread entry with full payload visible.
- **CLI parity**: `python cli.py approve|reject ...` writes the same
  `07_approval.json` the Observer writes. Either path is valid.

### 2.7 Edge cases and safety

- `approval_routes.on_*.target` points to a non-existent agent →
  blocked by structural validation; the run never starts.
- Operator rejects without providing a note → rejected, an empty
  string is written. The feedback block shows
  `**Note:** (no note provided)`.
- Routing to an upstream agent creates a loop. The loop is bounded by
  the existing `max_cycles` and `on_cycle_limit` machinery — no new
  escalation path is introduced.
- Approval timeout (`timeout_s`) elapses → `ApprovalTimeoutError`
  raised; pipeline fails. Agent status becomes `approval_timeout`.
- Process dies during wait → on re-run, the agent is re-entered in the
  `wait_for_approval` loop without re-invoking Claude (restart-safety
  from v3.5 preserved).

---

## 3. Secrets management

### 3.1 Trust model — stated up front

The vault is an *obfuscation and hygiene* layer for prompt authoring.
Its concrete properties:

1. Secret values never appear in your *source files* (`01_system.md`,
   `02_prompt.md`, `03_inputs/*`) — only `<<secret:NAME>>` placeholders.
2. Secret values never appear in `config.json` — they live in the
   vault file.
3. Every substitution is audited by name in a dedicated SQLite table.

It is **not** a production-grade secrets store. OS file permissions on
`pipelines/secrets.db` are the trust boundary. Values are stored in
plain SQLite (not encrypted at rest). If you need a production vault,
front this module with your real secret manager and treat the SQLite
file as a local cache.

With `secrets.rehydrate_outputs: true` (the default), the runtime
files `04_context.md` and `05_output.md` contain rehydrated values.
This is your explicit choice, traded in exchange for readability of
the final versioned artifacts.

### 3.2 Vault location

Single file at `pipelines/secrets.db`, co-located with the pipelines
directory. One file per repository; every pipeline in the repo shares
it, so the obfuscation terms you add while building one pipeline are
available to every other pipeline.

The Orchestrator adds `pipelines/secrets.db` to the auto-generated
`.gitignore` (alongside `.state/`).

### 3.3 Schema

`secrets` — the master table.

| Column | Type | Notes |
|---|---|---|
| `name` | TEXT primary key | Must match `[A-Za-z_][A-Za-z0-9_]*`. Used as `<<secret:NAME>>`. |
| `value` | TEXT, not null | The real secret. |
| `description` | TEXT | Human-readable description. |
| `tags` | TEXT | JSON array, e.g. `["auth","prod"]`. |
| `origin_pipeline` | TEXT | Pipeline that first registered the name. |
| `created_at` / `updated_at` | TEXT | ISO-8601 UTC. |

`secret_pipeline_link` — which pipelines reference which secrets.

| Column | Type |
|---|---|
| `secret_name` | TEXT |
| `pipeline_name` | TEXT |
| `first_used_at` | TEXT |
| `last_used_at` | TEXT |

Primary key: `(secret_name, pipeline_name)`.

`secret_audit` — append-only audit log. Names only, never values.

| Column | Type |
|---|---|
| `id` | INTEGER autoincrement |
| `ts` | TEXT (ISO-8601 UTC) |
| `run_id` | TEXT |
| `pipeline_name` | TEXT |
| `agent_id` | TEXT |
| `direction` | TEXT — one of `outbound`, `inbound`, `missing`, `leaked` |
| `secret_name` | TEXT |
| `file` | TEXT — one of `01_system`, `02_prompt`, `03_inputs/<name>`, `04_context`, `05_output`, `response_raw` |
| `occurrences` | INTEGER |

### 3.4 Placeholder syntax

| Marker | Purpose | Source of value |
|---|---|---|
| `{{VAR}}` | Non-secret templating | `config.json` `substitutions` |
| `<<secret:VAR>>` | Secret reference | `pipelines/secrets.db` |

Both may appear in the same file. The two substitution systems are
independent — adding a secret does not shadow a template variable and
vice versa. Both use the same `[A-Za-z_][A-Za-z0-9_]*` identifier
grammar.

### 3.5 Direction semantics

The vault has four directional operations, all logged in `secret_audit`.

| Direction | When | What it does |
|---|---|---|
| `outbound` | At send time, on the string about to go to Claude | Replaces `<<secret:NAME>>` with the vault value. (This is the "send real values to Claude" step.) |
| `inbound` | On the response body, before writing `05_output.md` | Replaces any `<<secret:NAME>>` Claude used back to the vault value — only if `secrets.rehydrate_outputs: true`. |
| `missing` | Any time a `<<secret:NAME>>` appears with no matching row | Placeholder left intact; warning audit row written. |
| `leaked` | Response scan | Raw secret value appeared in the response literally (Claude ignored the guardrail). Rehydration still proceeds; warning audit row written. |

### 3.6 Lifecycle — what happens on each invocation

1. **Source files** contain `<<secret:NAME>>` placeholders. No values.
2. **Context assembly** runs `{{VAR}}` substitution and writes
   `04_context.md`. `<<secret:NAME>>` placeholders still present.
3. **Outbound rehydration** — on the in-memory `04_context.md` and
   `01_system.md`, replaces `<<secret:NAME>>` with real values. This
   is what Claude sees.
4. **Claude is invoked.** System prompt goes via `--system-prompt` in
   argv; context goes via stdin.
5. **Final-file write** — if `secrets.rehydrate_outputs: true`
   (default): the rehydrated `04_context.md` is written to disk. The
   response is rehydrated (`<<secret:NAME>>` → value) and written as
   `05_output.md`. Leak scan runs on the response before write.
   If `secrets.rehydrate_outputs: false`: `04_context.md` is written
   with placeholders preserved, and `05_output.md` is written with the
   response as-returned (no inbound rehydration); leak scan still runs.

### 3.7 Which files hold what on disk after a run

| File | Authored by | `rehydrate_outputs: true` (default) | `rehydrate_outputs: false` |
|---|---|---|---|
| `01_system.md` | You | placeholders intact | placeholders intact |
| `02_prompt.md` | You | placeholders intact | placeholders intact |
| `03_inputs/*` (authored) | You | placeholders intact | placeholders intact |
| `04_context.md` | Orchestrator | rehydrated | placeholders intact |
| `05_output.md` | Orchestrator | rehydrated | Claude response as returned |
| `history/v{N}_.../agents/*/04_context.md` | Orchestrator | rehydrated | placeholders intact |
| `history/v{N}_.../agents/*/05_output.md` | Orchestrator | rehydrated | as returned |

Authoring artifacts are stable across environments. Runtime artifacts
under the default are human-readable records of a specific run.

### 3.8 Leak detection

After each Claude response, the Orchestrator scans the response body
for raw secret *values* (not placeholders). A match means Claude
echoed the literal value instead of using `<<secret:NAME>>`. For each
match, a `direction=leaked` row is written to `secret_audit`; the
Observer surfaces this as a warning on the run summary.

Recommended guardrail in the agent's system prompt (add once, per
project convention):

> When referring to sensitive values you receive in this conversation,
> always use the placeholder form `<<secret:NAME>>` rather than the
> literal value. Do not include raw credentials, tokens, or
> connection strings in your response.

### 3.9 Configurator behaviour

A **Vault** panel, global to the repository:

- Lists every secret: name, description, tags, origin pipeline,
  `updated_at`. Values are never displayed.
- Create / edit / delete per row.
- Creating a secret requires a name that matches the identifier
  grammar and a non-empty value.
- Each row shows a `used_in` count (joined via `secret_pipeline_link`).

In the prompt editors (`01_system.md`, `02_prompt.md`):

- Typing `<<s` triggers autocomplete from the vault.
- Committed `<<secret:NAME>>` tokens render as chips.
- Clicking a chip opens the edit-value dialog.
- A chip for a name that is not in the vault is shown in red with
  `not in vault — click to create`.

### 3.10 Observer behaviour

- **Run audit tab**: lists every substitution for the selected run by
  name, direction, file, count, and agent. No values shown.
- **Leak warnings**: prominent banner whenever any `direction=leaked`
  row exists for the current run, with a drill-through to the
  specific agents.
- **Vault viewer**: read-only list of vault names and metadata.
  Creation / editing happens in the Configurator.

### 3.11 CLI surface

For headless use and scripting:

```
python cli.py vault set    --name DB_PASSWORD
python cli.py vault set    --name DB_PASSWORD --from-stdin
python cli.py vault list
python cli.py vault show   --name DB_PASSWORD
python cli.py vault usage  --name DB_PASSWORD
python cli.py vault delete --name DB_PASSWORD
python cli.py vault audit  [--run <id>] [--pipeline <name>] [--since <iso>]
```

- `set` prompts interactively with echo disabled. `--from-stdin` reads
  the value from stdin (for piping from another secret store).
- `show` prints metadata only — never the value.
- `audit` prints names, directions, counts, files. Never values.

---

## 4. Configuration reference (v4 additions)

### 4.1 `config.json` (pipeline-wide)

```json
{
  "approval": {
    "approver":        "operator",
    "poll_interval_s": 10,
    "timeout_s":       3600
  },
  "secrets": {
    "rehydrate_outputs": true
  }
}
```

| Key | Default | Meaning |
|---|---|---|
| `approval.approver` | `"operator"` | Written to `07_approval.json` when a decision is recorded. |
| `approval.poll_interval_s` | `10` | How often the Orchestrator checks for the decision file. |
| `approval.timeout_s` | `3600` | How long to wait before `ApprovalTimeoutError`. |
| `secrets.rehydrate_outputs` | `true` | When true, `04_context.md` and `05_output.md` contain rehydrated values. When false, they preserve placeholders. |

### 4.2 `<agent>/00_config.json` (per-agent)

```json
{
  "requires_approval": false,

  "input_schema": {
    "mode":                   ["has_sections"],
    "sections":               ["Problem", "Goals", "Constraints"],
    "require_upstream":       ["pm"],
    "static_inputs_required": true
  },

  "output_schema": {
    "mode":     ["has_sections"],
    "sections": ["Architecture", "Open Questions"]
  },

  "approval_routes": {
    "on_reject":  { "target": "writer",    "include": ["output","note"], "mode": "feedback" },
    "on_approve": { "target": "publisher", "include": ["output"],        "mode": "task"     }
  }
}
```

### 4.3 Vault file

SQLite file at `pipelines/secrets.db`. Auto-created on first use.
`.gitignore` entry is added automatically by the Orchestrator.

---

## 5. End-to-end authoring workflow

1. In the Configurator, create or open a pipeline.
2. Add agents. Author each agent's `01_system.md` and `02_prompt.md`,
   referencing `{{VAR}}` for templates and `<<secret:NAME>>` for secrets.
   The Configurator autocompletes both.
3. In the Vault panel, add entries for every `<<secret:NAME>>` the
   agents reference. The Configurator flags any unresolved secret chip
   in red.
4. Declare `input_schema` / `output_schema` on each agent where a
   contract is worth enforcing. The Configurator runs
   `validate_pipeline()` on every save; structural errors appear
   inline.
5. Mark gate-bearing agents with `requires_approval`. For cyclic
   pipelines, configure `approval_routes.on_reject` (and optionally
   `on_approve`) so rejections route back into the graph.
6. Save. The Configurator writes `pipeline.json`, `config.json`,
   per-agent `00_config.json`, and the source prompt files.
7. Trigger the run from the Observer or the CLI (`python cli.py run`).
8. Watch progress in the Observer. When an approval gate is hit, the
   agent appears in the approval queue; approve or reject with a note.
9. After the run completes, inspect the rehydrated `04_context.md` and
   `05_output.md` for each agent, plus the run audit tab for the
   substitution log.

---

## 6. Migration from v3.5

- Existing `output_schema` definitions keep working unchanged.
- `config.json` `substitutions` keep working unchanged for non-secret
  templating. You are free to continue using them for template
  variables such as product names.
- `requires_approval` keeps working unchanged. Without
  `approval_routes`, rejection behaves exactly as in v3.5 (pipeline
  fails).
- The v3.5 `substitutions` block that previously held secret values is
  now strictly for non-secret templating. Move any secret-like values
  from there into the vault. The scanner in `secrets.py` continues to
  warn on credential-like patterns in source files.

No automatic migration is performed. The Configurator shows a
migration banner on any pipeline whose `config.json` has keys in
`substitutions` that look secret-like (matching the existing regex
patterns), offering a one-click move into the vault.
