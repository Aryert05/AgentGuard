# Phase 3.2 — Data Preparation Report

**Branch:** `phase/03-ml-intelligence`<br>
**Scope:** validated AgentTrace loading, feature allow-listing, grouped split generation, and training-only preprocessing. No classification or anomaly models were trained.

## Dataset validation

Loaded the actual `data/generated/agenttrace-v1/agenttrace_prefixes.csv` file and cross-checked its manifest and trace JSONL. The CSV contains 484 rows from 100 executions and 10 task templates; the JSONL contains 100 traces and 584 events. The binary target has 176 class-0 and 308 class-1 rows. `outcome_label` counts are 176 completed, 266 failed, and 42 partially completed.

Validation confirms the exact expected 27-column schema, all 18 allow-listed real numeric features, and all target columns; complex dtypes are rejected before conversion. Target values are binary and consistent with `outcome_label`; IDs are non-empty; execution labels and template identity are stable within groups; and feature values satisfy numeric, count, duration, and rate constraints. The data has zero duplicate rows, duplicate prefix IDs, or duplicate `(execution_id, prefix_index)` pairs. No records are silently dropped. Missingness is retained only for the five structurally nullable features; unexpected nulls are rejected.

Feature selection uses Phase 2 `MODEL_FEATURE_COLUMNS` exactly. IDs, group keys, prefix order, provenance, and all target columns are carried separately and never enter `features`.

## Grouped split results

Both experiments use deterministic seed `2026`. Every input row is assigned exactly once and no group crosses partitions.

| Experiment | Partition | Groups | Prefix rows | Class 0 | Class 1 |
|---|---|---:|---:|---:|---:|
| Execution-held-out | Train | 60 executions | 282 | 104 | 178 |
| Execution-held-out | Validation | 20 executions | 119 | 36 | 83 |
| Execution-held-out | Test | 20 executions | 83 | 36 | 47 |
| Task-template-held-out | Train | 6 templates | 262 | 96 | 166 |
| Task-template-held-out | Validation | 2 templates | 150 | 40 | 110 |
| Task-template-held-out | Test | 2 templates | 72 | 40 | 32 |

Template preflight found that each of the 10 task templates contains only one class: six templates are positive-only and four are negative-only. The deterministic 6/2/2 allocation distributes both kinds to every partition. This makes class metrics computable, but the experiment is sensitive to synthetic template/scenario signatures and does not establish broad generalization.

Exact group IDs, row positions, counts, seed, dataset SHA-256, and preflight composition are written to ignored local artifacts:

- `data/generated/agenttrace-v1/split-metadata/execution-held-out.json`
- `data/generated/agenttrace-v1/split-metadata/task-template-held-out.json`

The validated in-memory dataset also receives a deterministic SHA-256 content fingerprint covering schema version, ordered column names and dtypes, feature and target semantics, row order, and typed values encoded with explicit lengths and missing-value markers. Preprocessing verifies this fingerprint plus split audit fields, row coverage, group IDs at assigned positions, class counts, group disjointness, and exact assignments regenerated from the split seed and fractions before fitting. This prevents applying a split to a changed same-sized dataset or a coherently reassigned partition with unchanged split parameters. Source path and CSV file hash remain provenance fields and do not prevent using the same validated content from another path.

## Missing values and preprocessing

Observed nulls have structural meanings: elapsed time is undefined for a one-event prefix; outcome rate and durations are undefined before a tool outcome; time since failure is undefined before a failure. The sklearn-compatible pipeline median-imputes each numeric feature using training rows only and adds fixed indicators for those five nullable features. Optional scaling follows in the same pipeline. Validation and test data are transformed with the fitted training pipeline; neither contributes to imputer or scaler fitting. Tests inject large validation-only values and confirm training medians remain unchanged.

Scikit-learn 1.9.1 is declared in the optional `ml` dependency and was installed into the ignored project virtual environment for these preprocessing tests. The package's data validation and splitting imports remain usable without scikit-learn; importing preprocessing requires the optional dependency. `fit_transform_partitions` is the split-checked AgentTrace preparation path; fitting the raw pipeline returned by `build_preprocessing_pipeline` directly bypasses split-provenance validation. No estimator was trained.

## Verification

- Phase 3.2 data, splitting, and preprocessing unit tests: **37 passed**.
- Phase 1 regression tests: **25 passed**.
- Phase 2 unit regression tests: **30 passed**.
- Full suite: **92 passed, 1 skipped**. The PostgreSQL integration test was skipped because both `AGENTGUARD_TEST_DATABASE_URL` and `AGENTGUARD_TEST_MIGRATION_DATABASE_URL` were unset. PostgreSQL integration is therefore not claimed as part of this run.

Additional validation tests cover deterministic fingerprints, fingerprint sensitivity to row order/schema/value changes, optional-dependency import behavior, unexpected columns, invalid real numeric values including complex dtypes and fractional counts in `longdouble` columns, integral-float target normalization, all five missingness indicators, train-only scaler fitting, and rejection of changed datasets, invalid partitions, cross-group assignments, and coherently reassigned groups before preprocessing.

## Limitations

The 484 prefix rows are correlated observations from only 100 executions and 10 templates. Template-held-out evaluation has very few groups and templates are class-pure in this synthetic sample. Uncertainty and generalization claims should remain limited to preparation methodology; model evaluation and real-world performance remain untested.
