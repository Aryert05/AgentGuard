"""Training-fitted, sklearn-compatible preprocessing for numeric AgentTrace features."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
try:
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import MissingIndicator, SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
except ModuleNotFoundError as exc:
    if exc.name == "sklearn":
        raise ImportError("AgentGuard ML preprocessing requires scikit-learn; install with `pip install -e '.[dev,ml]'`") from exc
    raise

from agentguard.ml.data import AgentTraceDataset, STRUCTURALLY_NULLABLE_FEATURES
from agentguard.ml.splitting import GroupedSplit, SPLIT_NAMES, SplitName, validate_split_binding
from agentguard.processing.features import MODEL_FEATURE_COLUMNS


@dataclass(frozen=True)
class PreparedFeaturePartitions:
    """Transformed partitions and the transformer fitted only on the train rows."""

    transformer: Pipeline
    features: dict[SplitName, np.ndarray]
    row_indices: dict[SplitName, tuple[int, ...]]


def build_preprocessing_pipeline(*, scale: bool = True) -> Pipeline:
    """Build (but do not fit) a sklearn pipeline for the explicit feature allow-list.

    Median values are learned only when callers fit on training rows. Missingness
    indicators are included for fields whose nulls mean that an event/outcome has
    not yet been observed; the indicator schema is fixed independently of splits.
    This raw sklearn pipeline does not validate dataset/split binding. Use
    ``fit_transform_partitions`` when preparing AgentTrace train/validation/test data.
    """
    feature_transform = ColumnTransformer(
        transformers=[
            (
                "numeric",
                SimpleImputer(strategy="median", keep_empty_features=True),
                list(MODEL_FEATURE_COLUMNS),
            ),
            (
                "structural_missingness",
                MissingIndicator(features="all"),
                list(STRUCTURALLY_NULLABLE_FEATURES),
            ),
        ],
        remainder="drop",
    )
    steps: list[tuple[str, Any]] = [("features", feature_transform)]
    if scale:
        steps.append(("scaler", StandardScaler()))
    return Pipeline(steps)


def fit_transform_partitions(
    dataset: AgentTraceDataset,
    split: GroupedSplit,
    *,
    scale: bool = True,
) -> PreparedFeaturePartitions:
    """Fit preprocessing on train only, then transform train/validation/test."""
    validate_split_binding(dataset, split)

    frames = {
        name: dataset.features.iloc[list(split.partitions[name].row_indices)].copy()
        for name in SPLIT_NAMES
    }
    if list(frames["train"].columns) != list(MODEL_FEATURE_COLUMNS):
        raise ValueError("Training features do not match the explicit model feature allow-list")

    transformer = build_preprocessing_pipeline(scale=scale)
    transformed = {"train": transformer.fit_transform(frames["train"])}
    for name in ("validation", "test"):
        transformed[name] = transformer.transform(frames[name])
    return PreparedFeaturePartitions(
        transformer=transformer,
        features=transformed,
        row_indices={name: split.partitions[name].row_indices for name in SPLIT_NAMES},
    )
