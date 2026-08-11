# Phase 16 — IntegrationPlan Validator

> **구현 Phase** (실행/retry/route_multi 전환 없음)  
> 입력: `CrossFileUnderstanding` + `IntegrationPlan`  
> 출력: `IntegrationValidationResult`

---

## 1. Goal

Planner가 만든 `IntegrationPlan`이 **실제 파일 구조에서 안전하게 실행 가능한지** structural·observational 검증만 수행한다.

```text
CrossFileUnderstanding + IntegrationPlan
        ↓
validate_integration_plan()
        ↓
IntegrationValidationResult
  (valid | errors / warnings / infos + lineage)
```

**하지 않음:** pandas join/union/aggregate 실행, key/op 자동 수정, Planner retry loop.

---

## 2. Architecture

| 파일 | 역할 |
|---|---|
| `core/integrate/integration_validation_types.py` | Issue/Result contract + Phase 18 feedback formatter |
| `core/integrate/integration_plan_validate.py` | `validate_integration_plan()` — deterministic checks |
| `tests/test_phase16_integration_validator.py` | valid / invalid / warning / immutability |

Planner(`integration_planner.py`)와 types(`integration_plan_types.py`)는 **수정하지 않고 소비**한다.

---

## 3. IntegrationValidationResult Contract

```text
IntegrationValidationResult
├─ valid: bool                 # errors 비어 있으면 True
├─ errors: list[Issue]         # 실행 금지
├─ warnings: list[Issue]       # 실행 가능하나 위험
├─ infos: list[Issue]          # 관측 정보
├─ metadata: dict
└─ lineage: list[dict]         # step별 columns_used / metrics 요약
```

```text
IntegrationValidationIssue
├─ code
├─ severity   # error | warning | info
├─ message
├─ step_id?
└─ details    # evidence only (정답 key/op 제안 필드 차단)
```

`format_integration_validation_feedback(result)` → Phase 18 retry용 structured lines  
(`Failure stage: integration_plan_validation`, evidence, “Do not invent keys/ops”).

---

## 4. 핵심 원칙

1. **Validator ≠ semantic planner** — key/op/aggregation 자동 선택·수정 금지  
2. **관측 가능한 사실만** — column 존재, dtype family, uniqueness, null, overlap, row count  
3. **애매하면 Planner로 반환** — `ambiguous_key_selection` 등  
4. **Plan immutability** — 검증 전후 `plan.to_dict()` 동일 (assert + 테스트)

`user_prompt` 인자는 예약만 하고 Phase 16에서는 semantic keyword hardcoding 정렬을 하지 않는다 (Phase 18).

---

## 5. Common structure checks

| 검사 | severity |
|---|---|
| missing / duplicate step id | ERROR |
| nonexistent input (source/prior output) | ERROR |
| output collides with source / duplicate output | ERROR |
| unsupported op | ERROR |
| `planned` + empty steps / missing final_output | ERROR |
| unresolved final_output | ERROR |

중간 step output은 **스키마 시뮬레이션**(rename/filter/union/join/aggregate/select)으로 다음 step 검증에 전달한다. 실제 pandas 실행은 없다.

---

## 6. Operation rules (요약)

### rename_columns
- 원본 컬럼 존재, 빈 이름, 다중→동일명 충돌, 기존 컬럼과의 충돌 → ERROR

### filter_rows
- column/operator 존재, dtype 호환, between 범위, column-vs-column 양쪽 존재  
- 명백한 impossible filter → WARNING (0행이 의도일 수 있음)

### union_rows
- schema overlap / only_left / only_right / dtype matrix  
- `column_policy=aligned` + 양쪽에 exclusive + overlap ≤ 0.5 → `union_incompatible_schema` ERROR  
- 부분 불일치 → `union_partial_schema` WARNING  
- Phase 15 contract에 없는 필드를 새로 요구하지 않음 (`column_policy`만 사용)

### join
- 2 inputs, keys 존재, how ∈ contract  
- key dtype: incompatible → ERROR; normalizable mismatch → WARNING  
- null ratio / low inner overlap → WARNING  
- cardinality / amplification → 아래 정책  
- relationship label consistency → §8

### aggregate
- group_by / metric column 존재, fn ∈ contract  
- string에 sum/mean/… → ERROR  
- alias 충돌 → ERROR  
- summary-like rows 가능 → WARNING (detail 자동 필터 금지)

### select_columns
- 존재/중복/empty → ERROR  
- 이후 join이 필요로 하는 key를 제거한 경우 (시뮬레이션 스키마) → ERROR

---

## 7. Join cardinality & many-to-many

