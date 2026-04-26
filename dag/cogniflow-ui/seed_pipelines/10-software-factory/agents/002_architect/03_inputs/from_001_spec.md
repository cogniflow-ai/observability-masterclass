## Data Model

A todo item has exactly these fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Unique, monotonically increasing, assigned on creation. Never reused. |
| `title` | string | Non-empty, max 200 characters. May contain spaces. |
| `done` | boolean | `false` on creation; set to `true` by the `done` command. |
| `created_at` | string | ISO 8601 UTC timestamp (e.g. `2026-04-20T14:30:00Z`), set on creation. |

## Commands

Invocation: `python todo.py <command> [args]`

- **`add <title>`** — Creates a new item with the given title. `title` is the remainder of the argv joined by spaces, or a single quoted argument. Assigns `id` = max existing id + 1 (or 1 if none). Prints `Added #<id>: <title>`.
- **`list`** — Prints all items, sorted by `id` ascending, one per line, in the format: `[<id>] [<x or space>] <title> (<created_at>)`. Checked box `[x]` if done, `[ ]` if not. If the list is empty, prints `No todos.`.
- **`done <id>`** — Marks item with `<id>` as done. Prints `Marked #<id> done`. If already done, the command succeeds without modification and prints the same message.
- **`delete <id>`** — Removes the item with `<id>` from the list. Prints `Deleted #<id>`. Deleted ids are not reused.

## File Format

- Filename: `todos.json`, located in the current working directory.
- Encoding: UTF-8.
- Content: a single JSON array of objects, each object containing the four fields from the Data Model.
- An empty list is represented as `[]`.
- The file is rewritten in full on every mutation (`add`, `done`, `delete`).
- Example:
  ```
  [{"id": 1, "title": "Buy milk", "done": false, "created_at": "2026-04-20T14:30:00Z"}]
  ```

## Error Behaviour

All errors are written to stderr; the process exits with a non-zero status code.

| Condition | Exit code | Message |
|-----------|-----------|---------|
| Unknown command | 2 | `Unknown command: <cmd>` |
| Missing required argument | 2 | `Usage: python todo.py <command> [args]` |
| Empty title on `add` | 2 | `Title must not be empty` |
| `<id>` not an integer | 2 | `Id must be an integer` |
| Unknown `<id>` on `done`/`delete` | 1 | `No todo with id <id>` |
| `todos.json` missing | — | Treated as empty list; created on next mutation. No error. |
| `todos.json` malformed JSON | 1 | `Corrupt todos.json` |

Successful commands exit with status code 0.