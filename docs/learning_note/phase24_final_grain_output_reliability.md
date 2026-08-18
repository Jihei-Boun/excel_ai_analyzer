# Phase 24 — Final Grain & Output Selection Reliability

## Goal

올바른 integration/join chain 이후 **불필요 aggregate / 잘못된 final select**로
final grain·required fields가 깨지는 문제를 범용적으로 개선한다.

Phase 23 semantic equivalence는 유지. alias golden 하드코딩 / evaluator 추가 완화 없음.

---

## Phase 23 baseline

| KPI | P23 |
|-----|-----|
| unsafe | 0% |
| safe | 94.74% |
| overall_ok | 73.68% |
| grain_mismatch | 10.53% |
| structural_mismatch | 10.53% |
| true_wrong_result | 0% |
| composite_key / final | 100% / 0% |
| three_file join / final | 100% / 0% |
| dirty final | 0% |
| lookup final | 0% |

---

## Residual probe

`benchmark_results/multi/phase24_residual_probe.json`

| case | taxonomy | root cause |
|------|----------|------------|
| composite | unnecessary_transformation + wrong_final_grain | join OK → 불필요 aggregate |
| dirty | unnecessary_transformation + wrong_final_grain | rename+union 후 aggregate |
| lookup | required_field_loss | select가 key/field drop |
| three_file | wrong_final_selection | join OK; group key presentation |
| budget (P23) | planner_semantic_failure | join 시도 → suffix → failed (**safe 하락 원인**) |

---

## Final grain / contract design

### 도입: optional `final_output_requirements`

```json
{
  "grain": "detail|entity|group|summary",
  "required_columns": ["..."]
}
```

- Planner 선언 / Python 의미 채움 금지
- Executor 미사용
- Validator: required_columns ⊆ simulated final schema → **ERROR**
- grain vs plan structure → **WARNING** (아래 근거)
- Result Validator: declared required_columns ⊆ actual final

### Grain ERROR → WARNING 전환 근거 (live mid-phase)

첫 live에서 LLM이 올바른 aggregate plan에 `grain=detail`을 잘못 붙여
`final_grain_contradiction` ERROR → retry exhausted가
`union_aggregate` / `filter_union_aggregate` / `three_file`에 발생 (safe↓84%).

구조만으로 “잘못된 aggregate” vs “잘못된 grain 라벨”을 구분할 수 없어:

- **hard gate**: `final_required_field_missing` (detail fields declared but dropped)
- **soft signal**: grain contradiction warning

로 조정. Evaluator는 완화하지 않음.

---

## Production changes

| File | Change |
|------|--------|
| `integration_plan_types.py` | `FinalOutputRequirements` |
| `integration_plan_validate.py` | required field ERROR + grain WARNING |
| `integration_result_validate.py` | declared columns vs actual |
| `integration_planner.py` | final-grain/select/requirements self-check |
| feedback types | contract retry hints |
| Executor | unchanged |

---

## Live 3-run (final, after grain WARNING fix)

`qwen2.5:7b`, 19×3 — `phase24_live_3run_summary.json`

| metric | mean |
|--------|------|
| unsafe | **0** |
| safe | 89.47 |
| overall_ok | **73.68** (P23와 동일) |
| semantic_result_accuracy | 76.92 |
| true_wrong_result | **0** |
| grain_mismatch | **5.26** (−5.26pp) |
| structural_mismatch | 10.53 |
| dirty_final | 0 (run variance; mid-run은 100%였음) |
| lookup_final | 0 |
| composite_key | **100** |
| composite_final | 0 |
| three_file_join | **100** |
| three_file_final | 0 |
| unnecessary_cannot_plan | **0** |
| retry_exhausted | 21.05 |

### Attribution

```text
P23 overall 73.68
+ production dirty recovery (일부 run) / budget recovery
+ grain_mismatch 감소
− dirty/unrelated failed variance (safe↓)
− composite/lookup/three_file final 미회복
= P24 overall 73.68
evaluator correction ≈ 0pp
```

---

## Residuals / Shadow

가장 취약: **composite post-join aggregate**, **lookup final select**, **three-file final group columns**.

**권고: B — Phase 25 Semantic Reliability III** (route_multi shadow 보류).

이유: overall 정체, safe 회귀, composite/three-file final 0 유지.

---

## Tests / deterministic

pytest: **518 passed, 2 skipped** (phase24 tests 포함)  
deterministic: **100 / 100 / 0**
