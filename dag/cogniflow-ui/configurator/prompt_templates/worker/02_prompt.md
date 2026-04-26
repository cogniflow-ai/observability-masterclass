<description>
[Describe the single concrete task this worker must perform. One or two sentences.]
</description>

<goals>
- [Primary outcome this agent must achieve]
- [Secondary outcome — remove if not applicable]
- [Constraint the output must satisfy]
</goals>

<context>
[Where this agent sits in the pipeline. What upstream has produced, what downstream will do with this output. One short paragraph.]
</context>

<input>
[Name the inputs this agent will receive — upstream agent outputs, files, parameters.]
</input>

<output>
[Describe the artefact to produce, as concretely as possible: sections, length, structure.]
</output>

<format>
[Markdown / JSON / plain text. Any specific structural rules: headings, length limits, tone.]
</format>

<guardrails>
- [Scope boundary: what this agent must NOT do]
- [Content rule: claims must be traceable, no fabrication, no hedging, etc.]
- Output only the artefact itself — no preamble, no notes, no sign-off
</guardrails>
