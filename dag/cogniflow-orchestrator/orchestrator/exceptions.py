"""
Cogniflow Orchestrator — Exception hierarchy.
All pipeline failures map to a typed exception so callers can
distinguish validation errors, agent failures, and graph errors.
"""

from __future__ import annotations


class PipelineError(Exception):
    """Base class for all orchestrator errors."""


class PipelineValidationError(PipelineError):
    """
    Raised by validate_pipeline() when the pipeline definition is invalid.
    Contains a list of all detected problems so they can be reported together.
    """
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("\n".join(f"  • {p}" for p in problems))


class CycleDetectedError(PipelineValidationError):
    """Raised when the dependency graph contains a cycle."""
    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__([f"Cycle detected: {' → '.join(cycle)}"])


class AgentExecutionError(PipelineError):
    """Raised when a claude subprocess exits with a non-zero code."""
    def __init__(self, agent_id: str, exit_code: int, stderr: str = "") -> None:
        self.agent_id  = agent_id
        self.exit_code = exit_code
        self.stderr    = stderr
        super().__init__(
            f"Agent '{agent_id}' failed (exit {exit_code})"
            + (f": {stderr[:200]}" if stderr.strip() else "")
        )


class AgentTimeoutError(PipelineError):
    """Raised when a claude subprocess exceeds the configured timeout."""
    def __init__(self, agent_id: str, timeout_s: int) -> None:
        self.agent_id  = agent_id
        self.timeout_s = timeout_s
        super().__init__(f"Agent '{agent_id}' timed out after {timeout_s}s")


class TokenBudgetError(PipelineError):
    """
    Raised when an agent's assembled context exceeds the token budget
    and the configured strategy is 'hard_fail'.
    """
    def __init__(self, agent_id: str, estimated: int, budget: int) -> None:
        self.agent_id  = agent_id
        self.estimated = estimated
        self.budget    = budget
        super().__init__(
            f"Agent '{agent_id}' context too large: "
            f"~{estimated:,} tokens estimated, budget is {budget:,}"
        )


class RouterError(PipelineError):
    """Raised when a router agent produces an unrecognised decision."""
    def __init__(self, agent_id: str, decision: str, valid: list[str]) -> None:
        self.agent_id = agent_id
        self.decision = decision
        self.valid    = valid
        super().__init__(
            f"Router '{agent_id}' returned unknown decision '{decision}'. "
            f"Valid decisions: {valid}"
        )


class MissingDependencyOutputError(PipelineError):
    """Raised when collect_inputs() cannot find an upstream agent's output."""
    def __init__(self, agent_id: str, dep_id: str, path: str) -> None:
        super().__init__(
            f"Agent '{agent_id}' needs output from '{dep_id}' "
            f"but {path} does not exist"
        )


class MissingStaticInputError(PipelineError):
    """Raised when a static_inputs entry points to a file that doesn't exist."""
    def __init__(self, agent_id: str, rel_path: str, resolved_path: str) -> None:
        super().__init__(
            f"Agent '{agent_id}' declares static input '{rel_path}' "
            f"but {resolved_path} does not exist"
        )
