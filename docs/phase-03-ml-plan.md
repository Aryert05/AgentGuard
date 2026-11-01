# Phase 3: ML Intelligence — Dataset Audit and Experiment Plan

**Status:** Phase 3.2 preparation and the Phase 3.3 supervised validation-baseline implementation are complete, including execution-balanced metrics, group-bootstrap uncertainty, prefix/time strata, and the separate exploratory task-template-held-out experiment. The test partitions have not been scored; no anomaly detector has been trained.<br>
**Dataset audited:** `data/generated/agenttrace-v1/`<br>
**Manifest seed:** `2026`

## 1. ML problem formulation

The first supervised task is binary prediction from an observed execution-event prefix: estimate whether the execution represented by that prefix will ultimately be unsuccessful. A row is a prefix of one execution, not an independent execution. The intended inference point is after the events in that prefix have been observed. The experiment is an offline feasibility study on controlled synthetic traces, not a production performance claim.

The primary target is `eventual_failure`. `outcome_label` and `failure_category` are descriptive targets for later analysis only. No target, identifier, task text, scenario, final status, or event after the prefix may enter the model input.

## 2. Prediction target and label semantics

The Phase 2 label builder maps final execution status as follows:

| Final execution status | `eventual_failure` | `outcome_label` | Meaning |
|---|---:|---|---|
| `completed` | 0 | `completed` | Every tool call completed successfully. |
| `failed` | 1 | `failed` | No tool call produced a successful result, or execution failed before a usable result. |
| `partially_completed` | 1 | `partially_completed` | At least one tool call succeeded and at least one failed. |
| Other/unknown/in-progress | null | `unknown` | No binary outcome is assigned. Exclude from binary fitting and report separately. |

For failed or partially completed executions, `failure_category` summarizes recorded tool errors; it is not the binary target. In the audited generated file, no `eventual_failure` or `outcome_label` values are missing.

## 3. Audited dataset and schema

The actual files are:

- `data/generated/agenttrace-v1/agenttrace_prefixes.csv` — 484 prefix rows.
- `data/generated/agenttrace-v1/agenttrace_traces.jsonl` — 100 full synthetic traces, 584 events total.
- `data/generated/agenttrace-v1/manifest.json` — seed, configuration, counts, and execution-level outcome/category counts.
- `data/generated/agenttrace-v1/feature_dictionary.json` — definitions and declared types for the 18 model features.

Manifest outcome distribution is 44 completed, 50 failed, and 6 partially completed executions. At prefix level the binary target has 176 negatives (`0`, 36.36%) and 308 positives (`1`, 63.64%). `outcome_label` counts are 176 completed, 266 failed, and 42 partially completed prefixes. There are 100 distinct `execution_id` values and 10 distinct `task_template_id` values.

CSV schema below reports the types inferred from the audited CSV by pandas. `object` columns are strings in this file; nullable numeric features are encoded as blank CSV cells and read as `float64` with NaN.

