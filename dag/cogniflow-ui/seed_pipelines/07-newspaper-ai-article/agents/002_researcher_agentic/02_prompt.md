<description>
Research the state of agentic AI coding in early 2026 and produce a dense, structured briefing. Your work will be combined with two parallel research streams by a downstream synthesiser.
</description>

<goals>
- Define what "agentic coding" means in practice and how it differs from earlier assistant paradigms
- Characterise the leading frameworks and their real-world capabilities and failure modes
- Surface the gap between demo performance and production reliability
</goals>

<context>
This briefing feeds a seven-agent pipeline: three parallel researchers (tools, agentic, enterprise) → synthesiser → writer + fact-checker → editor. A rigorous fact-checker will later flag overstated or unverified claims, so precision and honesty about uncertainty are load-bearing.
</context>

<input>
Scope: agentic AI coding systems, multi-agent software development patterns, and related benchmarks in early 2026.
</input>

<output>
A structured research briefing covering:
1. What agentic coding means in practice — how agents plan, execute, and self-correct across multi-step programming tasks (file editing, test running, debugging loops)
2. Key frameworks and tools — Claude Code, Devin, SWE-agent, OpenHands, AutoCodeRover — their capabilities, limitations, and where they are being deployed
3. Multi-agent patterns in codebases — orchestrator-worker, specialist agents, code review agents, test generation agents
4. The gap between demo performance and production reliability — where agentic coding breaks down today
5. Benchmark results as of early 2026 (SWE-bench, HumanEval, LiveCodeBench) and what they reveal about real-world usefulness
</output>

<format>
Markdown. Use H2 headings for each of the five sections above. Use short paragraphs and tight bullet lists. Target 600–900 words.
</format>

<guardrails>
- Do not cover the broader AI coding tool landscape or individual editor assistants — that is the tools researcher's scope
- Do not cover enterprise adoption, ROI, or governance — that is the enterprise researcher's scope
- Be specific about what works and what does not; avoid generic optimism
- Where a benchmark figure is uncertain, state the uncertainty; do not invent numbers
</guardrails>
