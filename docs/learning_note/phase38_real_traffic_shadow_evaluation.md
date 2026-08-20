# Phase 38 — Limited Real-Traffic Shadow Evaluation & Production Gate

## Observation period / configuration

| Item | Value |
|------|-------|
| Observation window | 2026-08-20 (measurement tooling run) |
| Shadow production activation | **OFF** (`MULTI_SHADOW_ENABLED=false`) |
| Sample rate | N/A (not enabled in production) |
| Max concurrency / timeout | defaults 1 / 600s (unchanged Phase 37) |
| Candidate version | `phase35_semantic_escalation_v1` (frozen) |
| Shadow infrastructure | Phase 37 frozen |

Phase 38 is a **measurement** phase. No planner/verifier/validator/DSL/routing changes.

---

## Sample size (honest)

| Source | N | Label |
|--------|--:|-------|
| **Real multi-file traffic** | **0** | `real_traffic` |
| Controlled replay (Phase 35 full live 19×3) | 57 | `controlled_replay` |
| Synthetic / Phase 37 dry-run | excluded from gate | not real |

**observed N (real) = 0**

Without production Shadow ON and without inbound multi-file user requests in this environment, real-traffic evidence is **insufficient**. Replay is reported separately and **must not** be called real traffic.

---

## Sampling policy

When Shadow is eventually enabled for observation:

- random / operational sampling only
- no 3-file-only, aggregate-only, domain, or prompt selection
- accounting fields: eligible / sampled / started / completed / skipped_sampling / skipped_capacity / timeout / infrastructure_error

This window: Shadow never enabled → sampling counters for real traffic are zero / unknown.

---

## Real vs replay

| | Real | Controlled replay |
|--|------|-------------------|
| Source | `data/shadow_telemetry` production JSONL | Phase 35 `full_live` suite export |
| Dual L/S | yes (legacy route_multi + shadow) | **no** — candidate-only |
| Gate G12 | required | cannot satisfy alone |

Phase 35 suite has no paired `route_multi` outcome, so the L/S outcome matrix is **not applicable** to replay. Replay still provides latency / path / verifier / 32B characterization consistent with Phase 36.

---

## Privacy / data handling

- Telemetry defaults: prompt hash only; no full Excel rows
- Review packets: opaque ids + schema context; no unnecessary raw rows
- This run wrote aggregates only under `benchmark_results/multi/phase38/`

---

## Primary questions (answers)

| Q | Answer |
|---|--------|
| Q1 Real vs synthetic distribution | **Unknown** — real N=0 |
| Q2 Shadow success on real | **Unknown** — real N=0 |
| Q3 L/S structural agreement | **Unknown** — no correlated real pairs |
| Q4 Failure families (real) | **Unknown** |
| Q5 Type B/C/D on real | **Not observed** (absent traffic) |
| Q6 32B / latency on real | **Unknown**; replay mirrors Phase 36 (~26% 32B, mean ~103s) |
| Q7 Production latency impact | **No impact** — Shadow remained OFF |
| Q8 Enough for replacement eval? | **No** |

### Controlled replay (supporting only)

- Pipeline safe completion (`success|cannot_plan`, unsafe=0): **~91%+** class rates; suite overall_ok **96.49%** (Phase 35)
- Path A/B/C/D counts: 28 / 11 / 14 / 4 (same as Phase 36)
- Latency mean **103.14s**, p50 **23.98s**, p95 **313.75s**
- failure 32B **19.3%**, semantic 32B **7.02%**, total **26.32%**, double **0%**
- Verifier invoked **71.93%**; PASS/FAIL/UNCERTAIN = 37 / 3 / 1 on n=57

---

## Production Gate

| Gate | Result |
|------|--------|
| G1 production unaffected | **YES** (Shadow OFF) |
| G2 unsafe=0 | **YES** (replay unsafe=0; real N=0) |
| G3 shadow stable on real | **INSUFFICIENT** |
| G4 disagreement observable | **YES** (tooling) |
| G5 no systematic semantic harm | **INSUFFICIENT** (no human review) |
| G6 verifier useful on real | **INSUFFICIENT** |
| G7 Type-B frequency understood | **INSUFFICIENT** |
| G8 no new severe family | **INSUFFICIENT** |
| G9 resource burden understood | **YES** (Phase 36 prior) |
| G10 latency understood | **YES** (sync replacement still unsuitable) |
| G11 no semantic routing | **YES** |
| G12 evidence sufficient | **NO** |

### Recommendation: **C — Evidence Insufficient**

Next: keep candidate + Shadow infra frozen; enable limited Shadow observation when real multi-file traffic exists; continue Phase 38 observation (do not migrate).

Hard blockers for A/B were not triggered. Latency remains an **operational** blocker for sync replacement even if future real evidence is strong (**B** path).

---

## Architecture audit

All forbidden patterns absent (scenario/domain/column/file/op routing; Python semantic winner; shadow response replace/fallback; prompt/benchmark auto-tuning). **PASS.**

---

## Artifacts

```text
benchmark_results/multi/phase38/
  experiment_config.json
  traffic_summary.json
  sampling_summary.json
  outcome_matrix.json
  structural_comparison.json
  disagreement_inventory.json
  manual_review.json
  failure_taxonomy.json
  verifier_metrics.json
  strong_model_metrics.json
  latency_distribution.json
  path_latency.json
  production_impact.json
  resource_observation.json
  type_bcd_observations.json
  generalization_analysis.json
  production_gate.json
  architecture_audit.json
  phase38_summary.json
```

Tooling: `tests/benchmark_multi/phase38_eval.py`, `phase38_real_traffic.py`; tests: `tests/test_phase38_eval.py`.

---

## Limitations

1. No production Shadow enablement in this environment/window.
2. Replay ≠ real; no fabricated user traffic.
3. No human semantic review labels.
4. L/S matrix empty until correlated real telemetry exists.
