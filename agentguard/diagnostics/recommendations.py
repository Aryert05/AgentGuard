"""Deterministic recovery recommendations derived from a failure diagnosis."""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from agentguard.diagnostics.types import (
    DiagnosticValidationError,
    FailureCategory,
    FailureDiagnosis,
    RecoveryAction,
    RecoveryRecommendation,
    RecoveryRisk,
)


class RecoveryRecommender:
    def __init__(
        self,
        alternatives: Mapping[str, Sequence[str]] | None = None,
        *,
        approved_tool_names: Sequence[str] = ("calculator", "search", "company_data"),
    ) -> None:
        self.approved_tool_names = frozenset(approved_tool_names)
        self.alternatives: dict[str, tuple[str, ...]] = {}
        for tool_name, candidates in (alternatives or {}).items():
            if not isinstance(tool_name, str) or not tool_name:
                raise DiagnosticValidationError("Alternative-tool keys must be non-empty strings")
            if tool_name not in self.approved_tool_names:
                raise DiagnosticValidationError(f"Alternative-tool source {tool_name!r} is not approved")
            if any(not isinstance(candidate, str) or not candidate.strip() for candidate in candidates):
                raise DiagnosticValidationError("Alternative-tool names must be non-empty strings")
            values = tuple(candidate for candidate in candidates
                            if candidate != tool_name and candidate in self.approved_tool_names)
            if values:
                self.alternatives[tool_name] = values

    def recommend(self, diagnosis: FailureDiagnosis) -> tuple[RecoveryRecommendation, ...]:
        if not diagnosis.is_failure:
            if diagnosis.ml_risk is not None and diagnosis.ml_risk.predicted_failure:
                return (self._human_review(
                    diagnosis,
                    rationale=(
                        "The ML model predicts elevated failure risk, but no deterministic failure was observed. "
                        "Predicted risk must not be treated as confirmed failure."
                    ),
                    expected_benefit="A human can inspect the uncertain execution before taking action.",
                ),)
            if diagnosis.primary_category in {
                FailureCategory.REPEATED_ACTION,
                FailureCategory.POSSIBLE_LOOP,
                FailureCategory.EXCESSIVE_USAGE,
            }:
                return (self._human_review(
                    diagnosis,
                    rationale=(
                        "A deterministic activity pattern was observed, but no execution failure is confirmed; "
                        "automatic recovery is not justified by this prefix alone."
                    ),
                    expected_benefit="A human can inspect the risk signal and decide whether to stop or redirect the run.",
                ),)
            return ()

        category = diagnosis.primary_category
        tool_name = diagnosis.tool_name
        if category == FailureCategory.EXECUTION_LIMIT_REACHED:
            return (self._human_review(
                diagnosis,
                rationale="The execution has exhausted its bounded tool-call budget.",
                expected_benefit="A human can decide whether a new, separately bounded execution is justified.",
            ),)
        if category == FailureCategory.INVALID_ARGUMENTS:
            if not tool_name:
                return (self._human_review(
                    diagnosis,
                    rationale="Invalid arguments were observed, but the failing tool is unknown.",
                    expected_benefit="A human can inspect the malformed call without blind retrying.",
                ),)
            return (RecoveryRecommendation(
                action=RecoveryAction.RETRY_WITH_MODIFIED_ARGUMENTS,
                diagnosis_category=category,
                target_tool=tool_name,
                risk=RecoveryRisk.MEDIUM,
                requires_human_approval=True,
                max_attempts=1,
                rationale="Schema-invalid arguments are permanent for an unchanged call. A human must review corrected values before any retry.",
                expected_benefit="A corrected argument set can satisfy validation without repeating the malformed call.",
                evidence_event_ids=_evidence_ids(diagnosis),
            ),)
        if category == FailureCategory.TIMEOUT:
            if not tool_name:
                return (self._human_review(
                    diagnosis,
                    rationale="A timeout was observed, but the affected tool is unknown.",
                    expected_benefit="A human can identify the timed-out operation before retrying.",
                ),)
            return (RecoveryRecommendation(
                action=RecoveryAction.RETRY_TOOL,
                diagnosis_category=category,
                target_tool=tool_name,
                risk=RecoveryRisk.LOW,
                requires_human_approval=False,
                max_attempts=1,
                rationale="Timeouts can be transient, so one bounded retry is safe before escalation.",
                expected_benefit="A transient timeout may clear without changing the execution plan.",
                evidence_event_ids=_evidence_ids(diagnosis),
            ),)
        if category == FailureCategory.TOOL_UNAVAILABLE:
            if not tool_name:
                return (self._human_review(
                    diagnosis,
                    rationale="Tool unavailability was observed, but the affected tool is unknown.",
                    expected_benefit="A human can identify the unavailable tool before retrying.",
                ),)
            recommendations = [RecoveryRecommendation(
                action=RecoveryAction.RETRY_TOOL,
                diagnosis_category=category,
                target_tool=tool_name,
                risk=RecoveryRisk.LOW,
                requires_human_approval=False,
                max_attempts=1,
                rationale="Tool unavailability can be transient, so one bounded retry is permitted.",
                expected_benefit="The tool may become available without changing the execution plan.",
                evidence_event_ids=_evidence_ids(diagnosis),
            )]
            switch = self._switch_tool(diagnosis, tool_name)
            if switch is not None:
                recommendations.append(switch)
            return tuple(recommendations)
        if category == FailureCategory.TOOL_FAILURE:
            if not tool_name:
                return (self._human_review(
                    diagnosis,
                    rationale="A generic tool failure was observed, but the affected tool is unknown.",
                    expected_benefit="A human can inspect the failure without an unbounded retry loop.",
                ),)
            return (self._human_review(
                diagnosis,
                rationale="The generic tool error is not known to be transient or idempotent, so an automatic retry is unsafe.",
                expected_benefit="A human can inspect the error and decide whether a retry or alternate tool is safe.",
            ),)
        if category in {FailureCategory.REPEATED_FAILURE, FailureCategory.REPEATED_ACTION}:
            switch = self._switch_tool(diagnosis, tool_name)
            if switch is not None:
                return (switch,)
            return (self._human_review(
                diagnosis,
                rationale="Repeating the same failing action or tool was already observed; blind retries are unsafe.",
                expected_benefit="A human can change strategy rather than repeat a known-failing action.",
            ),)
        if category in {FailureCategory.POSSIBLE_LOOP, FailureCategory.EXCESSIVE_USAGE}:
            return (self._human_review(
                diagnosis,
                rationale="The execution shows repeated or excessive activity without evidence that retrying would help.",
                expected_benefit="A human can stop or redesign the workflow before additional resource use.",
            ),)
        if category == FailureCategory.INCOMPLETE_EXECUTION:
            if diagnosis.partial_results_available:
                return (RecoveryRecommendation(
                    action=RecoveryAction.CONTINUE_WITH_PARTIAL_RESULTS,
                    diagnosis_category=category,
                    target_tool=None,
                    risk=RecoveryRisk.LOW,
                    requires_human_approval=False,
                    max_attempts=1,
                    rationale="Successful tool results are already available and should not be discarded.",
                    expected_benefit="The execution can finalize with the partial results it already produced.",
                    evidence_event_ids=_evidence_ids(diagnosis),
                ),)
            return (RecoveryRecommendation(
                action=RecoveryAction.RESUME_EXECUTION,
                diagnosis_category=category,
                target_tool=None,
                risk=RecoveryRisk.MEDIUM,
                requires_human_approval=True,
                max_attempts=1,
                rationale="Resuming an incomplete execution can repeat work and requires explicit human approval.",
                expected_benefit="A human-approved resume can finish work that stopped before producing a result.",
                evidence_event_ids=_evidence_ids(diagnosis),
            ),)
        return (self._human_review(
            diagnosis,
            rationale="The failure category is unknown or lacks enough evidence for safe automation.",
            expected_benefit="A human can inspect the execution rather than applying an unsupported recovery.",
        ),)

    def _switch_tool(self, diagnosis: FailureDiagnosis, tool_name: str | None) -> RecoveryRecommendation | None:
        if not tool_name:
            return None
        candidates = self.alternatives.get(tool_name, ())
        if not candidates or candidates[0] not in self.approved_tool_names:
            return None
        target = candidates[0]
        return RecoveryRecommendation(
            action=RecoveryAction.SWITCH_TOOL,
            diagnosis_category=diagnosis.primary_category,
            target_tool=target,
            risk=RecoveryRisk.MEDIUM,
            requires_human_approval=False,
            max_attempts=1,
            rationale=f"Switching from {tool_name!r} to configured alternative {target!r} changes strategy instead of retrying.",
            expected_benefit="The configured alternative may avoid the failure mode of the original tool.",
            evidence_event_ids=_evidence_ids(diagnosis),
        )

    def _human_review(
        self,
        diagnosis: FailureDiagnosis,
        *,
        rationale: str,
        expected_benefit: str,
    ) -> RecoveryRecommendation:
        return RecoveryRecommendation(
            action=RecoveryAction.REQUEST_HUMAN_REVIEW,
            diagnosis_category=diagnosis.primary_category,
            target_tool=None,
            risk=RecoveryRisk.HIGH,
            requires_human_approval=True,
            max_attempts=1,
            rationale=rationale,
            expected_benefit=expected_benefit,
            evidence_event_ids=_evidence_ids(diagnosis),
        )


def _evidence_ids(diagnosis: FailureDiagnosis) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for evidence in diagnosis.evidence:
        for event_id in evidence.event_ids:
            if event_id not in seen:
                seen.add(event_id)
                ordered.append(event_id)
    return tuple(ordered)