| Column | Audited CSV dtype | Missing rows | Use |
|---|---|---:|---|
| `execution_id` | object | 0 | Group key only; never a model input. |
| `task_id` | object | 0 | Identifier only; never a model input. |
| `task_template_id` | object | 0 | Group key for template-held-out evaluation only; never a model input. |
| `prefix_id` | object | 0 | Row identifier only; never a model input. |
| `prefix_index` | int64 | 0 | Prefix ordering metadata; exclude from model inputs. |
| `synthetic_data` | bool | 0 | Provenance flag, constant true; exclude from model inputs. |
| `prefix_length` | int64 | 0 | **Permitted feature.** Number of events observed. |
| `elapsed_time_ms` | float64 | 100 | **Permitted feature.** First-to-last observed prefix time; null with fewer than two events. |
| `current_step_number` | int64 | 0 | **Permitted feature.** Step on the final observed event. |
| `total_tool_calls_so_far` | int64 | 0 | **Permitted feature.** Observed tool selections. |
| `successful_tool_calls` | int64 | 0 | **Permitted feature.** Observed successful tool outcomes. |
| `failed_tool_calls` | int64 | 0 | **Permitted feature.** Observed failed tool outcomes. |
| `tool_failure_rate_so_far` | float64 | 300 | **Permitted feature.** Failed / (failed + successful) observed outcomes; undefined before an outcome. |
| `average_tool_duration_ms` | float64 | 300 | **Permitted feature.** Mean observed non-null tool outcome duration. |
| `maximum_tool_duration_ms` | float64 | 300 | **Permitted feature.** Maximum observed non-null tool outcome duration. |
| `repeated_tool_call_count` | int64 | 0 | **Permitted feature.** Observed selections beyond the first selection of each tool. |
| `distinct_tool_count` | int64 | 0 | **Permitted feature.** Distinct tools selected so far. |
| `timeout_count` | int64 | 0 | **Permitted feature.** Timeout failures already observed in the prefix. |
| `invalid_argument_count` | int64 | 0 | **Permitted feature.** Invalid-argument failures already observed in the prefix. |
| `tool_unavailable_count` | int64 | 0 | **Permitted feature.** Unavailability failures already observed in the prefix. |
| `consecutive_failure_count` | int64 | 0 | **Permitted feature.** Trailing failed tool outcomes observed so far. |
| `time_since_last_failure_ms` | float64 | 362 | **Permitted feature.** Time since the last observed failed event; undefined before a failure. |
| `tool_switch_count` | int64 | 0 | **Permitted feature.** Adjacent tool-selection changes observed so far. |
| `repeated_failure_pattern_count` | int64 | 0 | **Permitted feature.** Repeated observed error-type occurrences after their first occurrence. |
| `eventual_failure` | int64 | 0 | Target only; exclude from inputs. |
| `outcome_label` | object | 0 | Descriptive target only; exclude from inputs. |
| `failure_category` | object | 0 | Descriptive target only; exclude from inputs. |

The complete model input allow-list is exactly the 18 feature columns declared by Phase 2 `MODEL_FEATURE_COLUMNS`: `prefix_length`, `elapsed_time_ms`, `current_step_number`, `total_tool_calls_so_far`, `successful_tool_calls`, `failed_tool_calls`, `tool_failure_rate_so_far`, `average_tool_duration_ms`, `maximum_tool_duration_ms`, `repeated_tool_call_count`, `distinct_tool_count`, `timeout_count`, `invalid_argument_count`, `tool_unavailable_count`, `consecutive_failure_count`, `time_since_last_failure_ms`, `tool_switch_count`, and `repeated_failure_pattern_count`. Build the matrix through that allow-list rather than selecting all non-target columns.

The audited CSV has zero exact duplicate rows, zero duplicate `prefix_id` values, and zero duplicate `(execution_id, prefix_index)` pairs. Across feature columns, missing counts are: `elapsed_time_ms` 100; `tool_failure_rate_so_far` 300; `average_tool_duration_ms` 300; `maximum_tool_duration_ms` 300; `time_since_last_failure_ms` 362; all other feature columns 0.

## 4. Leakage risks and controls

- **Prefix correlation / split leakage:** rows from one execution share one eventual label. Keep every execution wholly within a single split; never split rows randomly.
- **Template leakage:** task templates encode narrow scenario language. For the transfer experiment, keep every `task_template_id` wholly in one split. Never feed the hash itself to a model.
- **Targets and IDs:** exclude `eventual_failure`, `outcome_label`, `failure_category`, `execution_id`, `task_id`, `task_template_id`, `prefix_id`, `prefix_index`, and `synthetic_data` from inputs.
- **Post-outcome events:** Phase 2 features are derived from an event prefix and generated prefixes omit terminal execution events. Preserve this rule. Do not calculate features from the full trace and then attach them to earlier prefixes.
- **Observed failures:** timeout, invalid-argument, unavailable, and failure-count features are legitimate only when the corresponding event is already in the observed prefix. They can make later predictions close to the outcome and are a deployment timing consideration; evaluate and report performance by prefix length / time-to-terminal as well as overall.
- **Redundant features:** `prefix_length` and `prefix_index` encode the same sequence position in this generator; `prefix_index` stays excluded. Several cumulative counts are correlated. Do not interpret coefficient or tree importance as causal evidence.
- **Synthetic scenario signatures:** controlled failure modes and task text generation can make classes easier to distinguish than real executions. The generated sample cannot establish real-world generalization, calibration, or operational usefulness.
- **Preprocessing leakage:** fit imputers, missingness indicators, scalers, feature selection, calibration, and model parameters on training data only. Use validation groups for model/threshold selection. Keep the final test groups untouched until the experiment is frozen.

