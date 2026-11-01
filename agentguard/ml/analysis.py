"""Execution-aware validation analyses for correlated AgentTrace prefixes.

The functions in this module operate exclusively on validation predictions.
They never read or score a test partition.
"""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from agentguard.ml.evaluation import evaluate_binary_predictions


DEFAULT_BOOTSTRAP_ITERATIONS = 2000
DEFAULT_BOOTSTRAP_SEED = 20261101
DEFAULT_CONFIDENCE_LEVEL = 0.95
MIN_STRATUM_ROWS = 10
MIN_STRATUM_GROUPS = 10

PREFIX_LENGTH_BINS: tuple[dict[str, Any], ...] = (
    {"label": "1", "minimum": 1, "maximum": 1},
    {"label": "2", "minimum": 2, "maximum": 2},
    {"label": "3-5", "minimum": 3, "maximum": 5},
    {"label": "6-10", "minimum": 6, "maximum": 10},
    {"label": "11+", "minimum": 11, "maximum": None},
)

OBSERVATION_TIME_BINS: tuple[dict[str, Any], ...] = (
    {"label": "missing", "minimum": None, "maximum": None, "missing": True},
    {"label": "0-25 ms", "minimum": 0.0, "maximum": 25.0, "missing": False},
    {"label": "25-50 ms", "minimum": 25.0, "maximum": 50.0, "missing": False},
    {"label": "50-100 ms", "minimum": 50.0, "maximum": 100.0, "missing": False},
    {"label": "100+ ms", "minimum": 100.0, "maximum": None, "missing": False},
)

BIN_DEFINITION_NOTE = (
    "Bins are fixed before inspecting metric values. Prefix-length bins reflect "
    "interpretable early-history stages (1, 2, 3-5, 6-10, 11+ events). "
    "Observation-time bins use a fixed, generic schedule in milliseconds, with "
    "missing elapsed time reported separately rather than imputed. Bins are "
    "descriptive and may split prefixes from the same execution."
)


def group_balanced_metrics(
    y_true: Any,
    y_pred: Any,
    group_ids: Any,
    *,
    group_column: str = "execution_id",
    scores: Any | None = None,
    include_group_outcomes: bool = True,
) -> dict[str, Any]:
    """Return group-balanced and ordinary row-weighted validation metrics.

    Each group contributes total weight ``1.0``, divided equally across its
    prefix rows. This prevents executions with many prefixes from dominating a
    metric. Ordinary row-weighted metrics are returned separately and are never
    replaced silently.
    """
    truth, predicted, groups, score_values = _prepare_inputs(y_true, y_pred, group_ids, scores)
    weights, group_count = _group_weights(groups)
    balanced = _weighted_binary_metrics(truth, predicted, weights)
    return {
        "method": (
            "Each group contributes total weight 1.0, divided equally across its "
            "prefix rows; weighted TP/FP/FN/TN then define precision, recall, F1, "
            "and accuracy."
        ),
        "group_column": group_column,
        "sample_count": int(len(truth)),
        "group_count": int(group_count),
        "prefix_weight_range": [float(weights.min()), float(weights.max())] if len(weights) else None,
        "group_balanced": balanced,
        "row_weighted": evaluate_binary_predictions(truth, predicted, scores=score_values),
        "group_outcomes": (
            _group_outcomes(truth, predicted, groups, group_column)
            if include_group_outcomes else None
        ),
    }


