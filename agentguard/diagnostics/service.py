"""Stable service interfaces for later API integration."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from agentguard.diagnostics.engine import FailureDiagnosticEngine, validate_ml_signal
from agentguard.diagnostics.patterns import PatternConfig
from agentguard.diagnostics.recommendations import RecoveryRecommender
from agentguard.diagnostics.recovery import BoundedRecoveryController, RecoveryExecutor
from agentguard.diagnostics.types import (
    DiagnosisReport,
    DiagnosticValidationError,
    FailureDiagnosis,
    MLRiskSignal,
    RecoveryRecommendation,
    RecoverySession,
)
from agentguard.schemas.events import ExecutionEvent


class MLRiskScorer(Protocol):
    def score(self, events: Sequence[ExecutionEvent]) -> MLRiskSignal:
        """Return predictive failure risk without changing deterministic diagnosis."""


class DiagnosticService:
    def __init__(
        self,
        *,
        engine: FailureDiagnosticEngine | None = None,
        recommender: RecoveryRecommender | None = None,
        ml_scorer: MLRiskScorer | None = None,
    ) -> None:
        self.engine = engine or FailureDiagnosticEngine()
        self.recommender = recommender or RecoveryRecommender()
        self.ml_scorer = ml_scorer

    def analyze(
        self,
        events: Sequence[ExecutionEvent],
        *,
        ml_signal: MLRiskSignal | None = None,
    ) -> DiagnosisReport:
        if ml_signal is not None and self.ml_scorer is not None:
            raise DiagnosticValidationError("Provide either ml_signal or ml_scorer, not both")
        ml_error = None
        effective_signal = ml_signal
        if effective_signal is None and self.ml_scorer is not None:
            try:
                scored_signal = self.ml_scorer.score(events)
                if scored_signal is None:
                    raise DiagnosticValidationError("MLRiskScorer returned no prediction")
                effective_signal = validate_ml_signal(scored_signal)
            except Exception as exc:
                # Only scoring and its output contract are isolated here. Rule diagnosis
                # below remains outside this boundary so unrelated defects still surface.
                ml_error = f"{type(exc).__name__}: {exc}"
                effective_signal = None
        diagnosis = self.engine.diagnose(events, ml_signal=effective_signal)
        recommendations = self.recommender.recommend(diagnosis)
        return DiagnosisReport(
            diagnosis=diagnosis,
            recommendations=recommendations,
            ml_error=ml_error,
        )


class DiagnosticRecoveryService:
    def __init__(
        self,
        diagnostics: DiagnosticService | None = None,
        recovery: BoundedRecoveryController | None = None,
    ) -> None:
        self.diagnostics = diagnostics or DiagnosticService()
        self.recovery = recovery or BoundedRecoveryController()

    def analyze_and_recover(
        self,
        events: Sequence[ExecutionEvent],
        executor: RecoveryExecutor,
        *,
        ml_signal: MLRiskSignal | None = None,
        original_execution_id: str | None = None,
    ) -> tuple[FailureDiagnosis, tuple[RecoveryRecommendation, ...], RecoverySession]:
        report = self.diagnostics.analyze(events, ml_signal=ml_signal)
        session = self.recovery.recover(
            report.diagnosis,
            report.recommendations,
            executor,
            original_execution_id=original_execution_id,
        )
        return report.diagnosis, report.recommendations, session


def build_diagnostic_service(
    *,
    alternatives: Mapping[str, Sequence[str]] | None = None,
    approved_tool_names: Sequence[str] = ("calculator", "search", "company_data"),
    pattern_config: PatternConfig | None = None,
    ml_scorer: MLRiskScorer | None = None,
) -> DiagnosticService:
    return DiagnosticService(
        engine=FailureDiagnosticEngine(config=pattern_config),
        recommender=RecoveryRecommender(
            alternatives=alternatives,
            approved_tool_names=approved_tool_names,
        ),
        ml_scorer=ml_scorer,
    )
