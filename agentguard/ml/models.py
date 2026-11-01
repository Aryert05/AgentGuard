"""Small, deterministic supervised baselines for AgentTrace."""

from __future__ import annotations

from typing import Any

try:
    from sklearn.dummy import DummyClassifier
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
except ModuleNotFoundError as exc:
    if exc.name == "sklearn":
        raise ImportError("AgentGuard ML models require scikit-learn; install with `pip install -e '.[dev,ml]'`") from exc
    raise


MODEL_NAMES = ("dummy", "logistic_regression", "random_forest", "hist_gradient_boosting")


def build_classifier(model_name: str, *, random_state: int = 2026) -> Any:
    """Return a conservative, reproducible classifier configuration.

    Balanced class weights give the minority completed class meaningful loss
    weight despite the 36/64 prefix-level class distribution. The dummy model
    remains an unweighted majority/prevalence baseline.
    """
    if model_name == "dummy":
        return DummyClassifier(strategy="prior")
    if model_name == "logistic_regression":
        return LogisticRegression(
            C=1.0,
            class_weight="balanced",
            max_iter=1000,
            random_state=random_state,
            solver="liblinear",
        )
    if model_name == "random_forest":
        return RandomForestClassifier(
            n_estimators=200,
            max_depth=4,
            min_samples_leaf=5,
            max_features="sqrt",
            class_weight="balanced_subsample",
            random_state=random_state,
            n_jobs=1,
        )
    if model_name == "hist_gradient_boosting":
        return HistGradientBoostingClassifier(
            learning_rate=0.05,
            max_iter=100,
            max_leaf_nodes=7,
            min_samples_leaf=10,
            l2_regularization=1.0,
            class_weight="balanced",
            random_state=random_state,
            early_stopping=False,
        )
    raise ValueError(f"Unknown model {model_name!r}; expected one of {MODEL_NAMES}")
