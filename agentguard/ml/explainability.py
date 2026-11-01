"""Non-causal model explanations for fitted baseline classifiers."""

from __future__ import annotations

from typing import Any

import numpy as np
try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.inspection import permutation_importance
    from sklearn.linear_model import LogisticRegression
except ModuleNotFoundError as exc:
    if exc.name == "sklearn":
        raise ImportError("AgentGuard ML explainability requires scikit-learn; install with `pip install -e '.[dev,ml]'`") from exc
    raise

from agentguard.ml.data import AgentTraceDataset
from agentguard.ml.splitting import GroupedSplit, validate_split_binding
from agentguard.ml.training import FittedModelBundle, split_assignment_fingerprint
from agentguard.processing.features import MODEL_FEATURE_COLUMNS


def intrinsic_feature_importance(bundle: FittedModelBundle) -> dict[str, Any]:
    """Return fitted Logistic Regression coefficients or Random Forest importances."""
    estimator = bundle.estimator
    if isinstance(estimator, LogisticRegression):
        coefficients = np.asarray(estimator.coef_)
        if coefficients.shape[0] != 1:
            raise ValueError("Expected a binary LogisticRegression coefficient vector")
        values = coefficients[0]
        method = "logistic_regression_signed_coefficients"
    elif isinstance(estimator, RandomForestClassifier):
        values = np.asarray(estimator.feature_importances_)
        method = "random_forest_impurity_importance"
    else:
        return {
            "method": None,
            "values": None,
            "reason": "Use the validation-only permutation importance helper for this estimator.",
        }
    if len(values) != len(bundle.feature_names):
        raise ValueError("Estimator importance shape does not match transformed feature names")
    return {
        "method": method,
        "analysis_partition": "training-fitted model parameters",
        "causal_interpretation": False,
        "values": {name: float(value) for name, value in zip(bundle.feature_names, values)},
    }


def validation_permutation_importance(
    bundle: FittedModelBundle,
    dataset: AgentTraceDataset,
    split: GroupedSplit,
    *,
    n_repeats: int = 10,
    random_state: int = 2026,
) -> dict[str, Any]:
    """Measure validation-only permutation importance using average precision.

    The test partition is not transformed or scored here. This diagnostic is
    descriptive, can be unstable on the small validation set, and is not causal.
    """
    if split.strategy != "execution_held_out":
        raise ValueError("Permutation importance for the primary experiment requires execution-held-out data")
    validate_split_binding(dataset, split)
    if bundle.dataset_fingerprint != dataset.audit.dataset_fingerprint:
        raise ValueError("Fitted model bundle belongs to a different dataset")
    if bundle.split_fingerprint != split_assignment_fingerprint(split):
        raise ValueError("Fitted model bundle belongs to a different split assignment")
    validation_indices = list(split.partitions["validation"].row_indices)
    raw_validation = dataset.features.iloc[validation_indices].copy()
    if list(raw_validation.columns) != list(MODEL_FEATURE_COLUMNS):
        raise ValueError("Validation features do not match the explicit model allow-list")
    validation_features = bundle.transformer.transform(raw_validation)
    validation_labels = dataset.target.iloc[validation_indices].to_numpy(dtype=np.int64)
    if set(np.unique(validation_labels)) != {0, 1}:
        raise ValueError("Validation permutation importance requires both target classes")
    result = permutation_importance(
        bundle.estimator,
        validation_features,
        validation_labels,
        scoring="average_precision",
        n_repeats=n_repeats,
        random_state=random_state,
    )
    return {
        "method": "permutation_importance",
        "analysis_partition": "validation",
        "scoring": "average_precision",
        "n_repeats": n_repeats,
        "random_state": random_state,
        "causal_interpretation": False,
        "warning": "Validation-set importance is preliminary and may be unstable; it is not causal.",
        "values": {
            name: {"mean": float(mean), "standard_deviation": float(std)}
            for name, mean, std in zip(bundle.feature_names, result.importances_mean, result.importances_std)
        },
    }
