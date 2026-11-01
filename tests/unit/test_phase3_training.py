import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentguard.ml.data import EXCLUDED_MODEL_COLUMNS, load_agenttrace_csv, validate_agenttrace_frame
from agentguard.ml.explainability import intrinsic_feature_importance, validation_permutation_importance
from agentguard.ml.models import MODEL_NAMES
from agentguard.ml.preprocessing import build_preprocessing_pipeline
from agentguard.ml.splitting import SplitError, build_grouped_split
from agentguard.ml.training import (
    run_supervised_experiments,
    run_template_held_out_experiment,
    split_assignment_fingerprint,
)
from agentguard.processing.features import MODEL_FEATURE_COLUMNS


DATASET = Path("data/generated/agenttrace-v1/agenttrace_prefixes.csv")
BOOTSTRAP_ITERATIONS = 25


@pytest.fixture(scope="module")
def dataset_and_split():
    dataset = load_agenttrace_csv(DATASET)
    split = build_grouped_split(dataset, group_column="execution_id", seed=2026)
    return dataset, split


def test_all_supervised_baselines_share_one_split_and_only_report_validation(dataset_and_split):
    dataset, split = dataset_and_split
    run = run_supervised_experiments(dataset, split, random_state=17, bootstrap_iterations=BOOTSTRAP_ITERATIONS)
    assert tuple(run.bundles) == MODEL_NAMES
    assert run.results["split_assignment_fingerprint"] == split_assignment_fingerprint(split)
    assert run.results["split_strategy"] == "execution_held_out"
    assert run.results["feature_names"] == list(MODEL_FEATURE_COLUMNS)
    assert len(run.results["feature_names"]) == 18
    assert not set(run.results["feature_names"]) & set(EXCLUDED_MODEL_COLUMNS)
    assert len(run.results["preprocessed_feature_names"]) == 23
    assert run.results["target_name"] == "eventual_failure"
    assert run.results["split_groups"]["train"]["group_ids"] == list(split.partitions["train"].group_ids)
    assert run.results["split_groups"]["validation"]["group_ids"] == list(split.partitions["validation"].group_ids)
    assert run.results["split_groups"]["test"]["group_ids"] == list(split.partitions["test"].group_ids)
    assert run.results["test_metrics"] is None
    assert "test_metrics" not in run.results["models"]["dummy"]
    assert run.results["experiment_configuration"]["bootstrap_iterations"] == BOOTSTRAP_ITERATIONS
    assert run.results["analysis_configuration"]["bootstrap_resampling_unit"] == "complete execution_id groups, not rows"
    assert run.results["split_provenance"]["canonical_method"] == "deterministic_reconstruction"
    assert run.results["split_provenance"]["approved_split_file_matches"] is True
    for name, bundle in run.bundles.items():
        result = run.results["models"][name]
        assert result["validation_sample_count"] == split.partitions["validation"].row_count
        assert result["training_seconds"] >= 0
        assert result["validation_batch_inference_seconds"] >= 0
        assert result["selected_threshold"] in result["threshold_analysis"]["threshold_grid"]
        analysis = result["validation_analysis_at_selected_threshold"]
        assert analysis["group_column"] == "execution_id"
        assert analysis["group_balanced"]["group_count"] == split.partitions["validation"].group_count
        assert analysis["group_bootstrap"]["n_bootstrap"] == BOOTSTRAP_ITERATIONS
        assert analysis["group_bootstrap"]["resampling_unit"] == "complete groups, not individual prefix rows"
        assert len(analysis["group_balanced"]["group_outcomes"]) == analysis["group_balanced"]["group_count"]
        assert analysis["prefix_length_strata"]["bins"]
        assert analysis["observation_time_strata"]["bins"]
        assert "execution_balanced" not in analysis  # generic group-balanced key is intentional
        assert bundle.estimator.n_features_in_ == len(bundle.feature_names) == 23
        scores = bundle.predict_scores(dataset.features.iloc[list(split.partitions["validation"].row_indices)])
        predictions = bundle.predict(dataset.features.iloc[list(split.partitions["validation"].row_indices)])
        assert scores.shape == predictions.shape == (split.partitions["validation"].row_count,)
        assert np.isfinite(scores).all()
        assert set(np.unique(predictions)).issubset({0, 1})


