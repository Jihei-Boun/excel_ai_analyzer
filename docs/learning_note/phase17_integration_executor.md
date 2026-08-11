# Phase 17 — Deterministic Integration Executor

> **구현 Phase** (Result Validator / retry / route_multi 전환 없음)  
> 입력: source DataFrames + `IntegrationPlan` + `IntegrationValidationResult`  
> 출력: `IntegrationExecutionResult`

---

## 1. Goal

검증을 통과한 `IntegrationPlan`을 **그대로** deterministic pandas로 실행한다.

```text
sources + IntegrationPlan + ValidationResult
        ↓
execute_integration_plan()   [gate]
        ↓
atomic step execution
        ↓
IntegrationExecutionResult
```

Executor는 “더 나은 통합”을 찾지 않는다. Planner/Validator 판단을 재실행하지 않는다.

---

## 2. Responsibility / Non-responsibility

### Responsibility
- execution gate 강제
- atomic op → pandas mapping
- dataset registry / intermediate outputs
- runtime failure를 structured error로 중단
- actual amplification / match metadata 측정
- plan·source immutability
- step lineage

### Non-responsibility
- join key / how / direction 선택·변경
- union 전 자동 rename / schema 추론
- aggregation fn·group 기본값
- filter 완화 / 0행 recovery
- subtotal·중복 자동 제거
- Planner retry / Result validation
- LLM 호출

---

## 3. Execution gate

실행 조건 (둘 다 필수):

```text
plan.status == "planned"
AND
validation_result.valid == True
```

거부 예:
- `cannot_plan`
- `valid=False`
- `validation_result is None` → `validation_required`

Gate 실패 시 Executor는 validation을 재수행하거나 Plan을 수정하지 않는다.

---

## 4. Atomic operation mapping

| Plan op | 구현 |
|---|---|
| `rename_columns` | `DataFrame.rename(columns=mapping)` |
| `filter_rows` | boolean mask (`eq/ne/gt/gte/lt/lte`); True만 유지 |
| `union_rows` | `pd.concat`; `column_policy`만 적용 |
| `join` | `pd.merge(left_on, right_on, how, suffixes=(_left,_right))` |
| `aggregate` | `groupby(...).agg(NamedAgg...)` |
| `select_columns` | `df.loc[:, columns]` (Plan 순서) |

`aggregate_merge` / `smart_join` 등 high-level op 추가 없음.

Filter operators는 Phase 15 contract만 (`between`/`in` 미구현 — contract 밖).

Column-vs-column: `value`가 다른 컬럼명이거나 명시적 `right_column`일 때만.

Null policy: comparison 결과가 True인 행만 유지 (`fillna(False)`).

---

## 5. Dataset registry

```text
datasets = { source_id: copy(df), ... }
step.output → datasets[output] = result
final = datasets[plan.final_output]
```

Caller source는 deep-copy만 사용. `inplace` mutate 금지.

---

## 6. Join policy

- `inputs[0]=left`, `inputs[1]=right` 고정 (방향 뒤집기 금지)
- keys/how는 Plan 그대로
- suffix: mechanical `_left` / `_right` (non-key 충돌)
- `indicator`로 match 통계 측정 후 `_merge` 컬럼 제거 (schema 오염 없음)
- amplification은 **측정만** (`output_rows / max(left,right)`); 위험해도 취소/키변경 없음

`merge_engine.merge_named_frames` / `infer_common_keys` **미사용** (key 추론·align 포함).

---

## 7. Union policy

`column_policy` (Phase 15):

| policy | 동작 |
|---|---|
| `aligned` | 첫 input 컬럼 순서 + 이후 extra stable; null fill |
| `intersection` | 공통 컬럼만 (첫 input 순서) |
| `union_with_nulls` | 전체 컬럼 union, null fill |

유사도 rename / numeric 추론 없음.

---

## 8. Aggregate policy

- `group_by` / `metrics[].column` / `function` 필수 (fn 누락 → failure, silent sum 금지)
- alias 없으면 mechanical로 source column name 사용 (fn 발명 아님); 충돌 → failure
- `groupby(..., sort=True)`로 deterministic group order
- subtotal/detail 자동 필터 없음

---

## 9. Failure policy

step 실패 시:
1. structured `IntegrationExecutionError` 기록
2. 전체 execution `success=False`로 중단
3. 이후 step 미실행
4. Executor-level key/op retry 금지

---

## 10. Metadata (Phase 18용)

전체: `source_count`, `step_count`, `final_row_count`, `final_column_count`

Join step: `left_rows`, `right_rows`, `output_rows`, `actual_amplification_ratio`,
`left/right_unmatched_count|rate`, `matched_row_count`

Filter: `filtered_row_count`, `null_policy`  
Union: `column_order`, `schema_changes`  
Aggregate: `group_count`, metrics list

---

## 11. Lineage

step-level dict: inputs/output + column_map / key_map / metrics.

예: rename `source.old → renamed.new`, join key_map, aggregate `source.col → SUM → out.alias`.

Hidden `__source_file` 컬럼 삽입 없음 (provenance는 metadata).

---

## 12. Determinism

- LLM / randomness / heuristic choice 없음
- 동일 sources+plan → 동일 column order·row order(입력 순서·groupby sort)

---

## 13. Resource safety

Phase 16이 extreme amplification을 ERROR로 차단한다고 신뢰.  
이번 Phase에서 absolute row cap 등 resource guard는 **미구현** (semantic과 분리 필요 시 Phase 18+ 설계).

---

## 14. Legacy

- `merge_engine`: 재사용하지 않음 (semantic inference 포함)
- `aggregate_merge` / `plan_engine`: production 유지, Executor 중심 아님
- `route_multi` 미전환

---

## 15. Files

```text
core/integrate/integration_execution_types.py  (new)
core/integrate/integration_execute.py           (new)
core/integrate/__init__.py                      (exports)
tests/test_phase17_integration_executor.py      (new)
docs/learning_note/phase17_integration_executor.md
```

---

## 16. Phase 18 Handoff

Result Validator 입력 후보:

```text
CrossFileUnderstanding
IntegrationPlan
IntegrationValidationResult
IntegrationExecutionResult
  ├─ final_output DataFrame
  ├─ step_results[].metadata (actual amp, unmatched, row deltas)
  └─ lineage
```

Result Validator가 위험 amplification / 0행 / grain 이상을 판단하고, retry는 Planner가 담당.
