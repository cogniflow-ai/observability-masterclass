"""Tests for the exception hierarchy."""
import pytest
from orchestrator.exceptions import (
    PipelineError, PipelineValidationError, AgentExecutionError,
    AgentTimeoutError, TokenBudgetError, CycleDetectedError,
    DeadlockError, CycleLimitExceeded, MalformedOutputError,
    PipelineTimeoutError,
)


def test_all_inherit_pipeline_error():
    for cls in (
        PipelineValidationError, AgentExecutionError, AgentTimeoutError,
        TokenBudgetError, CycleDetectedError, DeadlockError,
        CycleLimitExceeded, MalformedOutputError, PipelineTimeoutError,
    ):
        assert issubclass(cls, PipelineError)


def test_validation_error_stores_errors():
    err = PipelineValidationError(["err A", "err B"])
    assert "err A" in err.errors
    assert "err B" in err.errors
    assert "err A" in str(err)


def test_agent_execution_error_stores_fields():
    err = AgentExecutionError("agent_x", 1, "bad output")
    assert err.agent_id == "agent_x"
    assert err.exit_code == 1


def test_deadlock_error_stores_agents():
    err = DeadlockError(["a", "b"])
    assert "a" in err.agents


def test_cycle_limit_stores_fields():
    err = CycleLimitExceeded("architect", "developer", 11)
    assert err.agent_a == "architect"
    assert err.cycle_count == 11