def test_training_rejects_changed_or_mismatched_splits(dataset_and_split):
    dataset, split = dataset_and_split
    with pytest.raises(SplitError, match="deterministic split"):
        run_supervised_experiments(dataset, replace(split, seed=split.seed + 1), model_names=("dummy",), bootstrap_iterations=BOOTSTRAP_ITERATIONS)
    template_split = build_grouped_split(dataset, group_column="task_template_id", seed=2026)
    with pytest.raises(SplitError, match="execution_held_out"):
        run_supervised_experiments(dataset, template_split, model_names=("dummy",), bootstrap_iterations=BOOTSTRAP_ITERATIONS)
    with pytest.raises(SplitError, match="task_template_held_out"):
        run_template_held_out_experiment(dataset, split, model_names=("dummy",), bootstrap_iterations=BOOTSTRAP_ITERATIONS)


def test_single_class_validation_reports_undefined_metrics_without_selecting_threshold():
    full_dataset = load_agenttrace_csv(DATASET)
    frame = pd.concat([full_dataset.metadata, full_dataset.features, full_dataset.target], axis=1)
    positive_execution = frame.loc[frame.eventual_failure.eq(1), "execution_id"].drop_duplicates().iloc[0]
    negative_executions = frame.loc[frame.eventual_failure.eq(0), "execution_id"].drop_duplicates().iloc[:4]
    keep = {positive_execution, *negative_executions}
    small_dataset = validate_agenttrace_frame(frame.loc[frame.execution_id.isin(keep)].reset_index(drop=True))
    split = build_grouped_split(
        small_dataset, group_column="execution_id", seed=4, require_each_class=False,
    )
    validation_labels = small_dataset.target.iloc[list(split.partitions["validation"].row_indices)]
    assert set(validation_labels.unique()) == {0}

    run = run_supervised_experiments(small_dataset, split, model_names=("dummy",), bootstrap_iterations=BOOTSTRAP_ITERATIONS)
    result = run.results["models"]["dummy"]
    assert result["selected_threshold"] is None
    assert result["inference_threshold"] == 0.5
    assert result["inference_threshold_fallback_reason"]
    assert result["validation_metrics_at_selected_threshold"] is None
    assert result["validation_metrics_at_default_threshold"]["roc_auc"] is None


def test_training_preprocessing_statistics_are_training_only(dataset_and_split):
    _, original_split = dataset_and_split
    frame = pd.read_csv(DATASET)
    validation_row = original_split.partitions["validation"].row_indices[0]
    frame.loc[validation_row, "elapsed_time_ms"] = 1_000_000_000_000.0
    dataset = validate_agenttrace_frame(frame)
    split = build_grouped_split(dataset, group_column="execution_id", seed=2026)
    run = run_supervised_experiments(dataset, split, model_names=("logistic_regression",), bootstrap_iterations=BOOTSTRAP_ITERATIONS)
    scaler = run.bundles["logistic_regression"].transformer.named_steps["scaler"]
    train = dataset.features.iloc[list(split.partitions["train"].row_indices)]
    expected_transform = build_preprocessing_pipeline(scale=False)
    expected_train = expected_transform.fit_transform(train)
    expected_mean = expected_train.mean(axis=0)
    expected_scale = expected_train.std(axis=0)
    expected_scale[expected_scale == 0] = 1.0
    np.testing.assert_allclose(scaler.mean_, expected_mean, rtol=0, atol=1e-12)
    np.testing.assert_allclose(scaler.scale_, expected_scale, rtol=0, atol=1e-12)


