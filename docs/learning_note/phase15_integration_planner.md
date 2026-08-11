# Phase 15 — IntegrationPlan v1 & LLM Integration Planner

> **구현 Phase** (Validator/Executor/route_multi 전환 없음)  
> 입력: Phase 14 `CrossFileUnderstanding` + `user_prompt`  
> 출력: `IntegrationPlan` (`planned` | `cannot_plan`)

---

## 1. Goal

Cross-file understanding을 받아 **실행 가능한 atomic IntegrationPlan**을 LLM이 생성하도록 한다.  
실행·검증·retry diversity는 Phase 16~18.

---

## 2. Architecture

```text
CrossFileUnderstanding + user_prompt
        ↓
build_integration_plan()          [LLM]
        ↓
integration_plan_from_dict()      [structural parse only]
        ↓
IntegrationPlan
```

모듈:

| 파일 | 역할 |
|---|---|
| `core/integrate/integration_plan_types.py` | contract + structural parser + signature |
| `core/integrate/integration_planner.py` | LLM planner + compact prompt + format retry |

Legacy `plan_types.py` / `plan_builder.py` / `plan_engine.py` (`aggregate_merge`)는 **유지** (coexistence).

---

## 3. IntegrationPlan v1 Contract

### planned

```json
{
  "status": "planned",
  "steps": [
    {
      "id": "step_1",
      "op": "join",
      "inputs": ["customers", "orders"],
      "output": "joined",
      "params": {
        "left_keys": ["customer_id"],
        "right_keys": ["customer_id"],
        "how": "left"
      }
    }
  ],
  "final_output": "joined",
  "reason": null,
  "ambiguities": [],
  "notes": []
}
```

### cannot_plan (first-class)

```json
{
  "status": "cannot_plan",
  "steps": [],
  "final_output": null,
  "reason": "Multiple plausible key relationships remain unresolved.",
  "ambiguities": ["customer_id and account_id both have strong evidence"],
  "notes": []
}
```

Status vocabulary: **`planned` | `cannot_plan`** only.

---

## 4. Atomic Operation Vocabulary

```text
rename_columns
filter_rows
union_rows
join
aggregate
select_columns
```

**Not in v1 main ops:** `aggregate_merge`, sort, derive, pivot, layout.

---

## 5. Operation Contracts

| op | required params | notes |
|---|---|---|
| rename_columns | `mapping` | non-empty |
| filter_rows | `conditions[{column,operator,value}]` | ops: eq/ne/gt/gte/lt/lte (`==`→eq) |
| union_rows | optional `column_policy` | default `aligned`; ≥2 inputs |
| join | `left_keys`, `right_keys`, `how` | exactly 2 inputs; how∈inner/left/right/outer |
| aggregate | `group_by`, `metrics[{column,function,alias?}]` | fn∈sum/mean/median/min/max/count |
| select_columns | `columns` | non-empty |

Parser = **shape/type**. Column existence / schema compatibility = **Phase 16**.

---

## 6. Planner Input

Compacted from `CrossFileUnderstanding.to_dict()`:

- `file_profiles[].observations` (columns trimmed samples)
- `pairwise_observations[]` (+ top candidate_pairs)
- `relationships[]` (label, key_candidates, ambiguities, evidence)

Plus **user_prompt**. Raw DataFrames are not dumped into the prompt.

---

## 7. Planner Prompt

System rules include:

- relationship ≠ operation
- key_candidates not truth
- no filename semantics / numeric→additive assumption
- minimal steps
- `cannot_plan` allowed
- plan only (no execution)

---

## 8. User Prompt Responsibility

User intent (append vs link vs aggregate) is interpreted by the LLM Planner.  
No Python shortcuts like `if "합산" in prompt`.

---

## 9. CrossFileRelationship Usage

Hints for the Planner only. **No** deterministic:

```python
if relationship == "same_schema": op = "union_rows"
```

---

## 10. Safe Failure

`cannot_plan` for unrelated / ambiguous keys / insufficient evidence / unsupported transforms.  
Parse exhaustion also yields `cannot_plan` with `reason=planner_parse_failed` (not a semantic guess plan).

---

## 11. Parsing / Format Retry

1. LLM JSON → `integration_plan_from_dict`
2. On `IntegrationPlanParseError` / call failure → **1 format retry** with parse feedback
3. Still failing → `cannot_plan`

**No semantic repair** (no auto keys/metrics/op rewrite).

---

## 12. Python vs LLM Boundary

| Python OK | Forbidden |
|---|---|
| enum/casing normalize | key_candidates[0] inject |
| `==` → `eq`, `avg` → `mean` | same_schema → union |
| empty optional → defaults (`column_policy`) | numeric → sum metrics |
| reject unsupported op | rewrite to aggregate_merge |
| signature helper | invent columns/sources |

---

## 13. Legacy aggregate_merge Position

```text
aggregate_merge = legacy sugar / compatibility path (plan_engine)
IntegrationPlan v1 main vocabulary does NOT include aggregate_merge
```

Conceptual decomposition for later compile:

```text
filter_rows → union_rows → aggregate
```

---

## 14. Tests

`tests/test_phase15_integration_planner.py` (17):

- contract parse (union, cannot_plan, filter alias)
- unsupported op / missing join keys / missing metrics (no autofill)
- mock planner: same-schema union, master/detail join, union→aggregate
- ambiguous / unrelated → cannot_plan
- invalid op → retry → cannot_plan
- signature stability
- CrossFileUnderstanding object input

---

## 15. Regression Result

```text
354 passed, 1 skipped
```

(Phase 14: 337 → +17 Phase 15; no regressions.)

---

## 16. Files Changed

```text
core/integrate/integration_plan_types.py   (new)
core/integrate/integration_planner.py      (new)
core/integrate/__init__.py                 (exports)
tests/test_phase15_integration_planner.py  (new)
docs/learning_note/phase15_integration_planner.md
```

Unchanged: route_multi, plan_engine executor, AnalysisPlan, merge_engine main flow.

---

## 17. Known Limitations

- No plan-time semantic validation yet (Phase 16)
- No execution (Phase 17)
- Live LLM quality not measured (Phase 19)
- Intermediate dependency order beyond final_output∈outputs is lightly checked; full DAG checks in Phase 16
- `union_rows` column_policy semantics deferred to Validator/Executor

---

## 18. Phase 16 Handoff

Validator can immediately check:

```text
source existence vs file_profiles
column existence in rename/join/aggregate/select/filter
join key dtype / coverage (using pairwise observations)
union schema compatibility
aggregate metric type compatibility
step dependency order / forward refs / collisions
ambiguity: planned despite relationship=ambiguous without resolution note
```

Contract already rejects: unknown ops, missing join keys shape, empty metrics, duplicate outputs, final_output not in step outputs.
