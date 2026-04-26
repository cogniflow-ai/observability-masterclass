<description>
Rate the readability of `input_code.py` and list the top three clarity improvements. Your findings will be merged with parallel security and performance reviews by an aggregator agent.
</description>

<goals>
- Produce a single defensible readability score on a 1–10 scale
- Identify the three highest-impact clarity improvements
- Give the aggregator a clean, parseable output that can be merged without rework
</goals>

<context>
This review runs in parallel with a security review and a performance review of the same file. A downstream aggregator will deduplicate and prioritise all three reviews for a junior developer. Keep your scope tight — security and performance belong to the other reviewers.
</context>

<input>
`input_code.py` — supplied under "# Input files" in the prompt context.
</input>

<output>
1. A readability score from 1 to 10 with a one-sentence justification
2. Exactly three clarity improvements, each stated as a specific change the author should make, with line numbers where applicable
</output>

<format>
Markdown. Start with the score on its own line, then a numbered list of exactly three improvements. Do not include a preamble.
</format>

<guardrails>
- Exactly three improvements — no more, no fewer
- Every improvement must point to something concrete (a line, a function, a name)
- Do not comment on security or performance
- Do not rewrite code or supply replacement snippets longer than a single identifier or signature
</guardrails>
