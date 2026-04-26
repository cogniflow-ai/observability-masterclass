"""Meta-prompt specialization — turns generic type templates into a tailored
(system, task) pair for a concrete agent instance via a one-shot `claude` CLI
call.

Design notes:
- The Configurator shells out to the `claude` CLI exactly like the Orchestrator
  does (see orchestrator/agent.py), to keep the Configurator's dependency
  surface identical: a working `claude` install, nothing else.
- This module has no knowledge of FastAPI, routes, or the pipeline on disk.
  filesystem.py is the only caller.
"""
from __future__ import annotations
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


SENTINEL_SYS = "___END_SYSTEM_PROMPT___"
SENTINEL_TASK = "___END_TASK_PROMPT___"

# Same tagline shape validation.py uses — any `<tag>` or `</tag>` where the
# name is a word starting with a letter/underscore. We compare the *sequence*
# of (kind, name) pairs in source vs tailored.
_TAG_RE = re.compile(r"<(/?)([A-Za-z_][\w-]*)>")

# Leftover-placeholder signals (spec §Validation):
#   - {{SOMETHING}}        — unresolved meta placeholder
#   - [short bracketed …]  — template hint like "[Primary outcome ...]"
#   - literal TODO token   — word boundaries so genuine prose like "todo list"
#                            is not flagged unless uppercase
#   - ellipsis character   — "…" and the three-dot "..." inside brackets
_LEFTOVER_PATTERNS = [
    (re.compile(r"\{\{[^}]+\}\}"), "unresolved `{{PLACEHOLDER}}`"),
    (re.compile(r"\[[^\[\]\n]{3,120}\]"), "bracketed template hint `[...]`"),
    (re.compile(r"\bTODO\b"), "literal `TODO` marker"),
    (re.compile(r"…"), "ellipsis character `…`"),
]


@dataclass
class SpecializeError(Exception):
    """Raised when the CLI call, parse, or validation fails. Carries the raw
    model response (if we got one) so the UI can display it."""
    stage: str           # 'cli' | 'parse' | 'validate'
    message: str
    raw_response: str = ""

    def __str__(self) -> str:
        return f"[{self.stage}] {self.message}"


# ── Public API ────────────────────────────────────────────────────────────────

def specialize(
    *,
    agent_name: str,
    type_description: str,
    instance_name: str,
    instance_description: str,
    system_template: str,
    task_template: str,
    meta_system: str,
    meta_task: str,
    claude_bin: str,
    timeout_s: int = 300,
) -> tuple[str, str]:
    """Run the one-shot specialization pipeline.

    Returns (tailored_system, tailored_task).
    Raises SpecializeError on any CLI / parse / validation failure.
    """
    if not claude_bin:
        raise SpecializeError("cli",
            "claude CLI not found. Set claude_bin in config.json or CLAUDE_BIN env var.")

    if not meta_system.strip() or not meta_task.strip():
        raise SpecializeError("cli",
            "meta prompts are empty. Check prompt_templates/meta/01_system.md "
            "and 02_prompt.md.")

    # 1. Substitute placeholders in the meta task prompt.
    user_message = _substitute(meta_task, {
        "AGENT_NAME":           agent_name,
        "TYPE_DESCRIPTION":     type_description,
        "INSTANCE_NAME":        instance_name,
        "INSTANCE_DESCRIPTION": instance_description,
        "SYSTEM_TEMPLATE":      system_template,
        "TASK_TEMPLATE":        task_template,
    })
    _assert_no_unresolved_meta_placeholders(user_message)

    # 2. Invoke the claude CLI.
    raw = _invoke_claude(claude_bin, meta_system, user_message, timeout_s)

    # 3. Split on the two sentinels and validate positioning.
    tailored_sys, tailored_task = _split_sentinels(raw)

    # 4. Structural validation.
    _check_tag_sequence(system_template, tailored_sys, where="system prompt")
    _check_tag_sequence(task_template,   tailored_task, where="task prompt")
    _check_no_leftovers(tailored_sys,  where="system prompt", raw=raw)
    _check_no_leftovers(tailored_task, where="task prompt",   raw=raw)

    return tailored_sys, tailored_task


# ── Substitution ──────────────────────────────────────────────────────────────

def _substitute(template: str, values: dict[str, str]) -> str:
    out = template
    for key, val in values.items():
        out = out.replace("{{" + key + "}}", val)
    return out


def _assert_no_unresolved_meta_placeholders(rendered: str) -> None:
    leftovers = re.findall(r"\{\{([A-Z_][A-Z0-9_]*)\}\}", rendered)
    if leftovers:
        unique = sorted(set(leftovers))
        raise SpecializeError("cli",
            f"Meta task prompt still contains unresolved placeholders: "
            f"{', '.join(unique)}")


# ── CLI call ──────────────────────────────────────────────────────────────────

