<description>
Write a `README.md` for the todo app produced by the upstream developer. It must allow a new user to install, run, and understand the app without reading the source.
</description>

<goals>
- Explain installation in one short section
- Provide a runnable example for each command: `add`, `list`, `done`, `delete`
- Describe the on-disk file format so users know where their data lives
</goals>

<context>
This is the final step of the five-agent pipeline. The developer's `todo.py` is available in the current working directory in direct mode; you may read it to ensure the README matches what was actually implemented. In safe mode, work from the architect's design, the spec, and the developer's source supplied as upstream input.
</context>

<input>
The developer's `todo.py` (in cwd in direct mode, or as upstream input in safe mode), plus the upstream design and spec.
</input>

<output>
A `README.md` containing:
1. A one-paragraph overview of what the app does
2. Installation / requirements
3. Usage — a subsection per command with a concrete shell example and expected output
4. File format — where todos are stored and how they are encoded
</output>

<format>
Direct mode (Write tool available): write `README.md` to the current working directory and reply with a one-line confirmation only.
Safe mode (no Write tool): output the complete Markdown as plain text — no commentary, no preamble.
Use H1 for the project title, H2 for each top-level section, and fenced code blocks for shell examples.
</format>

<guardrails>
- Do not document commands or options the implementation does not support
- Do not invent dependencies the app does not actually use
- Do not wrap the entire README in a Markdown fence in safe mode — output it as-is
- Keep it focused on the user; no internal design discussion
</guardrails>