## 5. Preprocessing strategy

Use only `MODEL_FEATURE_COLUMNS`. Keep identifiers in a parallel metadata frame strictly for grouping and diagnostic aggregation. Treat all allow-listed columns as numeric; use training-fitted median imputation and add fixed missingness indicators for the five structurally nullable columns. The implemented baseline uses one common training-fitted imputation, indicator, and scaling pipeline for all four models so they receive identical matrices; scaling is needed by Logistic Regression and is harmless for these fixed tree baselines. Do not impute targets. Do not perform feature selection in the first baseline; if later added, fit it inside each training fold. Record the exact pipeline and feature order.

Phase 3.2 implements these decisions in `agentguard/ml/`. `load_agenttrace_csv` validates the exact schema and values without dropping invalid rows. `build_preprocessing_pipeline` returns an unfitted sklearn `Pipeline` with a median `SimpleImputer`, explicit structural `MissingIndicator`, and optional `StandardScaler`. `fit_transform_partitions` fits on train rows only, then transforms validation and test rows. Before any fit or transform, it verifies the content/schema fingerprint, audit context, complete unique row coverage, group membership at each row position, class counts, pairwise group disjointness, and exact deterministic assignments regenerated from the recorded seed and fractions. The raw sklearn pipeline factory cannot enforce split provenance if fitted directly; use the guarded helper for AgentTrace partitions. The optional `ml` extra in `pyproject.toml` declares scikit-learn; this dependency is used only for data transformation at this stage.

Because many prefix rows belong to a small number of executions, compare ordinary row-weighted metrics with execution-balanced sample weighting (each execution contributes total weight 1, divided across its prefixes). Keep all splitting group-aware either way. Fit every preprocessing step independently within each training partition, including inner folds.

## 6. Grouped train / validation / test design

Run and report these as **two distinct experiments**, with independently constructed splits. Never use random row splitting.

### A. Execution-held-out evaluation — primary

Use `execution_id` as the indivisible group. The Phase 3.2 preparation split uses deterministic group-level stratification at approximately 60/20/20% of executions for train/validation/test. Verify that no execution ID overlaps, show execution and prefix class counts in each partition, and verify both classes exist where ROC-AUC is reported. If the small group count or class distribution prevents a valid fixed split, the splitter fails clearly. Later model experiments may use repeated `StratifiedGroupKFold` outer evaluation (or `GroupKFold` with explicitly reported class coverage), keeping all preprocessing and tuning inside each training fold. Do not use the prefix count as the independent sample size.

Use training groups to fit models and preprocessing; validation groups for model selection, probability calibration if justified, and threshold selection; test groups once for final reporting. For a more stable estimate, predeclare repeated grouped outer folds and report the fold distribution, not just the best fold. Keep comparisons on identical group partitions.

### B. Task-template-held-out evaluation

Use `task_template_id` as the indivisible group, so no samples from a template occur in more than one partition. There are only 10 class-pure templates. Phase 3.2 preflights each template's class composition and applies a deterministic 6/2/2 group split, distributing groups by their single observed class so all three partitions contain both classes. It fails clearly if the available groups cannot support this coverage. The resulting test partition contains only two independent templates (one template per class); treat this as an exploratory scenario-shift check, not a reliable generalization estimate or model-selection basis. Do not choose templates based on model outcomes. In later model experiments, leave-one-template-out outer evaluation is an alternative; report class-missing folds explicitly and estimate tuning only with group-aware inner splits over the remaining templates. This does not replace the primary execution-held-out evaluation.

