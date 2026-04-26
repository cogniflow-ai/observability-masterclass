<description>
Synthesise the upstream inputs into a single unified artefact.
</description>

<goals>
- Integrate the upstream inputs into one coherent narrative, not parallel summaries
- Surface tensions and contradictions that consumers of the output need to know about
- Produce output that stands alone — the consumer will not see the upstream inputs
</goals>

<context>
[Describe what the upstream inputs are, where this synthesis sits in the pipeline, and what the downstream will do with it.]
</context>

<input>
The upstream outputs supplied as inputs, one per upstream agent.
</input>

<output>
A unified artefact containing:
1. [Executive summary or headline insight]
2. [Integrated sections organised by theme, not by input source]
3. [Explicit treatment of contradictions / tensions]
4. [Recommendations or conclusions grounded in the inputs]
</output>

<format>
Markdown. Use H2 headings for each section. Prefer prose over bullet lists except where structure genuinely helps.
</format>

<guardrails>
- Do not summarise each upstream input in turn — weave them around themes
- Do not introduce claims not supported by at least one upstream input
- Do not repeat verbatim phrasing from the inputs
- Drop no distinct finding; merge overlapping ones
- Output only the unified artefact — no preamble, no sign-off
</guardrails>
