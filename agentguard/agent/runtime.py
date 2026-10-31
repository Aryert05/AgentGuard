"""Public agent runtime."""
from __future__ import annotations

from agentguard.agent.graph import MAX_TOOL_CALLS, run_llm
from agentguard.config.settings import Settings, load_settings, validate_failure_mode
from agentguard.execution.runner import run_plan
from agentguard.schemas.events import EventCollector
from agentguard.schemas.state import ExecutionState


def run_agent(task: str, *, demo: bool = False, settings: Settings | None = None,
              failure_mode: str | None = None, max_tool_calls: int = MAX_TOOL_CALLS) -> tuple[ExecutionState, EventCollector]:
    if not task.strip():
        raise ValueError("Task must not be empty")
    config = settings or load_settings()
    effective_failure_mode = validate_failure_mode(failure_mode or config.failure_mode)
    if demo:
        return run_plan(task, failure_mode=effective_failure_mode)
    if not config.llm_configured:
        raise RuntimeError("No LLM provider is configured. Set AGENTGUARD_LLM_PROVIDER and AGENTGUARD_LLM_MODEL (plus provider credentials), or run `python -m agentguard --demo` for deterministic rule-based execution.")
    return run_llm(task, config.provider or "", config.model or "", failure_mode=effective_failure_mode,
                   max_tool_calls=max_tool_calls)
