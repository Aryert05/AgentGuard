import json

import pandas as pd
import pytest

from agentguard.ml.data import load_agenttrace_csv, validate_agenttrace_frame
from agentguard.ml.splitting import SplitError, build_grouped_split, group_composition


DATASET = "data/generated/agenttrace-v1/agenttrace_prefixes.csv"


@pytest.fixture(scope="module")
def dataset():
    return load_agenttrace_csv(DATASET)


@pytest.mark.parametrize("group_column", ["execution_id", "task_template_id"])
def test_grouped_partitions_have_no_group_overlap_and_cover_every_row(dataset, group_column):
    split = build_grouped_split(dataset, group_column=group_column, seed=2026)
    group_sets = [set(split.partitions[name].group_ids) for name in ("train", "validation", "test")]
    assert not (group_sets[0] & group_sets[1])
    assert not (group_sets[0] & group_sets[2])
    assert not (group_sets[1] & group_sets[2])
    assert sorted(index for part in split.partitions.values() for index in part.row_indices) == list(range(len(dataset)))
    assert all(set(part.class_counts) == {0, 1} for part in split.partitions.values())
    for part in split.partitions.values():
        actual = set(dataset.metadata.iloc[list(part.row_indices)][group_column].astype(str))
        assert actual == set(part.group_ids)


def test_execution_split_is_reproducible_and_approximately_60_20_20(dataset):
    first = build_grouped_split(dataset, group_column="execution_id", seed=117)
    second = build_grouped_split(dataset, group_column="execution_id", seed=117)
    assert first.to_dict() == second.to_dict()
    assert [first.partitions[name].group_count for name in ("train", "validation", "test")] == [60, 20, 20]
    assert all(sum(first.partitions[name].class_counts.values()) == first.partitions[name].row_count
               for name in ("train", "validation", "test"))


def test_template_split_preflights_ten_pure_class_groups_and_uses_6_2_2(dataset, tmp_path):
    composition = group_composition(dataset, "task_template_id")
    assert len(composition) == 10
    assert all(len(group["class_counts"]) == 1 for group in composition)
    split = build_grouped_split(dataset, group_column="task_template_id", seed=2026)
    assert [split.partitions[name].group_count for name in ("train", "validation", "test")] == [6, 2, 2]
    assert all(set(split.partitions[name].class_counts) == {0, 1} for name in ("train", "validation", "test"))
    path = split.write_json(tmp_path / "split.json")
    saved = json.loads(path.read_text())
    assert saved == split.to_dict()
    assert saved["dataset_audit"]["source_sha256"] == dataset.audit.source_sha256


def test_grouped_split_repeats_exactly_with_same_seed(dataset):
    first = build_grouped_split(dataset, group_column="task_template_id", seed=9)
    second = build_grouped_split(dataset, group_column="task_template_id", seed=9)
    assert first.to_dict() == second.to_dict()


def test_grouped_split_rejects_unknown_group_column(dataset):
    with pytest.raises(SplitError, match="group_column"):
        build_grouped_split(dataset, group_column="prefix_id")


def test_insufficient_class_group_coverage_fails_clearly(dataset):
    source = pd.concat([dataset.metadata, dataset.features, dataset.target], axis=1)
    positive_id = source.loc[source["eventual_failure"].eq(1), "execution_id"].iloc[0]
    positive_rows = source.loc[source["execution_id"].eq(positive_id)]
    negative_source = source.loc[source["eventual_failure"].eq(0)]
    negative_rows = negative_source.groupby("execution_id", sort=False).head(1).head(2)
    frame = pd.concat([positive_rows, negative_rows], ignore_index=True)
    validated = validate_agenttrace_frame(frame)
    with pytest.raises(SplitError, match="at least three are required"):
        build_grouped_split(validated, group_column="execution_id", seed=1)


def test_binding_rejects_one_execution_split_across_partitions(dataset):
    from dataclasses import replace

    from agentguard.ml.preprocessing import fit_transform_partitions

    split = build_grouped_split(dataset, group_column="execution_id", seed=31)
    partitions = dict(split.partitions)
    train = partitions["train"]
    test = partitions["test"]
    train_indices = list(train.row_indices)
    test_indices = list(test.row_indices)
    train_groups = dataset.metadata.iloc[train_indices]["execution_id"].astype(str)
    moved_group = next(group for group, count in train_groups.value_counts().items() if count > 1)
    moved_index = next(index for index in train_indices if str(dataset.metadata.iloc[index]["execution_id"]) == moved_group)
    train_indices.remove(moved_index)
    test_indices.append(moved_index)
    test_groups = tuple(sorted({*test.group_ids, moved_group}))
    train_counts = dataset.target.iloc[train_indices].value_counts().sort_index().to_dict()
    test_counts = dataset.target.iloc[test_indices].value_counts().sort_index().to_dict()
    partitions["train"] = replace(
        train,
        row_indices=tuple(train_indices),
        class_counts={int(key): int(value) for key, value in train_counts.items()},
    )
    partitions["test"] = replace(
        test,
        row_indices=tuple(test_indices),
        group_ids=test_groups,
        class_counts={int(key): int(value) for key, value in test_counts.items()},
    )
    corrupted = replace(split, partitions=partitions)
    with pytest.raises(SplitError, match="groups cross partitions"):
        fit_transform_partitions(dataset, corrupted)


def test_binding_rejects_coherent_whole_group_reassignment(dataset):
    from dataclasses import replace

    from agentguard.ml.preprocessing import fit_transform_partitions

    split = build_grouped_split(dataset, group_column="execution_id", seed=47)
    groups_by_partition = {
        name: list(split.partitions[name].group_ids)
        for name in ("train", "validation", "test")
    }
    group_class = {}
    for name in groups_by_partition:
        for group_id in groups_by_partition[name]:
            rows = dataset.metadata["execution_id"].astype(str).eq(group_id)
            group_class[group_id] = int(dataset.target.loc[rows].iloc[0])

    train_group = groups_by_partition["train"][0]
    matching_validation_group = next(
        group_id for group_id in groups_by_partition["validation"]
        if group_class[group_id] == group_class[train_group]
    )
    groups_by_partition["train"].remove(train_group)
    groups_by_partition["validation"].remove(matching_validation_group)
    groups_by_partition["train"].append(matching_validation_group)
    groups_by_partition["validation"].append(train_group)

    partitions = {}
    group_to_partition = {
        group_id: name for name, group_ids in groups_by_partition.items() for group_id in group_ids
    }
    for name, original in split.partitions.items():
        indices = tuple(
            index for index in range(len(dataset))
            if group_to_partition[str(dataset.metadata.iloc[index]["execution_id"])] == name
        )
        class_counts = dataset.target.iloc[list(indices)].value_counts().sort_index().to_dict()
        partitions[name] = replace(
            original,
            row_indices=indices,
            group_ids=tuple(sorted(groups_by_partition[name])),
            class_counts={int(label): int(count) for label, count in class_counts.items()},
        )

    tampered = replace(split, partitions=partitions)
    with pytest.raises(SplitError, match="deterministic split"):
        fit_transform_partitions(dataset, tampered)
