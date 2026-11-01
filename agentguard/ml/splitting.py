"""Deterministic group-held-out splitting and serializable split metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass
from numbers import Integral
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from agentguard.ml.config import PreparationConfig
from agentguard.ml.data import (
    AgentTraceDataset,
    GROUP_COLUMNS,
    agenttrace_dataset_frame,
    fingerprint_agenttrace_frame,
)

SplitName = Literal["train", "validation", "test"]
SplitStrategy = Literal["execution_held_out", "task_template_held_out"]
SPLIT_NAMES: tuple[SplitName, ...] = ("train", "validation", "test")


class SplitError(ValueError):
    """Raised when groups cannot form a leakage-safe, class-valid split."""


@dataclass(frozen=True)
class SplitPartition:
    name: SplitName
    row_indices: tuple[int, ...]
    group_ids: tuple[str, ...]
    class_counts: dict[int, int]

    @property
    def row_count(self) -> int:
        return len(self.row_indices)

    @property
    def group_count(self) -> int:
        return len(self.group_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "row_count": self.row_count,
            "group_count": self.group_count,
            "group_ids": list(self.group_ids),
            "class_counts": {str(key): value for key, value in sorted(self.class_counts.items())},
            "row_indices": list(self.row_indices),
        }


@dataclass(frozen=True)
class GroupedSplit:
    strategy: SplitStrategy
    group_column: str
    seed: int
    fractions: dict[str, float]
    require_each_class: bool
    partitions: dict[SplitName, SplitPartition]
    dataset_audit: dict[str, Any]
    dataset_fingerprint: str
    group_composition: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "group_column": self.group_column,
            "seed": self.seed,
            "fractions": dict(self.fractions),
            "require_each_class": self.require_each_class,
            "dataset_audit": self.dataset_audit,
            "dataset_fingerprint": self.dataset_fingerprint,
            "group_composition": list(self.group_composition),
            "partitions": {name: self.partitions[name].to_dict() for name in SPLIT_NAMES},
        }

    def write_json(self, path: str | Path) -> Path:
        """Persist exact group assignments and audit context for reproducibility."""
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return destination


def group_composition(dataset: AgentTraceDataset, group_column: str) -> tuple[dict[str, Any], ...]:
    """Return per-group row and class counts before assigning any partitions."""
    _validate_group_column(group_column)
    groups = dataset.metadata[group_column].astype(str)
    summary: list[dict[str, Any]] = []
    for group_id in sorted(groups.unique()):
        mask = groups.eq(group_id)
        counts = dataset.target.loc[mask].value_counts().sort_index()
        summary.append({
            "group_id": group_id,
            "row_count": int(mask.sum()),
            "class_counts": {str(int(label)): int(count) for label, count in counts.items()},
        })
    return tuple(summary)


def build_grouped_split(
    dataset: AgentTraceDataset,
    *,
    group_column: str,
    seed: int = 2026,
    config: PreparationConfig | None = None,
    require_each_class: bool = True,
) -> GroupedSplit:
    """Split whole groups into deterministic train/validation/test partitions.

    Group allocation is stratified on the group-level binary label. No row-level
    random split is performed, and this function never drops samples.
    """
    _validate_group_column(group_column)
    cfg = config or PreparationConfig(split_seed=seed)
    if config is not None:
        seed = config.split_seed
    fractions = (cfg.train_fraction, cfg.validation_fraction, cfg.test_fraction)
    group_values = dataset.metadata[group_column].astype(str).to_numpy()
    labels = dataset.target.to_numpy(dtype=np.int64)
    group_labels: dict[str, int] = {}
    for group_id in sorted(set(group_values)):
        unique_labels = np.unique(labels[group_values == group_id])
        if len(unique_labels) != 1:
            raise SplitError(f"Group {group_id} in {group_column} has inconsistent class labels")
        group_labels[group_id] = int(unique_labels[0])

    classes = sorted(set(group_labels.values()))
    if classes != [0, 1]:
        raise SplitError(f"Grouped splitting requires both binary classes 0 and 1; found {classes}")

    rng = np.random.default_rng(seed)
    assigned: dict[SplitName, list[str]] = {name: [] for name in SPLIT_NAMES}
    for label in classes:
        class_groups = sorted(group_id for group_id, group_label in group_labels.items() if group_label == label)
        if require_each_class and len(class_groups) < len(SPLIT_NAMES):
            raise SplitError(
                f"Class {label} has only {len(class_groups)} {group_column} groups; "
                "at least three are required to represent the class in every partition"
            )
        allocation = _allocate_groups(len(class_groups), fractions)
        if require_each_class and any(count == 0 for count in allocation):
            raise SplitError(
                f"Class {label} has only {len(class_groups)} {group_column} groups; "
                "cannot represent it in train, validation, and test"
            )
        shuffled = [class_groups[index] for index in rng.permutation(len(class_groups))]
        offset = 0
        for split_name, count in zip(SPLIT_NAMES, allocation):
            assigned[split_name].extend(shuffled[offset:offset + count])
            offset += count

    if any(not assigned[name] for name in SPLIT_NAMES):
        raise SplitError("At least one partition has no groups; increase the dataset or adjust split fractions")

    group_series = pd.Series(group_values)
    partitions: dict[SplitName, SplitPartition] = {}
    all_rows: list[int] = []
    for split_name in SPLIT_NAMES:
        selected_groups = tuple(sorted(assigned[split_name]))
        positions = tuple(int(index) for index in np.flatnonzero(group_series.isin(selected_groups).to_numpy()))
        partition_labels = labels[list(positions)]
        counts = {int(label): int((partition_labels == label).sum()) for label in np.unique(partition_labels)}
        if require_each_class and set(counts) != {0, 1}:
            raise SplitError(f"{split_name} partition lacks one class; found class counts {counts}")
        partitions[split_name] = SplitPartition(
            name=split_name,
            row_indices=positions,
            group_ids=selected_groups,
            class_counts=counts,
        )
        all_rows.extend(positions)

    if sorted(all_rows) != list(range(len(dataset))):
        raise SplitError("Split assignments do not cover every row exactly once")
    _assert_no_group_overlap(partitions)

    strategy: SplitStrategy = (
        "execution_held_out" if group_column == "execution_id" else "task_template_held_out"
    )
    return GroupedSplit(
        strategy=strategy,
        group_column=group_column,
        seed=seed,
        fractions={name: fraction for name, fraction in zip(SPLIT_NAMES, fractions)},
        require_each_class=require_each_class,
        partitions=partitions,
        dataset_audit=dataset.audit.to_dict(),
        dataset_fingerprint=dataset.audit.dataset_fingerprint,
        group_composition=group_composition(dataset, group_column),
    )


def validate_split_binding(dataset: AgentTraceDataset, split: GroupedSplit) -> None:
    """Reject stale, corrupted, or misapplied split assignments before preprocessing."""
    try:
        current_fingerprint = fingerprint_agenttrace_frame(agenttrace_dataset_frame(dataset))
    except Exception as exc:
        raise SplitError("Could not verify the current dataset against the split fingerprint") from exc
    if not dataset.audit.dataset_fingerprint or current_fingerprint != dataset.audit.dataset_fingerprint:
        raise SplitError("Dataset contents or schema changed after validation; reload and resplit")
    if split.dataset_fingerprint != dataset.audit.dataset_fingerprint:
        raise SplitError("Split fingerprint does not match the supplied dataset")
    split_audit = split.dataset_audit
    current_audit = dataset.audit.to_dict()
    provenance_fields = {"source_path", "source_sha256"}
    if any(
        split_audit.get(key) != value
        for key, value in current_audit.items()
        if key not in provenance_fields
    ):
        raise SplitError("Split audit metadata does not match the supplied dataset schema or values")
    expected_strategy = {
        "execution_id": "execution_held_out",
        "task_template_id": "task_template_held_out",
    }
    if split.group_column not in expected_strategy or split.strategy != expected_strategy[split.group_column]:
        raise SplitError("Split strategy and grouping column do not match")
    if set(split.partitions) != set(SPLIT_NAMES):
        raise SplitError(f"Split must contain exactly these partitions: {SPLIT_NAMES}")
    if split.group_composition != group_composition(dataset, split.group_column):
        raise SplitError("Recorded group composition does not match the supplied dataset")

    group_series = dataset.metadata[split.group_column].astype(str).reset_index(drop=True)
    labels = dataset.target.reset_index(drop=True)
    all_indices: list[int] = []
    partition_groups: list[set[str]] = []
    observed_class_counts: list[tuple[str, dict[int, int], dict[int, int]]] = []
    for name in SPLIT_NAMES:
        part = split.partitions[name]
        if part.name != name:
            raise SplitError(f"Partition key {name!r} does not match its recorded name {part.name!r}")
        indices = list(part.row_indices)
        if any(not isinstance(index, Integral) or isinstance(index, bool) for index in indices):
            raise SplitError(f"{name} partition contains a non-integer row index")
        if len(indices) != len(set(indices)):
            raise SplitError(f"{name} partition contains duplicate row indices")
        if any(index < 0 or index >= len(dataset) for index in indices):
            raise SplitError(f"{name} partition contains an out-of-range row index")
        actual_groups = set(group_series.iloc[indices])
        recorded_groups = list(part.group_ids)
        if any(not isinstance(group_id, str) for group_id in recorded_groups):
            raise SplitError(f"{name} partition group IDs must be strings")
        if len(recorded_groups) != len(set(recorded_groups)):
            raise SplitError(f"{name} partition contains duplicate recorded group IDs")
        if actual_groups != set(recorded_groups):
            raise SplitError(
                f"{name} partition recorded {split.group_column} groups do not match groups at row_indices"
            )
        actual_counts = labels.iloc[indices].value_counts().sort_index().to_dict()
        normalized_counts = {int(key): int(value) for key, value in actual_counts.items()}
        observed_class_counts.append((name, normalized_counts, part.class_counts))
        partition_groups.append(actual_groups)
        all_indices.extend(int(index) for index in indices)

    if len(all_indices) != len(dataset) or sorted(all_indices) != list(range(len(dataset))):
        raise SplitError("Partition row indices must cover every dataset row exactly once")
    for name, actual_counts, recorded_counts in observed_class_counts:
        if actual_counts != recorded_counts:
            raise SplitError(f"{name} partition class counts do not match the assigned rows")
    for left_index, left_groups in enumerate(partition_groups):
        for right_index in range(left_index + 1, len(partition_groups)):
            overlap = left_groups & partition_groups[right_index]
            if overlap:
                raise SplitError(f"{split.group_column} groups cross partitions: {sorted(overlap)}")

    if not isinstance(split.seed, Integral) or isinstance(split.seed, bool):
        raise SplitError("Recorded split seed must be an integer")
    if not isinstance(split.require_each_class, bool):
        raise SplitError("Recorded require_each_class setting must be a boolean")
    if set(split.fractions) != set(SPLIT_NAMES):
        raise SplitError(f"Split fractions must contain exactly these names: {SPLIT_NAMES}")
    try:
        expected_config = PreparationConfig(
            split_seed=int(split.seed),
            train_fraction=split.fractions["train"],
            validation_fraction=split.fractions["validation"],
            test_fraction=split.fractions["test"],
        )
        expected_split = build_grouped_split(
            dataset,
            group_column=split.group_column,
            config=expected_config,
            require_each_class=split.require_each_class,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise SplitError("Recorded split seed or fractions cannot reproduce a valid split") from exc
    if any(
        split.partitions[name].row_indices != expected_split.partitions[name].row_indices
        or split.partitions[name].group_ids != expected_split.partitions[name].group_ids
        for name in SPLIT_NAMES
    ):
        raise SplitError("Recorded group assignments do not match the deterministic split for its seed and fractions")


def _allocate_groups(number: int, fractions: tuple[float, float, float]) -> tuple[int, int, int]:
    if number < 1:
        raise SplitError("Cannot allocate zero groups")
    ideal = np.asarray(fractions, dtype=float) * number
    allocation = np.floor(ideal).astype(int)
    remaining = number - int(allocation.sum())
    order = sorted(range(len(SPLIT_NAMES)), key=lambda index: (-(ideal[index] - allocation[index]), index))
    for index in order[:remaining]:
        allocation[index] += 1
    return tuple(int(value) for value in allocation)


def _assert_no_group_overlap(partitions: dict[SplitName, SplitPartition]) -> None:
    for left_index, left_name in enumerate(SPLIT_NAMES):
        left = set(partitions[left_name].group_ids)
        for right_name in SPLIT_NAMES[left_index + 1:]:
            overlap = left & set(partitions[right_name].group_ids)
            if overlap:
                raise SplitError(f"Groups cross {left_name}/{right_name} partitions: {sorted(overlap)}")


def _validate_group_column(group_column: str) -> None:
    if group_column not in GROUP_COLUMNS:
        raise SplitError(f"group_column must be one of {GROUP_COLUMNS}; got {group_column!r}")
