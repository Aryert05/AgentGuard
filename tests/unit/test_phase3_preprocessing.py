from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentguard.ml.data import load_agenttrace_csv, validate_agenttrace_frame
from agentguard.ml.preprocessing import (
    STRUCTURALLY_NULLABLE_FEATURES,
    build_preprocessing_pipeline,
    fit_transform_partitions,
)
from agentguard.ml.splitting import SplitError, build_grouped_split
from agentguard.processing.features import MODEL_FEATURE_COLUMNS


DATASET = Path("data/generated/agenttrace-v1/agenttrace_prefixes.csv")


@pytest.fixture(scope="module")
def dataset():
    return load_agenttrace_csv(DATASET)


def test_preprocessing_pipeline_has_fixed_structural_missing_indicators(dataset):
    split = build_grouped_split(dataset, group_column="execution_id", seed=33)
    prepared = fit_transform_partitions(dataset, split, scale=False)
    expected_columns = len(MODEL_FEATURE_COLUMNS) + len(STRUCTURALLY_NULLABLE_FEATURES)
    for name in ("train", "validation", "test"):
        values = prepared.features[name]
        assert values.shape == (split.partitions[name].row_count, expected_columns)
        assert np.isfinite(values).all()
    assert list(prepared.transformer.named_steps) == ["features"]


def test_scaler_mean_and_scale_are_exactly_training_only(dataset):
    frame = pd.read_csv(DATASET)
    original = validate_agenttrace_frame(frame)
    original_split = build_grouped_split(original, group_column="execution_id", seed=71)
    validation_indices = list(original_split.partitions["validation"].row_indices)
    # Exaggerated but valid, validation-only durations must not affect fitted statistics.
    frame.loc[validation_indices[0], "elapsed_time_ms"] = 1_000_000_000_000.0
    dataset = validate_agenttrace_frame(frame)
    split = build_grouped_split(dataset, group_column="execution_id", seed=71)

    prepared = fit_transform_partitions(dataset, split, scale=True)
    train_features = dataset.features.iloc[list(split.partitions["train"].row_indices)]
    train_only_transform = build_preprocessing_pipeline(scale=False)
    train_matrix = train_only_transform.fit_transform(train_features)
    expected_mean = train_matrix.mean(axis=0)
    expected_scale = train_matrix.std(axis=0)
    expected_scale[expected_scale == 0.0] = 1.0
    scaler = prepared.transformer.named_steps["scaler"]
    np.testing.assert_allclose(scaler.mean_, expected_mean, rtol=0, atol=1e-12)
    np.testing.assert_allclose(scaler.scale_, expected_scale, rtol=0, atol=1e-12)
    assert prepared.features["validation"].shape[0] == split.partitions["validation"].row_count


def test_missingness_indicators_have_exact_values_and_stable_order():
    values = pd.DataFrame(0.0, index=range(6), columns=MODEL_FEATURE_COLUMNS)
    for row_index, column in enumerate(STRUCTURALLY_NULLABLE_FEATURES, start=1):
        values.loc[row_index, column] = np.nan
    pipeline = build_preprocessing_pipeline(scale=False)
    transformed = pipeline.fit_transform(values)
    indicator_values = transformed[:, -len(STRUCTURALLY_NULLABLE_FEATURES):]
    expected = np.vstack([np.zeros(5), np.eye(5)])
    np.testing.assert_array_equal(indicator_values, expected)

    all_missing = values.iloc[[0]].copy()
    all_missing.loc[:, list(STRUCTURALLY_NULLABLE_FEATURES)] = np.nan
    result = pipeline.transform(all_missing)
    np.testing.assert_array_equal(result[:, -5:], np.ones((1, 5)))


def test_train_only_preprocessing_rejects_split_for_another_dataset(dataset, tmp_path):
    frame = pd.read_csv(DATASET)
    first = validate_agenttrace_frame(frame)
    split = build_grouped_split(first, group_column="execution_id", seed=5)
    changed_frame = frame.copy()
    changed_frame.loc[0, "task_id"] = "different-same-sized-frame"
    second = validate_agenttrace_frame(changed_frame)
    assert len(first) == len(second)
    assert first.audit.source_sha256 is second.audit.source_sha256 is None
    assert first.audit.dataset_fingerprint != second.audit.dataset_fingerprint
    with pytest.raises(SplitError, match="fingerprint"):
        fit_transform_partitions(second, split)


def test_integral_float_target_uses_canonical_fingerprint(dataset):
    frame = pd.read_csv(DATASET)
    frame["eventual_failure"] = frame["eventual_failure"].astype(float)
    validated = validate_agenttrace_frame(frame)
    split = build_grouped_split(validated, group_column="execution_id", seed=19)
    prepared = fit_transform_partitions(validated, split, scale=False)
    assert prepared.features["train"].shape[0] == split.partitions["train"].row_count


def test_reordered_or_mutated_group_metadata_is_rejected_before_preprocessing(dataset):
    split = build_grouped_split(dataset, group_column="execution_id", seed=11)
    frame = pd.read_csv(DATASET).iloc[::-1].reset_index(drop=True)
    reordered = validate_agenttrace_frame(frame)
    with pytest.raises(SplitError, match="fingerprint"):
        fit_transform_partitions(reordered, split)

    modified_metadata = dataset.metadata.copy()
    modified_metadata.loc[0, "execution_id"] = "modified-execution-id"
    modified = replace(dataset, metadata=modified_metadata)
    with pytest.raises(SplitError, match="changed after validation"):
        fit_transform_partitions(modified, split)


@pytest.mark.parametrize("corruption, message", [
    ("missing", "cover every dataset row exactly once"),
    ("duplicate", "duplicate row indices"),
    ("out_of_range", "out-of-range row index"),
    ("wrong_groups", "do not match groups at row_indices"),
])
def test_split_integrity_is_checked_before_preprocessing(dataset, corruption, message):
    from dataclasses import replace as dc_replace

    split = build_grouped_split(dataset, group_column="execution_id", seed=23)
    partitions = dict(split.partitions)
    test_part = partitions["test"]
    indices = list(test_part.row_indices)
    group_ids = test_part.group_ids
    if corruption == "missing":
        indices.pop()
    elif corruption == "duplicate":
        indices.append(indices[0])
    elif corruption == "out_of_range":
        indices[-1] = len(dataset)
    else:
        group_ids = tuple(list(group_ids[1:]) + ["not-a-real-group"])
    partitions["test"] = dc_replace(test_part, row_indices=tuple(indices), group_ids=group_ids)
    corrupted_split = dc_replace(split, partitions=partitions)
    with pytest.raises(SplitError, match=message):
        fit_transform_partitions(dataset, corrupted_split)


def test_pipeline_is_sklearn_compatible_and_unfitted_until_requested(dataset):
    from sklearn.pipeline import Pipeline

    pipeline = build_preprocessing_pipeline()
    assert isinstance(pipeline, Pipeline)
    assert not hasattr(pipeline.named_steps["features"], "transformers_")