If a validation partition cannot contain both labels, do not tune a threshold there. Report that the current data cannot support the intended estimate and keep the experiment descriptive. In both experiments, the final test partition is used once after model, preprocessing, and threshold rules are frozen.

## 7. Classification baselines

Use `DummyClassifier(strategy="prior")` as the majority/prevalence baseline. The implemented initial baselines are:

1. Logistic Regression with `C=1`, `liblinear`, `max_iter=1000`, training-only scaling, and `class_weight="balanced"`.
2. Random Forest with 200 trees, maximum depth 4, minimum leaf size 5, `max_features="sqrt"`, `class_weight="balanced_subsample"`, and a fixed random state.
3. `HistGradientBoostingClassifier` with learning rate 0.05, 100 iterations, at most 7 leaves, minimum leaf size 10, L2 regularization 1, balanced class weights, fixed random state, and internal early stopping disabled.

The class weighting gives the less frequent completed class meaningful loss weight on this 36/64 prefix distribution; it is a fixed baseline choice, not validation-tuned. All four models use the same feature matrix, training rows, and validation rows. The tree models receive the same scaled/imputed feature representation for comparability even though scaling is not required by those estimators. These are fixed baselines, not a hyperparameter search. Avoid large searches on 100 executions.

## 8. Evaluation metrics

For each model and each grouped experiment, report:

- Precision, recall, and F1 at the validation-selected operating threshold.
- ROC-AUC and Average Precision (PR-AUC), with positive class explicitly set to `eventual_failure=1`.
- Confusion matrix, support, and per-class precision, recall, and F1.
- Training time and one measured validation-batch inference duration, with environment recorded. This first run does not measure warm-up, repeated latency, or production-serving latency; do not interpret the batch duration as a stable latency estimate.
- Prefix-level results by prefix length / how much of the trace is observed, plus execution-balanced metrics and ordinary prefix-weighted metrics.

Report undefined metrics as undefined when a fold lacks a class; do not silently substitute a score. Because rows within an execution are dependent, estimate uncertainty by bootstrapping whole held-out execution groups for experiment A and whole held-out templates for experiment B, not individual prefix rows. With few groups, label intervals as unstable and show group-level outcomes.

The Phase 3.3 implementation fixes these analysis choices before reporting: execution-/template-balanced weighting gives each group total weight 1.0 divided across its rows; ordinary row-weighted metrics are always reported alongside. Cluster bootstrap uses complete groups, 2,000 iterations, seed `20261101`, and 95% percentile intervals. Prefix bins are 1, 2, 3–5, 6–10, and 11+; observation-time bins are missing, 0–25, 25–50, 50–100, and 100+ ms. A stratum is flagged when it has fewer than 10 rows or fewer than 10 independent groups. Degenerate bootstrap replicates are excluded only from the affected metric interval and are never imputed. Each overall analysis also records per-group row outcomes so small-group behavior remains visible; strata omit the repeated per-group detail. The persisted split file is an audited cross-check; deterministic reconstruction is canonical. The CLI runs either experiment with `python -m agentguard.ml.train --strategy execution_held_out` or `--strategy task_template_held_out`; artifacts include source-file hashes, a code-snapshot hash, split provenance, and an external SHA-256 sidecar.

## 9. Threshold selection

Use validation probabilities only. The implemented threshold grid is 0.1 through 0.9, including the default 0.50 reference. Select the threshold that maximizes validation F1; ties prefer higher precision, then the higher threshold. If validation lacks both classes and no F1 is defined, record selection as unavailable and use 0.50 only as an explicitly marked inference fallback. If product requirements later prioritize missed failures, agree on a minimum validation precision or recall constraint before fitting. Freeze the chosen threshold before any future test scoring. Never select or adjust a threshold from final-test predictions or labels. Any later refit on train plus validation must be a separately documented frozen protocol before a one-time test evaluation.

## 10. Anomaly detection

