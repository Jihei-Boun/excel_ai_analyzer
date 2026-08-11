# Phase 19 — Multi-file Integration Pipeline Benchmark

> **측정 Phase** (production Planner/Validator/Executor 성능 개선 금지)  
> `route_multi` 미전환 · legacy/PandasAI fallback 없음

---

## 1. Goal

새 multi-file pipeline의 **baseline**을 객관 측정한다.

질문: 다양한 관계/요청에서 얼마나 범용·안전한가?  
특히: **모르는 상황에서 잘못 합치지 않고 멈출 수 있는가?**

---

## 2. Layout

```text
tests/benchmark_multi/
  cases/*.yaml
  datasets/          # seed=19 synthetic xlsx
  generate_datasets.py
  schema.py
  evaluate.py        # L1–L6
  metrics.py
  runner.py
  test_deterministic.py
  test_evaluator.py
  README.md
benchmark_results/multi/
```

---

## 3. Success / Safety 정의

| status | 의미 |
|---|---|
| success | 안전한 통합 결과 전달 |
| cannot_plan | 거부 (모호/위험 시 정답 가능) |
| failed | retry 소진 등 |

`safe_outcome`: expected에 맞는 안전한 종료 (성공 통합 **또는** 올바른 거부/차단)  
`unsafe_execution`: **성공으로 잘못된/금지된 통합을 전달** (KPI → 0 목표)

Planner correctness ≠ Integration safety.

---

## 4. Evaluation levels

1. Understanding (relationship allowed/forbidden)  
2. Plan (required/forbidden ops, keys when unambiguous)  
3. Plan safety (validator block codes)  
4. Execution / golden result  
5. Result validation signals  
6. Recovery (first plan / retry / duplicate)

---

## 5. Deterministic baseline

```text
cases=20  overall_ok=100%  safe=100%  unsafe=0%
```

CI: `pytest tests/benchmark_multi/` — harness + fixed plans.

---

## 6. Live 3-run (qwen2.5:7b)

| metric | mean | min | max | std |
|---|---|---|---|---|
| pipeline_success_rate | 65.0 | 65 | 65 | 0 |
| safe_outcome_rate | 75.0 | 75 | 75 | 0 |
| unsafe_execution_rate | **10.0** | 10 | 10 | 0 |
| overall_ok_rate | 60.0 | 60 | 60 | 0 |
| first_plan_success_rate | 70.0 | 70 | 70 | 0 |
| retry_success_rate | 10.0 | 10 | 10 | 0 |
| cannot_plan_rate | 15.0 | 15 | 15 | 0 |

(3 runs identical — low sampling diversity / model determinism.)

### Unsafe cases (live)
- `ambiguous_keys_001`: join 강행 (should cannot_plan)
- `repeated_plan_001`: invalid key 이후에도 성공 경로로 unsafe 집계 (case intent=negative)

### Weak scenarios
- composition (filter→union→agg, union→agg, budget, three-file)
- ambiguous key refusal
- dirty robustness

### Relative strengths
- master_detail / lookup / rename-or-direct join / same-schema union
- unrelated → cannot_plan
- many_to_many / incompatible → validator block (safety)

---

## 7. Phase 20 priorities (effect order)

1. **Ambiguous key → cannot_plan** (unsafe_execution↓)  
2. Composition prompting (union→aggregate, filter chains, 3-file)  
3. Relationship label calibration (false join_candidate)  
4. Composite-key uniqueness (per-column false many_to_many) — validator evidence  
5. Dirty rename / whitespace column contract  
6. Retry diversity when planner repeats wrong composition

---

## 8. route_multi 전환 준비

**아직 아님.**  
`unsafe_execution_rate=10%` live baseline이 남아 있고 composition 성공률이 낮다.  
Safety≈0 unsafe + composition 개선 후 전환 검토.
