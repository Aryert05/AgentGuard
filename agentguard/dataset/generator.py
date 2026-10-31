"""Seeded synthetic AgentTrace generation with prefix-only feature construction."""
from __future__ import annotations

import argparse
import hashlib
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid5

import pandas as pd

from agentguard.dataset.labels import execution_labels
from agentguard.dataset.manifest import DatasetManifest, build_manifest
from agentguard.execution.runner import DeterministicClock, run_plan
from agentguard.processing.features import FEATURE_COLUMNS, extract_prefix_features
from agentguard.processing.trace_processor import normalize_trace
from agentguard.schemas.events import EventType


DEFAULT_DISTRIBUTION = {
    "success": 0.40, "invalid_arguments": 0.10, "timeout": 0.10,
    "unavailable": 0.10, "exception": 0.10, "repeated_failure": 0.10, "partial": 0.10,
}
SCENARIOS = set(DEFAULT_DISTRIBUTION)
TASKS = [
    "Calculate the total cost of 15 products priced at 250 each",
    "Search for tool calling",
    "Find information about Tata Motors and summarize the available company data",
    "Calculate 12 plus 30",
]
OUTCOME_EVENTS = {EventType.EXECUTION_COMPLETED, EventType.EXECUTION_FAILED}
DATASET_COLUMNS = ["execution_id", "task_id", "task_template_id", "prefix_id", "prefix_index",
                   "synthetic_data", *FEATURE_COLUMNS, "eventual_failure", "outcome_label", "failure_category"]


@dataclass(frozen=True)
class GeneratedDataset:
    samples: pd.DataFrame
    traces: list[dict[str, Any]]
    executions: list[dict[str, Any]]
    manifest: DatasetManifest


def generate_agenttrace(number_of_executions: int = 100, *, seed: int = 2026,
                        distribution: Mapping[str, float] | None = None) -> GeneratedDataset:
    if number_of_executions < 1:
        raise ValueError("number_of_executions must be at least 1")
    weights = dict(distribution or DEFAULT_DISTRIBUTION)
    if not weights or set(weights) - SCENARIOS or any(value < 0 for value in weights.values()) or sum(weights.values()) <= 0:
        raise ValueError(f"distribution must contain supported non-negative scenario weights: {sorted(SCENARIOS)}")
    scenario_names = list(sorted(weights))
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    event_total = 0
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seed % 86400)

    for index in range(number_of_executions):
        scenario = rng.choices(scenario_names, weights=[weights[name] for name in scenario_names], k=1)[0]
        task = rng.choice(TASKS)
        plan, failure_mode, task = _scenario_plan(scenario, task)
        execution_id = str(uuid5(NAMESPACE_URL, f"agenttrace-v1:{seed}:{index}:execution"))
        task_id = str(uuid5(NAMESPACE_URL, f"agenttrace-v1:{seed}:{index}:task"))
        clock = DeterministicClock(base_time + timedelta(seconds=index * 60))
        state, collector = run_plan(task, plan=plan, failure_mode=failure_mode,
            execution_id=execution_id, task_id=task_id, clock=clock,
            deterministic_event_ids=True, log_failures=False)
        execution_row = {"execution_id": state.execution_id, "task_id": state.task_id,
            "execution_status": state.execution_status, "execution_mode": "synthetic_deterministic",
            "started_at": state.start_time.isoformat(), "completed_at": state.end_time.isoformat() if state.end_time else None,
            "total_duration_ms": (state.end_time - state.start_time).total_seconds() * 1000 if state.end_time else None}
        normalized = normalize_trace(execution_row, collector.events)
        if not normalized.complete:
            raise RuntimeError(f"Generated execution {execution_id} is incomplete; refusing to label it")
        labels = execution_labels(state.execution_status, normalized.events)
        template_text = re.sub(r"\d+(?:\.\d+)?", "<n>", task.lower())
        template_id = hashlib.sha256(template_text.encode("utf-8")).hexdigest()[:16]
        execution_row.update({"task_template_id": template_id, **labels, "scenario": scenario})
        executions.append(execution_row)
        event_total += len(normalized.events)
        event_rows = [event.model_dump(mode="json") for event in normalized.events]
        traces.append({"execution_id": state.execution_id, "task_id": state.task_id,
            "task_template_id": template_id, "execution_status": state.execution_status,
            "total_duration_ms": execution_row["total_duration_ms"],
            "failure_category": labels["failure_category"], "synthetic_data": True, "events": event_rows})

        observed = [event for event in normalized.events if event.event_type not in OUTCOME_EVENTS]
        for prefix_index in range(1, len(observed) + 1):
            prefix = observed[:prefix_index]
            features = extract_prefix_features(prefix)
            rows.append({"execution_id": state.execution_id, "task_id": state.task_id,
                "task_template_id": template_id, "prefix_id": f"{state.execution_id}:{prefix_index:04d}",
                "prefix_index": prefix_index, "synthetic_data": True, **features, **labels})

    samples = pd.DataFrame(rows, columns=DATASET_COLUMNS)
    config = {"number_of_executions": number_of_executions, "scenario_distribution": weights,
              "feature_schema_version": "1.0.0", "prefixes_exclude_terminal_events": True}
    manifest = build_manifest(executions, event_total, len(samples), config, seed)
    return GeneratedDataset(samples=samples, traces=traces, executions=executions, manifest=manifest)


def _scenario_plan(scenario: str, fallback_task: str) -> tuple[list[tuple[str, dict[str, Any]]] | None, str | None, str]:
    calculator_plan = [("calculator", {"operation": "add", "a": 12, "b": 30})]
    if scenario == "success":
        return None, None, fallback_task
    if scenario == "partial":
        return [("calculator", {"operation": "add", "a": 12, "b": 30}),
                ("company_data", {"name": "Unknown Sample Company"})], None, "Compute a value and retrieve an unknown sample company"
    if scenario == "repeated_failure":
        return calculator_plan * 3, "exception", "Run repeated calculations under controlled failure injection"
    return calculator_plan, scenario, f"Run a calculator task with controlled {scenario} injection"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the synthetic AgentTrace prefix dataset")
    parser.add_argument("--executions", type=int, default=100)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--output", type=Path, default=Path("data/generated/agenttrace-v1"))
    args = parser.parse_args()
    from agentguard.dataset.exporter import export_agenttrace
    generated = generate_agenttrace(args.executions, seed=args.seed)
    paths = export_agenttrace(generated, args.output)
    print(f"Generated {generated.manifest.number_of_executions} synthetic executions, "
          f"{generated.manifest.number_of_events} events, {generated.manifest.number_of_samples} prefixes")
    print(f"Outcome distribution: {generated.manifest.label_distribution}")
    for name, path in paths.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
