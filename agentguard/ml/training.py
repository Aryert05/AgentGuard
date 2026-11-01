"""Leakage-safe training and validation for AgentTrace classifier baselines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import platform
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping, Sequence

import numpy as np
try:
    import sklearn
except ModuleNotFoundError as exc:
    if exc.name == "sklearn":
        raise ImportError("AgentGuard ML training requires scikit-learn; install with `pip install -e '.[dev,ml]'`") from exc
    raise

from agentguard.ml.analysis import (
    DEFAULT_BOOTSTRAP_ITERATIONS,
    DEFAULT_BOOTSTRAP_SEED,
    MIN_STRATUM_GROUPS,
    build_validation_analysis,
)
from agentguard.ml.data import AgentTraceDataset, TARGET_COLUMN
from agentguard.ml.evaluation import evaluate_binary_predictions
from agentguard.ml.models import MODEL_NAMES, build_classifier
from agentguard.ml.preprocessing import PreparedFeaturePartitions, fit_transform_partitions
from agentguard.ml.provenance import (
    code_snapshot_sha256,
    git_branch,
    git_head,
    source_file_hashes,
    worktree_dirty,
    write_artifact_sidecar,
)
from agentguard.ml.splitting import GroupedSplit, SplitError, validate_split_binding
from agentguard.ml.thresholds import (
    ThresholdSelectionError,
    analyze_validation_thresholds,
    unavailable_threshold_analysis,
)
from agentguard.processing.features import MODEL_FEATURE_COLUMNS

DEFAULT_RESULTS_PATH = Path("data/generated/agenttrace-v1/results/phase-03-3-validation.json")
DEFAULT_TEMPLATE_RESULTS_PATH = Path(
    "data/generated/agenttrace-v1/results/phase-03-3-template-held-out.json"
)
_SPLIT_METADATA_DIR = Path("data/generated/agenttrace-v1/split-metadata")
_SPLIT_FILES = {
    "execution_held_out": "execution-held-out.json",
    "task_template_held_out": "task-template-held-out.json",
}


@dataclass(frozen=True)
class FittedModelBundle:
    """A fitted classifier and its training-fitted preprocessing transformer."""

    estimator: Any
    transformer: Any
    feature_names: tuple[str, ...]
    threshold: float
    model_name: str
    dataset_fingerprint: str
    split_fingerprint: str

    def predict_scores(self, features: Any) -> np.ndarray:
        """Return class-1 probabilities for an exact allow-listed feature frame."""
        if list(features.columns) != list(MODEL_FEATURE_COLUMNS):
            raise ValueError("Inference features must match the exact 18-column model allow-list and order")
        transformed = self.transformer.transform(features)
        if not hasattr(self.estimator, "predict_proba"):
            raise ValueError(f"Model {self.model_name} does not expose predict_proba")
        probabilities = np.asarray(self.estimator.predict_proba(transformed), dtype=float)
        classes = list(self.estimator.classes_)
        if 1 not in classes:
            raise ValueError(f"Model {self.model_name} was not fitted with positive class 1")
        return probabilities[:, classes.index(1)]

    def predict(self, features: Any) -> np.ndarray:
        """Apply the validation-selected threshold to inference features."""
        return (self.predict_scores(features) >= self.threshold).astype(np.int64)


@dataclass(frozen=True)
class TrainingRun:
    """Validation-only experiment results and in-memory fitted bundles."""

    results: dict[str, Any]
    bundles: Mapping[str, FittedModelBundle]

    def write_json(self, path: str | Path = DEFAULT_RESULTS_PATH) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.results, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        write_artifact_sidecar(destination)
        return destination


def run_supervised_experiments(
    dataset: AgentTraceDataset,
    split: GroupedSplit,
    *,
    model_names: Sequence[str] = MODEL_NAMES,
    random_state: int = 2026,
    bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> TrainingRun:
    """Fit baselines on train only and select/report exclusively on validation.

    The execution-held-out test partition is never scored; no test labels are
    supplied to an estimator, metric calculation, threshold selector, or
    validation analysis. Split integrity validation checks recorded labels for
    consistency, while the shared preprocessing helper applies its
    training-fitted transformer to test features; this workflow consumes model
    inputs only from train/validation.
    """
    return _run_experiment(
        dataset,
        split,
        model_names=model_names,
        random_state=random_state,
        expected_strategy="execution_held_out",
        expected_group_column="execution_id",
        experiment_name="agenttrace-supervised-validation-baselines-v1",
        evaluation_scope="execution-held-out primary; validation metrics only; test partition not evaluated",
        exploratory=False,
        bootstrap_iterations=bootstrap_iterations,
        bootstrap_seed=bootstrap_seed,
    )


def run_template_held_out_experiment(
    dataset: AgentTraceDataset,
    split: GroupedSplit,
    *,
    model_names: Sequence[str] = MODEL_NAMES,
    random_state: int = 2026,
    bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> TrainingRun:
    """Run the separate exploratory task-template-held-out validation experiment.

    The task-template-held-out test partition is not scored. Two independent
    validation templates and two independent test templates remain an
    exploratory scenario-shift check, not a reliable generalization estimate.
    """
    return _run_experiment(
        dataset,
        split,
        model_names=model_names,
        random_state=random_state,
        expected_strategy="task_template_held_out",
        expected_group_column="task_template_id",
        experiment_name="agenttrace-supervised-template-held-out-exploratory-v1",
        evaluation_scope=(
            "task-template-held-out exploratory; validation metrics only; "
            "test partition not evaluated; do not combine with execution-held-out results"
        ),
        exploratory=True,
        bootstrap_iterations=bootstrap_iterations,
        bootstrap_seed=bootstrap_seed,
    )


def _run_experiment(
    dataset: AgentTraceDataset,
    split: GroupedSplit,
    *,
    model_names: Sequence[str],
    random_state: int,
    expected_strategy: str,
    expected_group_column: str,
    experiment_name: str,
    evaluation_scope: str,
    exploratory: bool,
    bootstrap_iterations: int,
    bootstrap_seed: int,
) -> TrainingRun:
    if split.strategy != expected_strategy or split.group_column != expected_group_column:
        raise SplitError(
            f"Experiment {experiment_name} requires the {expected_strategy} split grouped by "
            f"{expected_group_column}"
        )
    names = tuple(model_names)
    if not names or len(set(names)) != len(names):
        raise ValueError("model_names must be a non-empty sequence without duplicates")
    unknown = set(names) - set(MODEL_NAMES)
    if unknown:
        raise ValueError(f"Unknown model names: {sorted(unknown)}")
    if bootstrap_iterations < 1:
        raise ValueError("bootstrap_iterations must be a positive integer")

    validate_split_binding(dataset, split)
    if list(dataset.features.columns) != list(MODEL_FEATURE_COLUMNS):
        raise ValueError("Dataset model features do not match the explicit 18-feature allow-list")
    train_rows = list(split.partitions["train"].row_indices)
    validation_rows = list(split.partitions["validation"].row_indices)
    train_labels = dataset.target.iloc[train_rows].to_numpy(dtype=np.int64)
    validation_labels = dataset.target.iloc[validation_rows].to_numpy(dtype=np.int64)
    if set(np.unique(train_labels)) != {0, 1}:
        raise ValueError("Training partition must contain both target classes")

    validation_features = dataset.features.iloc[validation_rows].reset_index(drop=True)
    validation_groups = (
        dataset.metadata.iloc[validation_rows][split.group_column].astype(str).reset_index(drop=True)
    )
    prefix_lengths = validation_features["prefix_length"].to_numpy(dtype=float)
    elapsed_time_ms = validation_features["elapsed_time_ms"].to_numpy(dtype=float)

    preprocessing_start = perf_counter()
    prepared: PreparedFeaturePartitions = fit_transform_partitions(dataset, split, scale=True)
    preprocessing_seconds = perf_counter() - preprocessing_start
    feature_names = tuple(str(name) for name in prepared.transformer.named_steps["features"].get_feature_names_out())
    bundles: dict[str, FittedModelBundle] = {}
    model_results: dict[str, Any] = {}

    for model_name in names:
        # Revalidate immediately before each estimator fit; no model receives
        # any array other than the identical prepared train partition.
        validate_split_binding(dataset, split)
        estimator = build_classifier(model_name, random_state=random_state)
        training_start = perf_counter()
        estimator.fit(prepared.features["train"], train_labels)
        training_seconds = perf_counter() - training_start

        inference_start = perf_counter()
        validation_scores = _positive_class_scores(estimator, prepared.features["validation"])
        validation_inference_seconds = perf_counter() - inference_start
        threshold_selection_error: str | None = None
        try:
            threshold_analysis = analyze_validation_thresholds(validation_labels, validation_scores)
        except ThresholdSelectionError as exc:
            threshold_selection_error = str(exc)
            threshold_analysis = unavailable_threshold_analysis(
                validation_labels,
                validation_scores,
                reason=threshold_selection_error,
            )
        selected_threshold = threshold_analysis["selected_threshold"]
        inference_threshold = (
            float(selected_threshold) if selected_threshold is not None else threshold_analysis["default_threshold"]
        )
        default_predictions = (validation_scores >= threshold_analysis["default_threshold"]).astype(np.int64)
        selected_predictions = (
            (validation_scores >= inference_threshold).astype(np.int64)
            if selected_threshold is not None else None
        )
        default_metrics = evaluate_binary_predictions(
            validation_labels, default_predictions, scores=validation_scores,
        )
        selected_metrics = (
            evaluate_binary_predictions(validation_labels, selected_predictions, scores=validation_scores)
            if selected_predictions is not None else None
        )
        default_analysis = build_validation_analysis(
            validation_labels,
            default_predictions,
            validation_groups,
            prefix_lengths,
            elapsed_time_ms,
            group_column=split.group_column,
            scores=validation_scores,
            n_bootstrap=bootstrap_iterations,
            bootstrap_seed=bootstrap_seed,
        )
        selected_analysis = (
            build_validation_analysis(
                validation_labels,
                selected_predictions,
                validation_groups,
                prefix_lengths,
                elapsed_time_ms,
                group_column=split.group_column,
                scores=validation_scores,
                n_bootstrap=bootstrap_iterations,
                bootstrap_seed=bootstrap_seed,
            )
            if selected_predictions is not None else None
        )

        bundle = FittedModelBundle(
            estimator=estimator,
            transformer=prepared.transformer,
            feature_names=feature_names,
            threshold=float(inference_threshold),
            model_name=model_name,
            dataset_fingerprint=dataset.audit.dataset_fingerprint,
            split_fingerprint=split_assignment_fingerprint(split),
        )
        bundles[model_name] = bundle
        model_results[model_name] = {
            "configuration": _json_safe(estimator.get_params(deep=False)),
            "training_seconds": training_seconds,
            "validation_batch_inference_seconds": validation_inference_seconds,
            "validation_sample_count": int(len(validation_labels)),
            "validation_metrics_at_default_threshold": default_metrics,
            "validation_metrics_at_selected_threshold": selected_metrics,
            "validation_analysis_at_default_threshold": default_analysis,
            "validation_analysis_at_selected_threshold": selected_analysis,
            "threshold_analysis": threshold_analysis,
            "selected_threshold": float(selected_threshold) if selected_threshold is not None else None,
            "inference_threshold": float(inference_threshold),
            "inference_threshold_fallback_reason": threshold_analysis["selection_unavailable_reason"],
            "threshold_selection_error": threshold_selection_error,
            "selection_criterion": threshold_analysis["criterion"],
        }

    source_hashes = source_file_hashes()
    split_provenance = _split_provenance(split)
    results = {
        "experiment": experiment_name,
        "evaluation_scope": evaluation_scope,
        "exploratory": exploratory,
        "exploratory_reason": (
            "The task-template-held-out split has only 10 class-pure templates, two validation "
            "templates, and two independent test templates. It is a scenario-shift check, not a "
            "reliable generalization or model-selection estimate."
            if exploratory else None
        ),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_head(),
        "git_branch": git_branch(),
        "worktree_dirty": worktree_dirty(),
        "git_commit_note": (
            "Base commit only. The worktree may be dirty; relevant uncommitted source files are "
            "identified by source_file_hashes and code_snapshot_sha256, not by git_commit."
        ),
        "source_file_hashes": source_hashes,
        "code_snapshot_sha256": code_snapshot_sha256(source_hashes),
        "dataset_path": dataset.audit.source_path,
        "dataset_schema_version": dataset.audit.schema_version,
        "dataset_fingerprint": dataset.audit.dataset_fingerprint,
        "dataset_source_sha256": dataset.audit.source_sha256,
        "dataset_manifest_sha256": _dataset_manifest_sha256(dataset),
        "split_assignment_fingerprint": split_assignment_fingerprint(split),
        "split_strategy": split.strategy,
        "split_group_column": split.group_column,
        "split_seed": split.seed,
        "split_fractions": dict(split.fractions),
        "split_require_each_class": split.require_each_class,
        "split_provenance": split_provenance,
        "split_groups": {
            name: {
                "group_count": split.partitions[name].group_count,
                "row_count": split.partitions[name].row_count,
                "class_counts": {
                    str(key): int(value) for key, value in sorted(split.partitions[name].class_counts.items())
                },
                "group_ids": list(split.partitions[name].group_ids),
            }
            for name in ("train", "validation", "test")
        },
        "template_experiment": (
            {
                "template_count": dataset.audit.task_template_count,
                "class_pure_templates": True,
                "validation_template_count": split.partitions["validation"].group_count,
                "test_template_count": split.partitions["test"].group_count,
                "partition_class_counts": {
                    name: {
                        str(key): int(value)
                        for key, value in sorted(split.partitions[name].class_counts.items())
                    }
                    for name in ("train", "validation", "test")
                },
                "interpretation": (
                    "Class-pure templates make metrics highly scenario-dependent. "
                    "Do not combine this artifact with execution-held-out results."
                ),
            }
            if exploratory else None
        ),
        "model_random_state": random_state,
        "feature_names": list(MODEL_FEATURE_COLUMNS),
        "preprocessed_feature_names": list(feature_names),
        "preprocessed_feature_count": len(feature_names),
        "target_name": TARGET_COLUMN,
        "positive_label": 1,
        "preprocessing": {
            "method": "training-only median imputation; fixed missingness indicators; StandardScaler",
            "fit_partition": "train",
            "seconds": preprocessing_seconds,
        },
        "experiment_configuration": {
            "model_names": list(names),
            "model_random_state": random_state,
            "split_strategy": split.strategy,
            "split_group_column": split.group_column,
            "bootstrap_iterations": bootstrap_iterations,
            "bootstrap_seed": bootstrap_seed,
        },
        "analysis_configuration": {
            "execution_or_group_balanced_weighting": (
                "Each group contributes total weight 1.0 divided equally across its prefix rows; "
                "ordinary row-weighted metrics are reported separately."
            ),
            "bootstrap_resampling_unit": f"complete {split.group_column} groups, not rows",
            "bootstrap_confidence_level": 0.95,
            "prefix_length_bins": "1, 2, 3-5, 6-10, 11+",
            "observation_time_bins_ms": "missing, 0-25, 25-50, 50-100, 100+",
            "minimum_stratum_rows": 10,
            "minimum_stratum_groups": MIN_STRATUM_GROUPS,
        },
        "versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scikit_learn": sklearn.__version__,
            "split_bit_generator": type(np.random.default_rng(split.seed).bit_generator).__name__,
        },
        "threshold_protocol": {
            "data_partition": "validation",
            "default_reference": 0.5,
            "grid": list(threshold_analysis["threshold_grid"]),
            "criterion": threshold_analysis["criterion"],
        },
        "models": model_results,
        "test_metrics": None,
        "test_metrics_reason": (
            "Test predictions and labels were not used during this validation-only experiment."
        ),
    }
    return TrainingRun(results=results, bundles=bundles)


def split_assignment_fingerprint(split: GroupedSplit) -> str:
    """Hash strategy and exact row/group assignments for the experiment record."""
    payload = {
        "strategy": split.strategy,
        "group_column": split.group_column,
        "seed": int(split.seed),
        "fractions": {name: float(split.fractions[name]) for name in ("train", "validation", "test")},
        "require_each_class": split.require_each_class,
        "partitions": {
            name: {
                "group_ids": list(split.partitions[name].group_ids),
                "row_indices": list(split.partitions[name].row_indices),
            }
            for name in ("train", "validation", "test")
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _positive_class_scores(estimator: Any, features: np.ndarray) -> np.ndarray:
    if not hasattr(estimator, "predict_proba"):
        raise ValueError(f"{type(estimator).__name__} does not provide probabilities required for threshold selection")
    probabilities = np.asarray(estimator.predict_proba(features), dtype=float)
    if probabilities.ndim != 2 or probabilities.shape[0] != len(features):
        raise ValueError("Classifier returned probabilities with an invalid shape")
    classes = list(estimator.classes_)
    if 1 not in classes:
        raise ValueError("Classifier probability output does not contain positive label 1")
    scores = probabilities[:, classes.index(1)]
    if not np.isfinite(scores).all() or np.any((scores < 0) | (scores > 1)):
        raise ValueError("Classifier returned invalid positive-class probabilities")
    return scores


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _dataset_manifest_sha256(dataset: AgentTraceDataset) -> str | None:
    source = Path(dataset.audit.source_path)
    manifest = source.parent / "manifest.json"
    try:
        return hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.is_file() else None
    except OSError:
        return None


def _split_provenance(split: GroupedSplit) -> dict[str, Any]:
    filename = _SPLIT_FILES.get(split.strategy)
    path = _SPLIT_METADATA_DIR / filename if filename else None
    result: dict[str, Any] = {
        "canonical_method": "deterministic_reconstruction",
        "reason": (
            "Training deterministically reconstructs the split from the recorded strategy, seed, "
            "fractions, and require_each_class flag, then validates exact group/row assignments. "
            "The persisted Phase 3.2 split file is an audited cross-check when present."
        ),
        "approved_split_file": path.as_posix() if path is not None else None,
        "approved_split_file_sha256": None,
        "approved_split_file_matches": None,
    }
    if path is None or not path.is_file():
        result["approved_split_file_note"] = "Persisted split file unavailable; deterministic reconstruction remains canonical."
        return result
    try:
        persisted = json.loads(path.read_text(encoding="utf-8"))
        persisted_partitions = persisted.get("partitions", {})
        matches = all(
            list(persisted_partitions.get(name, {}).get("group_ids", [])) == list(split.partitions[name].group_ids)
            and list(persisted_partitions.get(name, {}).get("row_indices", [])) == list(split.partitions[name].row_indices)
            for name in ("train", "validation", "test")
        )
        result["approved_split_file_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        result["approved_split_file_matches"] = bool(matches)
        result["approved_split_file_note"] = (
            "Exact group IDs and row indices match the deterministic reconstruction."
            if matches else
            "Persisted split file differs from deterministic reconstruction; investigate before relying on it."
        )
    except (OSError, ValueError, TypeError) as exc:
        result["approved_split_file_note"] = f"Could not compare persisted split file: {exc}"
    return result
