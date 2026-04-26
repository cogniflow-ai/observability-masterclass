## Module Structure

```
todo.py          # Entry point; argparse dispatch to command handlers
storage.py       # JSON file load/save for todos.json
models.py        # Todo dataclass and (de)serialisation helpers
commands.py      # Pure command logic: add, list, done, delete
errors.py        # Exception types mapped to exit codes
```

## Class Diagram

```python
@dataclass
class Todo:
    """A single todo item as defined in the spec."""
    id: int
    title: str
    done: bool
    created_at: str

    def to_dict(self) -> dict:
        """Return the JSON-serialisable dict form of this todo."""

    @classmethod
    def from_dict(cls, data: dict) -> "Todo":
        """Build a Todo from a dict loaded from todos.json."""
```

```python
class TodoError(Exception):
    """Base class for all todo errors; carries exit_code and message."""
    exit_code: int
    message: str

class UsageError(TodoError):
    """Exit 2: unknown command, missing arg, empty title, non-integer id."""

class NotFoundError(TodoError):
    """Exit 1: no todo matches the given id."""

class CorruptStoreError(TodoError):
    """Exit 1: todos.json is not valid JSON."""
```

## Function List

### `todo.py`

```python
def main(argv: list[str] | None = None) -> int:
    """Parse argv, dispatch to the matching command, return the exit code."""

def _dispatch(command: str, args: list[str]) -> str:
    """Route a command name + remaining args to its handler; return stdout text."""
```

### `storage.py`

```python
STORE_PATH: str = "todos.json"

def load() -> list[Todo]:
    """Read todos.json from CWD; return [] if missing; raise CorruptStoreError on bad JSON."""

def save(todos: list[Todo]) -> None:
    """Rewrite todos.json in full as a UTF-8 JSON array of todo dicts."""
```

### `models.py`

```python
def next_id(todos: list[Todo]) -> int:
    """Return max(existing ids) + 1, or 1 if the list is empty."""

def now_iso_utc() -> str:
    """Return the current UTC time as an ISO 8601 string ending in 'Z'."""
```

### `commands.py`

```python
def add(args: list[str]) -> str:
    """Join args as the title, create a Todo, persist, return the 'Added #<id>: <title>' line."""

def list_cmd(args: list[str]) -> str:
    """Return all todos sorted by id formatted per spec, or 'No todos.' if empty."""

def done(args: list[str]) -> str:
    """Mark the todo with the given id as done, persist, return 'Marked #<id> done'."""

def delete(args: list[str]) -> str:
    """Remove the todo with the given id, persist, return 'Deleted #<id>'."""

def _parse_id(raw: str) -> int:
    """Return int(raw) or raise UsageError('Id must be an integer')."""

def _validate_title(title: str) -> str:
    """Return title if non-empty and <=200 chars, else raise UsageError."""

def _format_line(t: Todo) -> str:
    """Return '[<id>] [<x or space>] <title> (<created_at>)' for a single todo."""
```

## Control Flow

1. Shell runs `python todo.py <command> [args]`; `main(sys.argv[1:])` is called.
2. `main` validates that at least one arg is present (else `UsageError`) and calls `_dispatch`.
3. `_dispatch` maps `add`/`list`/`done`/`delete` to the corresponding `commands.*` handler, else raises `UsageError("Unknown command: <cmd>")`.
4. The handler calls `storage.load()` to read the current list (empty if file missing, `CorruptStoreError` if malformed).
5. The handler mutates the list in memory (validating title or id via `_parse_id`/`_validate_title`, raising `NotFoundError` if an id is unknown) and calls `storage.save()` on any mutation.
6. The handler returns the success message; `main` prints it to stdout and returns `0`.
7. Any `TodoError` raised en route is caught in `main`, its `message` written to stderr, and its `exit_code` returned.

## Assumptions

- The 200-character title limit from the Data Model triggers `UsageError` with the same `Title must not be empty` family of exit code 2; spec does not specify a distinct message, so the same "empty title" path is reused for over-length titles. Flag for spec clarification.
- `list` is implemented as `list_cmd` to avoid shadowing the builtin; external CLI name remains `list`.