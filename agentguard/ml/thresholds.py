"""Validation-only operating-threshold analysis."""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from agentguard.ml.evaluation import evaluate_binary_predictions


DEFAULT_THRESHOLD = 0.5
DEFAULT_THRESHOLD_GRID = tuple(round(step / 10, 1) for step in range(1, 10))
THRESHOLD_SELECTION_CRITERION = "maximize validation F1; ties prefer higher precision, then higher threshold"


class ThresholdSelectionError(ValueError):
    """Raised when validation cannot support an optimized threshold."""


def analyze_validation_thresholds(
    y_validation: Any,
    probabilities: Any,
    *,
    thresholds: Sequence[float] = DEFAULT_THRESHOLD_GRID,
) -> dict[str, Any]:
    """Select an operating threshold using validation labels and probabilities only.

    Both positive and negative validation classes are required. A single-class
    validation set raises ``ThresholdSelectionError`` rather than returning an
    optimized threshold that cannot be justified. Call
    ``unavailable_threshold_analysis`` to retain the default-0.5 reference for
    honest fallback reporting.
    """
    truth, scores, grid = _prepare_threshold_inputs(y_validation, probabilities, thresholds)
    _require_both_classes(truth)
    rows = _threshold_rows(truth, scores, grid)
    candidates = [row for row in rows if row["f1"] is not None]
    selected = max(candidates, key=lambda row: (row["f1"], row["precision"], row["threshold"]))
    return {
        "threshold_grid": grid,
        "criterion": THRESHOLD_SELECTION_CRITERION,
        "default_threshold": DEFAULT_THRESHOLD,
        "default_result": next(row for row in rows if row["threshold"] == DEFAULT_THRESHOLD),
        "selected_threshold": selected["threshold"],
        "selected_result": selected,
        "selection_unavailable_reason": None,
        "results": rows,
    }


def unavailable_threshold_analysis(
    y_validation: Any,
    probabilities: Any,
    *,
    reason: str,
    thresholds: Sequence[float] = DEFAULT_THRESHOLD_GRID,
) -> dict[str, Any]:
    """Return default-0.5 metrics and an explicit unavailable optimized threshold."""
    truth, scores, grid = _prepare_threshold_inputs(y_validation, probabilities, thresholds)
    rows = _threshold_rows(truth, scores, grid)
    return {
        "threshold_grid": grid,
        "criterion": THRESHOLD_SELECTION_CRITERION,
        "default_threshold": DEFAULT_THRESHOLD,
        "default_result": next(row for row in rows if row["threshold"] == DEFAULT_THRESHOLD),
        "selected_threshold": None,
        "selected_result": None,
        "selection_unavailable_reason": reason,
        "results": rows,
    }


def _prepare_threshold_inputs(
    y_validation: Any,
    probabilities: Any,
    thresholds: Sequence[float],
) -> tuple[np.ndarray, np.ndarray, list[float]]:
    truth = np.asarray(y_validation)
    scores = np.asarray(probabilities, dtype=float)
    if truth.ndim != 1 or scores.ndim != 1 or truth.shape != scores.shape:
        raise ValueError("validation labels and probabilities must be 1-D arrays with equal lengths")
    if not set(np.unique(truth)).issubset({0, 1}):
        raise ValueError("validation labels must contain only binary values 0 and 1")
    if not np.isfinite(scores).all() or np.any((scores < 0) | (scores > 1)):
        raise ValueError("threshold analysis requires finite probabilities in [0, 1]")
    grid = sorted({float(value) for value in thresholds} | {DEFAULT_THRESHOLD})
    if any(not np.isfinite(value) or value < 0 or value > 1 for value in grid):
        raise ValueError("thresholds must be finite values in [0, 1]")
    return truth.astype(np.int64), scores, grid


def _require_both_classes(truth: np.ndarray) -> None:
    classes = set(np.unique(truth).tolist())
    if classes != {0, 1}:
        found = "no classes" if not classes else ", ".join(str(value) for value in sorted(classes))
        raise ThresholdSelectionError(
            "Validation threshold selection requires both positive and negative classes; "
            f"found only class(es) {found}. Use default 0.5 as a reference and report the "
            "optimized threshold as unavailable."
        )


def _threshold_rows(truth: np.ndarray, scores: np.ndarray, grid: Sequence[float]) -> list[dict[str, Any]]:
    rows = []
    for threshold in grid:
        prediction = (scores >= threshold).astype(np.int64)
        metric = evaluate_binary_predictions(truth, prediction, scores=scores)
        rows.append({
            "threshold": threshold,
            "precision": metric["precision"],
            "recall": metric["recall"],
            "f1": metric["f1"],
            "confusion_matrix": metric["confusion_matrix"],
            "true_positives": metric["true_positives"],
            "true_negatives": metric["true_negatives"],
            "false_positives": metric["false_positives"],
            "false_negatives": metric["false_negatives"],
            "undefined_reasons": {
                key: value for key, value in metric["undefined_reasons"].items()
                if key in {"positive_precision", "positive_recall", "positive_f1"}
            },
        })
    return rows
