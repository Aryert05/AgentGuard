# Phase 3.3 — Supervised Baseline Implementation Report

**Branch:** `phase/03-ml-intelligence`<br>
**Scope:** fixed supervised baselines trained on grouped training partitions; validation-only threshold selection and reporting. No final-test predictions or metrics were generated. No anomaly detector was trained.

## Experiments and setup

Two separate validation-only experiments were run on `data/generated/agenttrace-v1/agenttrace_prefixes.csv` using the approved 18-column `MODEL_FEATURE_COLUMNS` allow-list:

1. **Execution-held-out (primary):** `execution_id` is the indivisible group. Seed `2026`, fractions `0.6/0.2/0.2`; train has 60 executions / 282 prefix rows, validation has 20 / 119, and test has 20 / 83. No execution crosses partitions.
2. **Task-template-held-out (exploratory):** `task_template_id` is the indivisible group. The deterministic 6/2/2 template split gives train 6 templates / 262 rows, validation 2 / 150, and test 2 / 72. The two validation and two test templates cover both classes, but each template is class-pure.

The model matrix has 18 numeric features plus five fixed structural-missingness indicators (23 transformed columns); identifiers, metadata, and targets are not model inputs. Preprocessing was fitted on train only: median imputation, fixed missingness indicators, then `StandardScaler`. Only train arrays/labels were passed to estimator fitting; validation arrays/labels/scores were used for metrics and threshold selection. The test partitions were not scored, and both artifacts have `test_metrics: null`.

Four fixed baselines used model random state `2026`: `DummyClassifier(strategy="prior")`, balanced Logistic Regression (`C=1`, `liblinear`, 1000 iterations), Random Forest (200 trees, depth 4, leaf size 5, balanced subsample weighting), and class-weighted HistGradientBoosting (100 iterations, 7 leaves, leaf size 10, L2=1, early stopping disabled). These are prespecified baselines, not tuned models.

Threshold selection remains validation row-weighted F1 over 0.1–0.9, breaking ties by precision then higher threshold. The 0.50 default is recorded as a separate reference. A single-class validation partition now raises `ThresholdSelectionError`; the workflow records the optimized threshold as unavailable and uses 0.50 only as an explicitly marked inference fallback. Thresholds are not selected from group-balanced F1, so a selected threshold need not maximize the execution-balanced metric.

**Threshold-selection optimism.** The selected threshold maximizes F1 on the validation rows, and the selected-threshold metrics are then computed on those same validation rows. Those metrics are therefore in-sample with respect to threshold selection and are optimistically biased. The group-bootstrap intervals below are conditional on the selected threshold; they resample validation groups but do not account for threshold-selection uncertainty and are not intervals for the complete selection procedure. Default-0.50 metrics avoid this specific selection step but remain single-split estimates.

## Execution-held-out validation results

All metrics use validation data only. ROC-AUC and Average Precision use validation probabilities. Confusion-matrix rows/columns are labels `[0, 1]`. These results describe one synthetic validation split and are not test or deployment estimates.

### Row-weighted metrics at the validation-selected threshold

| Model | Threshold | Precision | Recall | F1 | ROC-AUC | Average Precision | Confusion matrix | Fit seconds* | Validation batch seconds* |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| Dummy prior | 0.60 | 0.6975 | 1.0000 | 0.8218 | 0.5000 | 0.6975 | `[[0, 36], [0, 83]]` | 0.0003 | 0.00005 |
| Logistic Regression | 0.40 | 0.7523 | 0.9880 | 0.8542 | 0.8347 | 0.9197 | `[[9, 27], [1, 82]]` | 0.0067 | 0.00063 |
| Random Forest | 0.40 | 0.7523 | 0.9880 | 0.8542 | 0.8404 | 0.9194 | `[[9, 27], [1, 82]]` | 0.2024 | 0.00758 |
| HistGradientBoosting | 0.40 | 0.7523 | 0.9880 | 0.8542 | 0.8404 | 0.8979 | `[[9, 27], [1, 82]]` | 0.1954 | 0.00520 |

\* Single-run wall-clock measurements from this local environment. Inference is one batch of 119 validation rows; this run has no warm-up, repeated timing, per-row latency, or serving benchmark. Timing differences should not be interpreted as stable performance comparisons.

The three learned baselines have identical thresholded predictions in this run, but their probabilities and ranking metrics differ. No model-selection claim should be made from the close, single-split results.

### Per-class metrics at the selected threshold

