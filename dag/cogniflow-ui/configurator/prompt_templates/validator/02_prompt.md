<description>
Validate the upstream artefact against the acceptance criteria and return a structured verdict.
</description>

<goals>
- Produce a deterministic pass/fail verdict per criterion and overall
- Locate each failure precisely so the producer can fix it directly
- Give the pipeline a machine-readable result it can route on
</goals>

<context>
[Describe what the upstream artefact is, what happens on pass vs fail, and who reads the verdict.]
</context>

<input>
The upstream artefact supplied as input.
</input>

<criteria>
- `[criterion_1]` — [what it requires, how to check]
- `[criterion_2]` — [what it requires, how to check]
- `[criterion_3]` — [what it requires, how to check]
</criteria>

<output>
A verdict containing:
1. `results`: a list of `{criterion, passed, location, note}` — one entry per criterion
2. `overall`: `pass` if all criteria passed, otherwise `fail`
3. `summary`: a one-sentence overall assessment
</output>

<format>
JSON with exactly three top-level keys: `results`, `overall`, `summary`. No preamble, no Markdown fences.
</format>

<guardrails>
- Output valid JSON only
- Every criterion from the list must appear in `results`
- `overall` must be `pass` if and only if every `passed` is true
- Do not rewrite or correct the artefact
- Do not invent criteria beyond those listed
</guardrails>
