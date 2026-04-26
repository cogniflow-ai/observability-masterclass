<description>
Classify the incoming input into one of the defined categories.
</description>

<goals>
- Produce a deterministic classification that a downstream system can rely on
- Return a confidence level the consumer can use to gate follow-up actions
- Keep reasoning short but traceable
</goals>

<context>
[Describe what the input looks like, what consumer reads the classification, and what the fallback category is.]
</context>

<input>
The upstream input to be classified.
</input>

<categories>
- `[category_1]` — [definition]
- `[category_2]` — [definition]
- `[category_other]` — fallback when no category clearly applies
</categories>

<output>
A classification decision containing:
1. `category`: the selected category id
2. `confidence`: one of `high` | `medium` | `low`
3. `reason`: a one-sentence justification citing the specific feature of the input that drove the decision
</output>

<format>
JSON with exactly three keys: `category`, `confidence`, `reason`. No additional keys, no preamble.
</format>

<guardrails>
- Output valid JSON only — no Markdown fences, no prose wrapping
- Use only category ids from the list above
- When the input is ambiguous, pick the fallback and set confidence to `low`
- Do not include the original input in the output
</guardrails>
