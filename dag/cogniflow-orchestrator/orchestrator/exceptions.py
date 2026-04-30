"""
Cogniflow Orchestrator v3.5 — Exception hierarchy.

All pipeline exceptions inherit from PipelineError so callers can
catch a single base class when needed.
"""
from __future__ import annotations


class PipelineError(Exception):
    """Base class for all Cogniflow orchestrator errors."""


class PipelineValidationError(PipelineError):
    """Raised by validate_pipeline() when the pipeline definition is invalid."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


class AgentExecutionError(PipelineError):
    """Raised when an agent subprocess fails and retries are exhausted."""

    def __init__(self, agent_id: str, exit_code: int, message: str = "") -> None:
        self.agent_id  = agent_id
        self.exit_code = exit_code
        super().__init__(f"Agent '{agent_id}' failed (exit {exit_code}): {message}")


class AgentTimeoutError(PipelineError):
    """Raised when an agent subprocess exceeds its configured timeout."""

    def __init__(self, agent_id: str, timeout_s: int) -> None:
        self.agent_id  = agent_id
        self.timeout_s = timeout_s
        super().__init__(f"Agent '{agent_id}' timed out after {timeout_s}s")


class TokenBudgetError(PipelineError):
    """Raised when hard_fail budget strategy is triggered."""

    def __init__(self, agent_id: str, tokens: int, budget: int) -> None:
        self.agent_id = agent_id
        self.tokens   = tokens
        self.budget   = budget
        super().__init__(
            f"Agent '{agent_id}' context ({tokens} tokens) exceeds budget ({budget})"
        )


class CycleDetectedError(PipelineError):
    """Raised when the DAG loader finds a cycle in a dag-mode pipeline."""


class RouterError(PipelineError):
    """Raised when a router agent produces invalid or unresolvable routing.json."""

    def __init__(self, agent_id: str, reason: str) -> None:
        self.agent_id = agent_id
        super().__init__(f"Router '{agent_id}' error: {reason}")


class MissingDependencyOutputError(PipelineError):
    """Raised when an upstream agent's 05_output.md is absent at context assembly."""

    def __init__(self, agent_id: str, dep_id: str, path: str = "") -> None:
        self.agent_id = agent_id
        self.dep_id   = dep_id
        if path:
            super().__init__(
                f"Agent '{agent_id}' needs output from '{dep_id}' "
                f"but {path} does not exist"
            )
        else:
            super().__init__(
                f"Agent '{agent_id}' needs output from '{dep_id}', which has not run yet"
            )


class MissingStaticInputError(PipelineError):
    """Raised when a static_inputs entry points to a file that doesn't exist."""

    def __init__(self, agent_id: str, rel_path: str, resolved_path: str) -> None:
        self.agent_id = agent_id
        self.rel_path = rel_path
        super().__init__(
            f"Agent '{agent_id}' declares static input '{rel_path}' "
            f"but {resolved_path} does not exist"
        )


# ── Cyclic-mode exceptions ────────────────────────────────────────────────────

class DeadlockError(PipelineError):
    """Raised when on_deadlock=halt and a circular wait is detected."""

    def __init__(self, agents: list[str]) -> None:
        self.agents = agents
        super().__init__(
            f"Deadlock detected among agents: {', '.join(agents)}. "
            "Configure on_deadlock=escalate_pm or force_unblock_oldest to recover."
        )


class CycleLimitExceeded(PipelineError):
    """Raised when on_cycle_limit=halt and max_cycles is reached for a pair."""

    def __init__(self, agent_a: str, agent_b: str, cycle_count: int) -> None:
        self.agent_a     = agent_a
        self.agent_b     = agent_b
        self.cycle_count = cycle_count
        super().__init__(
            f"Cycle limit ({cycle_count}) exceeded between '{agent_a}' and '{agent_b}'. "
            "Configure on_cycle_limit=escalate_pm or force_done to recover."
        )


class MalformedOutputError(PipelineError):
    """Raised when an agent produces no valid routing block after max retries."""

    def __init__(self, agent_id: str, attempts: int) -> None:
        self.agent_id = agent_id
        self.attempts = attempts
        super().__init__(
            f"Agent '{agent_id}' produced malformed output after {attempts} attempt(s). "
            "Check 01_system.md — it must instruct the agent to end every response "
            "with the required JSON routing block."
        )


class PipelineTimeoutError(PipelineError):
    """Raised when the wall-clock pipeline timeout is reached."""

    def __init__(self, run_id: str, timeout_s: int) -> None:
        self.run_id    = run_id
        self.timeout_s = timeout_s
        super().__init__(
            f"Pipeline run '{run_id}' exceeded wall-clock timeout of {timeout_s}s"
        )


# ── Restored GAP exceptions (v3.5) ────────────────────────────────────────────

class SchemaViolationError(PipelineError):
    """Raised when an agent's output or input fails its declared schema.

    *phase* is ``"output"`` (default, v3.5 behaviour) or ``"input"``
    (v4, pre-invocation check against upstream outputs / static inputs).
    """

    def __init__(
        self,
        agent_id: str,
        violations: list[str],
        phase: str = "output",
    ) -> None:
        self.agent_id   = agent_id
        self.violations = violations
        self.phase      = phase
        bullet = "\n  • ".join(violations)
        label = "Input" if phase == "input" else "Output"
        super().__init__(
            f"{label} schema violation for '{agent_id}':\n  • {bullet}"
        )


class ApprovalTimeoutError(PipelineError):
    """Raised by GAP-3 when approval.timeout_s elapses with no decision."""

    def __init__(self, agent_id: str, timeout_s: int) -> None:
        self.agent_id  = agent_id
        self.timeout_s = timeout_s
        super().__init__(
            f"Agent '{agent_id}' timed out waiting for approval after {timeout_s}s. "
            f"Run: python cli.py approve <pipeline_dir> --agent {agent_id}"
        )


class ApprovalRejectedError(PipelineError):
    """Raised by GAP-3 when an approver writes status=rejected."""

    def __init__(self, agent_id: str, note: str = "") -> None:
        self.agent_id = agent_id
        self.note     = note
        super().__init__(
            f"Agent '{agent_id}' output was rejected"
            + (f": {note}" if note else "")
        )
