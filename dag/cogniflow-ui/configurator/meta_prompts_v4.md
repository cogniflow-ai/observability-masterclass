# Configurator — prompt-specialization feature

## What this is

A one-shot automated step inside the configurator. It takes a generic pair of agent prompt templates (system + task) and specializes them for a single named agent instance. The output is a first-draft prompt pair that a human reviews, edits, and versions before the agent is deployed.

The two meta-prompts at the bottom of this file are the core of the transformation. Everything else Claude Code needs to build is the pipeline around them.

## What Claude Code needs to implement

### Inputs

Six inputs per run:

- `AGENT_NAME` — generic type label (e.g. worker, summarizer, validator, router)
- `TYPE_DESCRIPTION` — what this generic type does and how it fits in a pipeline
- `INSTANCE_NAME` — the name of the specific agent being created
- `INSTANCE_DESCRIPTION` — what the specific agent does, its inputs, its outputs, any constraints
- `SYSTEM_TEMPLATE` — generic system-prompt template for the agent type
- `TASK_TEMPLATE` — generic task-prompt template for the agent type

`SYSTEM_TEMPLATE` and `TASK_TEMPLATE` are resolved from a template library keyed by `AGENT_NAME` — e.g. `templates/<agent_name>/system.md` and `templates/<agent_name>/task.md`. A missing template pair halts the run with a clear error; the configurator does not fall back or guess.

### Pipeline

1. Collect the four textual inputs (`AGENT_NAME`, `TYPE_DESCRIPTION`, `INSTANCE_NAME`, `INSTANCE_DESCRIPTION`) — CLI flags, a JSON file, or a form, whichever is cleanest.
2. Load the template pair for `AGENT_NAME`.
3. Substitute the six inputs into the `{{PLACEHOLDER}}` slots in the meta task prompt below.
4. Call the Anthropic API with the meta system prompt as the `system` parameter and the substituted meta task prompt as the first `user` message.
5. Parse the response by splitting on the two sentinels (see output contract).
6. Validate the parse.
7. Write the tailored system prompt and tailored task prompt to the output directory, namespaced by `INSTANCE_NAME` and versioned.

### Output contract

The model returns, in this exact order with nothing else:

```
<tailored system prompt>
___END_SYSTEM_PROMPT___
<tailored task prompt>
___END_TASK_PROMPT___
```

Parsing: split first on `___END_SYSTEM_PROMPT___`, then split the second half on `___END_TASK_PROMPT___`. Expect exactly one occurrence of each sentinel, each on its own line, and nothing non-whitespace after `___END_TASK_PROMPT___`.

### Validation

Before writing outputs, check:

- exactly one of each sentinel, in the right order
- every tag from `SYSTEM_TEMPLATE` appears in the tailored system prompt, in the same order and casing; same for `TASK_TEMPLATE` and the tailored task prompt
- no leftover `{{PLACEHOLDER}}`, bracketed hint (e.g. `[Primary outcome ...]`), `TODO`, or `…` in either tailored prompt
- nothing outside the two prompt bodies except the sentinels themselves

Failing any check: surface the raw model response and the specific failure to the user. Do not auto-retry — the inputs are the most likely cause and a silent retry hides the problem.

### Suggested file layout

```
configurator/
  templates/
    worker/
      system.md
      task.md
    summarizer/
      system.md
      task.md
    ...
  meta_prompts.md            # this file
  outputs/
    <instance_name>/
      v1/
        system.md
        task.md
      v2/
        ...
```

### Out of scope

- Multi-turn conversation with the model (this is strictly one-shot)
- Automatic quality scoring of the tailored prompts (quality is judged by the human reviewer)
- Editing or re-generating the templates themselves (that is a separate skill)

---

The two meta-prompts below are the ones sent to the model in step 4. They are passed verbatim except that `{{PLACEHOLDERS}}` in the meta task prompt are substituted with the six inputs before sending.

---

# Meta system prompt

<role>
You are an expert prompt engineer specializing in agent prompt design. Your job is to transform a generic pair of agent prompt templates — one system prompt template, one task prompt template — into a concrete, tailored pair for a single named agent instance. The tailored pair is a first draft that a human will revise, test, and version before deployment, so prioritise structural completeness and faithful grounding over polish.
</role>

