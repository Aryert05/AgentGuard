"""Execution orchestration over the existing agent runtime."""
from __future__ import annotations

from typing import TYPE_CHECKING

from agentguard.agent.runtime import run_agent
from agentguard.application.errors import (
    ExecutionUnavailableError,
    LLMNotConfiguredError,
    PersistenceUnavailableError,
)
from agentguard.application.protocols import ExecutionMode, ExecutionRunner
from agentguard.config.settings import ConfigurationError, Settings
from agentguard.database.repositories import TelemetryPersistenceError
from agentguard.schemas.events import EventCollector
from agentguard.schemas.state import ExecutionState
from agentguard.telemetry.privacy import redact_text

if TYPE_CHECKING:
    from agentguard.api.schemas.execution import ExecutionRequest, ExecutionResponse


class AgentExecutionRunner:
    """Adapter over the existing run_agent entry point."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(
        self,
        task: str,
        *,
        mode: ExecutionMode,
        max_tool_calls: int,
    ) -> tuple[ExecutionState, EventCollector]:
        return run_agent(
            task,
            demo=mode == "deterministic",
            settings=self.settings,
            max_tool_calls=max_tool_calls,
        )


class ExecutionService:
    """Run executions and map runtime state to the public response contract."""

    def __init__(
        self,
        runner: ExecutionRunner,
        *,
        persistence_enabled: bool,
        llm_configured: bool,
    ) -> None:
        self.runner = runner
        self.persistence_enabled = persistence_enabled
        self.llm_configured = llm_configured

    def run(self, request: ExecutionRequest) -> ExecutionResponse:
        if request.mode == "llm" and not self.llm_configured:
            raise LLMNotConfiguredError()
        try:
            state, _events = self.runner.run(
                request.task,
                mode=request.mode,
                max_tool_calls=request.max_tool_calls,
            )
        except TelemetryPersistenceError:
            raise PersistenceUnavailableError() from None
        except (ConfigurationError, ImportError):
            raise ExecutionUnavailableError() from None
        except RuntimeError as exc:
            if isinstance(exc.__cause__, ImportError):
                raise ExecutionUnavailableError() from None
            raise
        return _execution_response(state, persisted=self.persistence_enabled)


def _execution_response(state: ExecutionState, *, persisted: bool) -> ExecutionResponse:
    from agentguard.api.schemas.execution import ExecutionResponse, ToolCallSummary

    duration_ms = None
    if state.end_time is not None:
        duration_ms = max(
            0.0, (state.end_time - state.start_time).total_seconds() * 1000
        )
    return ExecutionResponse(
        execution_id=state.execution_id,
        task_id=state.task_id,
        status=state.execution_status,
        mode=state.execution_mode,
        started_at=state.start_time,
        completed_at=state.end_time,
        duration_ms=duration_ms,
        successful_tool_call_count=sum(
            call.status == "completed" for call in state.tool_calls
        ),
        failed_tool_call_count=sum(
            call.status == "failed" for call in state.tool_calls
        ),
        tool_calls=[
            ToolCallSummary(
                tool_name=call.tool_name,
                status=call.status,
                duration_ms=None,
            )
            for call in state.tool_calls
        ],
        final_output=redact_text(state.final_output),
        persisted=persisted,
    )
