"""Deterministic CSV and JSONL export for AgentTrace."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentguard.dataset.generator import GeneratedDataset
from agentguard.processing.features import FEATURE_DICTIONARY


def export_agenttrace(dataset: GeneratedDataset, output_directory: Path) -> dict[str, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    paths = {"prefix_csv": output_directory / "agenttrace_prefixes.csv",
             "traces_jsonl": output_directory / "agenttrace_traces.jsonl",
             "manifest": output_directory / "manifest.json",
             "feature_dictionary": output_directory / "feature_dictionary.json"}
    dataset.samples.to_csv(paths["prefix_csv"], index=False, lineterminator="\n", float_format="%.6f")
    with paths["traces_jsonl"].open("w", encoding="utf-8", newline="\n") as stream:
        for trace in dataset.traces:
            stream.write(json.dumps(trace, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
    paths["manifest"].write_text(dataset.manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    paths["feature_dictionary"].write_text(json.dumps(FEATURE_DICTIONARY, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return paths