Support is the number of validation rows whose true label belongs to that class. Values are copied from `validation_metrics_at_selected_threshold.per_class` in the execution-held-out artifact; undefined values are preserved rather than imputed.

| Model | Class | Support | Precision | Recall | F1 |
|---|---|---:|---:|---:|---:|
| Dummy prior | 0 | 36 | undefined | 0.0000 | undefined |
| Dummy prior | 1 | 83 | 0.6975 | 1.0000 | 0.8218 |
| Logistic Regression | 0 | 36 | 0.9000 | 0.2500 | 0.3913 |
| Logistic Regression | 1 | 83 | 0.7523 | 0.9880 | 0.8542 |
| Random Forest | 0 | 36 | 0.9000 | 0.2500 | 0.3913 |
| Random Forest | 1 | 83 | 0.7523 | 0.9880 | 0.8542 |
| HistGradientBoosting | 0 | 36 | 0.9000 | 0.2500 | 0.3913 |
| HistGradientBoosting | 1 | 83 | 0.7523 | 0.9880 | 0.8542 |

The dummy model's negative-class precision and F1 are undefined because it predicts no negative-class rows at its selected threshold.

### Default versus selected threshold, and execution-balanced metrics

Each execution contributes total weight `1.0`, divided equally across its prefix rows. Weighted confusion-matrix entries can therefore be fractional; ordinary row-weighted metrics are reported separately.

| Model | Default F1 (row) | Selected F1 (row) | Default F1 (execution-balanced) | Selected F1 (execution-balanced) |
|---|---:|---:|---:|---:|
| Dummy prior | 0.8218 | 0.8218 | 0.7097 | 0.7097 |
| Logistic Regression | 0.7424 | 0.8542 | 0.6770 | 0.7591 |
| Random Forest | 0.7424 | 0.8542 | 0.6770 | 0.7591 |
| HistGradientBoosting | 0.7424 | 0.8542 | 0.6770 | 0.7591 |

At the default 0.50 threshold, the learned models have row precision `1.0000`, recall `0.5904`, F1 `0.7424`, and confusion matrix `[[36, 0], [34, 49]]`. At the selected 0.40 threshold, row recall rises to `0.9880` but false positives increase to 27, giving the row F1 above.

The execution-balanced view is more conservative. The dummy model falls from row F1 `0.8218` to `0.7097`. The learned models fall from row F1 `0.8542` to execution-balanced F1 `0.7591` (precision `0.6166`, recall `0.9870`, accuracy `0.6554`, weighted confusion matrix `[[2.25, 6.75], [0.1429, 10.8571]]`). Their default-0.50 execution-balanced F1 is `0.6770` (precision `1.0000`, recall `0.5117`). Thus the selected threshold improves the balanced F1 over the default, but the learned models are only modestly above the dummy model once executions receive equal weight. Per-execution row outcomes are recorded in both artifacts: at the selected threshold, each of the 9 negative-class validation executions has 3 of its 4 rows predicted positive, while 10 of the 11 positive executions are classified correctly at every row (one 7-row positive execution has one missed row). That pattern explains why row precision and execution-balanced precision disagree.

### Group-bootstrap uncertainty

The cluster bootstrap resampled complete execution groups with replacement, 2000 iterations, seed `20261101`, 95% percentile intervals. All 2000 execution-level replicates contained both classes.

| Model / metric | Default F1 interval | Selected F1 interval |
|---|---|---|
| Dummy, row-weighted | `[0.6389, 0.9231]` | `[0.6389, 0.9231]` |
| Dummy, execution-balanced | `[0.5185, 0.8571]` | `[0.5185, 0.8571]` |
| Learned models, row-weighted | `[0.6329, 0.8000]` | `[0.7023, 0.9373]` |
| Learned models, execution-balanced | `[0.5492, 0.7736]` | `[0.5809, 0.8889]` |

Only 20 validation executions are available, so these intervals are unstable and exploratory, not confirmatory uncertainty estimates.

### Prefix-length and observation-time strata

The table shows selected-threshold execution-balanced F1 for the learned models. Prefix bins are `1`, `2`, `3-5`, `6-10`, `11+`; time bins are `missing`, `0-25`, `25-50`, `50-100`, `100+` ms. Bins were fixed before metric inspection. A bin is flagged when it has fewer than 10 rows or fewer than 10 independent groups.

