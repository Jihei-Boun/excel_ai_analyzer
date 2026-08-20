# Phase 34 — Cheap Semantic Verification Generalization & Invocation Strategy

## 1. Executive Summary

Frozen **7B V1 (Prompt+Plan)** **strongly generalizes on historical_real** data: Type-C **9/9**, valid FP **0/21**, parse **0**, latency ~**3.5s**. Overall set (incl. synthetic fixtures) shows Type-C recall **93.3%** and valid FP **5.9%** — FPs/FN only on synthetic boundary cases. **Blanket cheap verification** (~34+3.5≈**38s**) remains preferable to selective routers. Production unchanged.

## 2. Frozen Verifier

Identical to Phase 33:

```text
model = qwen2.5:7b
variant = V1 (user_prompt + IntegrationPlan only)
temperature = 0
prompt sha256 frozen in phase33_verifier_freeze.json
```

No V2/V3, no prompt retuning.

## 3. Generalization Dataset

| Slice | Count |
|-------|------:|
| valid | 35 |
| Type C | 15 |
| historical valid | 21 |
| synthetic valid (fixed_plan) | 14 |
| historical Type C | 9 |
| synthetic Type C | 6 |

Diversity: grains detail/entity/group/summary; ops include union, join, rename→join, filter→union→agg, join→agg, join→join→agg; **13 legitimate group/summary+aggregate** valids; domains orders/sales/inventory/budget/dirty/generic.

## 4. Main Results

| Metric | Phase 33 | Phase 34 overall | Phase 34 historical_real |
|--------|---------:|-----------------:|-------------------------:|
| Type-C recall | 100% | **93.3%** | **100%** |
| Type-C precision | 100% | **87.5%** | **100%** |
| Valid FP | 0% | **5.9%** | **0%** |
| Uncertain | 0% | 2% | 0% |
| Parse fail | 0% | **0%** | **0%** |
| Latency mean | ~3.6s | **3.53s** | 3.56s |

## 5. False Positive Analysis (n=2)

Both **synthetic_valid** fixed_plans with thin/empty `required_columns`:

1. `rename_join_001` — connect customers/orders; verifier demanded more explicit order fields  
2. `retry_recovery_001` — empty required_columns; verifier FAIL `missing_requested_output`

**Historical valid FP = 0.** Not an aggregate-shortcut failure (both were detail joins).

## 6. False Negative Analysis (n=1)

One **synthetic_type_c**: dirty “행 합쳐줘” prompt + union→aggregate group plan → PASS. Ambiguous stack language; verifier shared planner-like acceptance of aggregation.

**Historical Type-C FN = 0.**

## 7. Stability

24 items × 3 runs: **verdict stability 100%**, reason stability 100%.

## 8. Latency

mean **3.53** / p50 **3.35** / p95 **4.86** (n=50) — matches Phase 33.

## 9. Blanket Verification Simulation

```text
invocation = 100% of final deterministic successes
+verifier ≈ 3.5s → total ≈ 37.7s vs baseline 34.1s
Type-C detected = 14 / missed = 1
false rejection = 2 (synthetic only)
```

## 10. Selective Evidence Analysis

`aggregate ∧ grain∈{group,summary}` covers 100% Type C but also **37% of valid** (legitimate aggregates). Benefit does not justify a new gate/router vs +3.5s blanket.

## 11. Layered Failure Coverage

| Family | Detector |
|--------|----------|
| schema/key/cardinality unsafe | Plan Validator |
| execution/materialization | Executor / Result Validator |
| Type D grain contradiction | Plan Validator (P30) |
| Type C plan-vs-request mismatch | **7B V1 Semantic Verifier candidate** |
| Type B under-declaration | unresolved |

## 12. Architecture Audit

scenario/domain/column routing, golden leakage, Plan mutation, production wiring, validator/escalation/`route_multi` changes — **PASS (none)**.

## 13. Regression

pytest pass; deterministic **100/100/0**; Phase 30/32 baseline & production planner prompt unchanged.

## 14. Recommendation

### Generalization: **B. Limited** (overall)

with strong historical subset (**A-quality** on `historical_real`).

### Invocation: **A. Blanket cheap verification favored**

### Phase 35

```text
Semantic Verification-triggered Escalation Experiment
```

Offline/live experimental: final success → 7B V1 → FAIL → strong replan → same deterministic layers. Still **no** `route_multi` / Shadow.

## Hermetic unit fixture (CI)

Unit/CI `build_generalization_dataset()` uses only:

```text
tests/benchmark_multi/fixtures/phase34_historical_plans.json
  historical_valid = 21
  historical_type_c = 9
```

plus tracked case YAML `fixed_plan` synthetics. It does **not** read gitignored `benchmark_results/`.  
Live harvest (optional research): `harvest_live_historical_plans()` / `scripts/regenerate_phase34_historical_fixture.py`.

## Artifacts

```text
benchmark_results/multi/phase34/
  phase33_verifier_freeze.json
  generalization_dataset.json
  historical_only_score.json
  valid_stress_results.json / type_c_stress_results.json
  false_positive_analysis.json / false_negative_analysis.json
  verifier_stability.json / verifier_latency.json
  blanket_policy_simulation.json / selective_evidence_analysis.json
  layered_coverage.json / recommendation.json
```
