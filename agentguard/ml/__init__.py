"""Leakage-aware data preparation without making scikit-learn a base dependency."""

from importlib import import_module

from agentguard.ml.data import AgentTraceDataset, DatasetAudit, DatasetValidationError, load_agenttrace_csv
from agentguard.ml.splitting import GroupedSplit, SplitError, build_grouped_split, group_composition

_PREPROCESSING_EXPORTS = {"build_preprocessing_pipeline", "fit_transform_partitions"}

__all__ = [
    "AgentTraceDataset",
    "DatasetAudit",
    "DatasetValidationError",
    "GroupedSplit",
    "SplitError",
    "build_grouped_split",
    "build_preprocessing_pipeline",
    "fit_transform_partitions",
    "group_composition",
    "load_agenttrace_csv",
]


def __getattr__(name: str):
    if name not in _PREPROCESSING_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        module = import_module("agentguard.ml.preprocessing")
    except ModuleNotFoundError as exc:
        if exc.name == "sklearn":
            raise ImportError(
                "AgentGuard ML preprocessing requires scikit-learn; install with `pip install -e '.[dev,ml]'`"
            ) from exc
        raise
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted([*globals(), *_PREPROCESSING_EXPORTS])