Treat Isolation Forest as a separate unsupervised experiment, not another supervised classifier. Fit it on training-prefix feature vectors only, with training-fitted preprocessing and a fixed recorded random seed. Do not supply labels, final status, or identifiers. Use validation data to choose any anomaly-score threshold or contamination operating point; do not use test labels for threshold setting. On held-out groups, report anomaly score distributions and detection precision/recall against `eventual_failure` as a post-hoc diagnostic, clearly distinguishing anomaly detection from calibrated failure probability. Compare against the supervised and dummy baselines only as an alert-ranking exercise.

## 11. Explainability

The implementation exposes Logistic Regression coefficients and Random Forest impurity importances against the 23 transformed feature names (18 approved features plus 5 missingness indicators). HistGradientBoosting has no intrinsic importance output in this framework; its supported path is a validation-only permutation-importance helper using Average Precision. These are descriptive, not causal, and can be unstable on the small validation groups. No explanation is computed on the test set. SHAP remains deferred.

## 12. Experiment tracking and reproducibility

For the first experiments, use a versioned local JSON/CSV results artifact rather than adding a tracking server. Record Git commit, dataset manifest hash, data path/version, random seeds, exact split group IDs, model and parameters, preprocessing pipeline, dependency versions, threshold rule/value, metrics, timing protocol, and run timestamp. Keep generated metrics/plots under ignored output directories unless a deliberately reviewed compact report is added. MLflow is not justified for this single small local experiment; reconsider when there are multiple contributors, repeated runs, or a remote tracking requirement. Never log task text or credentials.

## 13. Dependency recommendations

`pyproject.toml` declares scikit-learn in the optional `ml` extra; MLflow, XGBoost, and SHAP are not declared dependencies. Base dataset loading/splitting does not import scikit-learn; preprocessing requires the `ml` extra.

- **scikit-learn: included in optional `ml` extra.** It supplies sklearn-compatible preprocessing and, in later approved phases, the planned classifiers, `IsolationForest`, splitters, and metrics. Install ML development dependencies with `pip install -e '.[dev,ml]'`.
- **MLflow: defer.** A local structured result artifact is sufficient for a small, seeded set of experiments.
- **XGBoost: defer.** HistGradientBoostingClassifier is a suitable initial boosting model and avoids a separate package/runtime dependency.
- **SHAP: defer.** Logistic coefficients and held-out permutation importance are adequate for initial baselines.

The Phase 3.3 validation-baseline runs used scikit-learn 1.9.1 from the optional `ml` extra. Four supervised baselines were fitted on training rows and evaluated on validation rows only in both the primary execution-held-out and exploratory task-template-held-out experiments. No anomaly detector was fitted.

## 14. Limitations and expected challenges

This generated dataset has only 100 executions and 10 task templates, despite 484 prefix rows. Prefixes from one execution share a label, generator, and event history, so treating 484 rows as independent would overstate sample size and confidence. Every task template is class-pure, and the task-template test contains only two independent templates, one per class; that result is exploratory and cannot establish broad task generalization. The synthetic outcomes are constructed by controlled scenarios, the positive class is 63.64% at prefix level, and the EDA already records correlation between observed failure rate and eventual failure; performance can be driven by those construction rules. At early prefixes, predictive signal may be weak; later prefixes may include an observed failure and answer a narrower detection question. The feature set is small and correlated, category handling is not relevant to the allow-list as currently defined, and confidence intervals will be wide. Results should be described as pipeline and methodology experiments only. Before claims about real agents, collect a larger, diverse, consented dataset with many executions per template and evaluate on a temporally and operationally representative holdout.

## Scope boundary

This document contains the Phase 3.1 experiment design, Phase 3.2 data-preparation decisions, Phase 3.3 supervised baseline decisions, and Phase 3.4 failure diagnosis/recovery service contracts. It does not add a frontend or deployment configuration. Supervised models have been fit on training rows and evaluated on validation only; neither final test partition has been scored. No anomaly detector has been trained.

## Phase 3.2 implementation decisions and verified split results