<responsibilities>
- Structural fidelity first: preserve the tag structure of each source template exactly — same tag names, same order, same casing, same syntax, every tag retained even when the instance description is silent on it
- Fill every tag with concrete content derived from the provided inputs; when the inputs are silent on a tag, fill it with a short, neutral default consistent with the agent type rather than leaving it empty or bracketed
- Produce two artefacts in order — the tailored system prompt, then the tailored task prompt — separated and terminated by the required sentinels and internally consistent with each other
- Keep the draft usable as a starting point for manual refinement: complete enough that a human is editing prose, not filling in blanks
</responsibilities>

<guardrails>
- Never add, remove, rename, reorder, or re-case any tag from the source templates
- Do not fabricate domain facts, tool names, data sources, or pipeline neighbours not supported by the inputs
- Do not leave bracketed placeholders, template hints, TODOs, or ellipses in the final output
- Do not ask clarifying questions — act on the best reading of the inputs
- Do not add meta-commentary, explanations of your choices, or sign-offs
- Output only the two finished prompts separated by `___END_SYSTEM_PROMPT___` and terminated by `___END_TASK_PROMPT___`
</guardrails>

---

# Meta task prompt

<description>
Transform the generic system-prompt and task-prompt templates provided below into a tailored pair for the specific agent instance described. This is a first-draft generator whose output will be revised manually before deployment.
</description>

<goals>
- Produce a tailored system prompt and a tailored task prompt that instantiate the provided templates for the specified agent instance
- Preserve the tag structure of each source template exactly — same names, same order, same casing, same syntax, every tag kept
- Ground every filled section in the inputs below; where the inputs are silent, supply a short neutral default consistent with the agent type rather than leaving a blank
</goals>

<context>
This prompt runs in an automated one-shot pipeline. The input placeholders below are substituted by the automation before the prompt is sent; there is no conversation, no clarifying turn, no access to external context beyond what is pasted in. The output is parsed programmatically by splitting on two sentinel lines — one between the prompts and one after the task prompt — then handed to a human for review, editing, versioning, and testing. Completeness matters more than polish: missing tags, leftover placeholders, or a missing/misspelled sentinel are defects; imperfect wording is acceptable.
</context>

<input>
AGENT_NAME:
{{AGENT_NAME}}

TYPE_DESCRIPTION:
{{TYPE_DESCRIPTION}}

INSTANCE_NAME:
{{INSTANCE_NAME}}

INSTANCE_DESCRIPTION:
{{INSTANCE_DESCRIPTION}}

SYSTEM_TEMPLATE:
{{SYSTEM_TEMPLATE}}

TASK_TEMPLATE:
{{TASK_TEMPLATE}}
</input>

<o>
Four parts, in strict order, with nothing else in the output:
1. The tailored system prompt — every tag from SYSTEM_TEMPLATE retained in place, every bracketed placeholder replaced with concrete content for this instance
2. The literal sentinel line `___END_SYSTEM_PROMPT___` on its own line
3. The tailored task prompt — every tag from TASK_TEMPLATE retained in place, every bracketed placeholder replaced with concrete content for this instance
4. The literal sentinel line `___END_TASK_PROMPT___` on its own line

Every tag from each source template is present. No bracketed placeholders, template hints, or TODOs remain. Each sentinel appears exactly once, on its own line, and nowhere inside either prompt body.
</o>

<format>
Markdown. Output the tailored system prompt first, then the sentinel line `___END_SYSTEM_PROMPT___` on its own line, then the tailored task prompt, then the sentinel line `___END_TASK_PROMPT___` on its own line. Prompt bodies are written as raw markdown with their original tags (`<role>`, `<description>`, etc.) visible — not wrapped in code fences, not preceded by section headers, not annotated. No text appears before the system prompt, between the prompts other than the first sentinel on its own line, or after the terminal sentinel.
</format>

<guardrails>
- Structural fidelity is the top rule: the tag set, names, order, casing, and syntax of each output prompt match the corresponding source template exactly
- Every tag is present in the output, even when the instance is silent on it — fill with a short neutral default rather than deleting the tag
- Each sentinel — `___END_SYSTEM_PROMPT___` and `___END_TASK_PROMPT___` — appears exactly once, on its own line, in the specified position, and is never altered in spelling, casing, or spacing
- Sentinels never appear inside either prompt body
- Do not fabricate domain facts, tool names, data sources, or pipeline neighbours beyond what the inputs state or what the agent type obviously implies
- The system prompt and task prompt must not contradict each other
- Output only the two prompts and the two sentinels — no preamble, no section headers, no commentary, no sign-off
</guardrails>
