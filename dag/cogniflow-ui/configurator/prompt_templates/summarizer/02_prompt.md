<description>
Summarise the upstream input into a shorter artefact that preserves every load-bearing point.
</description>

<goals>
- Fit within the defined length target
- Preserve every distinct claim, finding, or decision from the input
- Produce a stand-alone artefact the consumer can read without the original
</goals>

<context>
[Describe what the upstream input is, who reads the summary, and why they need it condensed.]
</context>

<input>
The upstream artefact to summarise.
</input>

<output>
A summary of the upstream input, capped at [N] words / [N] bullets, structured as:
1. [Headline or key takeaway]
2. [Main points, grouped by theme]
3. [Any surfaced contradictions or open questions]
</output>

<format>
Markdown. [Bullets or prose]. Hard cap at [N] words / [N] bullets.
</format>

<guardrails>
- Do not exceed the length target
- Do not introduce claims not in the input
- Do not drop distinct findings; merge overlapping ones
- Do not add meta-commentary or sign-offs
- Output only the summary
</guardrails>
