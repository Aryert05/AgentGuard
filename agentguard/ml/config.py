"""Configuration defaults for AgentTrace preparation experiments."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path


@dataclass(frozen=True)
class PreparationConfig:
    dataset_path: Path = Path("data/generated/agenttrace-v1/agenttrace_prefixes.csv")
    split_seed: int = 2026
    train_fraction: float = 0.6
    validation_fraction: float = 0.2
    test_fraction: float = 0.2

    def __post_init__(self) -> None:
        fractions = (self.train_fraction, self.validation_fraction, self.test_fraction)
        if any(not math.isfinite(value) or value <= 0 or value >= 1 for value in fractions):
            raise ValueError("All split fractions must be between 0 and 1")
        if abs(sum(fractions) - 1.0) > 1e-9:
            raise ValueError("Train, validation, and test fractions must sum to 1")
