"""Binary classification metrics with explicit undefined-value reporting."""

from __future__ import annotations

from typing import Any

import numpy as np
try:
    from sklearn.metrics import average_precision_score, roc_auc_score
except ModuleNotFoundError as exc:
    if exc.name == "sklearn":
        raise ImportError("AgentGuard ML evaluation requires scikit-learn; install with `pip install -e '.[dev,ml]'`") from exc
    raise


def evaluate_binary_predictions(
    y_true: Any,
    y_pred: Any,
    *,
    scores: Any | None = None,
    score_kind: str = "probability",
) -> dict[str, Any]:
    """Compute positive-class metrics and fixed-label confusion counts.

    Undefined ratios and ranking metrics are represented as ``None`` with a
    reason. Scores may be probabilities or another continuous ranking score.
    """
    truth = np.asarray(y_true)
    predicted = np.asarray(y_pred)
    if truth.ndim != 1 or predicted.ndim != 1 or truth.shape != predicted.shape:
        raise ValueError("y_true and y_pred must be one-dimensional arrays with equal lengths")
    if not set(np.unique(truth)).issubset({0, 1}) or not set(np.unique(predicted)).issubset({0, 1}):
        raise ValueError("y_true and y_pred must contain only binary labels 0 and 1")

    tn = int(np.sum((truth == 0) & (predicted == 0)))
    fp = int(np.sum((truth == 0) & (predicted == 1)))
    fn = int(np.sum((truth == 1) & (predicted == 0)))
    tp = int(np.sum((truth == 1) & (predicted == 1)))
    reasons: dict[str, str] = {}

    accuracy = float((tp + tn) / len(truth)) if len(truth) else None
    if accuracy is None:
        reasons["accuracy"] = "no samples are available"
    positive = _class_metrics(tp, fp, fn, "positive class")
    negative = _class_metrics(tn, fn, fp, "negative class")
    for prefix, result in (("positive", positive), ("negative", negative)):
        for metric, reason in result.pop("undefined_reasons").items():
            reasons[f"{prefix}_{metric}"] = reason

    score_values: np.ndarray | None = None
    score_error: str | None = None
    if scores is None:
        score_error = "continuous score output is unavailable"
    else:
        score_values = np.asarray(scores, dtype=float)
        if score_values.ndim != 1 or score_values.shape != truth.shape:
            raise ValueError("scores must be a one-dimensional array matching y_true")
        if not np.isfinite(score_values).all():
            score_error = "scores contain non-finite values"
            score_values = None

    roc_auc = average_precision = None
    if len(np.unique(truth)) < 2:
        reasons["roc_auc"] = "ROC-AUC requires both classes in y_true"
        reasons["average_precision"] = "Average Precision requires both classes in y_true"
    elif score_values is None:
        reasons["roc_auc"] = score_error or "continuous score output is unavailable"
        reasons["average_precision"] = score_error or "continuous score output is unavailable"
    else:
        roc_auc = float(roc_auc_score(truth, score_values))
        average_precision = float(average_precision_score(truth, score_values))

    return {
        "sample_count": int(len(truth)),
        "accuracy": accuracy,
        "precision": positive["precision"],
        "recall": positive["recall"],
        "f1": positive["f1"],
        "roc_auc": roc_auc,
        "average_precision": average_precision,
        "score_kind": score_kind if score_values is not None else None,
        "confusion_matrix": {"labels": [0, 1], "matrix": [[tn, fp], [fn, tp]]},
        "true_positives": tp,
        "true_negatives": tn,
        "false_positives": fp,
        "false_negatives": fn,
        "per_class": {"0": negative, "1": positive},
        "undefined_reasons": reasons,
    }


def _class_metrics(tp: int, fp: int, fn: int, class_name: str) -> dict[str, Any]:
    reasons: dict[str, str] = {}
    precision = float(tp / (tp + fp)) if tp + fp else None
    recall = float(tp / (tp + fn)) if tp + fn else None
    if precision is None:
        reasons["precision"] = f"no {class_name} predictions"
    if recall is None:
        reasons["recall"] = f"no true {class_name} samples"
    if precision is None or recall is None:
        f1 = None
        reasons["f1"] = f"precision or recall is undefined for the {class_name}"
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = float(2 * precision * recall / (precision + recall))
    return {"precision": precision, "recall": recall, "f1": f1, "support": int(tp + fn),
            "undefined_reasons": reasons}