`agentguard/ml/data.py` validates the real CSV's required columns, real numeric feature dtypes and value ranges, binary target values and status semantics, non-empty identifiers, unique prefix IDs and execution/prefix-index pairs, duplicate rows, and group consistency. Complex numeric dtypes are rejected before conversion. Invalid rows raise `DatasetValidationError`; no row is silently discarded. Every frame receives a deterministic SHA-256 fingerprint over schema version, ordered column names/dtypes, feature allow-list, target semantics, and length-delimited typed scalar encodings of ordered values and missing markers. File SHA-256 remains separate provenance metadata. In-memory frames always receive the same dataset fingerprint for equivalent schema and values. `AgentTraceDataset.features` contains exactly the 18 Phase 2 allow-listed features. Group IDs and targets remain in separate metadata/target objects.

Before any preprocessing fit or transform, `validate_split_binding` recomputes the fingerprint from the validated dataset components, compares dataset and split fingerprints/audits, validates partition names and index types/ranges/uniqueness/coverage, verifies each partition's recorded group IDs against the actual rows and class counts, checks preflight group metadata and cross-partition group overlap, then regenerates and compares the exact partition assignment from the recorded seed and fractions.

The five nullable features are structurally missing by design: `elapsed_time_ms` is undefined for a one-event prefix; failure rate and duration summaries are undefined before any tool outcome; `time_since_last_failure_ms` is undefined before a failed event. The preparation pipeline imputes each with the median learned from training rows and emits a fixed missingness indicator for each field. Scaling is optional and part of the same unfitted pipeline. The pipeline fitting helper only fits on the train partition and applies the learned transform to validation/test; tests verify validation-only extreme values do not change the training median.

Both deterministic splits use seed `2026` and are serialized locally under the ignored path `data/generated/agenttrace-v1/split-metadata/`. The JSON files record dataset SHA-256, split fractions, exact group IDs and row positions, class counts, group composition, and audit data.

| Experiment | Partition | Groups | Prefix rows | Class 0 | Class 1 |
|---|---|---:|---:|---:|---:|
| Execution-held-out | Train | 60 | 282 | 104 | 178 |
| Execution-held-out | Validation | 20 | 119 | 36 | 83 |
| Execution-held-out | Test | 20 | 83 | 36 | 47 |
| Task-template-held-out | Train | 6 | 262 | 96 | 166 |
| Task-template-held-out | Validation | 2 | 150 | 40 | 110 |
| Task-template-held-out | Test | 2 | 72 | 40 | 32 |

Preflight found 10 task templates; each is single-class in this synthetic dataset. Six templates contain only positive-class prefixes and four contain only negative-class prefixes. The implemented allocation places at least one template of each class in each partition (training has four positive and two negative templates; validation and test each have one of each). No group overlap was found. This template experiment is highly sensitive to template/scenario signatures and is not evidence of broad task generalization.

Phase 3.2 preparation results remain in [the Phase 3.2 data-preparation report](reports/phase-03-data-preparation-report.md). Phase 3.3 model configurations, validation-only results, safeguards, and verification are in [the Phase 3.3 implementation report](reports/phase-03-3-implementation-report.md). PostgreSQL integration is not part of either ML verification. The task-template experiment remains exploratory and has not been used for model selection.

## Phase 3.4 diagnosis and bounded recovery

`agentguard.diagnostics.DiagnosticService.analyze(events, ml_signal=None)` accepts an observed event prefix and returns a `DiagnosisReport`: deterministic diagnosis, evidence-backed patterns, safe recommendations, and an optional ML integration error. `DiagnosticRecoveryService.analyze_and_recover(...)` combines that interface with a caller-provided one-action `RecoveryExecutor` and a `BoundedRecoveryController`. Neither service invokes an LLM. Callers must provide only events observed at the requested diagnosis time; the service does not infer or slice a prefix from a complete trace. An empty sequence is a valid pre-start snapshot and yields an unknown diagnosis. Every non-empty prefix must begin with exactly one `execution_started` event; later streaming prefixes may omit a terminal event, but suffixes that omit the start event are rejected.

The engine reports tool failure, timeout, invalid arguments, tool unavailability, repeated failures, repeated actions, excessive usage, execution-limit exhaustion, incomplete execution, possible loops, and unknown. Confirmed failure (`is_failure`) requires an observed tool failure, failed terminal event, or explicit execution-limit signal. Repeated selection and loop patterns without such evidence are risk signals and do not become confirmed failures. ML probability/threshold is attached as predictive evidence only and cannot change the deterministic failure flag or category. A missing or failing optional scorer leaves rule diagnosis operational.