def test_results_are_json_safe_and_include_reproducibility_metadata(dataset_and_split, tmp_path):
    dataset, split = dataset_and_split
    run = run_supervised_experiments(dataset, split, model_names=("dummy",), random_state=9, bootstrap_iterations=BOOTSTRAP_ITERATIONS)
    metadata = run.results
    for field in ("dataset_fingerprint", "split_assignment_fingerprint", "split_seed", "model_random_state",
                  "split_groups", "feature_names", "target_name", "created_at_utc", "git_commit",
                  "git_branch", "worktree_dirty", "source_file_hashes", "code_snapshot_sha256",
                  "split_provenance", "experiment_configuration", "analysis_configuration",
                  "versions", "preprocessing"):
        assert field in metadata
    assert metadata["versions"]["numpy"]
    assert metadata["versions"]["scikit_learn"]
    assert metadata["versions"]["python"]
    assert metadata["versions"]["split_bit_generator"]
    assert metadata["source_file_hashes"]["agentguard/ml/training.py"]
    assert len(metadata["source_file_hashes"]) >= 10
    assert len(metadata["code_snapshot_sha256"]) == 64
    path = run.write_json(tmp_path / "results.json")
    saved = json.loads(path.read_text())
    assert saved["split_assignment_fingerprint"] == split_assignment_fingerprint(split)
    assert saved["test_metrics"] is None
    sidecar_path = path.with_name("results.sha256.json")
    sidecar = json.loads(sidecar_path.read_text())
    assert sidecar["artifact_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert sidecar["artifact"] == path.as_posix()
    assert sidecar["artifact_size_bytes"] == path.stat().st_size


def test_model_bundle_requires_exact_features(dataset_and_split):
    dataset, split = dataset_and_split
    bundle = run_supervised_experiments(dataset, split, model_names=("dummy",), bootstrap_iterations=BOOTSTRAP_ITERATIONS).bundles["dummy"]
    with pytest.raises(ValueError, match="exact 18-column"):
        bundle.predict_scores(dataset.metadata.iloc[:3])


def test_seeded_model_training_repeats_validation_predictions(dataset_and_split):
    dataset, split = dataset_and_split
    first = run_supervised_experiments(dataset, split, model_names=("random_forest",), random_state=81, bootstrap_iterations=BOOTSTRAP_ITERATIONS)
    second = run_supervised_experiments(dataset, split, model_names=("random_forest",), random_state=81, bootstrap_iterations=BOOTSTRAP_ITERATIONS)
    validation = dataset.features.iloc[list(split.partitions["validation"].row_indices)]
    np.testing.assert_array_equal(
        first.bundles["random_forest"].predict_scores(validation),
        second.bundles["random_forest"].predict_scores(validation),
    )


def test_template_held_out_experiment_is_separate_exploratory_and_never_scores_test(dataset_and_split):
    dataset, _ = dataset_and_split
    template_split = build_grouped_split(dataset, group_column="task_template_id", seed=2026)
    run = run_template_held_out_experiment(
        dataset, template_split, model_names=("dummy", "logistic_regression"),
        bootstrap_iterations=BOOTSTRAP_ITERATIONS,
    )
    assert run.results["exploratory"] is True
    assert run.results["split_strategy"] == "task_template_held_out"
    assert run.results["split_group_column"] == "task_template_id"
    assert run.results["split_groups"]["train"]["group_count"] == 6
    assert run.results["split_groups"]["validation"]["group_count"] == 2
    assert run.results["split_groups"]["test"]["group_count"] == 2
    assert set(run.results["split_groups"]["validation"]["class_counts"]) == {"0", "1"}
    assert run.results["template_experiment"]["class_pure_templates"] is True
    assert run.results["template_experiment"]["test_template_count"] == 2
    assert run.results["test_metrics"] is None
    assert run.results["split_provenance"]["approved_split_file_matches"] is True
    for name, result in run.results["models"].items():
        assert "test_metrics" not in result
        analysis = result["validation_analysis_at_default_threshold"]
        assert analysis["group_column"] == "task_template_id"
        assert analysis["group_balanced"]["group_count"] == 2
        assert analysis["group_bootstrap"]["group_count"] == 2
        assert analysis["group_bootstrap"]["replicate_class_composition"]["both_classes"] >= 0
        assert len(analysis["group_balanced"]["group_outcomes"]) == 2

def test_explainability_uses_training_parameters_or_validation_only(dataset_and_split):
    dataset, split = dataset_and_split
    run = run_supervised_experiments(dataset, split, model_names=("logistic_regression", "random_forest", "hist_gradient_boosting"), bootstrap_iterations=BOOTSTRAP_ITERATIONS)
    logistic = intrinsic_feature_importance(run.bundles["logistic_regression"])
    forest = intrinsic_feature_importance(run.bundles["random_forest"])
    hgb = intrinsic_feature_importance(run.bundles["hist_gradient_boosting"])
    assert logistic["analysis_partition"] == "training-fitted model parameters"
    assert len(logistic["values"]) == 23
    assert forest["method"] == "random_forest_impurity_importance"
    assert len(forest["values"]) == 23
    assert hgb["values"] is None
    permutation = validation_permutation_importance(
        run.bundles["hist_gradient_boosting"], dataset, split, n_repeats=2,
    )
    assert permutation["analysis_partition"] == "validation"
    assert permutation["causal_interpretation"] is False
    assert len(permutation["values"]) == 23
