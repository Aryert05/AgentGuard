import numpy as np
import pytest

from agentguard.ml.analysis import (
    PREFIX_LENGTH_BINS,
    cluster_bootstrap_indices,
    group_balanced_metrics,
    group_bootstrap,
    observation_time_strata,
    prefix_length_strata,
)


def test_group_balanced_metrics_prevent_execution_dominance():
    truth = [1, 1, 1, 0]
    predicted = [1, 1, 1, 1]
    groups = ["a", "a", "a", "b"]
    result = group_balanced_metrics(truth, predicted, groups)
    assert result["group_count"] == 2
    assert result["prefix_weight_range"] == pytest.approx([1 / 3, 1.0])
    assert result["group_balanced"]["true_positives"] == pytest.approx(1.0)
    assert result["group_balanced"]["false_positives"] == pytest.approx(1.0)
    assert result["group_balanced"]["precision"] == pytest.approx(0.5)
    assert result["group_balanced"]["recall"] == pytest.approx(1.0)
    assert result["group_balanced"]["f1"] == pytest.approx(2 / 3)
    assert result["row_weighted"]["precision"] == pytest.approx(0.75)
    assert result["row_weighted"]["f1"] == pytest.approx(6 / 7)
    outcomes = {item["execution_id"]: item for item in result["group_outcomes"]}
    assert outcomes["a"]["row_count"] == 3
    assert outcomes["a"]["predicted_positive_fraction"] == 1.0
    assert outcomes["b"]["row_count"] == 1
    assert outcomes["b"]["negative_rows"] == 1
    assert outcomes["b"]["error_rows"] == 1


def test_cluster_bootstrap_is_reproducible_and_resamples_complete_groups():
    groups = ["a", "a", "b", "b", "c"]
    first = cluster_bootstrap_indices(groups, n_bootstrap=12, seed=44)
    second = cluster_bootstrap_indices(groups, n_bootstrap=12, seed=44)
    assert first == second
    assert len(first) == 12
    for sample in first:
        selected = set(sample)
        assert (0 in selected) == (1 in selected)
        assert (2 in selected) == (3 in selected)
        assert selected.issubset(set(range(len(groups))))


def test_group_bootstrap_reports_group_resampling_and_invalid_replicates():
    truth = [1, 1, 0, 0, 1, 0]
    predicted = [1, 1, 0, 0, 1, 0]
    groups = ["a", "a", "b", "b", "c", "c"]
    result = group_bootstrap(truth, predicted, groups, n_bootstrap=30, seed=8)
    assert result["group_count"] == 3
    assert result["resampled_group_count_per_iteration"] == 3
    assert result["n_bootstrap"] == 30
    assert result["seed"] == 8
    assert result["group_balanced"]["interval"]["f1"]["valid_replicates"] > 0
    assert result["replicate_class_composition"]["both_classes"] > 0
    assert sum(result["replicate_class_composition"].values()) == 30
    assert result["exploratory"] is True


def test_prefix_and_time_strata_flag_empty_and_small_bins():
    truth = [1, 0, 1, 0]
    predicted = [1, 0, 1, 0]
    groups = ["a", "b", "c", "d"]
    lengths = [1, 1, 6, 6]
    prefix = prefix_length_strata(
        truth, predicted, groups, lengths, bins=PREFIX_LENGTH_BINS,
    )
    by_label = {item["bin"]["label"]: item for item in prefix["bins"]}
    assert by_label["1"]["row_count"] == 2
    assert by_label["1"]["insufficient_observations"] is True
    assert by_label["11+"]["row_count"] == 0
    assert by_label["11+"]["row_weighted"] is None
    assert by_label["11+"]["insufficient_observations"] is True

    elapsed = np.asarray([np.nan, 10.0, 75.0, 125.0])
    time = observation_time_strata(truth, predicted, groups, elapsed)
    time_by_label = {item["bin"]["label"]: item for item in time["bins"]}
    assert time["missing_elapsed_time_count"] == 1
    assert time_by_label["missing"]["row_count"] == 1
    assert time_by_label["missing"]["insufficient_observations"] is True
    assert time_by_label["0-25 ms"]["row_count"] == 1
    assert time_by_label["50-100 ms"]["row_count"] == 1
    assert time_by_label["100+ ms"]["row_count"] == 1
    assert time_by_label["25-50 ms"]["row_count"] == 0
    assert time_by_label["25-50 ms"]["insufficient_observations"] is True