Cardinality 출처:

1. pairwise observation `cardinality_evidence` (우선)  
2. 없으면 uniqueness threshold로 유도 (`≥0.98` unique 등)

| cardinality | 정책 |
|---|---|
| one_to_one / one_to_many / many_to_one | INFO + amplification 검사 |
| many_to_many | **ERROR** (`many_to_many_join_risk`) — 다른 key 제안 금지 |
| unknown | amplification만으로 위험 판단 |

명시적 user intent 플래그는 Phase 15 contract에 없으므로 v1에서는 many-to-many를 기본적으로 실행 금지(ERROR).

---

## 8. Amplification

pandas join 없이 근사:

```text
left_mult  ≈ 1 / uniqueness_left
right_mult ≈ 1 / uniqueness_right
overlap_keys ≈ overlap_ratio * min(left_distinct, right_distinct)
estimated_rows ≈ f(cardinality, overlap_keys, mult)
amplification_ratio = estimated_rows / max(left_rows, right_rows)
```

Constants:

| 상수 | 값 | 의미 |
|---|---|---|
| `AMP_WARNING_RATIO` | 2.0 | mild amplification WARNING |
| `AMP_ERROR_RATIO` | 10.0 | extreme amplification ERROR |
| `NULL_KEY_WARNING` / `STRONG` | 0.05 / 0.25 | null key WARNING |
| `LOW_MATCH_WARNING` | 0.25 | inner low overlap WARNING |
| `UNION_OVERLAP_STRICT` | 0.5 | aligned union incompatible |

---

## 9. CrossFileUnderstanding consistency

Join step에 대해 relationship label:

| label | Validator |
|---|---|
| `unrelated` | ERROR `join_against_unrelated` |
| `ambiguous` | ERROR `ambiguous_key_selection` |
| `insufficient_evidence` | ERROR `insufficient_evidence_forced_join` |
| join/master_detail/lookup/partial/same/compatible | INFO evidence only |

**금지:** `same_schema → must union` 같은 label→op 강제.

---

## 10. cannot_plan

정상적인 safe outcome:

- `steps` 비어 있어야 함 (아니면 ERROR)  
- `final_output` 없어야 함  
- `reason` 없으면 WARNING  
- `valid=True` (구조만 맞으면) — 실행 실패로 취급하지 않음  
- INFO `cannot_plan_accepted`

---

## 11. Lineage metadata

step별 예:

```json
{
  "step_id": "step_1",
  "op": "join",
  "inputs": ["customers", "orders"],
  "output": "joined",
  "columns_used": ["customers.customer_id", "orders.customer_id"],
  "join_how": "left",
  "cardinality_evidence": "one_to_many"
}
```

aggregate는 `metrics[{column,function,alias}]` 포함. Phase 17/18용 최소 준비.

---

## 12. Warning vs Error (정책 요약)

**ERROR:** dependency 깨짐, 없는 column/op, missing keys, many-to-many, extreme amp, unrelated/ambiguous/insufficient forced join, impossible aggregate, missing final_output, incompatible union(aligned)

**WARNING:** low overlap, null keys, mild amp, partial union, dtype coercion, subtotal/detail 혼재

**INFO:** cardinality, match/amp estimates, relationship evidence, final_output resolve

---

## 13. Tests

`tests/test_phase16_integration_validator.py` — 28 cases  
Valid / Invalid / Warning / immutability / no key-op rewrite / feedback / lineage

Regression:

```text
382 passed, 1 skipped
```

(Phase 15: 354 → +28 Phase 16)

---

## 14. Files Changed

```text
core/integrate/integration_validation_types.py   (new)
core/integrate/integration_plan_validate.py      (new)
core/integrate/__init__.py                       (exports)
tests/test_phase16_integration_validator.py      (new)
docs/learning_note/phase16_integration_validator.md
```

Unchanged: route_multi, merge_engine, plan_engine executor, AnalysisPlan single-file path.

---

## 15. Phase 17 Handoff (Executor)

Executor가 기대할 contract:

```text
valid IntegrationPlan (status=planned)
+ IntegrationValidationResult.valid == True
+ lineage (optional guidance)
→ deterministic pandas ops per step
→ materialize final_output DataFrame
```

Executor **금지:** validation 실패 plan 자동 수리, key 재선택, cannot_plan 실행.

---

## 16. Deferred (semantic / Phase 18+)

- user_prompt ↔ plan intent alignment (keyword hardcoding 없이)
- Planner retry loop + diversity
- Result Validator (실행 후)
- Live multi-file benchmark
- budget-specific rules
