"""Exploratory analysis for an exported synthetic AgentTrace dataset."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def analyze_agenttrace(prefix_csv: Path, traces_jsonl: Path, output_directory: Path) -> dict[str, Any]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("EDA charts require matplotlib; install the declared project dependencies") from exc

    frame = pd.read_csv(prefix_csv)
    traces = [json.loads(line) for line in traces_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    output_directory.mkdir(parents=True, exist_ok=True)
    outcomes = Counter(trace["execution_status"] for trace in traces)
    failure_categories = Counter(trace.get("failure_category", "unknown") for trace in traces)
    event_counts = Counter(event["event_type"] for trace in traces for event in trace["events"])
    tool_selected = Counter(event["tool_name"] for trace in traces for event in trace["events"]
                            if event["event_type"] == "tool_selected" and event.get("tool_name"))
    tool_failures = Counter(event["tool_name"] for trace in traces for event in trace["events"]
                            if event["event_type"] == "tool_failed" and event.get("tool_name"))
    duration = pd.Series([trace.get("total_duration_ms") for trace in traces], dtype="float64")
    numeric_features = [column for column in frame.select_dtypes(include=[np.number]).columns
                        if column not in {"eventual_failure", "prefix_index"}]
    distributions: dict[str, Any] = {}
    for column in numeric_features:
        clean = frame[column].dropna().to_numpy(dtype=float)
        distributions[column] = {"count": int(clean.size), "mean": float(np.mean(clean)) if clean.size else None,
            "std": float(np.std(clean)) if clean.size else None,
            "min": float(np.min(clean)) if clean.size else None,
            "median": float(np.median(clean)) if clean.size else None,
            "max": float(np.max(clean)) if clean.size else None}
    positive = int((frame["eventual_failure"] == 1).sum())
    negative = int((frame["eventual_failure"] == 0).sum())
    def correlation(column: str) -> float | None:
        pair = frame[[column, "eventual_failure"]].dropna()
        if len(pair) < 2 or pair[column].nunique() < 2 or pair["eventual_failure"].nunique() < 2:
            return None
        value = float(np.corrcoef(pair[column].to_numpy(dtype=float),
                                  pair["eventual_failure"].to_numpy(dtype=float))[0, 1])
        return value if np.isfinite(value) else None
    summary: dict[str, Any] = {
        "dataset_provenance": "synthetic controlled executions; not production telemetry",
        "number_of_executions": len(traces), "number_of_events": sum(event_counts.values()),
        "number_of_prefix_samples": int(len(frame)),
        "execution_outcomes": dict(sorted(outcomes.items())),
        "failure_categories": dict(sorted(failure_categories.items())),
        "tool_selection_counts": dict(sorted(tool_selected.items())),
        "tool_failure_counts": dict(sorted(tool_failures.items())),
        "tool_failure_rates": {tool: (tool_failures[tool] / calls if calls else 0.0)
                               for tool, calls in sorted(tool_selected.items())},
        "event_frequency": dict(sorted(event_counts.items())),
        "execution_duration_ms": {"count": int(duration.count()), "mean": float(duration.mean()) if duration.count() else None,
            "median": float(duration.median()) if duration.count() else None,
            "min": float(duration.min()) if duration.count() else None,
            "max": float(duration.max()) if duration.count() else None},
        "prefix_length_distribution": {str(k): int(v) for k, v in sorted(frame["prefix_length"].value_counts().items())},
        "feature_distributions": distributions,
        "prefix_failure_label_distribution": {"eventual_failure": positive, "eventual_success": negative,
            "failure_fraction": positive / len(frame) if len(frame) else None},
        "feature_failure_correlations": {
            column: correlation(column) for column in numeric_features},
        "small_sample_warning": len(traces) < 1000,
    }
    (output_directory / "eda_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    _bar_plot(plt, outcomes, "Execution outcomes (synthetic)", output_directory / "execution_outcomes.png")
    _bar_plot(plt, failure_categories, "Failure categories (synthetic)", output_directory / "failure_categories.png")
    _bar_plot(plt, tool_selected, "Tool selections", output_directory / "tool_usage.png")
    _bar_plot(plt, event_counts, "Event frequency", output_directory / "event_frequency.png")
    fig, ax = plt.subplots(figsize=(7, 4))
    duration.dropna().plot(kind="hist", bins=20, ax=ax)
    ax.set(title="Execution duration (ms)", xlabel="duration_ms")
    fig.tight_layout(); fig.savefig(output_directory / "execution_duration.png", dpi=140); plt.close(fig)
    fig, ax = plt.subplots(figsize=(7, 4))
    frame["prefix_length"].plot(kind="hist", bins=max(1, min(20, int(frame["prefix_length"].max()))), ax=ax)
    ax.set(title="Prefix length distribution", xlabel="observed event count")
    fig.tight_layout(); fig.savefig(output_directory / "prefix_length.png", dpi=140); plt.close(fig)
    correlation = pd.DataFrame({name: [value] for name, value in summary["feature_failure_correlations"].items()}).T
    fig, ax = plt.subplots(figsize=(9, 4))
    if not correlation.empty:
        correlation.plot(kind="bar", legend=False, ax=ax)
    ax.set(title="Observed prefix feature correlation with eventual failure", ylabel="Pearson correlation")
    fig.tight_layout(); fig.savefig(output_directory / "feature_failure_relationships.png", dpi=140); plt.close(fig)
    return summary


def _bar_plot(plt: Any, counts: Counter, title: str, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 4))
    names, values = list(counts.keys()), list(counts.values())
    ax.bar(names, values)
    ax.set(title=title, ylabel="count")
    ax.tick_params(axis="x", rotation=30)
    fig.tight_layout(); fig.savefig(path, dpi=140); plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Explore exported AgentTrace CSV and JSONL files")
    parser.add_argument("--prefix-csv", type=Path, default=Path("data/generated/agenttrace-v1/agenttrace_prefixes.csv"))
    parser.add_argument("--traces-jsonl", type=Path, default=Path("data/generated/agenttrace-v1/agenttrace_traces.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("analysis/output/agenttrace-v1"))
    args = parser.parse_args()
    summary = analyze_agenttrace(args.prefix_csv, args.traces_jsonl, args.output)
    print(json.dumps({key: summary[key] for key in ("number_of_executions", "number_of_events", "number_of_prefix_samples", "execution_outcomes", "prefix_failure_label_distribution")}, indent=2))
    print(f"EDA outputs: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
