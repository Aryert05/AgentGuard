from pathlib import Path
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from agentguard.ml.data import (
    EXCLUDED_MODEL_COLUMNS,
    DatasetValidationError,
    load_agenttrace_csv,
    validate_agenttrace_frame,
)
from agentguard.processing.features import MODEL_FEATURE_COLUMNS


DATASET = Path("data/generated/agenttrace-v1/agenttrace_prefixes.csv")


@pytest.fixture(scope="module")
def frame():
    return pd.read_csv(DATASET)


def test_actual_dataset_validates_without_dropping_rows():
    dataset = load_agenttrace_csv(DATASET)
    assert len(dataset) == 484
    assert dataset.audit.execution_count == 100
    assert dataset.audit.task_template_count == 10
    assert dataset.audit.target_counts == {0: 176, 1: 308}
    assert dataset.audit.duplicate_prefix_ids == 0
    assert dataset.audit.duplicate_rows == 0
    assert dataset.audit.missing_by_column["elapsed_time_ms"] == 100
    assert dataset.audit.missing_by_column["time_since_last_failure_ms"] == 362
    assert len(dataset.features) == len(dataset.target) == len(dataset.metadata) == 484


def test_feature_selection_is_exact_allow_list_and_excludes_metadata_targets(frame):
    dataset = validate_agenttrace_frame(frame)
    assert list(dataset.features.columns) == list(MODEL_FEATURE_COLUMNS)
    assert not set(dataset.features.columns) & set(EXCLUDED_MODEL_COLUMNS)
    assert len(dataset.features.columns) == 18


def test_in_memory_fingerprints_are_present_stable_and_sensitive_to_schema_and_rows(frame):
    first = validate_agenttrace_frame(frame.copy(deep=True))
    equivalent = validate_agenttrace_frame(frame.copy(deep=True))
    assert first.audit.source_sha256 is None
    assert first.audit.dataset_fingerprint
    assert first.audit.dataset_fingerprint == equivalent.audit.dataset_fingerprint

    changed_value = frame.copy(deep=True)
    changed_value.loc[0, "task_id"] = "a-different-task-id"
    changed = validate_agenttrace_frame(changed_value)
    assert changed.audit.dataset_fingerprint != first.audit.dataset_fingerprint

    reordered = validate_agenttrace_frame(frame.iloc[::-1].reset_index(drop=True))
    assert reordered.audit.dataset_fingerprint != first.audit.dataset_fingerprint

    reordered_columns = validate_agenttrace_frame(frame.loc[:, list(reversed(frame.columns))])
    assert reordered_columns.audit.dataset_fingerprint != first.audit.dataset_fingerprint

    changed_missingness = frame.copy(deep=True)
    row = changed_missingness["elapsed_time_ms"].first_valid_index()
    changed_missingness.loc[row, "elapsed_time_ms"] = None
    missing_changed = validate_agenttrace_frame(changed_missingness)
    assert missing_changed.audit.dataset_fingerprint != first.audit.dataset_fingerprint

    changed_dtype = frame.copy(deep=True)
    changed_dtype["prefix_length"] = changed_dtype["prefix_length"].astype(float)
    dtype_changed = validate_agenttrace_frame(changed_dtype)
    assert dtype_changed.audit.dataset_fingerprint != first.audit.dataset_fingerprint


