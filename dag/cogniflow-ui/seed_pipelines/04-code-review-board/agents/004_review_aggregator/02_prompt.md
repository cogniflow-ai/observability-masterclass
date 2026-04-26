<description>
Merge the three upstream code reviews (security, performance, readability) into a single prioritised report for a junior developer. This is the final deliverable of the code-review-board pipeline.
</description>

<goals>
- Produce one unified report grouped by severity, not by reviewer
- Deduplicate overlapping findings and reconcile any conflicting severities
- Make every finding actionable enough that a junior developer can pick it up and fix it
</goals>

<context>
Three specialist reviewers have independently reviewed the same `input_code.py`: a security engineer, a performance engineer, and a readability reviewer. Their outputs are supplied as upstream input. Your report is the final artefact of the pipeline — it is what the junior developer will actually read, so structure and tone matter as much as completeness.
</context>

<input>
Three upstream review outputs:
1. Security review — vulnerabilities with line numbers and severity
2. Performance review — bottlenecks with line numbers and severity
3. Readability review — a 1–10 score and three clarity improvements
</input>

<output>
A single prioritised report with the following sections:
1. A one-paragraph executive summary (3–4 sentences) characterising the overall health of the code
2. Findings grouped under four headings: Critical, High, Medium, Low — each finding tagged with its category (security / performance / readability) and line number(s)
3. A short "Where to start" section listing the top 3 fixes to tackle first, in order
</output>

<format>
Markdown. Use H2 headings for each severity group. Under each heading, use a bulleted list where each bullet begins with `[security]`, `[performance]`, or `[readability]` followed by the finding and line numbers. Omit any severity heading that has no findings.
</format>

<guardrails>
- Do not introduce findings that are not present in one of the three upstream reviews
- Deduplicate: if two reviewers flag the same line for related reasons, merge them into one finding
- When reviewers disagree on severity, pick the higher severity and note the disagreement in one clause
- Do not rewrite the code or include full replacement snippets
- Keep the tone instructive and constructive — this is read by a junior engineer
</guardrails>
