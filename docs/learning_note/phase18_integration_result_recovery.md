# Phase 18 — Integration Result Validator & Planner Recovery Loop

> **구현 Phase** (`route_multi` 전환 / Benchmark / legacy fallback 없음)  
> Plan Validator(전) + Executor + **Result Validator(후)** + **Planner retry**

---

## 1. Goal

1. 실행된 `IntegrationExecutionResult`가 계약·안전성상 정상인지 검증  
2. Plan/Execution/Result 실패 시 evidence만 Planner에 전달하여 제한 재계획  
3. `cannot_plan`은 정상 safe outcome

```text
Understanding + prompt
  → Planner → Plan Validator → Executor → Result Validator
       ↑______________ feedback (evidence only) ______________|
```

---

## 2. Filter contract audit (Phase 18 시작 시)

### 발견
Phase 17 Executor에 다음 semantic inference가 있었음:

```text
value가 문자열이고 DataFrame 컬럼명과 일치 → right_column으로 승격
```

예: `value: "안전재고"` + 컬럼 `안전재고` 존재 → column-vs-column 해석.

### 수정
| 계층 | 변경 |
|---|---|
| Phase 15 parser | literal `{column,operator,value}` vs explicit `{left_column,operator,right_column}`만 canonicalize |
| Phase 16 validator | value→column 자동 승격 제거; `right_column`만 col-vs-col |
| Phase 17 executor | 동일; value 문자열은 항상 literal |
| Planner prompt | col-vs-col은 `right_column` 명시 요구 |

---

## 3. Responsibility split

| 계층 | 질문 |
|---|---|
| Plan Validator | 실행 전 위험이 있는가? (estimated amp, cardinality, deps) |
| Executor | 검증된 Plan을 그대로 실행 |
| Result Validator | 실행 결과가 실제로 안전한가? (actual amp, invariants) |
| Planner Retry | evidence로 다른 계획을 세울 수 있는가? / cannot_plan |

**모두 Plan/DataFrame을 수정하지 않음.**

---

## 4. Contracts

### IntegrationResultValidationResult
`valid`, `errors/warnings/infos`, `metadata`, `failure_stage`

### IntegrationPipelineResult
`status: success|cannot_plan|failed`, plan, plan_validation, execution, result_validation, retry_log, final_output, metadata

### Failure stages
`integration_plan_generation` · `integration_plan_validation` · `integration_execution` · `integration_result_validation`

---

## 5. Operation result invariants (요약)

| Op | ERROR | WARNING/INFO |
|---|---|---|
| join | amp≥10x, severe inner retention&lt;2%, key missing | amp≥2x, unmatched≥50%, retention&lt;10%, estimate mismatch |
| union | row count ≠ Σ inputs, column order mismatch | numeric→object degradation, unexpected nulls |
| aggregate | group not unique, missing metric, all-NaN, Inf | empty aggregate |
| filter | row increase, predicate violation | empty result |
| rename | row count change, target missing | — |
| select | columns/order mismatch, row change | — |
| final | missing, duplicate cols, Inf | empty, all-null cols |

AMP thresholds: **Phase 16 `AMP_WARNING_RATIO` / `AMP_ERROR_RATIO` 재사용**.

Estimate vs actual: `actual/estimated ≥ 3` and `actual ≥ warning` → `unexpected_join_amplification`.

---

## 6. Feedback

`format_integration_result_validation_feedback` / `format_integration_execution_feedback`  
+ 기존 `format_integration_validation_feedback`

처방 금지: key/how/op 교체 지시. evidence + “materially different plan or cannot_plan”만.

---

## 7. Retry loop

`run_integration_pipeline(..., max_retries=2)` → 총 3 attempts (single-file과 동일).

흐름: plan → (duplicate sig?) → plan validate → execute → result validate → success / feedback retry / cannot_plan / exhausted failed.

Duplicate: `canonical_integration_plan_signature` 재사용 → `repeated_plan` feedback 강화. Python이 대체 Plan을 생성하지 않음.

Legacy/PandasAI fallback **없음**.

---

## 8. Observability (Phase 19용)

`attempt_count`, `retry_count`, `first_plan_success`,  
`plan_validation_failure_count`, `execution_failure_count`, `result_validation_failure_count`,  
`duplicate_plan_count`, `final_status`, `selected_operations`, `source_count`, `final_shape`, `warnings`

retry_log entry: attempt, failure_stage, failure_codes, plan_signature, selected_ops, evidence_summary.

---

## 9. Files

```text
core/integrate/integration_result_validation_types.py  (new)
core/integrate/integration_result_validate.py           (new)
core/integrate/integration_pipeline.py                  (new)
core/integrate/integration_plan_types.py                (filter contract)
core/integrate/integration_plan_validate.py             (filter)
core/integrate/integration_execute.py                   (filter)
core/integrate/integration_planner.py                   (retry_feedback + filter prompt)
core/integrate/__init__.py
tests/test_phase18_integration_result_recovery.py       (new)
tests/test_phase17_integration_executor.py              (filter tests)
docs/learning_note/phase18_integration_result_recovery.md
```

Unchanged: `route_multi`, single-file AnalysisPlan, merge_engine production path.

---

## 10. Phase 19 Benchmark contract

측정 가능 metric:

- `status` rates: success / cannot_plan / failed  
- stage별 failure counts  
- `duplicate_plan_count`, `retry_count`  
- `selected_operations` / plan signature diversity  
- final_shape, warning codes  
- (optional) actual amplification distribution from execution metadata
