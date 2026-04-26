<description>
Inspect the incoming input and route it to the correct downstream branch.
</description>

<goals>
- Pick the single branch that best matches the input
- Return a deterministic decision with no ambiguity
- Keep reasoning minimal but traceable — the decision must be defensible
</goals>

<context>
[Describe what the upstream input looks like, what the downstream branches are, and the default branch to fall back to.]
</context>

<input>
The upstream input to be routed.
</input>

<branches>
- `[branch_id_1]` — [when to route here]
- `[branch_id_2]` — [when to route here]
- `[branch_id_default]` — [fallback]
</branches>

<output>
A routing decision containing:
1. `selected_branch`: the chosen branch id
2. `reason`: a one-sentence justification citing the specific feature of the input that drove the decision
</output>

<format>
JSON with exactly two keys: `selected_branch` and `reason`. No additional keys, no preamble, no sign-off.
</format>

<guardrails>
- Output valid JSON only — no Markdown fences, no prose wrapping
- Select exactly one branch id from the list above
- If no branch clearly matches, select the default and say so in `reason`
- Do not include the original input in the output
</guardrails>
