"""Run the execution-held-out or exploratory task-template-held-out validation baselines."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentguard.ml.analysis import DEFAULT_BOOTSTRAP_ITERATIONS, DEFAULT_BOOTSTRAP_SEED
from agentguard.ml.config import PreparationConfig
from agentguard.ml.data import load_agenttrace_csv
from agentguard.ml.models import MODEL_NAMES
from agentguard.ml.splitting import build_grouped_split
from agentguard.ml.training import (
    DEFAULT_RESULTS_PATH,
    DEFAULT_TEMPLATE_RESULTS_PATH,
    run_supervised_experiments,
    run_template_held_out_experiment,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=PreparationConfig.dataset_path)
    parser.add_argument("--seed", type=int, default=2026, help="Model random state; split seed remains in PreparationConfig")
    parser.add_argument(
        "--strategy",
        choices=("execution_held_out", "task_template_held_out"),
        default="execution_held_out",
        help="Primary execution-held-out or exploratory task-template-held-out validation experiment",
    )
    parser.add_argument(
        "--bootstrap-iterations",
        type=int,
        default=DEFAULT_BOOTSTRAP_ITERATIONS,
        help="Execution/template-group bootstrap iterations",
    )
    parser.add_argument(
        "--bootstrap-seed",
        type=int,
        default=DEFAULT_BOOTSTRAP_SEED,
        help="Recorded bootstrap random seed",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    config = PreparationConfig(dataset_path=args.dataset)
    dataset = load_agenttrace_csv(config.dataset_path)
    if args.strategy == "execution_held_out":
        group_column = "execution_id"
        output = args.output or DEFAULT_RESULTS_PATH
        split = build_grouped_split(dataset, group_column=group_column, config=config)
        run = run_supervised_experiments(
            dataset,
            split,
            model_names=MODEL_NAMES,
            random_state=args.seed,
            bootstrap_iterations=args.bootstrap_iterations,
            bootstrap_seed=args.bootstrap_seed,
        )
    else:
        group_column = "task_template_id"
        output = args.output or DEFAULT_TEMPLATE_RESULTS_PATH
        split = build_grouped_split(dataset, group_column=group_column, config=config)
        run = run_template_held_out_experiment(
            dataset,
            split,
            model_names=MODEL_NAMES,
            random_state=args.seed,
            bootstrap_iterations=args.bootstrap_iterations,
            bootstrap_seed=args.bootstrap_seed,
        )
    destination = run.write_json(output)
    summary = {
        name: {
            "default_threshold": result["threshold_analysis"]["default_threshold"],
            "default_f1": result["validation_metrics_at_default_threshold"]["f1"],
            "selected_threshold": result["selected_threshold"],
            "selected_f1": (
                result["validation_metrics_at_selected_threshold"]["f1"]
                if result["validation_metrics_at_selected_threshold"] is not None else None
            ),
        }
        for name, result in run.results["models"].items()
    }
    print(f"Validation-only {args.strategy} experiment results written to {destination}")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
