<description>
Write a concise technical specification for a command-line todo list application in Python. This spec is the source of truth for the rest of the software-factory pipeline.
</description>

<goals>
- Define the data model for a todo item
- Define the command-line interface: `add`, `list`, `done`, `delete`
- Define the file format used to persist todos between invocations
- Make every requirement unambiguous for an architect and developer
</goals>

<context>
This is the first step of a five-agent pipeline: spec → architect → developer → tester → documenter. The architect will design the module structure from your spec, the developer will implement it, the tester will write a pytest suite against it, and the documenter will write a README. If the spec is vague, every downstream agent will compound the problem.
</context>

<input>
A command-line todo list app in Python, invoked as `python todo.py <command >
</input>

<output>
A technical specification covering:
1. Data model — the fields of a todo item and their types
2. Commands — `add`, `list`, `done`, `delete`, each with exact CLI syntax and expected behaviour
3. File format — the on-disk representation of the todo list, including filename
4. Error behaviour — what happens on invalid input, missing file, unknown id
</output>

<format>
Markdown. Use H2 headings for each of the four sections above. Use short bullet points or small tables. Total length: 200–400 words. No introduction paragraph, no closing remarks.
</format>

<guardrails>
- Do not design modules, classes, or function signatures — that is the architect's job
- Do not write or include any Python code
- Do not add commands beyond the four listed
- Do not specify a GUI, a web interface, or a database — this is a CLI with file-based persistence
</guardrails>
