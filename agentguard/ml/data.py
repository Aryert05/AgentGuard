"""Strict loading, validation, auditing, and allow-listed selection for AgentTrace."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
from typing import Any

import numpy as np
import pandas as pd
from pandas.api.types import is_bool_dtype, is_complex_dtype, is_numeric_dtype

from agentguard.processing.features import MODEL_FEATURE_COLUMNS


IDENTIFIER_COLUMNS = ("execution_id", "task_id", "task_template_id", "prefix_id")
GROUP_COLUMNS = ("execution_id", "task_template_id")
EXCLUDED_MODEL_COLUMNS = (
    "execution_id", "task_id", "task_template_id", "prefix_id", "prefix_index",
    "synthetic_data", "eventual_failure", "outcome_label", "failure_category",
)
TARGET_COLUMN = "eventual_failure"
DESCRIPTIVE_TARGET_COLUMNS = ("outcome_label", "failure_category")
DATASET_SCHEMA_VERSION = "agenttrace-prefix-v1"
DATASET_FINGERPRINT_VERSION = "typed-scalars-v1"
INTEGER_FEATURES = (
    "prefix_length", "current_step_number", "total_tool_calls_so_far",
    "successful_tool_calls", "failed_tool_calls", "repeated_tool_call_count",
    "distinct_tool_count", "timeout_count", "invalid_argument_count",
    "tool_unavailable_count", "consecutive_failure_count", "tool_switch_count",
    "repeated_failure_pattern_count",
)
COUNT_FEATURES = INTEGER_FEATURES
STRUCTURALLY_NULLABLE_FEATURES = (
    "elapsed_time_ms", "tool_failure_rate_so_far", "average_tool_duration_ms",
    "maximum_tool_duration_ms", "time_since_last_failure_ms",
)
DURATION_FEATURES = (
    "elapsed_time_ms", "average_tool_duration_ms", "maximum_tool_duration_ms",
    "time_since_last_failure_ms",
)
REQUIRED_COLUMNS = (
    *IDENTIFIER_COLUMNS, "prefix_index", "synthetic_data", *MODEL_FEATURE_COLUMNS,
    TARGET_COLUMN, *DESCRIPTIVE_TARGET_COLUMNS,
)


class DatasetValidationError(ValueError):
    """Raised when a dataset contains invalid rows or violates the AgentTrace schema."""


@dataclass(frozen=True)
class DatasetAudit:
    source_path: str
    source_sha256: str | None
    dataset_fingerprint: str
    schema_version: str
    column_names: tuple[str, ...]
    row_count: int
    execution_count: int
    task_template_count: int
    feature_columns: tuple[str, ...]
    missing_by_column: dict[str, int]
    target_counts: dict[int, int]
    duplicate_prefix_ids: int
    duplicate_rows: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "dataset_fingerprint": self.dataset_fingerprint,
            "schema_version": self.schema_version,
            "column_names": list(self.column_names),
            "row_count": self.row_count,
            "execution_count": self.execution_count,
            "task_template_count": self.task_template_count,
            "feature_columns": list(self.feature_columns),
            "missing_by_column": dict(self.missing_by_column),
            "target_counts": {str(key): value for key, value in self.target_counts.items()},
            "duplicate_prefix_ids": self.duplicate_prefix_ids,
            "duplicate_rows": self.duplicate_rows,
        }


@dataclass(frozen=True)
class AgentTraceDataset:
    """Validated model matrix, target, group metadata, and source audit."""

    features: pd.DataFrame
    target: pd.Series
    metadata: pd.DataFrame
    audit: DatasetAudit

    def __len__(self) -> int:
        return len(self.target)


def load_agenttrace_csv(path: str | Path) -> AgentTraceDataset:
    """Read and validate AgentTrace without dropping, coercing, or silently fixing rows."""
    source = Path(path)
    if not source.is_file():
        raise DatasetValidationError(f"AgentTrace CSV does not exist: {source}")
    try:
        frame = pd.read_csv(source)
    except Exception as exc:
        raise DatasetValidationError(f"Could not read AgentTrace CSV: {source}") from exc
    return validate_agenttrace_frame(frame, source_path=str(source), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())


def validate_agenttrace_frame(frame: pd.DataFrame, *, source_path: str = "<dataframe>",
                              source_sha256: str | None = None) -> AgentTraceDataset:
    """Validate all rows, then select exactly the Phase 2 model-feature allow-list."""
    if not isinstance(frame, pd.DataFrame):
        raise DatasetValidationError("AgentTrace input must be a pandas DataFrame")
    if frame.empty:
        raise DatasetValidationError("AgentTrace dataset contains no rows")
    if frame.columns.duplicated().any():
        duplicates = sorted(set(frame.columns[frame.columns.duplicated()].astype(str)))
        raise DatasetValidationError(f"Duplicate column names: {duplicates}")
    if any(not isinstance(column, str) for column in frame.columns):
        raise DatasetValidationError("All AgentTrace column names must be strings")
    # Normalize labels so positional split indices have one unambiguous meaning.
    frame = frame.reset_index(drop=True)

    missing_columns = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing_columns:
        raise DatasetValidationError(f"AgentTrace is missing required columns: {missing_columns}")
    unexpected_columns = sorted(set(frame.columns) - set(REQUIRED_COLUMNS))
    if unexpected_columns:
        raise DatasetValidationError(f"AgentTrace contains unexpected columns: {unexpected_columns}")

    problems: list[str] = []
    for column in IDENTIFIER_COLUMNS:
        values = frame[column]
        if values.isna().any() or values.astype(str).str.strip().eq("").any():
            problems.append(f"{column} contains missing or empty values")

    if frame["prefix_id"].duplicated().any():
        count = int(frame["prefix_id"].duplicated(keep=False).sum())
        problems.append(f"prefix_id has {count} rows participating in duplicates")
    if frame.duplicated().any():
        problems.append(f"dataset contains {int(frame.duplicated().sum())} duplicate rows")
    if frame.duplicated(["execution_id", "prefix_index"]).any():
        problems.append("(execution_id, prefix_index) pairs must be unique")

    for column in MODEL_FEATURE_COLUMNS:
        dtype = frame[column].dtype
        if not is_numeric_dtype(dtype) or is_bool_dtype(dtype):
            problems.append(f"feature {column} must have a numeric, non-boolean dtype; got {dtype}")
            continue
        if is_complex_dtype(dtype):
            problems.append(f"feature {column} must be real-valued; complex dtypes are not supported")
            continue
        raw_values = frame[column].array
        if any(not pd.isna(value) and np.isinf(value) for value in raw_values):
            problems.append(f"feature {column} contains infinite values")
        if column not in STRUCTURALLY_NULLABLE_FEATURES and frame[column].isna().any():
            problems.append(f"feature {column} contains missing values but is not structurally nullable")
        observed = [value for value in raw_values if not pd.isna(value) and np.isfinite(value)]
        if column in INTEGER_FEATURES and any(value != int(value) for value in observed):
            problems.append(f"feature {column} must contain integer-valued counts/steps")
        if column in COUNT_FEATURES and any(value < 0 for value in observed):
            problems.append(f"feature {column} cannot contain negative counts/steps")
        if column in DURATION_FEATURES and any(value < 0 for value in observed):
            problems.append(f"feature {column} cannot contain negative duration values")
        if column == "tool_failure_rate_so_far" and any(not 0 <= value <= 1 for value in observed):
            problems.append("feature tool_failure_rate_so_far must be between 0 and 1")

    prefix_index = frame["prefix_index"]
    if (not is_numeric_dtype(prefix_index.dtype) or is_bool_dtype(prefix_index.dtype)
            or is_complex_dtype(prefix_index.dtype)):
        problems.append("prefix_index must have a real numeric, non-boolean dtype")
    elif prefix_index.isna().any() or (prefix_index < 1).any() or (prefix_index % 1 != 0).any():
        problems.append("prefix_index must contain positive integers")

    synthetic = frame["synthetic_data"]
    if synthetic.isna().any() or not synthetic.map(lambda value: isinstance(value, (bool, np.bool_)) and value).all():
        problems.append("synthetic_data must be true for every AgentTrace row")

    target = frame[TARGET_COLUMN]
    if target.isna().any():
        problems.append(f"target {TARGET_COLUMN} contains missing labels")
    elif is_complex_dtype(target.dtype):
        problems.append(f"target {TARGET_COLUMN} must be real-valued; complex dtypes are not supported")
    elif not is_numeric_dtype(target.dtype) or is_bool_dtype(target.dtype):
        problems.append(f"target {TARGET_COLUMN} must be numeric binary values 0 or 1")
    elif not set(pd.unique(target)).issubset({0, 1}):
        problems.append(f"target {TARGET_COLUMN} contains values outside {{0, 1}}")

    status = frame["outcome_label"]
    if status.isna().any():
        problems.append("outcome_label contains missing values")
    else:
        expected = status.map({"completed": 0, "failed": 1, "partially_completed": 1})
        if expected.isna().any():
            problems.append("outcome_label contains unsupported or unknown statuses")
        elif target.notna().all() and not np.array_equal(expected.to_numpy(), target.to_numpy()):
            problems.append("eventual_failure does not match outcome_label semantics")

    # Group-level labels and template identity must be stable across all prefixes.
    for group_column in GROUP_COLUMNS:
        target_counts = frame.groupby(group_column, dropna=False)[TARGET_COLUMN].nunique(dropna=False)
        if (target_counts > 1).any():
            problems.append(f"{group_column} groups contain inconsistent eventual_failure labels")
    execution_templates = frame.groupby("execution_id", dropna=False)["task_template_id"].nunique(dropna=False)
    if (execution_templates != 1).any():
        problems.append("each execution_id must map to exactly one task_template_id")

    if problems:
        raise DatasetValidationError("Invalid AgentTrace dataset: " + "; ".join(problems))

    # Reindex explicitly; no metadata, target, or unrecognized columns can leak into X.
    features = frame.loc[:, list(MODEL_FEATURE_COLUMNS)].copy()
    labels = frame[TARGET_COLUMN].astype("int64").copy().rename(TARGET_COLUMN)
    metadata_columns = [*IDENTIFIER_COLUMNS, "prefix_index", "synthetic_data", *DESCRIPTIVE_TARGET_COLUMNS]
    metadata = frame.loc[:, metadata_columns].copy()
    missing = {column: int(count) for column, count in frame.isna().sum().items() if count}
    counts = labels.value_counts().sort_index().to_dict()
    # The returned validated dataset stores the binary target canonically as
    # int64; fingerprint that same representation so accepted integral floats
    # remain usable when the components are reconstructed for split checks.
    fingerprint_frame = frame.copy(deep=False)
    fingerprint_frame[TARGET_COLUMN] = labels
    fingerprint = fingerprint_agenttrace_frame(fingerprint_frame)
    audit = DatasetAudit(
        source_path=source_path,
        source_sha256=source_sha256,
        dataset_fingerprint=fingerprint,
        schema_version=DATASET_SCHEMA_VERSION,
        column_names=tuple(frame.columns),
        row_count=len(frame),
        execution_count=int(frame["execution_id"].nunique()),
        task_template_count=int(frame["task_template_id"].nunique()),
        feature_columns=tuple(MODEL_FEATURE_COLUMNS),
        missing_by_column=missing,
        target_counts={int(key): int(value) for key, value in counts.items()},
        duplicate_prefix_ids=0,
        duplicate_rows=0,
    )
    return AgentTraceDataset(features=features, target=labels, metadata=metadata, audit=audit)


def agenttrace_dataset_frame(dataset: AgentTraceDataset) -> pd.DataFrame:
    """Rebuild the validated frame in its original schema order for integrity checks."""
    combined = pd.concat([dataset.metadata, dataset.features, dataset.target], axis=1)
    required = list(dataset.audit.column_names)
    missing = [column for column in required if column not in combined.columns]
    if missing:
        raise DatasetValidationError(f"Validated dataset components are missing columns: {missing}")
    if combined.columns.duplicated().any():
        raise DatasetValidationError("Validated dataset components contain duplicate columns")
    return combined.loc[:, required].reset_index(drop=True)


def fingerprint_agenttrace_frame(frame: pd.DataFrame) -> str:
    """Hash schema and typed, ordered scalar encodings including missing values."""
    schema = {
        "version": DATASET_SCHEMA_VERSION,
        "fingerprint_encoding": DATASET_FINGERPRINT_VERSION,
        "columns": [{"name": column, "dtype": str(frame[column].dtype)} for column in frame.columns],
        "model_features": list(MODEL_FEATURE_COLUMNS),
        "target_semantics": {"completed": 0, "failed": 1, "partially_completed": 1},
    }
    digest = hashlib.sha256()
    digest.update(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    for row in frame.itertuples(index=False, name=None):
        for value in row:
            encoded = _encode_fingerprint_value(value)
            digest.update(struct.pack(">Q", len(encoded)))
            digest.update(encoded)
    return digest.hexdigest()


def _encode_fingerprint_value(value: Any) -> bytes:
    """Encode supported scalar values without lossy or delimiter-based serialization."""
    if value is None or value is pd.NA or value is pd.NaT:
        return b"M"
    if isinstance(value, (float, np.floating)) and np.isnan(value):
        return b"M"
    if isinstance(value, (bool, np.bool_)):
        return b"B1" if value else b"B0"
    if isinstance(value, (int, np.integer)):
        return b"I" + str(int(value)).encode("ascii")
    if isinstance(value, np.floating):
        return b"F" + np.format_float_scientific(value, unique=True, trim="k").encode("ascii")
    if isinstance(value, float):
        return b"F" + value.hex().encode("ascii")
    if isinstance(value, (str, np.str_)):
        return b"S" + str(value).encode("utf-8")
    if isinstance(value, (bytes, np.bytes_)):
        return b"Y" + bytes(value)
    raise DatasetValidationError(
        f"AgentTrace contains unsupported value type for deterministic fingerprinting: {type(value).__name__}"
    )
