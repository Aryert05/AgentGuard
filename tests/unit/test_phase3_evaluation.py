import numpy as np
import pytest

from agentguard.ml.evaluation import evaluate_binary_predictions
from agentguard.ml.thresholds import (
    ThresholdSelectionError,
    analyze_validation_thresholds,
    unavailable_threshold_analysis,
)


def test_binary_metrics_match_controlled_confusion_matrix():
    result = evaluate_binary_predictions(
        [0, 1, 1, 0], [0, 1, 0, 1], scores=[0.1, 0.9, 0.4, 0.8],
    )
    assert result["accuracy"] == 0.5
    assert result["precision"] == 0.5
    assert result["recall"] == 0.5
    assert result["f1"] == 0.5
    assert result["roc_auc"] == 0.75
    assert result["average_precision"] == pytest.approx(5 / 6)
    assert result["confusion_matrix"]["matrix"] == [[1, 1], [1, 1]]
    assert (result["true_positives"], result["true_negatives"],
            result["false_positives"], result["false_negatives"]) == (1, 1, 1, 1)
    assert result["per_class"]["0"]["f1"] == 0.5
    assert result["per_class"]["1"]["f1"] == 0.5


def test_undefined_metrics_are_null_with_reasons_for_single_class_and_missing_scores():
    single_class = evaluate_binary_predictions([0, 0], [0, 0], scores=[0.1, 0.2])
    assert single_class["precision"] is None
    assert single_class["recall"] is None
    assert single_class["f1"] is None
    assert single_class["roc_auc"] is None
    assert single_class["average_precision"] is None
    assert "both classes" in single_class["undefined_reasons"]["roc_auc"]

    missing_scores = evaluate_binary_predictions([0, 1], [0, 1])
    assert missing_scores["roc_auc"] is None
    assert missing_scores["average_precision"] is None
    assert "unavailable" in missing_scores["undefined_reasons"]["roc_auc"]


def test_threshold_analysis_includes_default_and_selects_validation_f1_only():
    result = analyze_validation_thresholds([0, 1, 1, 0], [0.1, 0.9, 0.4, 0.8])
    assert result["default_threshold"] == 0.5
    assert result["default_result"]["threshold"] == 0.5
    assert 0.5 in result["threshold_grid"]
    assert result["selected_threshold"] == 0.4
    assert result["selected_result"]["f1"] == 0.8
    assert "validation F1" in result["criterion"]
    assert all("confusion_matrix" in row for row in result["results"])


@pytest.mark.parametrize(
    "labels, probabilities",
    [([0, 0], [0.1, 0.2]), ([1, 1], [0.8, 0.9])],
)
def test_threshold_analysis_rejects_single_class_validation(labels, probabilities):
    with pytest.raises(ThresholdSelectionError, match="both positive and negative"):
        analyze_validation_thresholds(labels, probabilities)


def test_unavailable_threshold_analysis_retains_default_reference():
    result = unavailable_threshold_analysis(
        [1, 1], [0.8, 0.9], reason="single-class validation",
    )
    assert result["selected_threshold"] is None
    assert result["selected_result"] is None
    assert result["selection_unavailable_reason"] == "single-class validation"
    assert result["default_result"]["threshold"] == 0.5
    assert result["default_result"]["f1"] is not None


def test_thresholds_reject_non_probability_values():
    with pytest.raises(ValueError, match=r"probabilities in \[0, 1\]"):
        analyze_validation_thresholds([0, 1], [-1.0, 2.0])
