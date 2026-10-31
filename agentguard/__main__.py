"""AgentGuard CLI."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from agentguard.agent.runtime import run_agent
from agentguard.config.settings import ConfigurationError, load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the AgentGuard tool-using agent")
    parser.add_argument("task", nargs="*", help="Task to execute (prompted when omitted)")
    parser.add_argument("--demo", action="store_true", help="Use deterministic offline rule-based execution")
    parser.add_argument("--events-json", action="store_true", help="Print generated events as JSON")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    task = " ".join(args.task) or input("Task: ").strip()
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    if not args.demo and not settings.llm_configured:
        print("No LLM provider configured. Use --demo for deterministic offline execution.", file=sys.stderr)
        return 2
    try:
        state, events = run_agent(task, demo=args.demo, settings=settings)
    except Exception as exc:
        print(f"Agent error: {exc}", file=sys.stderr)
        return 1
    duration = (state.end_time - state.start_time).total_seconds() * 1000 if state.end_time else 0
    calls = [call.model_dump() for call in state.tool_calls]
    selected = [call["tool_name"] for call in calls]
    results = [{"tool_name": call["tool_name"], "status": call["status"], "result": call["result"], "error": call["error"]} for call in calls]
    print(f"Execution ID: {state.execution_id}\nTask: {state.user_input}\nTools selected: {json.dumps(selected)}\nTool execution results: {json.dumps(results, default=str)}\nStatus: {state.execution_status}\nFinal response: {state.final_output}\nDuration: {duration:.2f} ms")
    if args.events_json:
        print("Events:")
        print(events.to_json())
    return 0 if state.execution_status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
