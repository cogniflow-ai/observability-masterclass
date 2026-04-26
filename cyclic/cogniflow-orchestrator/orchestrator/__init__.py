"""
Cogniflow Multi-Agent Orchestrator v3.5

v3.5 is v3.0 with GAP-1, GAP-2, GAP-3 restored and all configuration
moved from environment variables into per-pipeline ``config.json``.

Supports both acyclic DAG pipelines (v2.1.0 compatible) and cyclic
multi-agent graphs with bidirectional feedback loops.
"""
from .core import run_pipeline, pipeline_status, watch_events
from .config import OrchestratorConfig, DEFAULT_CONFIG
from .exceptions import (
    PipelineError, PipelineValidationError, AgentExecutionError,
    AgentTimeoutError, TokenBudgetError, CycleDetectedError, RouterError,
    DeadlockError, CycleLimitExceeded, MalformedOutputError,
    PipelineTimeoutError, MissingDependencyOutputError,
    SchemaViolationError, ApprovalTimeoutError, ApprovalRejectedError,
)
from .schema   import (
    validate_output_schema, schema_from_agent_config,
    validate_input_schema, input_schema_from_agent_config,
)
from .secrets  import generate_gitignore, scan_for_secrets, apply_substitutions
from .approval import (
    request_approval, wait_for_approval,
    write_approval, get_approval_status,
)
from .vault    import (
    Vault, AuditCtx, open_vault_for, resolve_vault_path,
)

__version__ = "4.0.0"

__all__ = [
    # Core
    "run_pipeline", "pipeline_status", "watch_events",
    "OrchestratorConfig", "DEFAULT_CONFIG",
    # Exceptions
    "PipelineError", "PipelineValidationError", "AgentExecutionError",
    "AgentTimeoutError", "TokenBudgetError", "CycleDetectedError",
    "RouterError", "DeadlockError", "CycleLimitExceeded",
    "MalformedOutputError", "PipelineTimeoutError",
    "MissingDependencyOutputError",
    "SchemaViolationError", "ApprovalTimeoutError", "ApprovalRejectedError",
    # GAP-1 (schema) + v4 input schema
    "validate_output_schema", "schema_from_agent_config",
    "validate_input_schema", "input_schema_from_agent_config",
    # GAP-2 (secrets substitutions)
    "generate_gitignore", "scan_for_secrets", "apply_substitutions",
    # GAP-3 (approval)
    "request_approval", "wait_for_approval",
    "write_approval", "get_approval_status",
    # v4 vault
    "Vault", "AuditCtx", "open_vault_for", "resolve_vault_path",
]
