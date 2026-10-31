# AgentGuard

Phase 1 establishes an iterative tool-using agent, deterministic offline execution, and structured execution events. In provider mode, LangGraph routes model messages through a bounded `ToolNode` loop and sends tool results back to the model. The offline mode is deterministic and rule-based; it does not claim to use LLM reasoning. See [the Phase 1 guide](docs/phase-01-agent-foundation.md) and [the implementation report](docs/reports/phase-01-implementation-report.md).

```bash
python -m pip install -e '.[dev]'
python -m agentguard --demo "Calculate the total cost of 15 products priced at 250 each"
python -m agentguard --demo "Find information about Tata Motors and summarize the available company data"
python -m pytest
```

For provider-backed tool selection, configure `AGENTGUARD_LLM_PROVIDER`, `AGENTGUARD_LLM_MODEL`, and the provider's credential environment variable. The default provider integration dependency is `langchain-openai`; install the relevant LangChain integration if choosing another provider. The app never claims local mock search results are live internet results.

Set `AGENTGUARD_FAILURE_MODE` to `invalid_arguments`, `timeout`, `unavailable`, or `exception` to inject a controlled tool failure. It is disabled when unset. An invalid value is a configuration error. Status is `completed` when all calls succeed, `partially_completed` when at least one succeeds and another call or execution step fails, and `failed` when no tool call succeeds or provider execution fails before producing a usable result.
