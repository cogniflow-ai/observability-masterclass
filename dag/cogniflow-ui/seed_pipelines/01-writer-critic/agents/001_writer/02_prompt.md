<description>
Draft a 300-word blog post arguing why developers should learn to use Claude CLI. This draft will be passed to a critic agent for review.
</description>

<goals>
- Convince a working developer that Claude CLI is worth their time to learn
- Ground the argument in concrete developer workflows, not abstract capability claims
- Produce a self-contained piece that reads as a finished first draft
</goals>

<context>
This is the first step of a two-agent writer–critic pipeline. A downstream critic will rate your draft on clarity, accuracy, and tone (1–10 each) and list three concrete improvements. Write with that review in mind — strong structure and precise claims will score well; vague praise and filler will not.
</context>

<input>
Topic: why developers should learn to use Claude CLI (the Claude Code terminal tool).
</input>

<output>
A single blog post of approximately 300 words arguing the case for developers learning Claude CLI, with a clear opening hook, substantive body, and a concrete closing takeaway.
</output>

<format>
Plain Markdown. Begin with a short title as an H1. Use short paragraphs. No bullet lists unless they add real value. Target 300 words (±20).
</format>

<guardrails>
- Output only the blog post — no preamble, no notes, no word count annotation
- Do not fabricate features, benchmarks, or pricing
- Do not include phrases like "in conclusion", "it's worth noting", or "the landscape is evolving"
</guardrails>