def _invoke_claude(claude_bin: str, system_text: str,
                   user_text: str, timeout_s: int) -> str:
    """Shell out to `claude -p --system-prompt ... --output-format json`.
    Returns the parsed `result` field (the model's answer text)."""
    # On Windows the claude CLI is a .cmd wrapper and CreateProcess can't
    # pass multi-line argv values cleanly. Collapse newlines in the system
    # prompt the same way the Orchestrator does.
    if sys.platform == "win32":
        system_arg = system_text.replace("\r\n", " ").replace("\n", " ")
    else:
        system_arg = system_text

    argv = [claude_bin,
            "--system-prompt", system_arg,
            "--output-format", "json",
            "-p"]
    try:
        proc = subprocess.run(
            argv,
            input=user_text.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
        )
    except FileNotFoundError:
        raise SpecializeError("cli",
            f"claude executable not found at '{claude_bin}'.")
    except subprocess.TimeoutExpired:
        raise SpecializeError("cli",
            f"claude CLI timed out after {timeout_s}s.")

    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise SpecializeError("cli",
            f"claude exited with code {proc.returncode}: {stderr[:400]}")

    stdout = proc.stdout.decode("utf-8", errors="replace")
    if not stdout.strip():
        raise SpecializeError("cli", "claude returned an empty response.")

    # Envelope is {"result": "...", "usage": {...}, ...}. Be permissive about
    # the field name ("result" / "text") the way the Orchestrator is.
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise SpecializeError("cli",
            f"claude stdout was not valid JSON: {e.msg}",
            raw_response=stdout)

    if not isinstance(envelope, dict):
        raise SpecializeError("cli", "claude envelope is not an object.",
                              raw_response=stdout)
    answer = envelope.get("result")
    if answer is None:
        answer = envelope.get("text", "")
    if not isinstance(answer, str) or not answer.strip():
        raise SpecializeError("cli", "claude envelope missing `result` text.",
                              raw_response=stdout)
    return answer


# ── Parse sentinels ───────────────────────────────────────────────────────────

def _split_sentinels(raw: str) -> tuple[str, str]:
    """Extract (tailored_system, tailored_task) from a response that must
    contain each sentinel exactly once, each on its own line, in order, and
    nothing non-whitespace after the terminal sentinel."""
    n_sys = raw.count(SENTINEL_SYS)
    n_task = raw.count(SENTINEL_TASK)
    if n_sys != 1:
        raise SpecializeError("parse",
            f"Expected exactly one `{SENTINEL_SYS}`, got {n_sys}.",
            raw_response=raw)
    if n_task != 1:
        raise SpecializeError("parse",
            f"Expected exactly one `{SENTINEL_TASK}`, got {n_task}.",
            raw_response=raw)

    sys_idx = raw.index(SENTINEL_SYS)
    task_idx = raw.index(SENTINEL_TASK)
    if sys_idx > task_idx:
        raise SpecializeError("parse",
            f"Sentinels in wrong order (task before system).",
            raw_response=raw)

    # Own-line check for each sentinel.
    for sent in (SENTINEL_SYS, SENTINEL_TASK):
        for line in raw.splitlines():
            if sent in line and line.strip() != sent:
                raise SpecializeError("parse",
                    f"Sentinel `{sent}` is not alone on its line: {line!r}",
                    raw_response=raw)

    before, _, rest = raw.partition(SENTINEL_SYS)
    middle, _, after = rest.partition(SENTINEL_TASK)

    if after.strip():
        raise SpecializeError("parse",
            f"Non-whitespace content after `{SENTINEL_TASK}`: {after.strip()[:200]!r}",
            raw_response=raw)

    return before.strip("\n"), middle.strip("\n")


# ── Tag-sequence check ───────────────────────────────────────────────────────

def _tag_sequence(text: str) -> list[tuple[str, str]]:
    """Return the ordered list of (kind, name) tag occurrences, where kind is
    'open' or 'close'. Preserves both order and casing."""
    return [("close" if m.group(1) else "open", m.group(2))
            for m in _TAG_RE.finditer(text)]


def _check_tag_sequence(source: str, tailored: str, *, where: str) -> None:
    src_seq = _tag_sequence(source)
    out_seq = _tag_sequence(tailored)
    if src_seq == out_seq:
        return
    # Try to surface the most useful first divergence.
    for i, src in enumerate(src_seq):
        if i >= len(out_seq):
            raise SpecializeError("validate",
                f"{where}: tag sequence shorter than template — missing "
                f"{_fmt_tag(src)} at position {i+1}.")
        if out_seq[i] != src:
            raise SpecializeError("validate",
                f"{where}: tag mismatch at position {i+1} — "
                f"template has {_fmt_tag(src)}, output has "
                f"{_fmt_tag(out_seq[i])}.")
    # Output has extra tags past the template.
    extra = out_seq[len(src_seq):]
    raise SpecializeError("validate",
        f"{where}: output has {len(extra)} extra tag(s) past the template "
        f"(first: {_fmt_tag(extra[0])}).")


def _fmt_tag(tag: tuple[str, str]) -> str:
    kind, name = tag
    return f"</{name}>" if kind == "close" else f"<{name}>"


# ── Leftover-placeholder check ────────────────────────────────────────────────

def _check_no_leftovers(text: str, *, where: str, raw: str) -> None:
    for pattern, label in _LEFTOVER_PATTERNS:
        m = pattern.search(text)
        if m:
            raise SpecializeError("validate",
                f"{where} still contains {label}: {m.group(0)!r}",
                raw_response=raw)
