<description>
Write a complete pytest test suite for the `todo.py` implementation produced by the upstream developer. The suite must cover every command and realistic edge cases.
</description>

<goals>
- Test every CLI command: `add`, `list`, `done`, `delete`
- Cover edge cases: missing data file, empty todo list, invalid id, duplicate ids, malformed arguments
- Produce a test file that runs against `todo.py` without further configuration
</goals>

<context>
This is the fourth step of a five-agent pipeline. The developer's `todo.py` is already present in the current working directory in direct mode — you may read it with your tools if helpful. In safe mode, work from the architect's design and the developer's source supplied as upstream input. Your tests will be run verbatim.
</context>

<input>
The developer's `todo.py` (available in cwd in direct mode, or as upstream input in safe mode), plus the upstream architect's design and the spec.
</input>

<output>
A complete `test_todo.py` pytest suite that covers all commands and edge cases.

Target file: `test_todo.py` in the current working directory (direct mode) — or return the complete source as text (safe mode).
</output>

<format>
Direct mode (Write tool available): write `test_todo.py` to the current working directory and reply with a one-line confirmation only.
Safe mode (no Write tool): output the complete Python source as plain text — no Markdown fences, no commentary, no preamble.
</format>

<guardrails>
- Do not modify `todo.py`; test it as shipped
- Use `tmp_path` or equivalent fixtures so tests do not touch the user's real data file
- Do not invent commands or behaviours the spec does not define
- Do not wrap the source in Markdown fences in safe mode
</guardrails>
