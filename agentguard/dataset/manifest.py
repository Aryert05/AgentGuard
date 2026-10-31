"""Versioned AgentTrace generation metadata."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class DatasetManifest(BaseModel):
    dataset_name: str = "AgentTrace"
    dataset_version: str = "1.0.0"
    generation_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    number_of_executions: int
    number_of_events: int
    number_of_samples: int
    label_distribution: dict[str, int]
    failure_categories: dict[str, int]
    generation_configuration: dict[str, Any]
    provenance: str = "synthetic_controlled_agentguard_runs"
    random_seed: int


def build_manifest(executions: list[dict[str, Any]], event_count: int,
                   sample_count: int, config: dict[str, Any], seed: int) -> DatasetManifest:
    labels = Counter(str(item["execution_status"]) for item in executions)
    categories = Counter(str(item.get("failure_category") or "unknown") for item in executions)
    return DatasetManifest(number_of_executions=len(executions), number_of_events=event_count,
        number_of_samples=sample_count, label_distribution=dict(sorted(labels.items())),
        failure_categories=dict(sorted(categories.items())), generation_configuration=config,
        random_seed=seed)
