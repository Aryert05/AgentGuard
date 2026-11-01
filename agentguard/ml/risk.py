"""Optional adapter from a fitted Phase 3.3 model to diagnostic risk signals."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import pandas as pd

from agentguard.diagnostics.types import MLRiskSignal
from agentguard.processing.features import MODEL_FEATURE_COLUMNS, extract_prefix_features
from agentguard.schemas.events import ExecutionEvent


@dataclass(frozen=True)
class ModelRiskScorer:
    bundle: Any
    threshold: float | None = None

    def score(self, events: Sequence[ExecutionEvent]) -> MLRiskSignal:
        features = extract_prefix_features(events)
        frame = pd.DataFrame([features], columns=list(MODEL_FEATURE_COLUMNS))
        probability = float(self.bundle.predict_scores(frame)[0])
        selected_threshold = float(self.threshold if self.threshold is not None else self.bundle.threshold)
        return MLRiskSignal(
            probability=probability,
            threshold=selected_threshold,
            model_name=str(getattr(self.bundle, "model_name", "unspecified")),
            source="phase3.3_fitted_bundle",
        )