Patterns use only fields present in the supplied prefix. Rules include repeated same-tool failures (3), repeated error types (3), repeated invalid-argument failures for a known tool (3), consecutive failures (3), trailing failures without progress (3), alternating failed tools (4), repeated tool selections (3), repeated argument fingerprints (3), repeated tool cycles (two cycles of length 2 or 3), excessive selections (6), timeout clusters (2), consecutive timeout outcomes (2), and explicit tool-call-limit messages. Thresholds are configurable. Live and deterministic tool-selection telemetry stores a SHA-256 fingerprint of canonical JSON arguments, not raw arguments. The digest supports exact-repeat detection but is not a secrecy guarantee for low-entropy inputs.

Recommendations are separate from execution. They include the diagnosis category, action, target, risk, approval requirement, maximum attempts, evidence IDs, rationale/safety basis, and expected benefit. Tool alternatives must be configured and belong to the approved tool-name set. Automatic retries are limited to explicitly retry-safe tools for timeout/unavailable diagnoses, one action at a time, with configurable capped backoff (default 0.1, 0.25, and 0.5 seconds). An unsuccessful retry continues only for a subsequent `TimeoutError` or `ConnectionError`; permanent or unclassified errors stop the chain. Generic errors are escalated because their permanence/idempotency is unknown. Repeated failures, argument correction, incomplete resumes, high-risk work, and unknown diagnoses are approval-gated and this service returns them without executing them; it does not implement a human approval workflow.

`RecoveryPolicy` has an absolute cap of five attempts per original execution, a per-recommendation cap of three, and the default global cap of two. Each controller creates a local `InMemoryRecoveryAttemptLedger` by default; that budget does not coordinate separate controller instances. Inject the same in-memory ledger into controllers to coordinate within one process. `DatabaseRecoveryTelemetry` also implements the ledger protocol using a PostgreSQL atomic upsert; inject it as both `telemetry` and `attempt_ledger` for durable cross-process and restart-safe enforcement. Database concurrency has not been runtime-verified. The controller prevents nested/concurrent use of the same instance, sets `chain_depth=1` and `tool_call_budget=1` on each request, preserves the original execution ID, and never launches the agent graph recursively. A supplied executor must honor the one-action request and must not start another recovery chain. The recovery core does not alter the original agent's configured tool-call limit or mutate its execution state.

`RecoverySession` and `RecoveryAttempt` capture session/attempt IDs, original and recovery execution IDs, triggering event, diagnosis, recommended and actual actions, attempt number, status, duration, output/error, and final execution outcome. `DatabaseRecoveryTelemetry` routes session persistence through `persist_recovery_session` and budget claims through an atomic upsert; migration `0002_recovery_telemetry` creates separate recovery session, attempt, and budget tables without altering existing execution/event rows. The runtime role requires SELECT/INSERT/UPDATE on `recovery_sessions`, SELECT/INSERT on `recovery_attempts`, and SELECT/INSERT/UPDATE on `recovery_attempt_budgets`. Database migration/runtime behavior has not been exercised against PostgreSQL in this phase verification.

`RecoveryAttempt.duration_ms` uses an executor-reported action duration when provided; that duration excludes controller backoff. When omitted, the controller records elapsed time from attempt start through completion, including backoff. Attempt timestamps always enclose the backoff and executor call. Duration values must be finite, nonnegative, and no greater than that timestamp envelope (with one-microsecond rounding tolerance). This preserves executor-reported duration semantics while preventing impossible persisted durations.

These interfaces are Python service boundaries for later integration, not an HTTP API. Deterministic rule diagnosis remains available without a trained model or model artifact. Recovery executors must be supplied by a caller; Phase 3.4 does not wire automatic recovery into `run_agent` or bypass its execution budget. See the [Phase 3.4 report](reports/phase-03-4-diagnostics-recovery-report.md) for implementation and verification details.
