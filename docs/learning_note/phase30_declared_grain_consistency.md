# Phase 30 — Declared Grain Consistency Hardening

## 1. Executive Summary

`detail`/`entity` grain + final-feeding collapsing aggregate를 **WARNING → blocking ERROR**로 승격했다.  
Offline/stress **FP=0**, live에서 **composite final 0→100**, overall **84.21→89.47**, **unsafe=0**.  
Type C (`same_schema` group+agg)는 의도대로 미차단. **Adopt (A)**.

## 2. Exact Change

`integration_plan_validate.py` `_validate_final_output_requirements`:

```text
grain ∈ {detail, entity}
AND (aggregate_produces_final OR upstream_aggregate_feeds_final)
```

기존과 **동일 조건**, severity만 WARNING → **ERROR** (`final_grain_contradiction`).

새 user-intent inference 없음. `aggregate` 단독 차단 없음. `group`/`summary`+aggregate는 유효.

Escalation: `final_grain_contradiction`을 Phase 28 trigger set에 추가 (final-contract family; scenario routing 아님).

## 3. Offline Detection (Experiment A/B)

| | TP | FP | TN | FN | FPR |
|--|---:|---:|---:|---:|----:|
| A Phase 29 diagnostic | 8 | 0 | 60 | 8 | 0 |
| B Blocking offline | 8 | 0 | 60 | 8 | 0 |

FN=8은 Type C / non-row-grain silent (blocking 범위 밖).

## 4. Stress Test (Experiment C)

Valid fixtures (join→agg group, multi-key metrics+select, union→agg, rename→agg): **all pass, FP=0**.  
Entity+collapse fixture: correctly blocked.

## 5. Live End-to-End (Experiment D)

| Strategy | Overall | Safe | Unsafe | 32B % | Latency est. |
|----------|--------:|-----:|-------:|------:|-------------:|
| Phase 28 escalation | 84.21 | 96.49 | 0 | 10.53 | ~24s |
| Phase 30 grain harden | **89.47** | **96.49** | **0** | **17.54** | ~34s |

Scenario finals (live mean):

| | P28 | P30 |
|--|----:|----:|
| composite final | 0 | **100** |
| lookup final | 100 | 100 |
| three-file final | 0 | 0 |
| dirty final | 100 | 100 |

Residuals:

- **Type D recovery:** composite — 3/3 runs `strong_escalation_success`, ops=`join`, grain=`detail`
- **Type B/C:** three_file still 0 (run1 escalated but structurally wrong; run2/3 premature cannot_plan)
- **Type C:** same_schema silent wrong unchanged (group+agg consistent)

## 6. Retry / Escalation Trace (Type D)

```text
7B: entity/detail + join + aggregate (+select)
  → final_grain_contradiction ERROR
  → fast retries exhaust
  → escalate (recoverable_plan_validation_failure)
  → 32B: join-only, grain=detail
  → validators pass
  → overall_ok
```

## 7. Architecture Audit

| Check | Result |
|-------|--------|
| scenario hardcoding | PASS |
| domain / column hardcoding | PASS |
| semantic routing | PASS |
| Plan mutation | PASS |
| Validator auto-repair | PASS |
| Executor inference | PASS |
| strong-model bypass | PASS |
| evaluator relaxation | PASS |

## 8. Regression

- pytest: **581 passed, 2 skipped**
- deterministic: **100 / 100 / 0**
- Tests: `tests/test_phase30_grain_hardening.py` (+ updated P24/P25 expectations)

## 9. Final Recommendation

### **A. Adopt blocking grain consistency validation**

근거:

- FP=0 (offline + stress)
- unsafe=0
- Type D composite full recovery
- Type C not incorrectly blocked
- 32B invocation 17.54% ≪ 100%; latency ≪ 32B-only
- No scenario exceptions required

## Phase 31 gate

남은 residual: Type B (required-field under-declaration) + Type C (consistent wrong intent).  
권고: **Phase 31A required-output contract diagnostics** 또는 **31B semantic verification research**. `route_multi` / Shadow 아직 금지.

## Artifacts

```text
benchmark_results/multi/phase30/
  baseline_freeze.json
  grain_candidate_offline.json
  grain_stress_test.json
  live_grain_hardening/
  live_grain_hardening_summary.json
```