def test_base_ml_data_import_does_not_import_optional_sklearn():
    script = """
import builtins
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == 'sklearn' or name.startswith('sklearn.'):
        raise AssertionError('scikit-learn must stay optional for data imports')
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
from agentguard.ml.data import load_agenttrace_csv
assert callable(load_agenttrace_csv)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path.cwd(),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_missing_required_feature_is_rejected(frame):
    with pytest.raises(DatasetValidationError, match="missing required columns"):
        validate_agenttrace_frame(frame.drop(columns=[MODEL_FEATURE_COLUMNS[0]]))


def test_unrecognized_columns_are_rejected_instead_of_lost_from_fingerprint(frame):
    invalid = frame.assign(untracked_metadata="not-in-the-validated-schema")
    with pytest.raises(DatasetValidationError, match="unexpected columns"):
        validate_agenttrace_frame(invalid)


def test_duplicate_prefix_id_is_rejected(frame):
    invalid = frame.copy()
    invalid.loc[1, "prefix_id"] = invalid.loc[0, "prefix_id"]
    with pytest.raises(DatasetValidationError, match="prefix_id"):
        validate_agenttrace_frame(invalid)


@pytest.mark.parametrize("bad_value", [2, -1, 0.5])
def test_non_binary_target_is_rejected(frame, bad_value):
    invalid = frame.copy()
    invalid["eventual_failure"] = invalid["eventual_failure"].astype(float)
    invalid.loc[0, "eventual_failure"] = bad_value
    with pytest.raises(DatasetValidationError, match="eventual_failure"):
        validate_agenttrace_frame(invalid)


def test_target_semantics_mismatch_is_rejected(frame):
    invalid = frame.copy()
    invalid.loc[0, "eventual_failure"] = 1 - invalid.loc[0, "eventual_failure"]
    with pytest.raises(DatasetValidationError, match="does not match outcome_label"):
        validate_agenttrace_frame(invalid)


def test_missing_target_and_unknown_status_are_rejected(frame):
    missing = frame.copy()
    missing.loc[0, "eventual_failure"] = None
    with pytest.raises(DatasetValidationError, match="target eventual_failure contains missing"):
        validate_agenttrace_frame(missing)
    unknown = frame.copy()
    unknown.loc[0, "outcome_label"] = "unknown"
    with pytest.raises(DatasetValidationError, match="unsupported or unknown statuses"):
        validate_agenttrace_frame(unknown)


def test_non_numeric_or_infinite_feature_is_rejected(frame):
    non_numeric = frame.copy()
    non_numeric[MODEL_FEATURE_COLUMNS[0]] = non_numeric[MODEL_FEATURE_COLUMNS[0]].astype(str)
    with pytest.raises(DatasetValidationError, match="numeric, non-boolean dtype"):
        validate_agenttrace_frame(non_numeric)
    infinite = frame.copy()
    infinite[MODEL_FEATURE_COLUMNS[0]] = infinite[MODEL_FEATURE_COLUMNS[0]].astype(float)
    infinite.loc[0, MODEL_FEATURE_COLUMNS[0]] = float("inf")
    with pytest.raises(DatasetValidationError, match="infinite values"):
        validate_agenttrace_frame(infinite)


def test_complex_features_and_targets_are_rejected_before_lossy_conversion(frame):
    complex_feature = frame.copy()
    complex_feature["prefix_length"] = complex_feature["prefix_length"].astype(complex)
    complex_feature.loc[0, "prefix_length"] = 1 + 9j
    with pytest.raises(DatasetValidationError, match="prefix_length must be real-valued"):
        validate_agenttrace_frame(complex_feature)

    complex_target = frame.copy()
    complex_target["eventual_failure"] = complex_target["eventual_failure"].astype(complex)
    with pytest.raises(DatasetValidationError, match="eventual_failure must be real-valued"):
        validate_agenttrace_frame(complex_target)


def test_high_precision_fractional_count_is_not_rounded_during_validation(frame):
    invalid = frame.copy()
    invalid["prefix_length"] = invalid["prefix_length"].astype("longdouble")
    one = invalid["prefix_length"].dtype.type(1)
    fractional = np.nextafter(one, invalid["prefix_length"].dtype.type(2))
    if float(fractional) != 1.0:
        # On platforms where longdouble aliases double, exercise a representable
        # fraction instead of asserting a precision distinction the platform lacks.
        fractional = invalid["prefix_length"].dtype.type("1.5")
    assert fractional != 1
    invalid.loc[0, "prefix_length"] = fractional
    with pytest.raises(DatasetValidationError, match="integer-valued"):
        validate_agenttrace_frame(invalid)


def test_fractional_counts_negative_counts_invalid_rates_and_negative_durations_are_rejected(frame):
    fractional = frame.copy()
    fractional["prefix_length"] = fractional["prefix_length"].astype(float)
    fractional.loc[0, "prefix_length"] = 1.5
    with pytest.raises(DatasetValidationError, match="integer-valued"):
        validate_agenttrace_frame(fractional)

    negative_count = frame.copy()
    negative_count.loc[0, "failed_tool_calls"] = -1
    with pytest.raises(DatasetValidationError, match="cannot contain negative"):
        validate_agenttrace_frame(negative_count)

    invalid_rate = frame.copy()
    rate_row = invalid_rate["tool_failure_rate_so_far"].first_valid_index()
    invalid_rate.loc[rate_row, "tool_failure_rate_so_far"] = 1.01
    with pytest.raises(DatasetValidationError, match="between 0 and 1"):
        validate_agenttrace_frame(invalid_rate)

    negative_duration = frame.copy()
    duration_row = negative_duration["elapsed_time_ms"].first_valid_index()
    negative_duration.loc[duration_row, "elapsed_time_ms"] = -0.01
    with pytest.raises(DatasetValidationError, match="negative duration"):
        validate_agenttrace_frame(negative_duration)


def test_duplicate_rows_and_execution_prefix_pairs_are_rejected(frame):
    duplicate_row = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    with pytest.raises(DatasetValidationError, match="prefix_id has"):
        validate_agenttrace_frame(duplicate_row)
    duplicate_pair = frame.copy()
    duplicate_pair.loc[1, "prefix_index"] = duplicate_pair.loc[0, "prefix_index"]
    with pytest.raises(DatasetValidationError, match="pairs must be unique"):
        validate_agenttrace_frame(duplicate_pair)