def cluster_bootstrap_indices(
    group_ids: Any,
    *,
    n_bootstrap: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> tuple[tuple[int, ...], ...]:
    """Return reproducible cluster-bootstrap row indices, resampling groups.

    Every iteration samples the same number of groups as are present, with
    replacement. All rows belonging to a sampled group are retained together.
    """
    groups = np.asarray(group_ids, dtype=str)
    if groups.ndim != 1:
        raise ValueError("group_ids must be a one-dimensional array")
    if n_bootstrap < 1:
        raise ValueError("n_bootstrap must be a positive integer")
    unique_groups = np.asarray(sorted(set(groups.tolist())), dtype=str)
    if len(unique_groups) == 0:
        raise ValueError("At least one group is required for cluster bootstrap")
    group_to_indices = {
        group: tuple(int(index) for index in np.flatnonzero(groups == group))
        for group in unique_groups
    }
    rng = np.random.default_rng(seed)
    samples: list[tuple[int, ...]] = []
    for _ in range(n_bootstrap):
        sampled_groups = rng.choice(unique_groups, size=len(unique_groups), replace=True)
        indices = [index for group in sampled_groups for index in group_to_indices[str(group)]]
        samples.append(tuple(indices))
    return tuple(samples)


def group_bootstrap(
    y_true: Any,
    y_pred: Any,
    group_ids: Any,
    *,
    group_column: str = "execution_id",
    scores: Any | None = None,
    n_bootstrap: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
) -> dict[str, Any]:
    """Cluster-bootstrap validation metrics with complete groups as the unit.

    Replicates containing only one true class have undefined ROC-AUC and Average
    Precision. Undefined values are excluded only from that metric's interval;
    they are never imputed. The returned intervals are exploratory because the
    validation partition contains few independent groups.
    """
    if not 0 < confidence_level < 1:
        raise ValueError("confidence_level must be between 0 and 1")
    truth, predicted, groups, score_values = _prepare_inputs(y_true, y_pred, group_ids, scores)
    unique_groups = sorted(set(groups.tolist()))
    if n_bootstrap < 1:
        raise ValueError("n_bootstrap must be a positive integer")

    weights, group_count = _group_weights(groups)
    row_point = evaluate_binary_predictions(truth, predicted, scores=score_values)
    balanced_point = _weighted_binary_metrics(truth, predicted, weights)

    row_metrics = ("precision", "recall", "f1", "accuracy", "roc_auc", "average_precision")
    balanced_metrics = ("precision", "recall", "f1", "accuracy")
    row_values: dict[str, list[float]] = {name: [] for name in row_metrics}
    balanced_values: dict[str, list[float]] = {name: [] for name in balanced_metrics}
    row_invalid = {name: 0 for name in row_metrics}
    balanced_invalid = {name: 0 for name in balanced_metrics}
    class_composition = {"both_classes": 0, "positive_only": 0, "negative_only": 0}
    replicate_row_counts: list[int] = []

    group_to_indices = {
        group: tuple(int(index) for index in np.flatnonzero(groups == group))
        for group in unique_groups
    }
    rng = np.random.default_rng(seed)
    for _ in range(n_bootstrap):
        sampled_groups = rng.choice(np.asarray(unique_groups, dtype=str), size=len(unique_groups), replace=True)
        row_parts: list[np.ndarray] = []
        instance_parts: list[np.ndarray] = []
        for instance_index, group in enumerate(sampled_groups):
            indices = np.asarray(group_to_indices[str(group)], dtype=int)
            row_parts.append(indices)
            instance_parts.append(np.full(len(indices), f"{group}::{instance_index}", dtype=object))
        indices = np.concatenate(row_parts)
        instance_groups = np.concatenate(instance_parts)
        replicate_row_counts.append(int(len(indices)))

        replicate_truth = truth[indices]
        replicate_predicted = predicted[indices]
        replicate_scores = score_values[indices] if score_values is not None else None
        classes = set(np.unique(replicate_truth).tolist())
        if classes == {0, 1}:
            class_composition["both_classes"] += 1
        elif classes == {1}:
            class_composition["positive_only"] += 1
        else:
            class_composition["negative_only"] += 1

        row_result = evaluate_binary_predictions(
            replicate_truth, replicate_predicted, scores=replicate_scores,
        )
        for name in row_metrics:
            value = row_result[name]
            if value is None:
                row_invalid[name] += 1
            else:
                row_values[name].append(float(value))

        instance_weights, _ = _group_weights(instance_groups)
        balanced_result = _weighted_binary_metrics(replicate_truth, replicate_predicted, instance_weights)
        for name in balanced_metrics:
            value = balanced_result[name]
            if value is None:
                balanced_invalid[name] += 1
            else:
                balanced_values[name].append(float(value))

    alpha = (1.0 - confidence_level) / 2.0
    row_intervals = {
        name: _percentile_interval(values, alpha, n_bootstrap) for name, values in row_values.items()
    }
    balanced_intervals = {
        name: _percentile_interval(values, alpha, n_bootstrap) for name, values in balanced_values.items()
    }

    return {
        "method": (
            "Cluster bootstrap resampling complete groups with replacement; all "
            "prefixes belonging to each sampled group are retained, and percentile "
            "intervals are computed over bootstrap replicates."
        ),
        "group_column": group_column,
        "resampling_unit": "complete groups, not individual prefix rows",
        "n_bootstrap": int(n_bootstrap),
        "seed": int(seed),
        "confidence_level": float(confidence_level),
        "percentile_interval": [float(alpha), float(1.0 - alpha)],
        "group_count": int(group_count),
        "resampled_group_count_per_iteration": int(group_count),
        "replicate_row_count": {
            "minimum": int(min(replicate_row_counts)) if replicate_row_counts else 0,
            "maximum": int(max(replicate_row_counts)) if replicate_row_counts else 0,
            "mean": float(np.mean(replicate_row_counts)) if replicate_row_counts else None,
        },
        "replicate_class_composition": class_composition,
        "row_weighted": {
            "point": row_point,
            "interval": row_intervals,
            "valid_replicates": {name: len(values) for name, values in row_values.items()},
            "invalid_replicates": row_invalid,
        },
        "group_balanced": {
            "point": balanced_point,
            "interval": balanced_intervals,
            "valid_replicates": {name: len(values) for name, values in balanced_values.items()},
            "invalid_replicates": balanced_invalid,
        },
        "invalid_replicate_handling": (
            "A replicate lacking a defined metric is excluded only from that metric's "
            "interval and is not imputed. Single-class replicates make ROC-AUC and "
            "Average Precision undefined; missing positive truth or predictions can "
            "also make precision, recall, or F1 undefined."
        ),
        "exploratory": True,
        "warning": (
            f"Only {group_count} validation {group_column} groups are available; "
            "cluster-bootstrap intervals are unstable and should not be read as "
            "confirmatory uncertainty estimates."
        ),
    }


def prefix_length_strata(
    y_true: Any,
    y_pred: Any,
    group_ids: Any,
    prefix_lengths: Any,
    *,
    group_column: str = "execution_id",
    scores: Any | None = None,
    bins: Sequence[dict[str, Any]] = PREFIX_LENGTH_BINS,
) -> dict[str, Any]:
    """Report validation metrics in fixed prefix-length bins."""
    truth, predicted, groups, score_values = _prepare_inputs(y_true, y_pred, group_ids, scores)
    lengths = np.asarray(prefix_lengths, dtype=float)
    if lengths.ndim != 1 or lengths.shape != truth.shape:
        raise ValueError("prefix_lengths must be a one-dimensional array matching labels")
    if not np.isfinite(lengths).all():
        raise ValueError("prefix_lengths must be finite")

    return {
        "bin_definition": BIN_DEFINITION_NOTE,
        "group_column": group_column,
        "insufficient_threshold": {
            "minimum_rows": MIN_STRATUM_ROWS,
            "minimum_groups": MIN_STRATUM_GROUPS,
        },
        "bins": [
            _stratum_result(
                truth, predicted, groups, score_values, _prefix_mask(lengths, definition),
                definition, group_column,
            )
            for definition in bins
        ],
    }


def observation_time_strata(
    y_true: Any,
    y_pred: Any,
    group_ids: Any,
    elapsed_time_ms: Any,
    *,
    group_column: str = "execution_id",
    scores: Any | None = None,
    bins: Sequence[dict[str, Any]] = OBSERVATION_TIME_BINS,
) -> dict[str, Any]:
    """Report validation metrics in fixed elapsed-observation-time bins."""
    truth, predicted, groups, score_values = _prepare_inputs(y_true, y_pred, group_ids, scores)
    elapsed = np.asarray(elapsed_time_ms, dtype=float)
    if elapsed.ndim != 1 or elapsed.shape != truth.shape:
        raise ValueError("elapsed_time_ms must be a one-dimensional array matching labels")

    return {
        "bin_definition": BIN_DEFINITION_NOTE,
        "group_column": group_column,
        "missing_elapsed_time_count": int(np.isnan(elapsed).sum()),
        "insufficient_threshold": {
            "minimum_rows": MIN_STRATUM_ROWS,
            "minimum_groups": MIN_STRATUM_GROUPS,
        },
        "bins": [
            _stratum_result(
                truth, predicted, groups, score_values, _time_mask(elapsed, definition),
                definition, group_column,
            )
            for definition in bins
        ],
    }


def build_validation_analysis(
    y_true: Any,
    y_pred: Any,
    group_ids: Any,
    prefix_lengths: Any,
    elapsed_time_ms: Any,
    *,
    group_column: str = "execution_id",
    scores: Any | None = None,
    n_bootstrap: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Build all validation-only group-aware analyses for one threshold."""
    return {
        "group_column": group_column,
        "group_balanced": group_balanced_metrics(
            y_true, y_pred, group_ids, group_column=group_column, scores=scores,
        ),
        "group_bootstrap": group_bootstrap(
            y_true, y_pred, group_ids, group_column=group_column, scores=scores,
            n_bootstrap=n_bootstrap, seed=bootstrap_seed,
        ),
        "prefix_length_strata": prefix_length_strata(
            y_true, y_pred, group_ids, prefix_lengths, group_column=group_column, scores=scores,
        ),
        "observation_time_strata": observation_time_strata(
            y_true, y_pred, group_ids, elapsed_time_ms, group_column=group_column, scores=scores,
        ),
    }


def _prepare_inputs(
    y_true: Any,
    y_pred: Any,
    group_ids: Any,
    scores: Any | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray | None]:
    truth = np.asarray(y_true)
    predicted = np.asarray(y_pred)
    groups = np.asarray(group_ids, dtype=str)
    if truth.ndim != 1 or predicted.ndim != 1 or groups.ndim != 1:
        raise ValueError("labels, predictions, and group IDs must be one-dimensional")
    if truth.shape != predicted.shape or truth.shape != groups.shape:
        raise ValueError("labels, predictions, and group IDs must have equal lengths")
    if not set(np.unique(truth)).issubset({0, 1}) or not set(np.unique(predicted)).issubset({0, 1}):
        raise ValueError("labels and predictions must contain only binary values 0 and 1")
    score_values = None
    if scores is not None:
        score_values = np.asarray(scores, dtype=float)
        if score_values.ndim != 1 or score_values.shape != truth.shape:
            raise ValueError("scores must be a one-dimensional array matching labels")
        if not np.isfinite(score_values).all():
            raise ValueError("scores must contain only finite values")
    return truth.astype(np.int64), predicted.astype(np.int64), groups, score_values


def _group_outcomes(
    truth: np.ndarray,
    predicted: np.ndarray,
    groups: np.ndarray,
    group_column: str,
) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for group in sorted(set(groups.tolist())):
        mask = groups == group
        group_truth = truth[mask]
        group_predicted = predicted[mask]
        row_count = int(mask.sum())
        positive_predictions = int((group_predicted == 1).sum())
        outcomes.append({
            group_column: group,
            "row_count": row_count,
            "positive_rows": int((group_truth == 1).sum()),
            "negative_rows": int((group_truth == 0).sum()),
            "group_truth_classes": sorted(int(value) for value in np.unique(group_truth)),
            "predicted_positive_rows": positive_predictions,
            "predicted_negative_rows": int(row_count - positive_predictions),
            "predicted_positive_fraction": float(positive_predictions / row_count) if row_count else None,
            "correct_rows": int((group_truth == group_predicted).sum()),
            "error_rows": int((group_truth != group_predicted).sum()),
        })
    return outcomes


def _group_weights(groups: np.ndarray) -> tuple[np.ndarray, int]:
    if len(groups) == 0:
        return np.asarray([], dtype=float), 0
    unique, counts = np.unique(groups, return_counts=True)
    weight_by_group = {str(group): 1.0 / int(count) for group, count in zip(unique, counts)}
    weights = np.asarray([weight_by_group[str(group)] for group in groups], dtype=float)
    return weights, int(len(unique))


def _weighted_binary_metrics(truth: np.ndarray, predicted: np.ndarray, weights: np.ndarray) -> dict[str, Any]:
    weighted_tn = float(np.sum(weights[(truth == 0) & (predicted == 0)]))
    weighted_fp = float(np.sum(weights[(truth == 0) & (predicted == 1)]))
    weighted_fn = float(np.sum(weights[(truth == 1) & (predicted == 0)]))
    weighted_tp = float(np.sum(weights[(truth == 1) & (predicted == 1)]))
    reasons: dict[str, str] = {}
    precision = _ratio(weighted_tp, weighted_tp + weighted_fp)
    recall = _ratio(weighted_tp, weighted_tp + weighted_fn)
    if precision is None:
        reasons["precision"] = "no positive-class weighted predictions"
    if recall is None:
        reasons["recall"] = "no positive-class weighted truth"
    if precision is None or recall is None:
        f1 = None
        reasons["f1"] = "precision or recall is undefined"
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = float(2.0 * precision * recall / (precision + recall))
    total = weighted_tn + weighted_fp + weighted_fn + weighted_tp
    accuracy = _ratio(weighted_tp + weighted_tn, total)
    if accuracy is None:
        reasons["accuracy"] = "no weighted samples are available"
    return {
        "sample_count": int(len(truth)),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": accuracy,
        "true_positives": weighted_tp,
        "true_negatives": weighted_tn,
        "false_positives": weighted_fp,
        "false_negatives": weighted_fn,
        "confusion_matrix": {
            "labels": [0, 1],
            "matrix": [[weighted_tn, weighted_fp], [weighted_fn, weighted_tp]],
        },
        "undefined_reasons": reasons,
    }


def _ratio(numerator: float, denominator: float) -> float | None:
    return float(numerator / denominator) if denominator else None


def _percentile_interval(values: list[float], alpha: float, n_bootstrap: int) -> dict[str, Any]:
    if not values:
        return {
            "lower": None,
            "upper": None,
            "valid_replicates": 0,
            "invalid_replicates": int(n_bootstrap),
        }
    array = np.asarray(values, dtype=float)
    return {
        "lower": float(np.quantile(array, alpha)),
        "upper": float(np.quantile(array, 1.0 - alpha)),
        "valid_replicates": int(len(values)),
        "invalid_replicates": int(n_bootstrap - len(values)),
    }


def _prefix_mask(lengths: np.ndarray, definition: dict[str, Any]) -> np.ndarray:
    minimum = definition["minimum"]
    maximum = definition["maximum"]
    mask = lengths >= float(minimum)
    if maximum is not None:
        mask &= lengths <= float(maximum)
    return mask


def _time_mask(elapsed: np.ndarray, definition: dict[str, Any]) -> np.ndarray:
    if definition.get("missing"):
        return np.isnan(elapsed)
    mask = ~np.isnan(elapsed)
    minimum = definition["minimum"]
    maximum = definition["maximum"]
    mask &= elapsed >= float(minimum)
    if maximum is not None:
        mask &= elapsed < float(maximum)
    return mask


def _stratum_result(
    truth: np.ndarray,
    predicted: np.ndarray,
    groups: np.ndarray,
    scores: np.ndarray | None,
    mask: np.ndarray,
    definition: dict[str, Any],
    group_column: str,
) -> dict[str, Any]:
    row_count = int(mask.sum())
    subset_truth = truth[mask]
    subset_predicted = predicted[mask]
    subset_groups = groups[mask]
    subset_scores = scores[mask] if scores is not None else None
    group_count = int(len(set(subset_groups.tolist()))) if row_count else 0
    positive_count = int((subset_truth == 1).sum())
    negative_count = int((subset_truth == 0).sum())
    reasons: list[str] = []
    if row_count < MIN_STRATUM_ROWS:
        reasons.append(f"fewer than {MIN_STRATUM_ROWS} prefix rows")
    if group_count < MIN_STRATUM_GROUPS:
        reasons.append(f"fewer than {MIN_STRATUM_GROUPS} independent {group_column} groups")
    return {
        "bin": dict(definition),
        "row_count": row_count,
        "group_count": group_count,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "positive_prevalence": float(positive_count / row_count) if row_count else None,
        "insufficient_observations": bool(reasons),
        "insufficiency_reasons": reasons or None,
        "row_weighted": evaluate_binary_predictions(
            subset_truth, subset_predicted, scores=subset_scores,
        ) if row_count else None,
        "group_balanced": group_balanced_metrics(
            subset_truth, subset_predicted, subset_groups,
            group_column=group_column, scores=subset_scores,
            include_group_outcomes=False,
        ) if row_count else None,
    }