`elapsed_time_ms` measures the duration between the first and last events in the observed prefix. It is not the time from the prefix to the terminal outcome or failure. The time-stratified table therefore describes how long the observed prefix spans, not time-to-failure; it does not directly measure how early a failure can be predicted, and no time-to-failure claim is made.

| Stratum | Rows | Executions | Balanced F1 | Flag |
|---|---:|---:|---:|---|
| Prefix 1 | 20 | 20 | 0.7097 | |
| Prefix 2 | 20 | 20 | 0.7097 | |
| Prefix 3–5 | 47 | 20 | 0.8153 | |
| Prefix 6–10 | 32 | 7 | 1.0000 | insufficient groups |
| Prefix 11+ | 0 | 0 | undefined | empty bin |
| Time missing | 20 | 20 | 0.7097 | |
| Time 0–25 ms | 40 | 20 | 0.7097 | |
| Time 25–50 ms | 0 | 0 | undefined | empty bin |
| Time 50–100 ms | 34 | 20 | 0.9846 | |
| Time 100+ ms | 25 | 7 | 1.0000 | insufficient groups |

The dummy model's available bins all have execution-balanced F1 `0.7097`; its `6-10` prefix and `100+ ms` bins are also flagged. At the default 0.50 threshold, the learned models made no positive predictions at prefix lengths 1–2 or in the missing-time group, so F1 was undefined there. The selected threshold predicts positives in those groups, but early-prefix F1 remains lower than later-prefix F1. The small flagged bins should not be read as evidence of strong late-prefix performance.

## Task-template-held-out exploratory results

This experiment is scenario-shift evidence only: 10 class-pure templates, two validation templates, and two independent test templates. It is not a reliable generalization estimate or model-selection basis, and it should not be combined with the execution-held-out artifact.

| Model | Threshold | Precision | Recall | Row F1 | Execution-balanced F1 | ROC-AUC | Average Precision | Confusion matrix |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Dummy prior | 0.60 | 0.7333 | 1.0000 | 0.8462 | 0.6667 | 0.5000 | 0.7333 | `[[0, 40], [0, 110]]` |
| Logistic Regression | 0.40 | 0.7857 | 1.0000 | 0.8800 | 0.7273 | 0.8873 | 0.9525 | `[[10, 30], [0, 110]]` |
| Random Forest | 0.40 | 0.7857 | 1.0000 | 0.8800 | 0.7273 | 0.8875 | 0.9516 | `[[10, 30], [0, 110]]` |
| HistGradientBoosting | 0.40 | 0.7857 | 1.0000 | 0.8800 | 0.7273 | 0.8875 | 0.9357 | `[[10, 30], [0, 110]]` |

### Per-class metrics at the selected threshold

Support is the number of validation rows whose true label belongs to that class. Values are copied from `validation_metrics_at_selected_threshold.per_class` in the task-template-held-out artifact; undefined values are preserved rather than imputed.

| Model | Class | Support | Precision | Recall | F1 |
|---|---|---:|---:|---:|---:|
| Dummy prior | 0 | 40 | undefined | 0.0000 | undefined |
| Dummy prior | 1 | 110 | 0.7333 | 1.0000 | 0.8462 |
| Logistic Regression | 0 | 40 | 1.0000 | 0.2500 | 0.4000 |
| Logistic Regression | 1 | 110 | 0.7857 | 1.0000 | 0.8800 |
| Random Forest | 0 | 40 | 1.0000 | 0.2500 | 0.4000 |
| Random Forest | 1 | 110 | 0.7857 | 1.0000 | 0.8800 |
| HistGradientBoosting | 0 | 40 | 1.0000 | 0.2500 | 0.4000 |
| HistGradientBoosting | 1 | 110 | 0.7857 | 1.0000 | 0.8800 |

The dummy model's negative-class precision and F1 are undefined because it predicts no negative-class rows at its selected threshold.

At the default 0.50 threshold, each learned model has row precision `1.0000`, recall `0.7000`, row F1 `0.8235`, execution-balanced F1 `0.8235`, and confusion matrix `[[40, 0], [33, 77]]`. The selected threshold raises row F1 but lowers execution-balanced F1 from `0.8235` to `0.7273` because the two validation templates are weighted equally and one large template dominates the row result.

The template bootstrap has only two resampled groups. Of 2000 replicates, 997 contained both classes, 500 were positive-only, and 503 were negative-only; 503 replicates therefore had undefined F1 and were excluded from the F1 intervals. Selected-threshold intervals are degenerate or very wide (row F1 `[0.8800, 1.0000]`; execution-balanced F1 `[0.7273, 1.0000]`). These intervals are not usable as stable uncertainty estimates.

