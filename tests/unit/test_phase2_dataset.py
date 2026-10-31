import json

import pytest

from agentguard.dataset.exporter import export_agenttrace
from agentguard.dataset.generator import DATASET_COLUMNS, generate_agenttrace
from agentguard.dataset.labels import execution_labels
from agentguard.processing.features import FEATURE_COLUMNS, FEATURE_DICTIONARY, model_feature_matrix
from agentguard.schemas.events import EventType, ExecutionEvent


def test_dataset_generation_is_reproducible_and_schema_stable():
    first = generate_agenttrace(14, seed=913)
    second = generate_agenttrace(14, seed=913)
    assert first.samples.equals(second.samples)
    assert first.traces == second.traces
    assert first.samples.columns.tolist() == DATASET_COLUMNS
    assert first.manifest.number_of_executions == 14
    assert first.manifest.number_of_events == sum(len(trace["events"]) for trace in first.traces)
    assert first.manifest.number_of_samples == len(first.samples)
    assert all(first.samples["synthetic_data"])
    assert len({item["execution_id"] for item in first.executions}) == 14
    assert "unique_tools_used" not in FEATURE_COLUMNS
    assert {item["name"] for item in FEATURE_DICTIONARY} == set(FEATURE_COLUMNS)


def test_model_feature_matrix_excludes_ids_and_all_target_columns():
    generated = generate_agenttrace(2, seed=91)
    matrix = model_feature_matrix(generated.samples)
    assert matrix.columns.tolist() == FEATURE_COLUMNS
    assert not ({"execution_id", "task_id", "task_template_id", "prefix_id", "eventual_failure",
                 "outcome_label", "failure_category", "synthetic_data"} & set(matrix.columns))


def test_prefixes_exclude_terminal_events_and_labels_use_final_outcome():
    generated = generate_agenttrace(1, seed=22, distribution={"timeout": 1.0})
    trace = generated.traces[0]
    terminal_types = {EventType.EXECUTION_COMPLETED.value, EventType.EXECUTION_FAILED.value}
    assert trace["events"][-1]["event_type"] in terminal_types
    assert generated.samples["eventual_failure"].eq(1).all()
    assert generated.samples["outcome_label"].eq("failed").all()
    assert generated.samples["failure_category"].eq("timeout").all()
    assert generated.samples["prefix_length"].max() < len(trace["events"])


def test_partial_execution_is_positive_failure_target_with_separate_outcome():
    generated = generate_agenttrace(1, seed=32, distribution={"partial": 1.0})
    assert generated.executions[0]["execution_status"] == "partially_completed"
    assert generated.samples["eventual_failure"].eq(1).all()
    assert generated.samples["outcome_label"].eq("partially_completed").all()
    assert generated.samples["failure_category"].eq("partial_tool_error").all()


def test_exported_csv_and_trace_jsonl_are_regenerable(tmp_path):
    generated = generate_agenttrace(8, seed=61)
    paths_a = export_agenttrace(generated, tmp_path / "a")
    paths_b = export_agenttrace(generate_agenttrace(8, seed=61), tmp_path / "b")
    assert paths_a["prefix_csv"].read_bytes() == paths_b["prefix_csv"].read_bytes()
    assert paths_a["traces_jsonl"].read_bytes() == paths_b["traces_jsonl"].read_bytes()
    manifest = json.loads(paths_a["manifest"].read_text())
    assert manifest["provenance"] == "synthetic_controlled_agentguard_runs"
    assert manifest["random_seed"] == 61
    assert json.loads(paths_a["feature_dictionary"].read_text())


def test_invalid_dataset_configuration_and_unknown_outcome_are_not_guessed():
    with pytest.raises(ValueError, match="number_of_executions"):
        generate_agenttrace(0)
    with pytest.raises(ValueError, match="distribution"):
        generate_agenttrace(2, distribution={"made_up_failure": 1.0})
    unknown = execution_labels("running", [])
    assert unknown["eventual_failure"] is None and unknown["outcome_label"] == "unknown"


def test_failure_label_mapping_uses_only_recorded_error_types():
    event = ExecutionEvent(execution_id="e", task_id="t", event_type=EventType.TOOL_FAILED,
        component="tool", status="failed", error_type="ConnectionError", error_message="Simulated tool unavailability")
    assert execution_labels("failed", [event])["failure_category"] == "tool_unavailable"
