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

<output>
Four parts, in strict order, with nothing else in the output:
1. The tailored system prompt — every tag from SYSTEM_TEMPLATE retained in place, every bracketed placeholder replaced with concrete content for this instance
2. The literal sentinel line `___END_SYSTEM_PROMPT___` on its own line
3. The tailored task prompt — every tag from TASK_TEMPLATE retained in place, every bracketed placeholder replaced with concrete content for this instance
4. The literal sentinel line `___END_TASK_PROMPT___` on its own line

Every tag from each source template is present. No bracketed placeholders, template hints, or TODOs remain. Each sentinel appears exactly once, on its own line, and nowhere inside either prompt body.
</output>

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
