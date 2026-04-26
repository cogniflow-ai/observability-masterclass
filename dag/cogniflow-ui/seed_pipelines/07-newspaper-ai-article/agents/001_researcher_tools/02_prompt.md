<description>
Research the current landscape of AI coding assistant tools as of early 2026 and produce a dense, structured briefing. Your work will be combined with two parallel research streams by a downstream synthesiser.
</description>

<goals>
- Map the leading AI coding tools, their positioning, differentiators, and pricing
- Characterise what each tool does well and poorly across typical developer tasks
- Identify how these tools integrate into developer workflows and where the market is shifting
</goals>

<context>
This briefing feeds a seven-agent pipeline: three parallel researchers (tools, agentic, enterprise) → synthesiser → writer + fact-checker → editor. A rigorous fact-checker will later flag overstated or unverified claims, so precision and honesty about uncertainty are load-bearing.
</context>

<input>
Scope: AI coding assistant tools in early 2026.
</input>

<output>
A structured research briefing covering:
1. The leading tools (GitHub Copilot, Cursor, Codeium, Tabnine, Amazon Q Developer, JetBrains AI, Replit Ghostwriter) — positioning, key differentiators, pricing models
2. Capability benchmarks — what each tool does well vs poorly across completion, refactoring, test generation, documentation, and bug detection
3. Integration patterns — IDE plugins, CLI tools, API access, CI/CD hooks
4. The shift from autocomplete to chat-first and agentic interfaces — what drove it and where it stands now
5. Notable new entrants or consolidation in the past 12 months
</output>

<format>
Markdown. Use H2 headings for each of the five sections above. Use short paragraphs and tight bullet lists. Target 600–900 words.
</format>

<guardrails>
- Do not cover agentic frameworks or multi-agent architectures — that is the agentic researcher's scope
- Do not cover enterprise adoption, ROI, or governance — that is the enterprise researcher's scope
- Where a figure is uncertain, state the uncertainty; do not invent numbers
- Do not include vendor marketing language or promotional framing
</guardrails>
