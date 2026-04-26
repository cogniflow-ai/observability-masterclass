<description>
Given the technical specification from the upstream spec agent, design the module structure and function-level interface for the todo CLI. The developer, tester, and documenter will all work from your design.
</description>

<goals>
- Produce a module/file layout that cleanly separates CLI parsing, core logic, and persistence
- Define every function and class with a complete signature and a one-line docstring
- Resolve every implementation decision so the developer does not need to improvise
</goals>

<context>
This is the second step of a five-agent pipeline (spec → architect → developer → tester → documenter). The developer will implement your design verbatim, the tester will write pytest tests against the same interface, and the documenter will describe the user-facing behaviour. Keep interfaces stable and explicit.
</context>

<input>
The technical specification produced by the upstream spec agent.
</input>

<output>
A design document containing:
1. Module/file structure — a tree or list of files with a one-line purpose each
2. Class diagram — any classes with their attributes and method names (signatures only)
3. Function list — every function with its full signature and one-line docstring
4. Control flow — a short description of how a single CLI invocation flows from entry point to file write
</output>

<format>
Markdown. Use H2 headings for each section. Use fenced code blocks for signatures and docstrings (Python syntax). Total length: roughly 300–500 words. No implementation bodies.
</format>

<guardrails>
- Only signatures and docstrings — no function bodies, no implementation logic
- Do not introduce data fields, commands, or behaviours that are not in the spec
- If the spec is ambiguous, call it out in a short "Assumptions" list at the end rather than silently deciding
- Output only the design — no preamble, no notes to the developer beyond what is required
</guardrails>