## Reproducibility and provenance

Artifacts are local and ignored by Git:

- `data/generated/agenttrace-v1/results/phase-03-3-validation.json` — SHA-256 `1a502ff72bb050c9696fc00100ceb040f11944b5f9a9a41c84fa92fc1d26d345`, size `557810` bytes.
- `data/generated/agenttrace-v1/results/phase-03-3-template-held-out.json` — SHA-256 `cacd7f8394fbc1287c357e1b689d06d7847fd6aad1eef4413479de494abb7500`, size `490858` bytes.
- Sidecars `phase-03-3-validation.sha256.json` and `phase-03-3-template-held-out.sha256.json` record the digest of the final artifact bytes. The digest is not embedded in the artifact.

Both artifacts record:

- Base Git commit `1efa7ea18bfe896c2239bbca3ad82fb070db6791`, branch `phase/03-ml-intelligence`, and `worktree_dirty: true`.
- Hashes of 17 relevant source files and a combined code-snapshot SHA-256 (`ed044a0719d367d37332491bed1089ea4d7936aabcc11258c7cf43e05bbae07d`) so uncommitted implementation code is identifiable even though the worktree is dirty.
- Dataset fingerprint `8ae6117c758e02aa2affdac4518e1240a0361784f35cd18afef76017f8c3f544`, source SHA-256 `078bbe526a623a5c1992a8c18b144c970a26075047d0c8668efeaee77e1e28c4`, and manifest SHA-256 `1bc0dd56c4f70a5761f4b0d4b2d2bb1e88d4e5b7416ab94321e21928cd7a2b54`.
- Execution split fingerprint `af4a349e4842d6c88327996f52ed1122cd05ac7953ac20c7d24ace770fe4634e`; template split fingerprint `53f4732d3943d64dd9cc311551990032e35f0f761856838c75ebabcb4cbfe3db`.
- Persisted split-file cross-checks: execution held-out file SHA-256 `1cc2f3dca8ff5ac511a0e8b1bdda46e497966c48aad179c1eccdc6483d3f03bc`; template held-out file SHA-256 `3659878472c96b0bf8dfa606383416b9ff667d2a746f2a99015804ccf2e8a0e3`. Both exact group/row assignments matched the deterministic reconstruction.
- Split strategy, group column, seed, fractions, class counts, group IDs, model parameters, analysis configuration, bootstrap iterations/seed, threshold grid, metrics, and timings.
- Runtime versions: Python 3.13.12, NumPy 2.5.4, scikit-learn 1.9.1; split RNG bit generator PCG64.

The deterministic split reconstruction remains canonical; persisted Phase 3.2 split files are an audited cross-check. No model binary is saved.

## Scope and limitations

The only scored partitions are validation partitions. Test labels were not supplied to estimators, metrics, threshold selection, or validation analyses; shared split-integrity checks verify labels for audit consistency, and the shared preprocessing helper applies its train-fitted transformer to test features, but this run consumes neither test output for modeling nor test predictions.

The dataset has 100 executions, 10 class-pure templates, and 484 correlated prefixes. The 119-row execution validation set represents only 20 executions; prefix-level metrics overstate independent sample size if read as 119 independent outcomes. Execution-balanced metrics and cluster-bootstrap intervals address this partially but remain small-sample estimates.

The synthetic generator can encode scenario signatures and observed failure cues; these metrics do not establish real-agent quality. Execution-held-out remains the primary experiment. Template-held-out is exploratory because only two independent templates are in each of validation and test. The threshold criterion is row-weighted F1, so selected thresholds are not optimized for execution-balanced F1; the template experiment shows the two can move in opposite directions.

Broader data collection, repeated group-aware outer evaluation, a threshold rule aligned with the intended operating objective, and a frozen one-time test protocol are needed before generalization claims.

## Verification

- Phase 3.3-specific tests: **20 passed**.
- Full Phase 3 tests: **57 passed**.
- Phase 1 regression tests: **25 passed**.
- Phase 2 unit regression tests: **30 passed**.
- Full suite: **112 passed, 1 skipped**.
- `git diff --check` passed.

The PostgreSQL integration test was skipped because the runtime and migration test database setup was unavailable; it was not accessed or verified. No Docker/PostgreSQL service was started for this work.
