<description>
Synthesise three upstream research briefings into a single unified briefing titled "The State of AI-Assisted Software Development — Early 2026". This is the pivot point of the pipeline — the writer and fact-checker both depend on it.
</description>

<goals>
- Integrate the three research streams into one coherent narrative, not three parallel summaries
- Surface the tensions and contradictions between streams that senior leaders need to act on
- Produce recommendations concrete enough to be operational, not platitudes
</goals>

<context>
This is the fan-in step of a seven-agent pipeline. A writer will turn your briefing into a publication-ready article, and an independent fact-checker will audit it for unsupported or overstated claims. Both downstream agents depend on your output, so precision matters as much as integration.
</context>

<input>
Three upstream research briefings:
- The AI coding tool landscape (tools, capabilities, integrations)
- Agentic coding systems (frameworks, benchmarks, real-world gaps)
- Enterprise adoption (ROI, governance, security, organisational friction)
</input>

<output>
A single unified briefing titled "The State of AI-Assisted Software Development — Early 2026" with the following sections:
1. **Executive Summary** (≤150 words) — the single most important insight a CTO needs to act on today
2. **The Landscape in One View** — how tools, agentic systems, and enterprise reality fit together as a coherent picture
3. **Where the Value Is Real** — concrete, evidence-backed claims about what is genuinely working and for whom
4. **Where the Hype Outpaces Reality** — what marketing says vs what is actually happening in production, with specifics
5. **The Tensions Worth Watching** — 3–4 genuine contradictions or unsolved problems that will shape the next 12 months
6. **What Technical Leaders Should Do Now** — 3–5 specific, actionable recommendations
</output>

<format>
Markdown. Begin with the title as H1. Use H2 headings for each of the six sections above. Executive Summary must be 150 words or fewer. Use short paragraphs; prefer prose over bullet lists except in "Tensions Worth Watching" and "What Technical Leaders Should Do Now".
</format>

<guardrails>
- Do not summarise each upstream stream in turn — weave them together around themes
- Do not introduce claims not supported by at least one upstream briefing
- Recommendations must be specific and actionable — no platitudes like "invest in training"
- Do not repeat verbatim phrasing from the source briefings
</guardrails>
